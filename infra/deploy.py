#!/usr/bin/env python3
"""Deploy the private service and its OIDC-authenticated recurring sync job."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlencode

from cloud import Cloud
from put_secret import (JOB_NAME, attach_secret, enabled_secret_version,
                        require_secret_version, set_scheduler_state)


def verify_private(cloud: Cloud, url: str):
    # Exercise a real application route and Firestore access through private ingress.
    status_url = url.rstrip("/") + "/v1/status"
    try:
        with urllib.request.urlopen(status_url, timeout=45) as response:
            raise RuntimeError(f"Unauthenticated request unexpectedly returned {response.status}")
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403):
            raise RuntimeError(f"Unexpected anonymous HTTP status {exc.code}") from None
    token = cloud.command("auth", "print-identity-token").strip()
    request = urllib.request.Request(status_url, headers={
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status != 200:
            raise RuntimeError(f"Authenticated status check returned {response.status}")
        try:
            state = json.load(response)
        except (ValueError, UnicodeError):
            raise RuntimeError("Authenticated status check returned invalid JSON.") from None
        if (not isinstance(state, dict)
                or state.get("source_connection") not in ("configured", "awaiting_api_key")
                or not isinstance(state.get("stale"), bool)):
            raise RuntimeError("Authenticated status check returned an unexpected response.")
    print("Verified: anonymous access denied; authenticated service and database status succeeds.")


def ensure_monitoring(cloud: Cloud, service: str):
    metric_name = "ai_coach_sync_failures"
    if cloud.json("logging", "metrics", "describe", metric_name,
                  allow_missing=True) is None:
        log_filter = (f'resource.type="cloud_run_revision" '
                      f'AND resource.labels.service_name="{service}" '
                      'AND (jsonPayload.event="sync_failed" OR severity>=ERROR)')
        cloud.command("logging", "metrics", "create", metric_name,
                      "--description=AI Coach sync failures and service errors",
                      f"--log-filter={log_filter}")
    base = f"https://monitoring.googleapis.com/v3/projects/{cloud.project}/alertPolicies"
    display_name = "AI Coach sync errors"
    existing = []
    token = None
    while True:
        page = cloud.rest("GET", base + ("?" + urlencode({"pageToken": token}) if token else ""))
        existing.extend(page.get("alertPolicies", []))
        token = page.get("nextPageToken")
        if not token:
            break
    if not any(p.get("displayName") == display_name for p in existing):
        cloud.rest("POST", base, {
            "displayName": display_name,
            "documentation": {"mimeType": "text/markdown", "content":
                "Check Cloud Run logs and GET /v1/status. No email or external "
                "notification channel is configured. Scheduler retries transient failures."},
            "combiner": "OR", "enabled": True,
            "conditions": [{"displayName": "At least one service error in 5 minutes",
                "conditionThreshold": {
                    "filter": 'resource.type="cloud_run_revision" AND '
                              'metric.type="logging.googleapis.com/user/ai_coach_sync_failures"',
                    "aggregations": [{"alignmentPeriod": "300s",
                                      "perSeriesAligner": "ALIGN_DELTA"}],
                    "comparison": "COMPARISON_GT", "thresholdValue": 0,
                    "duration": "0s", "trigger": {"count": 1},
                }}],
            "notificationChannels": [],
        })
    print("Configured error metric and console alert policy (no external notifications).")


def prepare_service(cloud: Cloud, args, version: int | None) -> None:
    """Build the service, or require it to exist while preserving its settings."""
    if args.configure_only:
        if args.env_file:
            raise SystemExit("--env-file cannot be used with --configure-only; existing environment is preserved.")
        existing = cloud.json("run", "services", "describe", args.service,
                              f"--region={args.region}", allow_missing=True)
        if existing is None:
            raise SystemExit("Cloud Run service does not exist. Deploy it before using --configure-only.")
        return
    bucket = args.bucket or f"{args.project}-training-files"
    runtime = f"ai-coach-runtime@{args.project}.iam.gserviceaccount.com"
    builder = f"ai-coach-build@{args.project}.iam.gserviceaccount.com"
    env = {
        "GCP_PROJECT_ID": args.project,
        "GCS_BUCKET": bucket,
        "FIRESTORE_DATABASE": "(default)",
        "INTERVALS_ATHLETE_ID": args.athlete_id,
    }
    if args.env_file:
        extra = json.loads(args.env_file.read_text())
        if any("KEY" in k or "TOKEN" in k or "SECRET" in k for k in extra):
            raise SystemExit("Use Secret Manager for secret values, not --env-file.")
        env.update({k: str(v) for k, v in extra.items()})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as temporary:
        json.dump(env, temporary)
        temporary.flush()
        cloud.command("run", "deploy", args.service,
            f"--source={args.source.resolve()}", f"--region={args.region}",
            f"--service-account={runtime}",
            f"--build-service-account=projects/{args.project}/serviceAccounts/{builder}",
            "--no-allow-unauthenticated", "--invoker-iam-check", "--ingress=all",
            "--min=0", "--max=2", "--cpu=1", "--memory=1Gi", "--concurrency=1",
            "--timeout=900s", "--port=8080", f"--env-vars-file={temporary.name}",
            (f"--update-secrets=INTERVALS_API_KEY=intervals-api-key:{version}"
             if version is not None else "--remove-secrets=INTERVALS_API_KEY"),
            capture=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--region", default="europe-north1")
    parser.add_argument("--scheduler-region", default="europe-west1",
                        help="Cloud Scheduler location; europe-north1 is not supported")
    parser.add_argument("--bucket")
    parser.add_argument("--service", default="ai-coach-sync")
    parser.add_argument("--athlete-id", default="0")
    parser.add_argument("--env-file", type=Path, help="Additional non-secret environment JSON")
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--allow-unconnected", action="store_true",
                        help="Deploy without an API key; pause imports until a key is connected")
    parser.add_argument("--configure-only", action="store_true",
                        help="Configure an existing service without rebuilding or replacing its environment")
    args = parser.parse_args(argv)
    cloud = Cloud(args.project)
    # Numeric pins make redeploys reproducible. No placeholder or empty secret is used.
    version = require_secret_version(cloud, args.allow_unconnected)
    prepare_service(cloud, args, version)
    scheduler = f"ai-coach-scheduler@{args.project}.iam.gserviceaccount.com"
    # A key may have been supplied while Cloud Build was running.
    version = enabled_secret_version(cloud)
    connected = version is not None and attach_secret(cloud, version, args.service, args.region)
    url = cloud.command("run", "services", "describe", args.service,
                        f"--region={args.region}", "--format=value(status.url)").strip()
    cloud.command("run", "services", "add-iam-policy-binding", args.service,
                  f"--region={args.region}", f"--member=serviceAccount:{scheduler}",
                  "--role=roles/run.invoker")
    policy = cloud.json("run", "services", "get-iam-policy", args.service,
                        f"--region={args.region}")
    if any(m in ("allUsers", "allAuthenticatedUsers")
           for binding in policy.get("bindings", []) for m in binding.get("members", [])):
        raise SystemExit("Unexpected public IAM binding: remove it before scheduling sync.")
    verify_private(cloud, url)

    job_name = JOB_NAME
    existing_job = cloud.json("scheduler", "jobs", "describe", job_name,
                               f"--location={args.scheduler_region}", allow_missing=True)
    action = "update" if existing_job else "create"
    headers = "--update-headers=Content-Type=application/json" if existing_job else \
              "--headers=Content-Type=application/json"
    cloud.command("scheduler", "jobs", action, "http", job_name,
        f"--location={args.scheduler_region}", "--schedule=*/5 * * * *", "--time-zone=Etc/UTC",
        f"--uri={url}/internal/sync", "--http-method=POST", "--message-body={}",
        headers, f"--oidc-service-account-email={scheduler}", f"--oidc-token-audience={url}",
        "--attempt-deadline=900s", "--min-backoff=10s", "--max-backoff=300s",
        "--max-doublings=5", "--max-retry-attempts=3", capture=False)
    # Pause before the final key check. If the connection script ran while the job
    # was being created, this final reconciliation enables it using that new key.
    schedule = set_scheduler_state(cloud, args.scheduler_region, connected=connected)
    latest_version = enabled_secret_version(cloud)
    if latest_version is not None:
        connected = attach_secret(cloud, latest_version, args.service, args.region)
        version = latest_version
    else:
        version = None
        connected = False
    schedule = set_scheduler_state(cloud, args.scheduler_region, connected=connected,
                                   run_now=args.run_now)
    ensure_monitoring(cloud, args.service)
    print(json.dumps({"service_url": url, "project": args.project,
                      "region": args.region, "scheduler_job": job_name,
                      "scheduler_region": args.scheduler_region,
                      "key_configured": version is not None,
                      "secret_version": version, "connected": connected,
                      **schedule}, indent=2))


if __name__ == "__main__":
    main()
