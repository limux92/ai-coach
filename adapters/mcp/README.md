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

## Authentication boundaries

1. **Client → adapter:** an established provider issues an OAuth access token. The adapter validates JWT signature, exact issuer, `/mcp` audience, expiry, issued-at/not-before, `coach:read` and the configured immutable owner subject. Only RS256/ES256 are accepted. A valid token for another subject is denied.
2. **Adapter → backend:** the attached Google service account obtains an identity token for the exact backend URL. Incoming OAuth tokens and cookies are never forwarded. Keep backend Cloud Run IAM invocation checks enabled.

Grant the adapter service account `roles/run.invoker` on the one backend service only. It needs no Firestore, Storage, Secret Manager, Intervals or project-wide administrative permission. IAM invocation is service-wide; the adapter's fixed method/path allowlist restricts its exposed operations to reads.

Missing required authentication settings fail startup. Static health, OAuth discovery, the dashboard shell and public OAuth configuration are accessible without a training-data token. All training API routes require owner authorization.

MCP is available at `/mcp`. The outer ASGI lifespan manages the MCP session manager and HTTP clients. Streamable HTTP is stateless with JSON responses; multiple instances do not require sticky sessions. MCP host/origin allowlists remain enabled.

## Configuration

Set these variables using your deployment's actual values. The examples are placeholders:

```text
BACKEND_URL=https://your-backend.run.app
BACKEND_ALLOWED_HOST=your-backend.run.app
MCP_PUBLIC_URL=https://your-adapter.run.app/mcp
OAUTH_ISSUER=https://YOUR-TENANT.auth0.com/
OAUTH_JWKS_URL=https://YOUR-TENANT.auth0.com/.well-known/jwks.json
OAUTH_OWNER_SUBJECT=VERIFIED-IMMUTABLE-OWNER-SUBJECT
```

`BACKEND_URL` must use HTTPS, have no credentials/query/path, use a `run.app` hostname and match `BACKEND_ALLOWED_HOST` exactly. Incoming requests cannot change it. Cloud Run uses an attached service account without downloaded key files.

Optional `DASHBOARD_CLIENT_ID` configures a separate public SPA client. Its absence leaves MCP operational and shows the dashboard's setup state. The dashboard obtains configuration from `/dashboard/config`, uses authorization code with PKCE and an in-memory token cache, and reads `/dashboard/api`. The gateway validates owner tokens before routing and restricts dates, query fields and page sizes. Responses use `no-store` and a constrained Content Security Policy.

## Build and tests

From `adapters/mcp`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Tests use generated signing keys, mocked JWKS and mocked backend responses. They do not retrieve live training data, mint Google credentials or contact cloud services. The production container targets Python 3.12.

Build the frontend from the repository root before building the adapter container:

```sh
npm --prefix dashboard ci
npm --prefix dashboard run build
```

Generated assets belong in `adapters/mcp/static/dashboard/`; the adapter Dockerfile includes them. See [dashboard documentation](../../docs/DASHBOARD.md).

## Deployment and connection

Configure authorization-code/PKCE S256, discovery, the exact resource audience, client registration or preregistration, and the callback required by your MCP host. Establish ownership through a real login; do not infer it from an unverified email or management account.

Review the operator helper's deployment configuration before running [`infra/deploy_chat.py`](../../infra/deploy_chat.py). It checks the provider and private backend, deploys the adapter privately, probes static health/discovery/OAuth denial, then publishes only the adapter. Failed final probes restore private IAM. Those probes do not establish a successful owner login or hosted tool call.

Complete owner login and verify an actual read in your chosen client. Check wrong-user denial and token renewal separately. Keep live verification results private. The adapter implements a resource server; it does not issue access tokens or complete consent for the user.

See [chat connection setup](../../docs/CHAT_CONNECTION.md), [infrastructure](../../infra/README.md) and [summary semantics](../../docs/COMPUTED_SUMMARIES.md).

## References

- [OpenAI MCP authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI MCP server guide](https://developers.openai.com/plugins/build/mcp-server)
- [OpenAI connection testing](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Official Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [SDK ASGI mounting](https://py.sdk.modelcontextprotocol.io/run/asgi/)
- [SDK authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/)
