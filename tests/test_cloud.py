"""CLI absence handling must not hide deployment permission or network errors."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parent.parent / "infra" / "cloud.py"
SPEC = importlib.util.spec_from_file_location("ai_coach_infra_cloud_test", PATH)
cloud_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud_module)


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("GCLOUD_BIN", "/mock/gcloud")
    return cloud_module.Cloud("test-project")


def failed_command(monkeypatch, error):
    def run(args, **kwargs):
        assert args[0] == "/mock/gcloud"
        assert "--project=test-project" in args and "--quiet" in args
        return SimpleNamespace(returncode=1, stderr=error, stdout="")
    monkeypatch.setattr(cloud_module.subprocess, "run", run)


@pytest.mark.parametrize("suffix", ["", ".", "\n", ".\n"])
def test_undeployed_run_service_is_missing_only_when_allowed(cloud, monkeypatch, suffix):
    error = "ERROR: (gcloud.run.services.describe) Cannot find service [ai-coach-chat]" + suffix
    failed_command(monkeypatch, error)
    assert cloud.command("run", "services", "describe", "ai-coach-chat", allow_missing=True) is None
    assert cloud.json("run", "services", "describe", "ai-coach-chat", allow_missing=True) is None
    with pytest.raises(RuntimeError):
        cloud.command("run", "services", "describe", "ai-coach-chat")


@pytest.mark.parametrize("error", [
    "ERROR: (gcloud.run.services.describe) PERMISSION_DENIED: Permission run.services.get denied",
    "ERROR: (gcloud.run.services.describe) UNAVAILABLE: Connection timed out",
    "ERROR: (gcloud.run.services.describe) Cloud Run API is disabled",
    "ERROR: (gcloud.run.services.describe) INVALID_ARGUMENT: Invalid region",
    "ERROR: (gcloud.run.services.describe) Cannot find service [different-service]",
    "ERROR: (gcloud.run.services.describe) Cannot find service [ai-coach-chat]\nPERMISSION_DENIED: failure",
    "Cannot find service [ai-coach-chat]",
])
def test_unrelated_errors_are_not_swallowed_by_allow_missing(cloud, monkeypatch, error):
    failed_command(monkeypatch, error)
    with pytest.raises(RuntimeError) as result:
        cloud.command("run", "services", "describe", "ai-coach-chat", allow_missing=True)
    assert str(result.value) == error.strip()


def test_missing_message_is_not_reused_for_mutating_or_unrelated_commands(cloud, monkeypatch):
    failed_command(monkeypatch, "ERROR: (gcloud.run.services.describe) Cannot find service [ai-coach-chat]")
    with pytest.raises(RuntimeError):
        cloud.command("run", "services", "update", "ai-coach-chat", allow_missing=True)
    with pytest.raises(RuntimeError):
        cloud.command("iam", "service-accounts", "describe", "ai-coach-chat", allow_missing=True)


def test_existing_not_found_behavior_is_preserved(cloud, monkeypatch):
    failed_command(monkeypatch, "ERROR: NOT_FOUND: Secret does not exist")
    assert cloud.command("secrets", "describe", "missing-secret", allow_missing=True) is None


def test_successful_json_response_is_preserved(cloud, monkeypatch):
    monkeypatch.setattr(cloud_module.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout='{"metadata":{"name":"ai-coach-chat"}}', stderr=""))
    assert cloud.json("run", "services", "describe", "ai-coach-chat", allow_missing=True) == {
        "metadata": {"name": "ai-coach-chat"}}
