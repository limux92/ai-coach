# Firebase authentication and MCP OAuth

Firebase provides Google sign-in for both dashboard and chat login. Intervals.icu still
uses its own API key in Secret Manager; Firebase authenticates the person reading
the imported records, not the upstream importer.

`gcloud auth login` authenticates the administrator's Google Cloud CLI. It is
separate from the application's Firebase sign-in. Retired provider setup
helpers, their obsolete tests, and old dashboard instructions have been removed.

## Boundaries

- The dashboard uses Firebase Google sign-in. Its ID tokens must have a valid
  Google RSA signature, exact Firebase project audience/issuer, valid times,
  verified email, Google sign-in provider and the configured immutable owner UID.
- Hosted MCP clients use the gateway's OAuth authorization server. Firebase is
  the sign-in step, not an OAuth authorization-server replacement on its own.
- Authorization code + PKCE S256, exact approved redirects and explicit owner
  consent produce opaque `coach:read` access tokens. Codes expire after 60
  seconds, access tokens after 15 minutes, and grants after an absolute 30 days.
- Refresh tokens rotate atomically. Reuse revokes the entire grant, including
  its access tokens. `/revoke` also revokes the grant. Firebase ID tokens are
  accepted only by the browser API/consent step, never as MCP access tokens.
- OAuth state lives in the separate `ai-coach-auth` Firestore database. Bearer
  tokens are stored as SHA-256 document IDs; code/token values are not stored.
  Client-registration secrets are held in this private database. TTL deletes
  expired records eventually; authorization checks expiration immediately.
- The chat service has a conditional IAM grant only for that database. Training
  records remain in `(default)` and the archive remains private. The gateway
  invokes the backend with its Google service identity and a fixed read allowlist.
- Firebase sessions use browser session storage; sign-out clears displayed data
  and cancels pending reads. The backend remains protected by Cloud Run IAM.

## Existing-installation migration

These operator helpers target the existing AI Coach project. Review their
constants before using them in another installation. Receipts live in ignored
`.local/`; never commit them or paste tokens into chat.

```sh
export GCLOUD_BIN="$PWD/scripts/gcloud"
# Renew only when the CLI login expires:
./scripts/gcloud auth login
.venv/bin/python infra/configure_firebase.py
.venv/bin/python infra/provision_auth_store.py
adapters/mcp/.venv/bin/python infra/bind_firebase_owner.py
npm --prefix dashboard ci
npm --prefix dashboard run build
.venv/bin/python infra/deploy_chat.py --firebase-config .local/firebase-auth.json --check-only
.venv/bin/python infra/deploy_chat.py --firebase-config .local/firebase-auth.json
```

The owner-binding helper opens a temporary loopback page, verifies a real Google
sign-in against the signed-in CLI operator's email, and records the signed UID.
It never stores the ID token. Sign in using that Google account. The helper
stops after verification or 15 minutes. Additional MCP hosts require their exact
HTTPS callback supplied with repeated `--callback` options and a redeployment.

The deployment preflight checks the Firebase owner, Google provider, authorized
domain, signing keys, isolated database and narrowly conditioned IAM grant.
It refuses a public data backend. Deployment temporarily makes the gateway
private, tests metadata and missing/invalid-token denial, then publishes it.
Failed final probes restore private gateway IAM.

Reconnect a chat connector that still uses the previous identity provider;
its old tokens will not work. Use the same `/mcp` URL, OAuth, `coach:read`, and complete
Google sign-in/consent. Dynamic registration accepts only configured exact
callback URLs. Other client callback URLs must be explicitly configured.

## Validation and rollback

Run backend, adapter and dashboard tests and build the dashboard. Tests use
real generated RSA signatures, mocked public-key HTTP, and synthetic training
records. OAuth tests exercise the complete SDK authorization/token endpoints,
PKCE failures, CSRF/cookie binding, wrong users, single-use codes, refresh replay,
expiration, scope/resource restrictions, registration and revocation.

To run the deployed acceptance check with a fresh owner login:

```sh
GCLOUD_BIN="$PWD/scripts/gcloud" adapters/mcp/.venv/bin/python infra/bind_firebase_owner.py --verify-live
```

The helper revokes all test grants, records only pass/fail facts, and does not
save tokens or training payloads.

A live acceptance check must separately confirm dashboard owner login, seven MCP
tools, a real training read, token renewal, wrong-user denial and anonymous
backend denial. Health/discovery alone do not prove these work. The existing
`infra/verify_chat_live.py` uses `.local/firebase-chat-login.json` and a private
baseline.

Before deploying, save Cloud Run service descriptions privately. Rollback to the
previously serving revision restores its environment; database/IAM changes are
separate. Retired provider setup helpers are not part of the current source tree.
Do not delete the old tenant until the Firebase client connection is verified.

Firebase user disablement does not automatically revoke already issued gateway
OAuth grants. Revoke the gateway grant via `/revoke`, or remove the corresponding
OAuth grant document; its access and refresh tokens then fail. Changing the
configured owner UID rejects all grants for the previous UID. Firebase browser
ID tokens remain valid until expiry unless additional revocation checks are added.

## Primary references

- [Firebase ID-token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
- [Firebase Google sign-in](https://firebase.google.com/docs/auth/web/google-signin)
- [Firebase authentication provisioning](https://firebase.google.com/docs/auth/configure-providers-cli)
- [Firestore database IAM conditions](https://docs.cloud.google.com/firestore/native/docs/manage-databases)
- [MCP authorization specification](https://modelcontextprotocol.io/specification/latest/basic/authorization)
