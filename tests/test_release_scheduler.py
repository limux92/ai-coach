"""Exact infrastructure review binding and existing-job-only update behavior."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_git import PHYSIOLOGY_INFRA_DIFF, ReleaseError, routine_scope
from release_scheduler import SchedulerMigration, EXPECTED_NAME, BACKEND_URL, PROJECT


def job(state="ENABLED"):
    return {"name": EXPECTED_NAME, "schedule": "*/5 * * * *", "timeZone": "Etc/UTC", "state": state,
            "attemptDeadline": "900s", "retryConfig": {"retryCount": 3, "maxRetryDuration": "0s",
                "minBackoffDuration": "10s", "maxBackoffDuration": "300s", "maxDoublings": 5},
            "httpTarget": {"uri": BACKEND_URL + "/internal/sync", "httpMethod": "POST",
                "oidcToken": {"audience": BACKEND_URL,
                    "serviceAccountEmail": f"ai-coach-scheduler@{PROJECT}.iam.gserviceaccount.com"}}}


def test_infrastructure_review_is_bound_to_exact_diff_and_only_one_path():
    routine_scope(["infra/deploy.py", "src/ai_coach/main.py"], physiology_migration=True,
                  infra_diff_sha256=PHYSIOLOGY_INFRA_DIFF)
    for paths, digest in ((["infra/deploy.py"], "changed"), (["infra/deploy.py", "infra/iam.py"], PHYSIOLOGY_INFRA_DIFF),
                          ([], PHYSIOLOGY_INFRA_DIFF)):
        with pytest.raises(ReleaseError):
            routine_scope(paths, physiology_migration=True, infra_diff_sha256=digest)
    with pytest.raises(ReleaseError):
        routine_scope(["infra/deploy.py"], infra_diff_sha256=PHYSIOLOGY_INFRA_DIFF)


class Cloud:
    def __init__(self, tmp_path, value):
        self.value, self.receipt, self.commands = value, {}, []
        self.run = SimpleNamespace(directory=tmp_path, save=lambda: None)
    def document(self, *args):
        return deepcopy(self.value)
    def note(self, text):
        pass
    def command(self, *args):
        self.commands.append(args)
        self.value["attemptDeadline"] = "300s"
        self.value["retryConfig"].update(retryCount=0, maxRetryDuration="0s")
        self.value["userUpdateTime"] = "synthetic-change"


@pytest.mark.parametrize("state", ["ENABLED", "PAUSED"])
def test_migration_changes_only_retry_policy_and_preserves_state(tmp_path, state):
    cloud = Cloud(tmp_path, job(state))
    target = deepcopy(cloud.value["httpTarget"])
    migration = SchedulerMigration(cloud)
    migration.preflight()
    migration.apply()
    assert cloud.receipt["scheduler_migration"]["status"] == "verified"
    assert cloud.value["state"] == state and cloud.value["httpTarget"] == target
    assert len(cloud.commands) == 1
    assert set(cloud.commands[0][5:]) == {"--location=europe-west1", "--attempt-deadline=300s",
                                         "--max-retry-attempts=0", "--max-retry-duration=0s"}


def test_changed_target_or_concurrent_configuration_prevents_update(tmp_path):
    cloud = Cloud(tmp_path, job())
    migration = SchedulerMigration(cloud)
    migration.preflight()
    cloud.value["state"] = "PAUSED"
    with pytest.raises(ReleaseError, match="concurrently"):
        migration.apply()
    cloud.value["httpTarget"]["uri"] = "https://unreviewed.invalid"
    with pytest.raises(ReleaseError, match="identity"):
        migration.preflight()
    assert not cloud.commands
