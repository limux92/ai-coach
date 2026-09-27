"""Release gates and rollback tests. No GitHub or Google Cloud connections."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release
from release_git import CHECK_NAMES, GitRelease, ReleaseError, ci_complete, routine_scope, validate_paths
import release_cloud as cloud


def passing_checks():
    return [{"name": name, "workflow": "CI", "bucket": "pass", "state": "SUCCESS"} for name in CHECK_NAMES]


def test_ci_requires_every_named_job_and_actual_success():
    assert ci_complete(passing_checks())
    assert not ci_complete(passing_checks()[:-1])
    assert not ci_complete([{**c, "workflow": "Different workflow"} for c in passing_checks()])
    rows = passing_checks()
    rows[0]["bucket"] = "pending"
    assert not ci_complete(rows)
    for state in ("fail", "skipping", "cancel"):
        rows[0]["bucket"] = state
        with pytest.raises(ReleaseError):
            ci_complete(rows)


@pytest.mark.parametrize("path", [".local/token.json", "exports/activity.FIT", "data/archive.json",
                                 "key.pem", ".env.production", "adapters/mcp/static/dashboard/index.html"])
def test_private_or_generated_source_is_rejected(path):
    with pytest.raises(ReleaseError):
        validate_paths([path])
    validate_paths([".env.example", "dashboard/src/main.js", "scripts/release.py"])


@pytest.mark.parametrize("path", ["infra/deploy.py", "infra/terraform.tf"])
def test_routine_release_leaves_infrastructure_changes_for_review(path):
    with pytest.raises(ReleaseError):
        routine_scope([path])
    routine_scope(["dashboard/src/main.js", "adapters/mcp/src/ai_coach_mcp/app.py", "scripts/release.py",
                   "src/ai_coach/main.py", "Dockerfile", "requirements.lock"])


def test_release_requires_explicit_destination_and_message():
    with pytest.raises(SystemExit) as error:
        release.main(["--release"])
    assert error.value.code == 2


def test_runner_failure_and_timeout_are_not_success(tmp_path):
    run = release.Runner(tmp_path, {})
    with pytest.raises(ReleaseError, match="exit 4"):
        run([sys.executable, "-c", "raise SystemExit(4)"], label="failed check")
    with pytest.raises(ReleaseError, match="timed out"):
        run([sys.executable, "-c", "import time; time.sleep(10)"], label="hung check", timeout=0.05)


def test_git_requires_complete_staging_and_detects_concurrent_edits(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    git("init", "-b", "main")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "release@example.invalid")
    git("remote", "add", "origin", "https://github.com/limux92/ai-coach.git")
    source = tmp_path / "app.py"
    source.write_text("print('base')\n")
    git("add", "app.py")
    git("commit", "-m", "base")
    logs = tmp_path / ".local/releases/test"
    logs.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text(".local/\n")
    git("add", ".gitignore")
    runner = release.Runner(logs, {})
    # The runner normally uses the actual workspace; bind all test commands here.
    def run(args, **kwargs):
        return runner(args, cwd=tmp_path, **kwargs)
    run.directory = logs
    subject = GitRelease(run, tmp_path, {})
    source.write_text("print('unstaged')\n")
    with pytest.raises(ReleaseError, match="stage intended"):
        subject.inspect(strict=True)
    git("add", "app.py")
    subject.inspect(strict=True)
    tree = subject.fingerprint()
    source.write_text("print('changed during checks')\n")
    with pytest.raises(ReleaseError, match="Workspace changed"):
        subject.unchanged(tree)
    git("add", "app.py")
    with pytest.raises(ReleaseError, match="Staged source changed"):
        subject.unchanged(tree)


def test_wrong_remote_rejected_before_publication(tmp_path):
    class Run:
        directory = tmp_path
        def __call__(self, args, **kwargs):
            if args[1] == "rev-parse":
                return str(tmp_path)
            return "https://github.com/somebody/another-repo.git"
    with pytest.raises(ReleaseError, match="origin must point"):
        GitRelease(Run(), tmp_path, {}).inspect(strict=True)


def test_check_failure_stops_before_github_or_cloud(monkeypatch, tmp_path):
    runner = release.Runner(tmp_path, {})
    git = SimpleNamespace(inspect=lambda **_: None, prepare=lambda: None, fingerprint=lambda: "tree")
    def fail_checks(*args):
        raise ReleaseError("local test failed")
    monkeypatch.setattr(release, "checks", fail_checks)
    monkeypatch.setattr(release.Runner, "__call__", lambda *a, **k: "")
    with pytest.raises(ReleaseError, match="local test failed"):
        release.release(runner, git, SimpleNamespace(message="title", bootstrap_backend_health=None), "test")
    assert not runner.receipt.get("commit")
    assert not runner.receipt.get("revision")


def test_ci_wait_handles_registration_delay_and_pending_jobs(monkeypatch, tmp_path):
    subject = GitRelease(None, tmp_path, {})
    pending = passing_checks()
    pending[0]["bucket"] = "pending"
    states = iter([{}, {"statusCheckRollup": [1]}, {"statusCheckRollup": [1]}])
    checks = iter([pending, passing_checks()])
    subject.assert_head = lambda *_: next(states)
    subject.checks = lambda *_: next(checks)
    monkeypatch.setattr("release_git.time.sleep", lambda _: None)
    subject.wait_for_ci("2", "sha")
    assert subject.receipt["github_ci_passed"]


def test_pr_head_change_blocks_release(tmp_path):
    subject = GitRelease(None, tmp_path, {})
    subject.gh = lambda *a: json.dumps({"headRefOid": "someone-elses-commit", "state": "OPEN",
                                       "baseRefName": "main", "isCrossRepository": False})
    with pytest.raises(ReleaseError, match="PR changed"):
        subject.assert_head("2", "tested-commit")


def test_failed_github_ci_never_builds_or_deploys_cloud(monkeypatch, tmp_path):
    runner = release.Runner(tmp_path, {})
    git = SimpleNamespace(inspect=lambda **_: None, prepare=lambda: None, fingerprint=lambda: "tree",
                          unchanged=lambda _: None, publish=lambda *a: ("2", "sha"))
    def fail_ci(*args):
        raise ReleaseError("GitHub CI failed")
    git.wait_for_ci = fail_ci
    monkeypatch.setattr(release, "checks", lambda *a: {})
    monkeypatch.setattr(release.Runner, "__call__", lambda *a, **k: "")
    monkeypatch.setattr(cloud, "CloudRelease", lambda *a: SimpleNamespace(preflight=lambda: None))
    with pytest.raises(ReleaseError, match="GitHub CI failed"):
        release.release(runner, git, SimpleNamespace(message="title", bootstrap_backend_health=None), "test")
    assert not runner.receipt.get("revision")


def test_candidate_bundle_rejects_changed_build_assets(tmp_path, monkeypatch):
    import hashlib
    import zipfile
    assets = tmp_path / "adapters/mcp/static/dashboard"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("new untested content")
    directory = tmp_path / "receipt"
    directory.mkdir()
    with zipfile.ZipFile(directory / "source.zip", "w") as archive:
        archive.writestr("adapters/mcp/Dockerfile", "FROM scratch\n")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    git = SimpleNamespace(git=lambda *a: None)
    with pytest.raises(ReleaseError, match="Build assets changed"):
        release.bundle(SimpleNamespace(directory=directory), git, "sha", {"index.html": hashlib.sha256(b"tested").hexdigest()})


def test_bundle_nonroot_permissions_preserve_private_receipts(tmp_path, monkeypatch):
    import hashlib
    import os
    import stat
    import zipfile
    assets = tmp_path / "adapters/mcp/static/dashboard"
    assets.mkdir(parents=True)
    (assets / "index.html").write_bytes(b"tested")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    previous = os.umask(0o077)
    try:
        directory = tmp_path / "private-receipt"
        directory.mkdir()
        (directory / "private.log").write_text("private")
        with zipfile.ZipFile(directory / "source.zip", "w") as archive:
            archive.writestr("src/ai_coach/main.py", "app = object()\n")
            archive.writestr("adapters/mcp/Dockerfile", "FROM scratch\n")
            executable = zipfile.ZipInfo("scripts/gcloud")
            executable.external_attr = 0o100755 << 16
            archive.writestr(executable, "#!/bin/sh\n")
        paths = release.bundle(SimpleNamespace(directory=directory), SimpleNamespace(git=lambda *a: None),
                               "sha", {"index.html": hashlib.sha256(b"tested").hexdigest()})
        source = paths["ai-coach-sync"]
        assert stat.S_IMODE(source.stat().st_mode) == 0o755
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o755 for p in source.rglob("*") if p.is_dir())
        assert stat.S_IMODE((source / "src/ai_coach/main.py").stat().st_mode) == 0o644
        assert stat.S_IMODE((source / "scripts/gcloud").stat().st_mode) == 0o755
        assert stat.S_IMODE((paths["ai-coach-chat"] / "static/dashboard/index.html").stat().st_mode) == 0o644
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / "private.log").stat().st_mode) == 0o600
    finally:
        os.umask(previous)


def test_missing_firebase_receipt_has_actionable_error(monkeypatch):
    def missing(path):
        raise cloud.DeploymentError("Read a valid private Firebase configuration receipt")
    monkeypatch.setattr(cloud, "load_settings", missing)
    with pytest.raises(ReleaseError, match="Firebase configuration receipt"):
        cloud.CloudRelease(None, {})
