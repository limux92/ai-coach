# AI Coach data platform

A self-hosted, single-athlete training archive, dashboard and read-only AI coaching connection. It imports eligible Garmin and Zwift workouts from Intervals.icu, preserves original files, and calculates reusable training summaries.

## Architecture

```text
Garmin / uploaded Garmin or Zwift FIT
                ↓
          Intervals.icu
                ↓
      Scheduled private importer
                ↓
    Firestore + Cloud Storage archive
                ↑
        Private Cloud Run API
                ↑
      Owner-authenticated gateway
           ↙              ↘
   Training dashboard    MCP client
```

Cloud Scheduler starts imports every five minutes. The private API uses Google Cloud IAM. A separate OAuth-protected service exposes the dashboard and seven read-only MCP tools; its service account can invoke the backend without direct database or archive credentials.

## Features

- Monthly KPI dashboard, twelve-week volume chart and Monday-first month/week calendar.
- Completed and planned workouts, sport filters, historical navigation and workout details.
- Preserved original FIT files and source JSON, with separate decoded sample artifacts.
- Daily, weekly, monthly and rolling summaries, grouped by sport and historical heart-rate zone definitions.
- Wellness records and local observations with explicit source attribution.
- Read-only MCP tools for compact coaching context and targeted workout analysis.
- Replay-safe imports, bounded historical backfills, durable checkpoints and automatic reconciliation.

The application does not invent missing measurements or treat an empty date as a confirmed rest day. Virtual cycling remains distinguishable from outdoor activity. The dashboard and MCP adapter read planned workouts; local plan creation is available only through the private backend API.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/ai_coach/` | Importer, private API, storage and computed summaries |
| `adapters/mcp/` | OAuth-protected MCP server and dashboard gateway |
| `dashboard/` | Dashboard frontend source and synthetic unit tests |
| `infra/` | Cloud provisioning, deployment and operator helpers |
| `docs/` | Integration contracts, data semantics and setup guidance |
| `tests/` | Backend and infrastructure tests |

## Development

The Python containers target Python 3.12. Install each Python component in its own virtual environment.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

For the MCP adapter, run the equivalent commands from `adapters/mcp`. Build the frontend from the repository root:

```sh
npm --prefix dashboard ci
npm --prefix dashboard test
npm --prefix dashboard run build
```

The frontend build writes assets to `adapters/mcp/static/dashboard/`. Build them before deploying the adapter. Generated assets, dependency directories, local credentials and operational verification reports are not public source artifacts.

## Set up your deployment

Use a dedicated billing-enabled Google Cloud project and your own Intervals.icu and OAuth accounts. Configuration examples use placeholders; they are not working account credentials.

```sh
python3 infra/provision.py --project YOUR_PROJECT_ID
python3 infra/put_secret.py --project YOUR_PROJECT_ID
python3 infra/deploy.py --project YOUR_PROJECT_ID --source . --run-now
```

Project creation and billing linking happen separately. The scripts do not silently choose a billing account. See [infrastructure setup](infra/README.md) for permissions, regional settings and the deployment-specific configuration required by operator helpers.

Store the Intervals key in Secret Manager using the hidden prompt. Do not put credentials in command-line arguments, source, frontend builds or chat. Scheduled imports can access only data available through your connected Intervals account; a completed scan does not prove that an upstream service transferred your entire history.

Configure an established OAuth provider, bind the verified owner's immutable subject, and use the adapter's exact `/mcp` URL as the API audience. See [chat connection](docs/CHAT_CONNECTION.md), [adapter setup](adapters/mcp/README.md) and [dashboard setup](docs/DASHBOARD.md). Public HTML, healthy services and OAuth denial probes do not by themselves verify an owner login or a successful training-data read.

## Data model

Firestore uses collections rather than SQL tables.

| Collection | Purpose |
| --- | --- |
| `workouts` | Completed eligible activities, summaries, provenance and artifact references |
| `planned_workouts` | Imported WORKOUT events and locally authored plans |
| `wellness` | Daily measurements and subjective observations from available sources |
| `observations` | Workout-linked notes, RPE and other user observations |
| `sync_state`, `sync_runs` | Checkpoints, leases, outcomes and synchronization status |
| `athletes`, `schema` | Source identity and schema metadata |
| `training_summaries`, `summary_jobs` | Saved aggregates and durable rebuild jobs |

Original FIT bytes and source JSON are content-addressed Cloud Storage objects. Decoded samples stay in compressed artifacts instead of creating a Firestore document for every sample. Source IDs and hashes support provenance and deduplication.

Imported plans preserve source structures and pairing references. Only Intervals calendar events categorized as WORKOUT enter `planned_workouts`. Edit imported plans in Intervals.icu; replacing them through the local API returns HTTP 409. Local plans are not pushed to Intervals or a watch.

## Supported source handling

Direct `GARMIN_CONNECT` activities are eligible. Manually uploaded FIT files labeled Garmin or Zwift must pass checksum and native manufacturer/activity verification before publication. Strava-origin and unknown-source records are excluded under the current source policy; a direct Garmin activity is not excluded merely because it also carries a Strava metadata ID.

Uploads retain `provider_source=UPLOAD`, manual-upload attribution and verification metadata. Zwift activities retain virtual-distance labeling and Zwift attribution. FIT metadata supports provenance checks but is not a cryptographic authenticity guarantee.

Large eligible Zwift files can use a bounded full-file inspection without expanding every sample into JSON. Their source summaries remain available with `parse_status=summary_only`; the original stays archived and sample reads return HTTP 409. Ordinary supported files retain decoded samples. Rejected candidates retain their safe verification status and original evidence without becoming coach-visible workouts.

## Private API

All deployed backend routes require Cloud Run invoker authorization.

| Route | Purpose |
| --- | --- |
| `GET /v1/status` | Sync status and stale-data indication |
| `GET /v1/context` | Compact coaching overview, saved totals and freshness |
| `GET /v1/summaries` | Saved day/week/month/rolling summary |
| `GET /v1/workouts`, `GET /v1/workouts/{id}` | Completed workout lists and details |
| `GET /v1/workouts/{id}/samples` | Paginated samples with selected fields |
| `GET /v1/planned-workouts` | Planned workout list |
| `POST /v1/planned-workouts`, `PUT /v1/planned-workouts/{id}` | Create or replace local plans |
| `GET /v1/wellness` | Daily measures |
| `GET/POST /v1/observations` | User observations |
| `GET /v1/dashboard/...` | Compact dashboard projections |
| `POST /internal/sync` | Importer with overlap protection |

List clients must follow `next_cursor`; sample clients must follow `next_offset`. See authenticated `/openapi.json` for complete parameters. Check source and summary freshness before drawing conclusions from absent data.

## Operations and limitations

Firestore client rules deny direct browser/mobile access. The archive uses uniform bucket access and public-access prevention. Runtime, build and scheduler identities are separate. Logs should contain counts and fixed error classes, not training payloads or credentials.

Cloud usage is metered. Scale-to-zero, instance bounds and budget alerts help control costs; budget alerts are not spending caps. Provider revocation or expired account access may require owner action.

Additional documentation: [computed summaries](docs/COMPUTED_SUMMARIES.md), [Intervals integration](docs/INTERVALS_INTEGRATION.md), [coaching instructions template](docs/COACH_CHAT_INSTRUCTIONS.md).
