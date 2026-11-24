# Connect the private training MCP server

The gateway now provides its own OAuth authorization endpoints and uses Firebase
Google sign-in to verify the owner.
See [Firebase setup](FIREBASE_AUTH.md) for configuration, security
boundaries, token lifetime, revocation and live acceptance checks.

Use `https://YOUR-ADAPTER.run.app/mcp` as the MCP URL and resource audience, with
OAuth scope `coach:read`. The gateway publishes protected-resource metadata and
OAuth metadata, supports authorization code with PKCE S256, and allows dynamic
registration only for operator-approved exact callback URLs.

The authorization server advertises RFC 9207 issuer identification and includes
the exact issuer in successful and error callback responses. This lets new
ChatGPT connections use the configured stable callback
`https://chatgpt.com/connector_platform_oauth_redirect`. Without that declaration,
new connections use a callback-specific URL and exact registration rejects it.
Never replace the callback allowlist with a wildcard to make registration pass.

After changing the login provider or OAuth issuer, update the host's saved MCP
server authentication configuration before reconnecting. Reconnect alone can
reuse the previous issuer and registered client. Check the actual sign-in
destination: it must be the current gateway's `/authorize` and Firebase consent
flow. A redirect to the retired provider means the host migration is incomplete,
even if discovery at the gateway is correct. Do not sign in to the retired
provider or restore its tokens as a workaround.

For a developer-mode connection, open its management page in ChatGPT Plugins
and select **Refresh** after deploying authentication changes. Confirm the
advertised metadata changed, then reconnect and test in a new conversation.
See OpenAI's [metadata refresh instructions](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata).

Complete Google sign-in and explicit consent with the existing `coach:read`
scope. Tokens from a previous identity provider are rejected.
A locally configured MCP client and a hosted connector are separate connections.

Seven read-only tools provide context, summaries, workout lists/details/samples,
plans and wellness. They cannot modify a workout, schedule a sync or send a plan
to a watch. Check freshness and paginate before interpreting missing history.
Use the [coaching instructions](COACH_CHAT_INSTRUCTIONS.md) for data semantics.

An actual successful tool call is the acceptance test. Public HTML, health and
metadata are not proof of owner access. Verify the actual hosted connection with
context, a completed-workout list, and details/samples for a returned workout.
Record which connection was tested and whether renewal was exercised; a separate
local OAuth client does not prove ChatGPT's saved connection works.

For a separate fresh-login gateway check, use the `--verify-live` command in
[Firebase setup](FIREBASE_AUTH.md). It keeps tokens in memory, revokes its test
grants, and saves only pass/fail facts. Do not use the legacy
`infra/verify_chat_live.py` token-file flow for current gateway OAuth tokens: it
assumes a JWT access token, while the gateway issues opaque tokens.
