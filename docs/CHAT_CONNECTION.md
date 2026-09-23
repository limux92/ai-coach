# Connect the private training MCP server

The gateway now provides its own OAuth authorization endpoints and uses Firebase
Google sign-in to verify the owner.
See [Firebase setup](FIREBASE_AUTH.md) for configuration, security
boundaries, token lifetime, revocation and live acceptance checks.

Use `https://YOUR-ADAPTER.run.app/mcp` as the MCP URL and resource audience, with
OAuth scope `coach:read`. The gateway publishes protected-resource metadata and
OAuth metadata, supports authorization code with PKCE S256, and allows dynamic
registration only for operator-approved exact callback URLs.

After changing the login provider or OAuth issuer, reconnect the host's connector
and complete Google sign-in and explicit consent. Tokens from a previous
identity provider are rejected.
A locally configured MCP client and a hosted connector are separate connections.

Seven read-only tools provide context, summaries, workout lists/details/samples,
plans and wellness. They cannot modify a workout, schedule a sync or send a plan
to a watch. Check freshness and paginate before interpreting missing history.
Use the [coaching instructions](COACH_CHAT_INSTRUCTIONS.md) for data semantics.

An actual successful tool call is the acceptance test. Public HTML, health and
metadata are not proof of owner access. Keep tokens and personal verification
records in ignored local files. `infra/verify_chat_live.py` reads a private
`.local/firebase-chat-login.json` and `--expected-json` baseline; it verifies
known records, tool annotations and pagination without printing training data.
