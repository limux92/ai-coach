# Training dashboard

The dashboard is a responsive, read-only view of your training data, served at
`/dashboard/` by the MCP adapter. Firebase Google sign-in protects data access.
See [Firebase setup](FIREBASE_AUTH.md) and [custom domain routing](CUSTOM_DOMAIN.md).

## Views

| View | Description |
| --- | --- |
| Overview | Monthly moving time, distance, session count, provider training load, twelve-week volume, heart-rate zones and recent workouts. |
| Calendar | Monday-first month or week layout, completed and planned sessions, weekly totals, sport filters and date navigation. |
| Workout details | Summary metrics, notes, source activity ID, laps and heart-rate/power samples. Samples load in pages of 500; the chart identifies partial coverage. |

Calendar dates use the recorded local workout date. Current date and sync times
use Europe/Oslo. Totals describe known imported workouts; blank days do not prove
rest days, and missing measurements are not fabricated. Virtual cycling is
identified; heart-rate zone definitions remain separate when boundaries differ.

Planned workouts are displayed from the existing collection. Source plans remain
managed in Intervals.icu. Oversized FIT files with summary-only availability have
no sample chart; their originals remain archived.

## Private access

The `ai-coach-chat` Cloud Run service hosts the dashboard and read gateway.
The gateway verifies Firebase signatures, project, provider, verified email and
the exact immutable owner UID on each API request. It assigns the owner's
`coach:read` permission and queries the private backend using its own Google
service identity. Browser tokens are never forwarded to that backend.

Firebase sessions use browser session storage. Sign-out clears displayed data
and cancels pending reads. No training records, API secrets or owner tokens are
embedded in the frontend build. `/dashboard/config` contains public Firebase
configuration. API responses use `no-store`; there is no service worker or
offline training archive.

## Build, test and deploy

From the repository root:

```sh
npm --prefix dashboard ci
npm --prefix dashboard test
npm --prefix dashboard run build
GCLOUD_BIN="$PWD/scripts/gcloud" .venv/bin/python infra/deploy_chat.py \
  --firebase-config .local/firebase-auth.json
```

Assets build into `adapters/mcp/static/dashboard`. This command deploys the
gateway and dashboard; backend deployment is a separate operation described in
the [infrastructure runbook](../infra/README.md).

Backend calendar projections under `/v1/dashboard` allow at most 366 days,
50 rows and a 58 KB response budget. Follow `next_cursor` even when a page has
fewer than 50 rows. Browser sample pages are capped at 500 records.

After deployment, verify Google owner sign-in, calendar totals, workout details,
samples, sign-out denial, private backend IAM and MCP OAuth. Public HTML alone
does not prove authenticated training-data access.
