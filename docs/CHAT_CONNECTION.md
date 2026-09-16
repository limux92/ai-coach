# Connect a private training MCP server

This guide describes the connection architecture and acceptance checks. It contains no deployment credentials, personal training records or private conversation links. Replace every placeholder with configuration for your own deployment.

## Connection design

```text
MCP client → OAuth-protected adapter → private Cloud Run backend → saved training records
```

The adapter exposes seven read-only tools. Its dedicated service account can invoke the one backend service and has no direct Firestore, Storage or Intervals credentials. The browser dashboard uses a separate OAuth client and shares the adapter's resource-server validation.

The public MCP URL has this form:

```text
https://YOUR-ADAPTER.run.app/mcp
```

The API audience must exactly match the deployed URL, including `/mcp`. A public endpoint is necessary for hosted clients, but training-data access still requires a valid owner access token. Static health, OAuth discovery and the dashboard shell/configuration do not contain training records.

## OAuth provider contract

Use an established provider with authorization code, PKCE S256, discovery and supported JWT signing keys.

1. Register the adapter's exact `/mcp` URL as the API audience. Define `coach:read`; enable offline access if refresh tokens are required.
2. Restrict grants to the intended delegated user scope. Do not allow anonymous or machine-to-machine grants to substitute for owner login.
3. Configure resource-parameter compatibility and issuer identification where required by the MCP client. Validate the actual provider discovery document.
4. Use supported dynamic registration or preregister a client with the exact callback required by the host. Do not invent a callback or broaden it with wildcards.
5. Complete a real application login and bind the verified owner's immutable `sub`. A management administrator is not automatically an application user; an email search alone does not establish ownership.
6. Verify access-token signature, exact `iss`, `/mcp` audience, `sub`, `iat`, `exp`, `scope` including `coach:read`, and `client_id` or `azp`.
7. Configure rotating refresh tokens if needed and request `offline_access`. Receiving a refresh token and successfully exercising renewal are separate verification steps.

The adapter supports RS256 and ES256 JWT access tokens. Incoming OAuth tokens terminate at the adapter. The private backend receives a separate Google identity token from the adapter's service account.

## Hosted client setup

Consult the host's current MCP connection documentation for feature availability and account/workspace requirements. Register the adapter URL with OAuth, complete owner login and consent, then select the custom connection for the conversation.

A locally configured MCP client and a hosted chat connection are separate installations. If a conversation does not support the custom connection, use a compatible new conversation with it selected. Repeating a prompt in a conversation that cannot invoke the tools does not test the backend.

Auth0 strict third-party applications have different OIDC behavior from first-party applications. Select the host's OAuth/OIDC settings to match the actual client capability; do not assume that success with a separate diagnostic client proves hosted-client compatibility. See [Auth0 third-party troubleshooting](https://auth0.com/docs/get-started/applications/third-party-applications/troubleshooting#no-id-token-returned-from-oauth-token).

## Deployment configuration

Create a private local configuration file with exactly these nonsecret fields:

```json
{
  "oauth_issuer": "https://YOUR-TENANT.auth0.com/",
  "oauth_jwks_url": "https://YOUR-TENANT.auth0.com/.well-known/jwks.json",
  "owner_subject": "VERIFIED-IMMUTABLE-OWNER-SUBJECT"
}
```

These example values are intentionally placeholders. The deployment helper rejects placeholder configuration. Keep actual account identifiers and verification evidence in ignored local files. Never add access tokens, refresh tokens or client secrets to this file.

Operator helpers must be configured for your own cloud project, backend, public audience and OAuth tenant before use; review their deployment-specific configuration and safety checks. From the project root:

```sh
python3 infra/deploy_chat.py --oauth-config .local/chat-oauth.json --check-only
python3 infra/deploy_chat.py --oauth-config .local/chat-oauth.json
```

`--check-only` performs provider and cloud reads. Deployment keeps the adapter private while checking static health, exact discovery metadata and OAuth denial, then publishes it and repeats the checks. Failed final checks restore private adapter IAM. The data backend remains private throughout.

These probes intentionally do not claim that an owner login or hosted client connection works. Complete the acceptance checks below after deployment.

## Acceptance checks

- Anonymous and invalid-token MCP requests receive the expected OAuth challenge.
- A valid token for another subject is denied; owner matching is exact.
- Owner login loads all seven expected read-only tools.
- An actual tool invocation retrieves a known record from the database. Compare it with your private source record; do not rely on values pasted into the prompt.
- Source and summary freshness, cursor pagination, unavailable samples and missing history are reported accurately.
- Refresh-token renewal is exercised separately when applicable.
- The backend still denies anonymous requests, and the adapter identity retains only backend invocation permission.

Use synthetic fixtures for automated tests. Keep live acceptance evidence, activity identifiers, account metadata and health data outside public source control.

The optional operator acceptance helper reads its expected values from a private
baseline instead of embedding a person's workout in source:

```sh
adapters/mcp/.venv/bin/python infra/verify_chat_live.py \
  --expected-json .local/verification/live-expected.json
```

The baseline requires exactly `workout_id`, `workout_name`, `local_date`,
`metrics` (`distance_m`, `moving_time_s`, `average_heart_rate_bpm`), `record_count`
and `week_totals` (`distance_m`, `moving_time_s`). Supply real values privately
for your own deployment; never commit the baseline or its output. The record
count must exceed five for the helper's pagination checks.

## Coaching context and scope

The tools read recorded data and planned workouts. They cannot create a plan, edit a source activity or send a session to a watch. Prior conversations and local coaching notes are not copied automatically into a new chat; preserve desired context explicitly using the [instructions template](COACH_CHAT_INSTRUCTIONS.md).

A similarly named fitness connector may use a different service and source history. Verify tool calls against this custom connection rather than assuming another connector's results describe this database.

## References

- [OpenAI MCP authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Auth0 MCP authorization](https://auth0.com/ai/docs/mcp/get-started/authorization-for-your-mcp-server)
- [Auth0 resource compatibility](https://auth0.com/ai/docs/mcp/guides/resource-param-compatibility-profile)
- [Auth0 application access policies](https://auth0.com/docs/get-started/apis/api-access-policies-for-applications)
- [Auth0 refresh tokens](https://auth0.com/docs/secure/tokens/refresh-tokens/get-refresh-tokens)
