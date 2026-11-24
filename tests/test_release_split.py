"""Tests for deployment split: artifacts, check gate, and deployment validation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_artifacts as artifacts
import release_check as check
import release_deploy as deploy
from release_git import REPOSITORY, ReleaseError


def create_mock_git_repo(tmp_path: Path) -> tuple[Path, str]:
    """Helper to create a temporary git repo and return (repo_path, tree_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-b", "main")
    git("config", "user.name", "Test Runner")
    git("config", "user.email", "runner@example.invalid")
    git("remote", "add", "origin", f"https://github.com/{REPOSITORY}.git")

    (repo / "src").mkdir()
    (repo / "src/main.py").write_text("print('hello')\n")
    (repo / "adapters/mcp").mkdir(parents=True)
    (repo / "adapters/mcp/app.py").write_text("# adapter\n")
    (repo / "scripts").mkdir()
    script = repo / "scripts/run.sh"
    script.write_text("#!/bin/sh\necho ok\n")
    script.chmod(0o755)

    git("add", ".")
    tree_sha = git("write-tree")
    git("commit", "-m", "initial commit")
    return repo, tree_sha


def create_valid_receipt_fixture(tmp_path: Path, tree_sha: str = "0" * 40) -> tuple[Path, dict]:
    """Create a fully valid schema v2 receipt directory with source bundle and assets."""
    receipt_dir = tmp_path / "valid_receipt"
    receipt_dir.mkdir(parents=True)

    source_dir = receipt_dir / "source"
    source_dir.mkdir(parents=True, mode=0o755)
    (source_dir / "main.py").write_text("print('hello')\n")
    (source_dir / "main.py").chmod(0o644)
    (source_dir / "scripts").mkdir(mode=0o755)
    (source_dir / "scripts/run.sh").write_text("#!/bin/sh\n")
    (source_dir / "scripts/run.sh").chmod(0o755)

    dashboard_dir = source_dir / "adapters/mcp/static/dashboard"
    dashboard_dir.mkdir(parents=True, mode=0o755)
    (dashboard_dir / "index.html").write_text("<html></html>\n")
    (dashboard_dir / "index.html").chmod(0o644)

    # Normalize directory permissions to 0755
    source_dir.chmod(0o755)
    for p in source_dir.rglob("*"):
        if p.is_dir():
            p.chmod(0o755)

    bundle_manifest = artifacts.build_directory_manifest(source_dir)
    assets_manifest = {
        "index.html": hashlib.sha256(b"<html></html>\n").hexdigest(),
    }
    (receipt_dir / "assets.json").write_text(json.dumps(assets_manifest, indent=2) + "\n")

    receipt = {
        "schema_version": 2,
        "status": "checked",
        "started_at": "260927-120000-abcd",
        "repository": REPOSITORY,
        "services": ["ai-coach-sync", "ai-coach-chat"],
        "tree": tree_sha,
        "local_checks_passed": True,
        "publication_files": ["scripts/release_deploy.py"],
        "assets": assets_manifest,
        "bundle_manifest": bundle_manifest,
        "bundles": {
            "ai-coach-sync": {
                "path": "source",
                "manifest_sha256": artifacts.manifest_sha256(bundle_manifest),
            },
            "ai-coach-chat": {
                "path": "source/adapters/mcp",
                "manifest_sha256": artifacts.manifest_sha256(
                    artifacts.build_directory_manifest(source_dir / "adapters/mcp")),
            },
        },
    }
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt_dir, receipt


def test_valid_receipt_verification(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    loaded = artifacts.load_receipt(receipt_dir)
    assert loaded["schema_version"] == 2
    assert loaded["tree"] == receipt["tree"]
    assert loaded["integrity_digest"] == receipt["integrity_digest"]
    artifacts.verify_artifacts(receipt_dir, loaded)


def test_missing_receipt_raises(tmp_path):
    with pytest.raises(ReleaseError, match="Receipt file not found"):
        artifacts.load_receipt(tmp_path / "nonexistent")


def test_failed_status_receipt_raises(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    receipt["status"] = "failed"
    receipt["error"] = "pytest failed"
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))

    with pytest.raises(ReleaseError, match="successful checked receipt"):
        artifacts.load_receipt(receipt_dir)


@pytest.mark.parametrize("status,passed", [("planned", True), ("released", True), ("checked", False)])
def test_only_successful_checked_receipt_is_deployable(tmp_path, status, passed):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    receipt.update(status=status, local_checks_passed=passed)
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))
    with pytest.raises(ReleaseError, match="checked receipt|local_checks_passed"):
        artifacts.load_receipt(receipt_dir)


def test_malformed_receipt_raises(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)

    # Malformed JSON
    (receipt_dir / "summary.json").write_text("{not valid json")
    with pytest.raises(ReleaseError, match="Malformed receipt JSON"):
        artifacts.load_receipt(receipt_dir)

    # Wrong schema version
    receipt["schema_version"] = 1
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))
    with pytest.raises(ReleaseError, match="Unsupported receipt schema version"):
        artifacts.load_receipt(receipt_dir)

    # Invalid tree SHA
    receipt["schema_version"] = 2
    receipt["tree"] = "invalid_tree_hash"
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))
    with pytest.raises(ReleaseError, match="Invalid or missing tree SHA"):
        artifacts.load_receipt(receipt_dir)

    # Missing bundle_manifest
    receipt["tree"] = "0" * 40
    del receipt["bundle_manifest"]
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))
    with pytest.raises(ReleaseError, match="Receipt missing bundle_manifest"):
        artifacts.load_receipt(receipt_dir)


def test_receipt_integrity_digest_mismatch(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    # Tamper with receipt field without updating integrity_digest
    receipt["tree"] = "1" * 40
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))

    with pytest.raises(ReleaseError, match="Receipt integrity digest mismatch"):
        artifacts.load_receipt(receipt_dir)


def test_tree_mismatch_verification(tmp_path):
    repo, tree_sha = create_mock_git_repo(tmp_path)
    different_tree = "f" * 40
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path, tree_sha=different_tree)

    def git_fn(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    # Index tree mismatch
    with pytest.raises(ReleaseError, match="Current index tree mismatch"):
        deploy.verify_deployment_readiness(receipt_dir, git_fn=git_fn)

    # Commit tree mismatch
    with pytest.raises(ReleaseError, match="Commit HEAD\\^\\{tree\\} mismatch"):
        deploy.verify_commit_tree(git_fn, "HEAD", different_tree)


def test_commit_tree_invariant_explicit(tmp_path):
    repo, tree_sha = create_mock_git_repo(tmp_path)
    def git_fn(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    # Successful verification when commit tree matches
    deploy.verify_commit_tree(git_fn, "HEAD", tree_sha)

    # Failure when wrong tree expected
    with pytest.raises(ReleaseError, match="mismatch"):
        deploy.verify_commit_tree(git_fn, "HEAD", "e" * 40)


def test_one_byte_artifact_tampering_detected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    target_file = receipt_dir / "source/main.py"
    target_file.write_bytes(target_file.read_bytes() + b"# tampering\n")

    with pytest.raises(ReleaseError, match="Artifact hash mismatch"):
        artifacts.verify_artifacts(receipt_dir, receipt)


def test_one_byte_asset_file_tampering_detected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    assets_file = receipt_dir / "assets.json"
    assets_file.write_text(json.dumps({"index.html": "0" * 64}))

    with pytest.raises(ReleaseError, match="assets.json does not match receipt assets"):
        artifacts.verify_artifacts(receipt_dir, receipt)


def test_extra_file_in_bundle_detected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    extra = receipt_dir / "source/unexpected.txt"
    extra.write_text("extra file content")
    extra.chmod(0o644)

    with pytest.raises(ReleaseError, match="Unexpected extra files"):
        artifacts.verify_artifacts(receipt_dir, receipt)


def test_missing_file_in_bundle_detected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    (receipt_dir / "source/main.py").unlink()

    with pytest.raises(ReleaseError, match="Missing files in bundle"):
        artifacts.verify_artifacts(receipt_dir, receipt)


def test_symlink_rejected_in_bundle(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    symlink_file = receipt_dir / "source/link.py"
    symlink_file.symlink_to(receipt_dir / "source/main.py")

    with pytest.raises(ReleaseError, match="Symlinks are not allowed"):
        artifacts.build_directory_manifest(receipt_dir / "source")


def test_symlink_receipt_file_rejected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    real_summary = receipt_dir / "summary.json"
    link_summary = receipt_dir / "link_summary.json"
    link_summary.symlink_to(real_summary)

    with pytest.raises(ReleaseError, match="Receipt path must not be a symlink"):
        artifacts.load_receipt(link_summary)


def test_symlink_receipt_directory_rejected_by_deploy_verifier(tmp_path):
    receipt_dir, _ = create_valid_receipt_fixture(tmp_path)
    alias = tmp_path / "receipt-alias"
    alias.symlink_to(receipt_dir, target_is_directory=True)
    with pytest.raises(ReleaseError, match="symlink"):
        deploy.verify_deployment_readiness(alias, git_fn=lambda *_: "")


def test_unsafe_mode_rejected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)

    # 0o777 file mode
    (receipt_dir / "source/main.py").chmod(0o777)
    with pytest.raises(ReleaseError, match="File mode must be 0644 or 0755"):
        artifacts.build_directory_manifest(receipt_dir / "source")

    # 0o600 file mode
    (receipt_dir / "source/main.py").chmod(0o600)
    with pytest.raises(ReleaseError, match="File mode must be 0644 or 0755"):
        artifacts.build_directory_manifest(receipt_dir / "source")

    # Restore 0o644, test 0o777 directory mode
    (receipt_dir / "source/main.py").chmod(0o644)
    (receipt_dir / "source/scripts").chmod(0o777)
    with pytest.raises(ReleaseError, match="Directory mode must be 0755"):
        artifacts.build_directory_manifest(receipt_dir / "source")


def test_path_escape_in_manifest_rejected(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    receipt["bundle_manifest"]["../outside.txt"] = {
        "sha256": "0" * 64,
        "mode": "0o644",
        "size": 10,
    }
    with pytest.raises(ReleaseError, match="Path escape in manifest"):
        artifacts.verify_artifacts(receipt_dir, receipt)


def test_validate_only_no_external_mutation(tmp_path, monkeypatch):
    repo, tree_sha = create_mock_git_repo(tmp_path)
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path, tree_sha=tree_sha)
    summary_path = receipt_dir / "summary.json"
    original_bytes = summary_path.read_bytes()
    original_mtime = summary_path.stat().st_mtime_ns

    # Mock git functions to ensure no mutating commands are called
    def git_fn(*args):
        assert args[0] in ("write-tree", "rev-parse", "diff", "ls-files")
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    monkeypatch.setattr(deploy, "git_cmd", git_fn)
    rc = deploy.main(["--receipt", str(receipt_dir), "--validate-only", "--commit", "HEAD"])
    assert rc == 0

    # Verify summary.json was not modified
    assert summary_path.read_bytes() == original_bytes
    assert summary_path.stat().st_mtime_ns == original_mtime


@pytest.mark.parametrize("kind", ["unstaged", "untracked", "conflict"])
def test_validate_only_rejects_workspace_changes_before_deploy(tmp_path, kind):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    outputs = {
        ("diff", "--name-only", "--diff-filter=U", "-z"): "conflict.py\0" if kind == "conflict" else "",
        ("diff", "--name-only", "-z"): "changed.py\0" if kind == "unstaged" else "",
        ("ls-files", "--others", "--exclude-standard", "-z"): "new.py\0" if kind == "untracked" else "",
        ("write-tree",): receipt["tree"],
    }
    with pytest.raises(ReleaseError, match="conflicts|unstaged or untracked"):
        deploy.verify_deployment_readiness(receipt_dir, git_fn=lambda *args: outputs[args])


def test_receipt_manifest_fields_are_strict(tmp_path):
    receipt_dir, receipt = create_valid_receipt_fixture(tmp_path)
    first = next(iter(receipt["bundle_manifest"].values()))
    first["mode"] = "0o600"
    receipt["integrity_digest"] = artifacts.compute_integrity_digest(receipt)
    (receipt_dir / "summary.json").write_text(json.dumps(receipt))
    with pytest.raises(ReleaseError, match="Malformed bundle mode"):
        artifacts.load_receipt(receipt_dir)


def test_local_publication_scope_never_fetches_or_publishes(tmp_path):
    calls = []
    class Git:
        def git(self, *args):
            calls.append(args)
            if args == ("rev-parse", "--is-shallow-repository"):
                return "false"
            if args == ("rev-parse", "--verify", "origin/main"):
                return "a" * 40
            return ""
        def paths(self, *args):
            calls.append(args)
            return ["scripts/release_check.py"]
    directory = tmp_path / "receipt"
    directory.mkdir()
    runner = check.Runner(directory, {})
    assert check.validate_local_publication_scope(runner, Git()) == ["scripts/release_check.py"]
    forbidden = {"fetch", "push", "commit", "checkout", "switch"}
    assert not any(command and command[0] in forbidden for command in calls)


def test_create_bundle_normalizes_permissions(tmp_path):
    repo, tree_sha = create_mock_git_repo(tmp_path)
    assets_dir = repo / "adapters/mcp/static/dashboard"
    assets_dir.mkdir(parents=True)
    (assets_dir / "index.html").write_bytes(b"<h1>test</h1>\n")
    assets = {"index.html": hashlib.sha256(b"<h1>test</h1>\n").hexdigest()}

    def git_cmd(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout

    out_dir = tmp_path / "bundle_test"
    out_dir.mkdir()
    bundles = artifacts.create_bundle(out_dir, git_cmd, tree_sha, assets, repo)

    sync_bundle = bundles["ai-coach-sync"]
    chat_bundle = bundles["ai-coach-chat"]

    assert stat.S_IMODE(sync_bundle.stat().st_mode) == 0o755
    assert stat.S_IMODE(chat_bundle.stat().st_mode) == 0o755

    for d in sync_bundle.rglob("*"):
        if d.is_dir():
            assert stat.S_IMODE(d.stat().st_mode) == 0o755, f"Directory {d} not 0755"

    assert stat.S_IMODE((sync_bundle / "src/main.py").stat().st_mode) == 0o644
    assert stat.S_IMODE((sync_bundle / "scripts/run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((chat_bundle / "static/dashboard/index.html").stat().st_mode) == 0o644
