#!/usr/bin/env python3
"""Provision an already-created, billed, dedicated AI Coach project."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

from cloud import Cloud

HERE = Path(__file__).resolve().parent
APIS = [
    "serviceusage.googleapis.com", "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com", "iamcredentials.googleapis.com",
    "run.googleapis.com", "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com", "firestore.googleapis.com",
    "storage.googleapis.com", "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com", "logging.googleapis.com",
    "monitoring.googleapis.com", "firebaserules.googleapis.com",
]


def ensure_rules(cloud: Cloud):
    base = f"https://firebaserules.googleapis.com/v1/projects/{cloud.project}"
    release_url = f"{base}/releases/cloud.firestore"
    content = (HERE / "firestore.rules").read_text()
    release = cloud.rest("GET", release_url, allow_missing=True)
    if release:
        current = cloud.rest("GET", "https://firebaserules.googleapis.com/v1/"
                             + release["rulesetName"])
        if any(f.get("content") == content for f in current["source"]["files"]):
            print("Firestore deny-all client rules already current.")
            return
    ruleset = cloud.rest("POST", f"{base}/rulesets", {
        "source": {"files": [{"name": "firestore.rules", "content": content}]},
    })
    body = {"name": f"projects/{cloud.project}/releases/cloud.firestore",
            "rulesetName": ruleset["name"]}
    if release:
        cloud.rest("PATCH", release_url, {"release": body,
                                         "updateMask": "rulesetName"})
    else:
        cloud.rest("POST", f"{base}/releases", body)
    print("Published Firestore deny-all client rules (propagation takes minutes).")


def ensure_indexes(cloud: Cloud):
    base = (f"https://firestore.googleapis.com/v1/projects/{cloud.project}"
            "/databases/(default)/collectionGroups")
    definitions = json.loads((HERE / "firestore.indexes.json").read_text())["indexes"]
    groups = {}
    for definition in definitions:
        group = definition["collectionGroup"]
        if group not in groups:
            groups[group] = []
            page_token = None
            while True:
                suffix = "?" + urlencode({"pageToken": page_token}) if page_token else ""
                page = cloud.rest("GET", f"{base}/{group}/indexes{suffix}")
                groups[group].extend(page.get("indexes", []))
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
        target = definition["fields"]
        exists = any(
            [f for f in item["fields"] if f["fieldPath"] != "__name__"] == target
            and item.get("queryScope") == definition["queryScope"]
            for item in groups[group]
        )
        if not exists:
            cloud.rest("POST", f"{base}/{group}/indexes", {
                "queryScope": definition["queryScope"], "fields": target,
            })
            print(f"Requested index for {group}: {[f['fieldPath'] for f in target]}")


def ensure_schema(cloud: Cloud):
    """Document empty collection schemas without fabricating training records."""
    schemas = {
        "workouts": "Verified Garmin activities with raw/parsed Cloud Storage artifacts and versioned HR zone summaries.",
        "planned_workouts": "Scheduled workout table: local_date, sport, name, structured_workout_json, metrics/targets, status, completed_workout_id, source.",
        "wellness": "Daily wellness and subjective measures, keyed by local ISO date.",
        "observations": "User-entered lactate/RPE/notes with optional workout and lap references.",
        "sync_state": "Durable synchronization lease, watermarks and safe diagnostics.",
        "sync_runs": "Individual synchronization results with counts and sanitized errors.",
        "athletes": "Verified connected provider athlete identity.",
        "training_summaries": "Known imported totals by day, Monday-based week, calendar month and rolling 7/28 days; sport/zone definitions and contributor coverage retained.",
        "summary_jobs": "Private durable old/new-date invalidations for replay-safe summary rebuilding.",
    }
    base = (f"https://firestore.googleapis.com/v1/projects/{cloud.project}"
            "/databases/(default)/documents/schema")
    for collection, description in schemas.items():
        cloud.rest("PATCH", f"{base}/{collection}", {"fields": {
            "collection": {"stringValue": collection},
            "description": {"stringValue": description},
            "version": {"integerValue": "1"},
        }})
    print("Initialized collection schema registry, including planned_workouts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="europe-north1")
    parser.add_argument("--bucket")
    args = parser.parse_args()
    cloud = Cloud(args.project)
    project = cloud.json("projects", "describe", args.project)
    billing = cloud.json("billing", "projects", "describe", args.project)
    if not billing.get("billingEnabled"):
        raise SystemExit("Billing must be linked to this dedicated project first.")
    bucket = args.bucket or f"{args.project}-training-files"
    print(f"Provisioning project {args.project} in {args.region}.")
    cloud.command("services", "enable", *APIS, capture=False)

    accounts = {}
    for name, title in [
        ("ai-coach-runtime", "AI Coach runtime"),
        ("ai-coach-scheduler", "AI Coach scheduled invoker"),
        ("ai-coach-build", "AI Coach source builder"),
    ]:
        email = f"{name}@{args.project}.iam.gserviceaccount.com"
        if cloud.json("iam", "service-accounts", "describe", email,
                      allow_missing=True) is None:
            cloud.command("iam", "service-accounts", "create", name,
                          f"--display-name={title}")
        accounts[name] = email

    db = cloud.json("firestore", "databases", "describe", "--database=(default)",
                    allow_missing=True)
    if db is None:
        cloud.command("firestore", "databases", "create", "--database=(default)",
                      f"--location={args.region}", "--type=firestore-native",
                      "--delete-protection", capture=False)
    elif db.get("type") != "FIRESTORE_NATIVE" or db.get("locationId") != args.region:
        raise SystemExit("Existing database location/type differs; refusing migration.")

    existing_bucket = cloud.json("storage", "buckets", "describe", f"gs://{bucket}",
                                 allow_missing=True)
    if existing_bucket is None:
        cloud.command("storage", "buckets", "create", f"gs://{bucket}",
                      f"--location={args.region}", "--uniform-bucket-level-access",
                      "--public-access-prevention", capture=False)
    else:
        number = existing_bucket.get("projectNumber", existing_bucket.get("project_number"))
        if number is not None and str(number) != str(project["projectNumber"]):
            raise SystemExit("Bucket belongs to another project; refusing modification.")
        cloud.command("storage", "buckets", "update", f"gs://{bucket}",
                      "--uniform-bucket-level-access", "--public-access-prevention")

    if cloud.json("secrets", "describe", "intervals-api-key", allow_missing=True) is None:
        cloud.command("secrets", "create", "intervals-api-key",
                      "--replication-policy=user-managed", f"--locations={args.region}")
    runtime = f"serviceAccount:{accounts['ai-coach-runtime']}"
    cloud.command("projects", "add-iam-policy-binding", args.project,
                  f"--member={runtime}", "--role=roles/datastore.user", "--condition=None")
    cloud.command("storage", "buckets", "add-iam-policy-binding", f"gs://{bucket}",
                  f"--member={runtime}", "--role=roles/storage.objectUser")
    cloud.command("secrets", "add-iam-policy-binding", "intervals-api-key",
                  f"--member={runtime}", "--role=roles/secretmanager.secretAccessor")
    cloud.command("projects", "add-iam-policy-binding", args.project,
                  f"--member=serviceAccount:{accounts['ai-coach-build']}",
                  "--role=roles/run.builder", "--condition=None")
    ensure_rules(cloud)
    ensure_indexes(cloud)
    ensure_schema(cloud)
    print(json.dumps({"project": args.project, "region": args.region,
                      "bucket": bucket, "database": "(default)",
                      "secret": "intervals-api-key", "service_accounts": accounts}, indent=2))


if __name__ == "__main__":
    main()
