"""One reviewed existing-job migration; no provisioning, IAM or pause/resume."""
from copy import deepcopy
import json

from release_git import require
from release_health import BACKEND_URL, PROJECT

JOB = "intervals-sync-every-five-minutes"
LOCATION = "europe-west1"
EXPECTED_NAME = f"projects/{PROJECT}/locations/{LOCATION}/jobs/{JOB}"
READ_ONLY_FIELDS = {"lastAttemptTime", "scheduleTime", "status", "userUpdateTime", "satisfiesPzs"}


def configuration(job):
    return {k: v for k, v in job.items() if k not in READ_ONLY_FIELDS}


def preserved_configuration(job):
    result = deepcopy(configuration(job))
    result.pop("attemptDeadline", None)
    retry = result.setdefault("retryConfig", {})
    retry.pop("retryCount", None)
    retry.pop("maxRetryDuration", None)
    return result


def validate_existing(job):
    target = job.get("httpTarget", {})
    oidc = target.get("oidcToken", {})
    require(job.get("name") == EXPECTED_NAME and job.get("schedule") == "*/5 * * * *"
            and job.get("timeZone") == "Etc/UTC" and job.get("state") in {"ENABLED", "PAUSED"}
            and target.get("uri") == BACKEND_URL + "/internal/sync" and target.get("httpMethod") == "POST"
            and oidc.get("audience") == BACKEND_URL
            and oidc.get("serviceAccountEmail") == f"ai-coach-scheduler@{PROJECT}.iam.gserviceaccount.com",
            "Existing Scheduler identity, target or schedule does not match the reviewed migration.")


class SchedulerMigration:
    def __init__(self, cloud):
        self.cloud, self.before = cloud, None

    def read(self):
        return self.cloud.document("scheduler", "jobs", "describe", JOB, "--location=" + LOCATION)

    def preflight(self):
        self.before = self.read()
        validate_existing(self.before)
        (self.cloud.run.directory / "scheduler-before.json").write_text(json.dumps(self.before, indent=2) + "\n")
        self.cloud.receipt["scheduler_migration"] = {"status": "reviewed", "job": EXPECTED_NAME,
            "previous_attempt_deadline": self.before.get("attemptDeadline"),
            "previous_retry_config": self.before.get("retryConfig"), "state": self.before["state"]}

    def apply(self):
        self.cloud.note("Apply separately reviewed Scheduler deadline and retry policy")
        current = self.read()
        require(configuration(current) == configuration(self.before), "Scheduler changed concurrently; migration stopped.")
        self.cloud.receipt["scheduler_migration"]["status"] = "updating"
        self.cloud.run.save()
        # Only these three fields change. Existing target, OIDC and state stay intact.
        self.cloud.command("scheduler", "jobs", "update", "http", JOB, "--location=" + LOCATION,
                           "--attempt-deadline=300s", "--max-retry-attempts=0", "--max-retry-duration=0s")
        after = self.read()
        (self.cloud.run.directory / "scheduler-after.json").write_text(json.dumps(after, indent=2) + "\n")
        require(preserved_configuration(after) == preserved_configuration(self.before),
                "Scheduler configuration outside the reviewed fields changed; inspect the saved snapshots.")
        retry = after.get("retryConfig", {})
        require(after.get("attemptDeadline") == "300s" and retry.get("retryCount", 0) == 0
                and retry.get("maxRetryDuration", "0s") == "0s", "Scheduler migration did not match expected policy.")
        self.cloud.receipt["scheduler_migration"]["status"] = "verified"
        self.cloud.run.save()
