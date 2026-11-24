#!/usr/bin/env python3
"""Local-only gate: staging checks, test suites, secret scans, and normalized bundle creation."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from release_artifacts import (
    SCHEMA_VERSION,
    SERVICES,
    build_directory_manifest,
    compute_integrity_digest,
    create_bundle,
    manifest_sha256,
)
from release_git import GitRelease, REPOSITORY, ReleaseError, require, routine_scope

ROOT = Path(__file__).resolve().parents[1]


class Runner:
    """Run local check commands and log output securely."""
    def __init__(self, directory: Path, receipt: dict):
        self.directory, self.receipt = directory, receipt
        self.counter = 0

    def save(self):
        (self.directory / "summary.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def __call__(self, args, *, label, cwd=None, timeout=900, allowed=(0,)):
        self.counter += 1
        log = self.directory / f"{self.counter:03d}.log"
        self.receipt["stage"] = label
        self.receipt["last_log"] = str(log)
        self.save()
        print(f"[{self.counter}] {label} | {log.name}", flush=True)
        started = time.monotonic()
        try:
            process = subprocess.Popen(args, cwd=cwd or ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
        except OSError:
            log.write_text("Could not start executable: " + str(args[0]) + "\n")
            raise ReleaseError(f"Cannot start {label}; check executable {args[0]}.") from None
        try:
            while True:
                try:
                    output, error = process.communicate(timeout=min(15, timeout))
                    break
                except subprocess.TimeoutExpired:
                    require(time.monotonic() - started < timeout, label + " timed out; inspect the log.")
                    print(f"  {label}: still running ({int(time.monotonic() - started)}s)", flush=True)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, 15)
            try:
                output, error = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                output, error = process.communicate()
            log.write_text(output + "\n" + error)
            raise
        log.write_text(output + "\n" + error)
        require(process.returncode in allowed, f"{label} failed (exit {process.returncode}). Inspect {log}; no automatic retry.")
        return output


def run_checks(run: Runner, git: GitRelease, *, root: Path = ROOT) -> dict[str, str]:
    """Execute local tests, builds, and secret scans without mutating external systems."""
    require(not any(name.startswith("VITE_") for name in os.environ),
            "Unset VITE_* environment overrides before building.")
    env_files = [p for p in (root / "dashboard").glob(".env*") if p.name != ".env.example"]
    require(not env_files, "Remove private dashboard .env overrides from this build; release assets must be reproducible.")

    for path in (root / ".venv/bin/python", root / "adapters/mcp/.venv/bin/python", root / ".tools/gitleaks/gitleaks"):
        require(path.is_file(), "Missing prerequisite: " + str(path))

    run([str(root / ".venv/bin/python"), "-m", "pytest", "-q"], label="Backend tests", cwd=root)
    run([str(root / "adapters/mcp/.venv/bin/python"), "-m", "pytest", "-q"],
        cwd=root / "adapters/mcp", label="MCP adapter tests")
    run(["npm", "--prefix", "dashboard", "run", "check"], label="Dashboard formatting, tests, build and budgets", cwd=root)

    scanner = str(root / ".tools/gitleaks/gitleaks")
    run([scanner, "git", "--staged", "--pre-commit", "--redact", "--no-banner", "."],
        label="Staged secret scan", cwd=root)
    run([scanner, "git", "--redact", "--no-banner", "--log-opts=--all", "."],
        label="Full-history secret scan", cwd=root)

    assets_dir = root / "adapters/mcp/static/dashboard"
    run([scanner, "dir", "--redact", "--no-banner", str(assets_dir)],
        label="Built assets secret scan", cwd=root)
    require((assets_dir / "index.html").is_file(), "Dashboard build did not produce index.html.")

    manifest: dict[str, str] = {}
    for path in sorted(assets_dir.rglob("*")):
        require(not path.is_symlink(), "Symlinks are not allowed in the dashboard bundle.")
        if path.is_file():
            rel = path.relative_to(assets_dir).as_posix()
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

    (run.directory / "assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    run.receipt["local_checks_passed"] = True
    return manifest


def validate_local_publication_scope(run: Runner, git: GitRelease, *, physiology_migration=False) -> list[str]:
    """Validate the existing local origin/main ref without fetching or publishing."""
    require(git.git("rev-parse", "--is-shallow-repository") == "false",
            "Use full Git history for the secret scan.")
    git.git("rev-parse", "--verify", "origin/main")
    paths = git.paths("diff", "--name-only", "origin/main")
    digest = None
    if physiology_migration:
        patch = git.git("diff", "--binary", "--full-index", "origin/main", "--", "infra/") + "\n"
        digest = hashlib.sha256(patch.encode()).hexdigest()
    routine_scope(paths, physiology_migration=physiology_migration, infra_diff_sha256=digest)
    run.receipt["publication_files"] = paths
    if physiology_migration:
        run.receipt["reviewed_infra_diff_sha256"] = digest
    (run.directory / "publication-files.json").write_text(json.dumps(paths, indent=2) + "\n")
    return paths


def check_and_bundle(run: Runner, git: GitRelease, args, *, root: Path = ROOT) -> dict:
    """Perform staging checks, test suites, bundle creation, and receipt sealing."""
    git.inspect(strict=True)
    validate_local_publication_scope(
        run, git, physiology_migration=getattr(args, "physiology_scheduler_migration", False))

    tree = git.fingerprint()
    require(bool(tree) and len(tree) == 40, f"Invalid write-tree result: {tree}")

    # Locked dashboard dependencies
    run(["npm", "--prefix", "dashboard", "ci", "--no-audit", "--no-fund"],
        label="Install locked dashboard dependencies", cwd=root)

    assets = run_checks(run, git, root=root)
    git.unchanged(tree)

    # Create immutable normalized bundle
    bundle_paths = create_bundle(run.directory, git.git, tree, assets, root)

    # Cloud source secret scan
    scanner = str(root / ".tools/gitleaks/gitleaks")
    run([scanner, "dir", "--redact", "--no-banner", str(run.directory / "source")],
        label="Cloud source secret scan", cwd=root)

    # Build directory manifest of generated bundle
    bundle_manifest = build_directory_manifest(run.directory / "source")
    bundles = {
        service: {
            "path": path.relative_to(run.directory).as_posix(),
            "manifest_sha256": manifest_sha256(build_directory_manifest(path)),
        }
        for service, path in bundle_paths.items()
    }

    run.receipt.update({
        "schema_version": SCHEMA_VERSION,
        "status": "checked",
        "repository": REPOSITORY,
        "services": list(SERVICES),
        "tree": tree,
        "assets": assets,
        "bundle_manifest": bundle_manifest,
        "bundles": bundles,
        "local_checks_passed": True,
    })

    # Seal receipt with integrity digest
    run.receipt["integrity_digest"] = compute_integrity_digest(run.receipt)
    run.save()
    return run.receipt


def parser():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physiology-scheduler-migration", action="store_true",
                   help="Allow separately reviewed Scheduler diff against origin/main")
    p.add_argument("--receipt-dir", type=Path, default=None,
                   help="Explicit receipt directory (defaults to .local/release-checks/<run_id>)")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)

    parent = ROOT / ".local/release-checks"
    parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.receipt_dir:
            supplied = Path(args.receipt_dir)
            require(not supplied.is_symlink() and not supplied.exists(),
                    "Explicit receipt directory must be a new nonsymlink path.")
            directory = supplied.resolve()
            require(directory.parent == parent.resolve(),
                    "Explicit receipt directory must be one direct child of .local/release-checks.")
            directory.mkdir()
            run_id = directory.name
        else:
            run_id = datetime.now(timezone.utc).strftime("%y%m%d-%H%M%S") + "-" + uuid4().hex[:4]
            directory = parent / run_id
            directory.mkdir()
    except (OSError, ReleaseError) as error:
        print("STOPPED: " + str(error), file=sys.stderr, flush=True)
        return 1

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "mode": "check",
        "repository": REPOSITORY,
        "services": list(SERVICES),
        "started_at": run_id,
    }
    run = Runner(directory, receipt)
    print("Release check receipts: " + str(directory), flush=True)

    try:
        lock_file = ROOT / ".local/release.lock"
        with lock_file.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReleaseError("Another release/check is running in this workspace.") from None

            git = GitRelease(run, ROOT, receipt)
            check_and_bundle(run, git, args, root=ROOT)

        print(f"Completed: {receipt['status']}. Receipt: {directory / 'summary.json'}", flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        receipt["status"] = "failed"
        message = str(error) if isinstance(error, ReleaseError) else type(error).__name__ + ": inspect last stage log."
        receipt["error"] = message
        print("STOPPED: " + message, file=sys.stderr, flush=True)
        print("Receipt: " + str(directory / "summary.json"), file=sys.stderr, flush=True)
        return 1
    finally:
        run.save()


if __name__ == "__main__":
    sys.exit(main())
