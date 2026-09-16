# Private AI Coach infrastructure

These scripts target only an explicitly supplied Google Cloud project. They do
not select a global gcloud project, create service-account keys, or delete
existing data. Some operator helpers contain deployment-specific configuration;
review and configure them for your own account before execution. The chat deployment publishes its OAuth-protected adapter after private checks; the data backend remains private. Use a new project
dedicated to this training archive.

## Prerequisites

- Python 3.12+ and the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install-sdk).
  On macOS, use Google's official ARM64/x86_64 archive or the Homebrew Google
  Cloud SDK cask. `GCLOUD_BIN` may point to an unpacked CLI executable.
- A signed-in Google identity (`gcloud auth login`). A browser sign-in is an
  unavoidable initial identity step when no Google credentials are available.
  These scripts use gcloud credentials; local Application Default Credentials
  are only needed to run the application itself outside Cloud Run.
- A new Google Cloud project and an active billing account linked to it. Linking
  an existing billing account requires Billing Account User on that account and
  Project Billing Manager on the project. Creating a project requires Project
  Creator on its parent organization/folder, or personal project creation access.
- A personal Intervals.icu API key. Never place this in chat, source control,
  `.env.example`, command-line arguments, VS Code settings, or deployment output.

The provisioning identity needs service enablement, IAM/service-account
administration, Cloud Run/source deployment, bucket administration, Firestore
database/index administration, Firebase Rules administration, Secret Manager
administration, Scheduler administration, logging-metric administration and
Monitoring alert-policy administration. A project owner of a newly created
personal project normally has this scope. Organization policies can still limit
regions, service activation, billing or service-account use. Build/deploy-specific
roles are `roles/run.sourceDeveloper`, `roles/serviceusage.serviceUsageConsumer`
and Service Account User on the selected runtime/build/scheduler identities; the
provisioning script needs the broader administrative permissions listed above.

## Execute

From the new AI Coach workspace, substitute the actual project ID:

```sh
python3 infra/provision.py --project YOUR_PROJECT_ID
python3 infra/put_secret.py --project YOUR_PROJECT_ID
python3 infra/deploy.py --project YOUR_PROJECT_ID --source . --run-now
```

Project creation and billing linking happen before these steps; they are not
silently chosen by the scripts. The defaults use `europe-north1` (Finland), the
Firestore Native `(default)` database and bucket `YOUR_PROJECT_ID-training-files`.
The database location is difficult to change, so provisioning stops if an
existing database has a different location or type. It enables database deletion
protection for newly created databases.

`put_secret.py` prompts without echo and uploads directly from memory to a
regional Secret Manager secret. Deployment selects and pins the newest enabled
numeric secret version. The connection script updates the existing service without rebuilding, resumes its schedule and requests one import. To rotate the key, run `put_secret.py` again.

`deploy.py --allow-unconnected` permits an initial deployment without a key and pauses imports. `--configure-only` requires an existing service and completes IAM verification, scheduling and monitoring without replacing its environment or rebuilding.

Cloud Run, Firestore and archive storage use `--region=europe-north1`. Cloud Scheduler does not support that region; `--scheduler-region=europe-west1` controls its independent European location in both deployment and key-connection scripts. The timer sends an empty authenticated request; it stores no training files.

## Resources and permissions

| Resource | Purpose / access |
|---|---|
| Cloud Run `ai-coach-sync` | Private HTTP API; IAM authentication required; 1 CPU, 1 GiB, minimum 0, maximum 2 instances, concurrency 1, 900-second timeout |
| `ai-coach-runtime` service account | Project `roles/datastore.user`; archive-bucket `roles/storage.objectUser`; only `intervals-api-key` `roles/secretmanager.secretAccessor` |
| `ai-coach-build` service account | Project `roles/run.builder`; separate identity from the runtime |
| `ai-coach-scheduler` service account | `roles/run.invoker` on this service only |
| Cloud Scheduler (`europe-west1`) | Authenticated `POST /internal/sync` every five minutes; OIDC audience is the base service URL, with retries |
| Cloud Storage | Regional private archive; uniform bucket-level IAM and enforced public-access prevention; no lifecycle rule deleting workouts |
| Firestore | Workouts, planned workouts, wellness, sync state and run history; all mobile/web client requests denied by rules; server access uses IAM |
| Secret Manager | Intervals.icu API key; user-managed regional replication |
| Logging / Monitoring | Error log metric and console incident policy; no email or external notification channel configured |

Google-managed service agents retain the standard service roles granted when
their APIs are enabled. Do not give the runtime the build identity or broad
Editor/Owner access. No OAuth refresh-token or static Google credential file is
needed on Cloud Run.

The service's environment uses `GCP_PROJECT_ID`, `GCS_BUCKET`,
`FIRESTORE_DATABASE`, `INTERVALS_ATHLETE_ID` (default `0`, meaning the key owner)
and the secret-backed `INTERVALS_API_KEY`. Additional non-secret environment
configuration can be passed as JSON with `--env-file`.

## Database schema and indexes

Firestore collections act as the requested tables. No fake workout rows are
created merely to make an empty collection appear in the console. Imported or
created documents materialize the collection automatically.

- `workouts`: completed training sessions; `source`, `provider_source`,
  `start_date_local`, `local_date` and original-file references.
- `planned_workouts`: planned sessions, separate from completed activities;
  `source`, `source_id`, `start_date_local`, `local_date`, `status`, category and
  structured workout details. Passing the planned date does not imply completion.
- `wellness`: daily measurements and their provenance.
- `sync_state`: durable cursors, catch-up progress and concurrency leases.
- `sync_runs`: outcomes and counts; omit secrets and health payloads from logs.

`firestore.indexes.json` defines ascending date indexes with source/provider and
planned status. Existing single-field date indexes support the basic range
queries. Index creation is asynchronous; check state before using a new composite
query. Large FIT records and sample arrays belong in Cloud Storage, not inside a
single Firestore document.

## Verification and operation

Deployment checks that there is no `allUsers` or `allAuthenticatedUsers` binding,
then verifies anonymous `/v1/status` is denied and an authenticated database-backed status request succeeds. Use `/v1/status` for deployed backend checks; `/healthz` is intended for in-container process health.
The scheduled job is created only after those checks pass. `--run-now` submits
the first job only when a key is configured; inspect the scheduler result and `/v1/status` afterwards to confirm
the connection and first import. Creating a job is not proof of a successful sync.

Use `gcloud run services proxy ai-coach-sync --project YOUR_PROJECT_ID
--region europe-north1` for an authenticated local proxy, then query `/v1/status`,
`/v1/workouts` and `/v1/planned-workouts`. CLI commands always specify the project.

Emit structured error logs with `event="sync_failed"` or Cloud Logging
`severity="ERROR"` to feed the provided metric. The alert policy creates incidents
in Cloud Monitoring only; no external messages are sent. The app's persistent
sync status remains the source for spotting stale data or a revoked API key.

This setup incurs metered Google Cloud usage; provider subscriptions may incur additional costs.
Scale-to-zero and bounded instances limit idle compute cost; they are not a hard
spending cap. The archive deliberately has no automatic data-expiration policy.

To configure a billing warning, explicitly choose your own billing account and
review the helper's project and threshold settings:

```sh
python3 infra/budget.py --billing-account YOUR_BILLING_ACCOUNT_ID
```

A budget warning is a notification, not a hard spending cap. Keep real billing
account identifiers and operational receipts in ignored local configuration.

## Primary references

- [Source deployment and custom build identities](https://docs.cloud.google.com/run/docs/configuring/services/build-service-account)
- [Cloud Scheduler supported regions](https://docs.cloud.google.com/scheduler/docs/locations)
- [Private Cloud Run scheduled invocation](https://docs.cloud.google.com/run/docs/triggering/using-scheduler)
- [Cloud Scheduler HTTP/OIDC authentication](https://docs.cloud.google.com/scheduler/docs/http-target-auth)
- [Firestore Native database creation](https://docs.cloud.google.com/sdk/gcloud/reference/firestore/databases/create)
- [Security Rules management API](https://firebase.google.com/docs/rules/manage-deploy)
- [Uniform Cloud Storage bucket creation](https://docs.cloud.google.com/sdk/gcloud/reference/storage/buckets/create)

Local syntax checks do not validate actual account permissions or cloud readiness;
those are verified during provisioning.

## MCP and dashboard deployment

`deploy_chat.py` deploys a separate OAuth-protected adapter. Configure its own
project, project number, backend URL and public resource audience before use.
Provider setup helpers likewise need your own tenant and resource configuration.
Never deploy using identifiers copied from another person's installation.

The OAuth config file must contain exactly `oauth_issuer`, `oauth_jwks_url` and
`owner_subject`. Use the immutable subject established by a verified owner login.
Keep this account configuration in ignored local storage; do not include tokens
or client secrets.

```sh
python3 infra/deploy_chat.py --oauth-config .local/chat-oauth.json --check-only
python3 infra/deploy_chat.py --oauth-config .local/chat-oauth.json
```

The helper checks provider metadata and private backend IAM, deploys privately,
then verifies discovery and missing/invalid-token denial before publication.
A failed final probe removes public adapter access. The data backend remains
private. These deployment probes do not prove a successful owner login, tool call
or token renewal; verify those separately in the intended MCP client.

For the dashboard, configure a dedicated first-party SPA using
`configure_dashboard.py`, build its frontend, and pass its nonsecret receipt with
`--dashboard-config .local/dashboard-auth0.json`. The helper validates the exact
tenant, audience and callback and requires compiled dashboard HTML. Later
redeploys without this flag preserve the existing dashboard client ID.

Use an exact callback, PKCE, owner-bound API access and rotating refresh tokens.
The dashboard and hosted MCP client are separate OAuth applications. See
[connection setup](../docs/CHAT_CONNECTION.md) and
[dashboard setup](../docs/DASHBOARD.md).

## Manual date-range sync

After configuring the operator helper for your deployment, run a bounded historical
refresh using the same importer as the scheduled service. These dates are illustrative:

```sh
.venv/bin/python -u infra/manual_sync.py --oldest 2024-01-01 --newest 2024-12-31
```

The runner uses this project's authenticated Google CLI and existing Secret Manager key. It imports eligible workouts and files, wellness, and historical planned workouts, then refreshes saved summaries. It shares the scheduled import lease and records progress in a separate manual job; automatic history cursors are preserved. Boundary days overlap and existing deduplication prevents duplicate workouts or files. Source exclusions and unavailable history remain explicit in the result.

Results are saved to `.local/verification/manual_*.json` and Firestore `sync_runs`; completed batches are also recorded in their own `sync_state` document. Re-running the date range safely reuses imported data. API credentials and upstream payloads are never printed.
