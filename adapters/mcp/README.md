# AI Coach MCP adapter

An owner-authenticated, read-only MCP resource server for the private training backend. The same service serves a dashboard shell and authenticated browser read gateway. Setup and live acceptance are deployment-specific; this repository contains no public account credentials or private verification records.

The adapter uses the official `mcp==2.2.0` Python SDK and its `MCPServer` API. Install the pinned dependencies in this package rather than the unrelated `fastmcp` package.

## Read-only tools

| Tool | Private backend route | Bounds |
| --- | --- | --- |
| `get_coach_context` | `GET /v1/context` | Compact saved totals and bounded recent facts/plans |
| `get_training_summary` | `GET /v1/summaries` | One saved day/week/month/rolling7/rolling28 period |
| `list_completed_workouts` | `GET /v1/workouts` | At most 366 inclusive calendar days; 1–100 records/page |
| `get_workout_details` | `GET /v1/workouts/{id}` | Validated ID from a listing |
| `list_planned_workouts` | `GET /v1/planned-workouts` | At most 366 inclusive calendar days; 1–100 records/page |
| `list_wellness` | `GET /v1/wellness` | At most 366 inclusive calendar days; 1–100 records/page |
| `get_workout_samples` | `GET /v1/workouts/{id}/samples` | Default 100 selected-field records; 1–1000/page, offset 0–1,000,000 |

Pagination cursors and sample offsets are preserved. Responses above 64 KB are rejected with a safe message requesting a narrower query. No write operation, arbitrary URL, sync control, raw database query or Intervals credential is exposed.

Plans imported from Intervals and plans stored locally remain distinguishable. Reading a plan does not send it to a watch. Coaching instructions require checking freshness and treating missing data as unknown.

## Authentication and deployment

Both the dashboard and hosted chat use Firebase Google sign-in. The adapter also
implements an OAuth authorization service because Firebase ID-token login alone
cannot supply the authorization-code/PKCE flow required by hosted MCP clients.

See [Firebase authentication](../../docs/FIREBASE_AUTH.md) for setup, owner binding,
the isolated OAuth database, deployment, revocation and acceptance checks.

Required environment settings are `BACKEND_URL`, `BACKEND_ALLOWED_HOST`,
`MCP_PUBLIC_URL`, `FIREBASE_PROJECT_ID`, `FIREBASE_API_KEY`, `FIREBASE_OWNER_UID`
and `OAUTH_REDIRECT_URIS` (a JSON array of exact approved HTTPS callbacks).
`AUTH_FIRESTORE_DATABASE` must be `ai-coach-auth`.

The browser API verifies Firebase ID tokens. MCP accepts only gateway-issued
opaque OAuth tokens bound to the owner, client, `coach:read` and exact resource.
Incoming tokens are never forwarded to the private training backend. The gateway
uses its attached Google identity to invoke fixed read-only backend routes.

## Build and tests

Use a separate environment in this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Tests exercise real RSA signature verification and the OAuth endpoints, with
mocked Google HTTP, synthetic records and a memory store. No training records or
cloud writes are used. Build `dashboard/` before building the adapter image.
The production adapter container uses Python 3.12.
