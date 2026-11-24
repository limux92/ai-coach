"""Deterministic manifests, sha256 verification, secure receipt loading, and bundle creation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

from release_git import REPOSITORY, ReleaseError, require, routine_scope, validate_paths

SCHEMA_VERSION = 2
SERVICES = ("ai-coach-sync", "ai-coach-chat")


def valid_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def file_sha256(path: Path) -> str:
    """Compute sha256 digest of a regular file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_integrity_digest(receipt: dict) -> str:
    """Deterministic sha256 digest of canonical receipt contents."""
    cleaned = {k: v for k, v in receipt.items() if k not in {"integrity_digest", "receipt_digest"}}
    canonical = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def manifest_sha256(manifest: dict) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_directory_manifest(directory: Path) -> dict[str, dict[str, str | int]]:
    """Build deterministic file manifest from directory."""
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), f"Directory not found or symlink: {directory}")
    manifest: dict[str, dict[str, str | int]] = {}

    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory).as_posix()
        pure = PurePosixPath(rel)
        require(not pure.is_absolute() and ".." not in pure.parts, f"Path traversal in directory entry: {rel}")
        require(not path.is_symlink(), f"Symlinks are not allowed in release bundle: {rel}")

        st = path.stat()
        mode = stat.S_IMODE(st.st_mode)
        if path.is_dir():
            require(mode == 0o755, f"Directory mode must be 0755: {rel} (got {oct(mode)})")
        elif path.is_file():
            require(mode in (0o644, 0o755), f"File mode must be 0644 or 0755: {rel} (got {oct(mode)})")
            manifest[rel] = {
                "sha256": file_sha256(path),
                "mode": oct(mode),
                "size": st.st_size,
            }
        else:
            raise ReleaseError(f"Unsupported filesystem object: {rel}")

    return manifest


def verify_directory_manifest(directory: Path, expected_manifest: dict[str, dict[str, str | int]]) -> None:
    """Verify that directory exactly matches expected manifest with no extra or missing files."""
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), f"Directory missing or symlink: {directory}")

    # Check for path escapes in manifest keys
    for rel_path in expected_manifest:
        pure = PurePosixPath(rel_path)
        require(not pure.is_absolute() and ".." not in pure.parts, f"Path escape in manifest: {rel_path}")
        target = directory / rel_path
        require(not target.is_symlink(), f"Symlink artifact in manifest: {rel_path}")
        try:
            resolved = target.resolve()
            require(resolved.is_relative_to(directory.resolve()), f"Artifact escapes directory: {rel_path}")
        except Exception:
            raise ReleaseError(f"Cannot resolve artifact path: {rel_path}")

    actual_manifest = build_directory_manifest(directory)

    extra_files = set(actual_manifest.keys()) - set(expected_manifest.keys())
    require(not extra_files, f"Unexpected extra files in bundle: {sorted(extra_files)}")

    missing_files = set(expected_manifest.keys()) - set(actual_manifest.keys())
    require(not missing_files, f"Missing files in bundle: {sorted(missing_files)}")

    for rel_path, expected in expected_manifest.items():
        actual = actual_manifest[rel_path]
        require(actual["sha256"] == expected["sha256"],
                f"Artifact hash mismatch for {rel_path}: expected {expected['sha256']} got {actual['sha256']}")
        expected_mode = expected["mode"] if isinstance(expected["mode"], str) else oct(expected["mode"])
        require(actual["mode"] == expected_mode,
                f"Artifact mode mismatch for {rel_path}: expected {expected_mode} got {actual['mode']}")
        require(actual["size"] == expected["size"],
                f"Artifact size mismatch for {rel_path}: expected {expected['size']} got {actual['size']}")


def create_bundle(directory: Path, git_command_fn, tree_sha: str, assets: dict[str, str], root: Path) -> dict[str, Path]:
    """Create normalized non-root-readable immutable bundle directories from git tree and built assets."""
    directory = Path(directory)
    destination = directory / "source"
    require(not destination.exists(), f"Destination bundle directory already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    archive = directory / "source.zip"
    git_command_fn("archive", "--format=zip", "--output=" + str(archive), tree_sha)
    require(archive.is_file(), "Git archive failed to create bundle zip.")

    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            name = PurePosixPath(item.filename)
            require(not name.is_absolute() and ".." not in name.parts, f"Unsafe source archive entry: {item.filename}")
            mode = item.external_attr >> 16
            require(mode & 0o170000 != 0o120000, f"Symlinks are not allowed in release bundle: {item.filename}")
            target = destination / item.filename
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(item))
                target.chmod(0o755 if (mode & 0o111) else 0o644)

    # Clean up intermediate zip archive
    archive.unlink()

    # Embed static dashboard assets
    dashboard_dest = destination / "adapters/mcp/static/dashboard"
    dashboard_dest.mkdir(parents=True, exist_ok=True)
    for name, expected_digest in sorted(assets.items()):
        pure_asset = PurePosixPath(name)
        require(not pure_asset.is_absolute() and ".." not in pure_asset.parts, f"Unsafe dashboard asset path: {name}")
        src_asset = root / "adapters/mcp/static/dashboard" / name
        require(src_asset.is_file(), f"Missing dashboard asset: {name}")
        require(not src_asset.is_symlink(), f"Symlinks are not allowed in dashboard assets: {name}")
        content = src_asset.read_bytes()
        actual_digest = hashlib.sha256(content).hexdigest()
        require(actual_digest == expected_digest, f"Dashboard asset changed during checks: {name}")
        target_asset = dashboard_dest / name
        target_asset.parent.mkdir(parents=True, exist_ok=True)
        target_asset.write_bytes(content)
        target_asset.chmod(0o644)

    # Normalize directory permissions across entire source bundle
    destination.chmod(0o755)
    for dirpath in destination.rglob("*"):
        if dirpath.is_dir():
            dirpath.chmod(0o755)

    # Verify all files in bundle adhere to normalized permissions
    for filepath in destination.rglob("*"):
        require(not filepath.is_symlink(), f"Symlink found in bundle: {filepath}")
        st = filepath.stat()
        mode = stat.S_IMODE(st.st_mode)
        if filepath.is_dir():
            require(mode == 0o755, f"Non-normalized directory mode: {filepath} ({oct(mode)})")
        elif filepath.is_file():
            require(mode in (0o644, 0o755), f"Non-normalized file mode: {filepath} ({oct(mode)})")

    return {
        "ai-coach-sync": destination,
        "ai-coach-chat": destination / "adapters/mcp",
    }


def load_receipt(receipt_path: Path) -> dict:
    """Securely load and validate schema v2 release receipt and its integrity digest."""
    supplied = Path(receipt_path)
    require(not supplied.is_symlink(), f"Receipt path must not be a symlink: {supplied}")
    if supplied.is_dir():
        receipt_dir = supplied
        path = supplied / "summary.json"
    else:
        receipt_dir = supplied.parent
        path = supplied
    require(receipt_dir.is_dir() and not receipt_dir.is_symlink(),
            f"Receipt directory not found or is symlink: {receipt_dir}")

    require(path.is_file() and not path.is_symlink(), f"Receipt file not found or is symlink: {path}")

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as error:
        raise ReleaseError(f"Malformed receipt JSON in {path}: {error}") from None

    require(isinstance(data, dict), f"Receipt root must be JSON object: {path}")
    require(data.get("schema_version") == SCHEMA_VERSION,
            f"Unsupported receipt schema version: {data.get('schema_version')} (expected {SCHEMA_VERSION})")
    require(data.get("status") == "checked",
            f"Deploy requires a successful checked receipt, got: {data.get('status')}")
    require(data.get("local_checks_passed") is True,
            "Deploy requires local_checks_passed=true in the check receipt")
    require(data.get("repository") == REPOSITORY,
            f"Invalid repository in receipt: {data.get('repository')}")

    publication_files = data.get("publication_files")
    require(isinstance(publication_files, list) and publication_files
            and all(isinstance(name, str) and name for name in publication_files)
            and len(publication_files) == len(set(publication_files)),
            "Receipt must contain a nonempty unique publication_files list")
    validate_paths(publication_files)
    infra_digest = data.get("reviewed_infra_diff_sha256")
    routine_scope(publication_files, physiology_migration=infra_digest is not None,
                  infra_diff_sha256=infra_digest)

    tree = data.get("tree")
    require(isinstance(tree, str) and len(tree) == 40 and all(c in "0123456789abcdefABCDEF" for c in tree),
            f"Invalid or missing tree SHA in receipt: {tree}")

    services = data.get("services")
    require(isinstance(services, list) and set(services) == set(SERVICES),
            f"Invalid services in receipt: {services}")

    require("assets" in data and isinstance(data["assets"], dict) and data["assets"],
            "Receipt missing assets manifest")
    for asset_name, asset_sha in data["assets"].items():
        pure = PurePosixPath(asset_name)
        require(not pure.is_absolute() and ".." not in pure.parts, f"Path traversal in asset manifest: {asset_name}")
        require(valid_sha256(asset_sha), f"Malformed asset sha256: {asset_name}")

    require("bundle_manifest" in data and isinstance(data["bundle_manifest"], dict),
            "Receipt missing bundle_manifest")
    require(bool(data["bundle_manifest"]), "Receipt bundle_manifest must not be empty")
    for name, row in data["bundle_manifest"].items():
        pure = PurePosixPath(name)
        require(not pure.is_absolute() and ".." not in pure.parts and name not in {"", "."},
                f"Path escape in bundle manifest: {name}")
        require(isinstance(row, dict) and set(row) == {"sha256", "mode", "size"},
                f"Malformed bundle manifest entry: {name}")
        require(valid_sha256(row.get("sha256")), f"Malformed bundle sha256: {name}")
        require(row.get("mode") in {"0o644", "0o755"}, f"Malformed bundle mode: {name}")
        size = row.get("size")
        require(isinstance(size, int) and not isinstance(size, bool) and size >= 0,
                f"Malformed bundle size: {name}")

    bundles = data.get("bundles")
    expected_paths = {"ai-coach-sync": "source", "ai-coach-chat": "source/adapters/mcp"}
    require(isinstance(bundles, dict) and set(bundles) == set(SERVICES),
            "Receipt must describe both deployment bundles")
    for service, expected_path in expected_paths.items():
        row = bundles.get(service)
        require(isinstance(row, dict) and row.get("path") == expected_path
                and valid_sha256(row.get("manifest_sha256")),
                f"Malformed bundle descriptor: {service}")

    digest = data.get("integrity_digest")
    require(valid_sha256(digest), "Missing or malformed integrity_digest")

    expected_digest = compute_integrity_digest(data)
    require(digest == expected_digest, "Receipt integrity digest mismatch: receipt was tampered with or corrupted")

    return data


def verify_artifacts(receipt_dir: Path, receipt: dict) -> None:
    """Verify all receipt artifacts, bundles, permissions, digests, and check for escapes."""
    receipt_dir = Path(receipt_dir)
    require(receipt_dir.is_dir() and not receipt_dir.is_symlink(), f"Invalid receipt directory: {receipt_dir}")
    receipt_dir = receipt_dir.resolve()

    # Check assets.json if present
    assets_file = receipt_dir / "assets.json"
    if assets_file.exists():
        require(assets_file.is_file() and not assets_file.is_symlink(), "assets.json must be regular file")
        try:
            saved_assets = json.loads(assets_file.read_text(encoding="utf-8"))
        except Exception as error:
            raise ReleaseError(f"Malformed assets.json: {error}") from None
        require(saved_assets == receipt.get("assets"), "assets.json does not match receipt assets")

    source_dir = receipt_dir / "source"
    require(source_dir.is_dir() and not source_dir.is_symlink(), f"Source bundle missing: {source_dir}")

    verify_directory_manifest(source_dir, receipt["bundle_manifest"])
    expected_paths = {"ai-coach-sync": source_dir, "ai-coach-chat": source_dir / "adapters/mcp"}
    for service, directory in expected_paths.items():
        require(directory.is_dir() and not directory.is_symlink(), f"Bundle directory missing: {service}")
        descriptor = receipt["bundles"][service]
        require(directory.resolve().is_relative_to(receipt_dir), f"Bundle escapes receipt directory: {service}")
        require(manifest_sha256(build_directory_manifest(directory)) == descriptor["manifest_sha256"],
                f"Bundle manifest digest mismatch: {service}")
