# Training dashboard

The dashboard is served at `/dashboard/` on your configured adapter origin, for
example `https://your-adapter.run.app/dashboard/`. Public HTML alone does not
prove that owner login or authenticated training-data access works; complete the
acceptance checks after deployment.

## Views

- **Overview:** monthly moving time, distance, session count and provider training load; twelve weeks of training time; heart-rate zone totals; recent workouts.
- **Calendar:** Monday-first month or week layout, completed and planned sessions, weekly totals, previous/next/Today controls, and a month picker for historical data.
- **Filters:** all activities, running, cycling, or other sports.
- **Workout details:** summary metrics, notes, source activity ID, laps and heart-rate/power samples. Samples load in pages of 500; the chart states when only part of a session is loaded.

Calendar dates use the recorded local workout date. Current date and sync times use Europe/Oslo. Summary totals describe known imported workouts, not a guarantee of complete training history. Blank calendar days do not confirm rest days. Missing measurements are not fabricated. Virtual cycling is identified; heart-rate zone definitions remain separate when their boundaries differ.

Planned workouts are displayed from the existing collection. The dashboard does not create or edit plans. Source plans remain managed in Intervals.icu. An oversized FIT with summary-only availability has no sample chart; its original remains archived.

## Private access

The existing `ai-coach-chat` Cloud Run service serves the interface and read gateway. A dedicated first-party Auth0 SPA uses authorization code with PKCE and rotating refresh tokens; tokens are cached in memory. The gateway checks the existing exact owner identity, issuer, audience, expiry and `coach:read` scope on every API request. It uses its own Google service identity to query the private backend. Browser OAuth tokens and cookies never reach the backend.

No training records, API secrets, service-account keys or owner tokens are embedded in the frontend build. Runtime `/dashboard/config` contains public OAuth configuration only. API responses are marked `no-store`; there is no service worker or offline training archive in the browser. The MCP endpoint and ChatGPT connection share the existing service but retain their own clients and routes.

## Build and test

```sh
cd dashboard
npm ci
npm test
npm run build
```

Build output goes to `adapters/mcp/static/dashboard`. The backend build excludes `dashboard/`; the adapter build includes the generated assets. Dependencies are locked in `dashboard/package-lock.json`.

Backend endpoints are compact read projections under `/v1/dashboard`, with at most 366 calendar days per request, up to 50 rows and a 58 KB response budget. Clients must follow `next_cursor` even when a page contains fewer than 50 rows. The browser gateway caps sample pages at 500 records.

## Login setup and deployment

First configure the operator helpers for your own project, OAuth tenant and audience.
With an authenticated Auth0 CLI session for that tenant:

```sh
.venv/bin/python infra/configure_dashboard.py --connection-id EXISTING_DATABASE_CONNECTION_ID
```

This reconciles only the marked dashboard SPA, its user grant and its membership in the existing login connection. Membership uses Auth0’s current cursor-paginated `connections/{id}/clients` endpoint and an additive update for the dashboard client only; it does not use the retired `enabled_clients` field. It preserves other clients and does not change the tenant, API, owner binding, or ChatGPT applications. Its `.local/dashboard-auth0.json` receipt contains only public resource identifiers.

Build the frontend, deploy the private backend, then deploy the adapter:

```sh
CLOUDSDK_CONFIG="$PWD/.local/gcloud" \
GCLOUD_BIN="$PWD/.tools/google-cloud-sdk/bin/gcloud" \
.venv/bin/python infra/deploy_chat.py \
  --oauth-config .local/chat-oauth.json \
  --dashboard-config .local/dashboard-auth0.json
```

The deployment helper validates the receipt against the configured tenant, audience and exact dashboard callback. It refuses to deploy a configured dashboard without its compiled HTML. Later redeploys without `--dashboard-config` preserve the existing dashboard client ID.

After deployment, verify owner sign-in, calendar/KPI values against stored records, workout details and samples, sign-out denial, private backend IAM, and the existing MCP OAuth challenge. An Auth0 setup permission or login still awaiting user action must remain explicitly marked as unverified.
