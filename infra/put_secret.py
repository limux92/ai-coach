#!/usr/bin/env python3
"""Validate a hidden Intervals.icu key, save it and connect a deployed service."""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import urllib.error
import urllib.request

from cloud import Cloud


SECRET_NAME = "intervals-api-key"
JOB_NAME = "intervals-sync-every-five-minutes"


def enabled_secret_version(cloud: Cloud) -> int | None:
    """Return the latest enabled numeric version, never its secret value."""
    versions = cloud.json("secrets", "versions", "list", SECRET_NAME,
                          "--filter=state=ENABLED", allow_missing=True)
    return max((int(v["name"].rsplit("/", 1)[1]) for v in versions or []), default=None)


def require_secret_version(cloud: Cloud, allow_unconnected: bool) -> int | None:
    version = enabled_secret_version(cloud)
    if version is None and not allow_unconnected:
        raise SystemExit("Upload an Intervals.icu key first, or explicitly use --allow-unconnected.")
    return version


def attach_secret(cloud: Cloud, version: int, service: str, region: str) -> bool:
    """Attach a version without rebuilding or replacing other service settings."""
    existing = cloud.json("run", "services", "describe", service,
                          f"--region={region}", allow_missing=True)
    if existing is None:
        return False
    containers = existing.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    attached = any(
        env.get("name") == "INTERVALS_API_KEY"
        and env.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == SECRET_NAME
        and str(env.get("valueFrom", {}).get("secretKeyRef", {}).get("key")) == str(version)
        for container in containers for env in container.get("env", [])
    )
    if not attached:
        cloud.command("run", "services", "update", service, f"--region={region}",
                      f"--update-secrets=INTERVALS_API_KEY={SECRET_NAME}:{version}")
    return True


def set_scheduler_state(cloud: Cloud, scheduler_region: str, *, connected: bool,
                        run_now: bool = False) -> dict:
    job = cloud.json("scheduler", "jobs", "describe", JOB_NAME,
                     f"--location={scheduler_region}", allow_missing=True)
    if job is None:
        return {"scheduler_pending": True, "scheduler_paused": None,
                "first_sync_requested": False}
    paused = job.get("state") == "PAUSED"
    if not connected and not paused:
        cloud.command("scheduler", "jobs", "pause", JOB_NAME, f"--location={scheduler_region}")
        paused = True
    elif connected and paused:
        cloud.command("scheduler", "jobs", "resume", JOB_NAME, f"--location={scheduler_region}")
        paused = False
    requested = connected and run_now
    if requested:
        cloud.command("scheduler", "jobs", "run", JOB_NAME, f"--location={scheduler_region}")
    return {"scheduler_pending": False, "scheduler_paused": paused,
            "first_sync_requested": requested}


def activate_connection(cloud: Cloud, version: int, service: str, region: str,
                        scheduler_region: str = "europe-west1") -> dict:
    connected = attach_secret(cloud, version, service, region)
    state = {"key_configured": True, "secret_version": version,
             "connected": connected, "service_pending": not connected,
             "region": region, "scheduler_region": scheduler_region}
    if connected:
        state.update(set_scheduler_state(cloud, scheduler_region, connected=True, run_now=True))
    else:
        state.update({"scheduler_pending": True, "scheduler_paused": None,
                      "first_sync_requested": False})
    return state


def validate_key(key: str) -> None:
    if not key or any(c.isspace() for c in key):
        raise SystemExit("API key must be nonempty and contain no whitespace.")
    encoded = base64.b64encode(("API_KEY:" + key).encode()).decode()
    request = urllib.request.Request("https://intervals.icu/api/v1/athlete/0", headers={
        "Authorization": "Basic " + encoded,
        "Accept": "application/json",
        "User-Agent": "AI-Coach/0.1",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            profile = json.load(response)
            if not isinstance(profile, dict) or not profile.get("id"):
                raise SystemExit("Intervals.icu returned an unexpected profile; key was not saved.")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Intervals.icu rejected the connection (HTTP {exc.code}); key was not saved.") from None
    except (urllib.error.URLError, TimeoutError):
        raise SystemExit("Could not reach Intervals.icu; key was not saved. Try again when online.") from None
    except (ValueError, UnicodeError):
        raise SystemExit("Intervals.icu returned an invalid response; key was not saved.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="europe-north1")
    parser.add_argument("--scheduler-region", default="europe-west1",
                        help="Cloud Scheduler location, which may differ from the service region")
    parser.add_argument("--service", default="ai-coach-sync")
    args = parser.parse_args(argv)
    key = getpass.getpass("Intervals.icu personal API key (hidden): ").strip()
    validate_key(key)
    print("Intervals.icu connection verified.")
    cloud = Cloud(args.project)
    try:
        added = json.loads(cloud.command("secrets", "versions", "add", SECRET_NAME,
                            "--data-file=-", "--format=json", input_text=key))
        version = int(added["name"].rsplit("/", 1)[1])
    except (RuntimeError, ValueError, KeyError, TypeError):
        raise SystemExit("Could not confirm that Google Secret Manager saved the key. "
                         "Check Google Cloud access and retry this connection script.") from None
    del key
    print("Key saved in Google Secret Manager; no local key file was created.")
    try:
        result = activate_connection(cloud, version, args.service, args.region,
                                     args.scheduler_region)
    except RuntimeError:
        raise SystemExit("The key is saved, but service activation did not finish. "
                         "Retry this connection script after deployment completes.") from None
    print(json.dumps(result, indent=2))
    if result["service_pending"] or result["scheduler_pending"]:
        print("Deployment is still pending. The deploy script will pick up the saved key.")
    else:
        print("Service connected. Scheduled imports are enabled and a first sync was requested.")


if __name__ == "__main__":
    main()
