# Migration review — 22 September 2026

The unfinished Firebase migration had several concrete regressions. They are
fixed in the current code:

| Finding | Fix |
| --- | --- |
| Approximate payload check skipped the actual byte limit for many oversized payloads and permitted nested non-finite values | Restored exact JSON byte counting with `allow_nan=False`; regression tests cover ASCII and escaped Unicode |
| Full exception logging could include private exception messages and removed the monitoring event | Structured error event preserves exception class and frame locations, omitting messages, source lines and locals |
| Firebase verification used synchronous network I/O and omitted explicit issuer/provider checks | Async cached Google public-key fetch; real RSA verification and exact project, owner, provider, verified-email and time checks |
| Global test fixture replaced signature verification with JSON parsing | Removed the global verification mock; generated RSA keys and mocked only Google's HTTP key endpoint |
| Firebase issuer was advertised as an OAuth authorization server | Added gateway OAuth discovery, authorization code/PKCE, Firebase consent, scoped opaque tokens, rotation and revocation |
| Old deployment configuration still required retired provider fields | Firebase receipt/preflight/deployment and verified-owner binding replace the old configuration |
| Sign-out could race an in-flight data load | Clear session state, advance request generation and abort active reads when identity changes |

The existing importer retains fixed-origin API-key authentication, bounded
responses, rate-limit retries, provenance checks, import leases and checkpoints.
The private backend remains protected by Cloud Run IAM. The gateway continues
to expose only bounded reads of training data.

Validation: 394 backend/infrastructure tests plus 50 subtests, 209 gateway tests,
7 frontend tests and the production frontend build passed. A live Firebase
owner read and complete gateway OAuth flow passed, including a training-context
read, refresh rotation and replay revocation. Public-source secret scan passed.

The existing hosted chat connection must be reconnected to the new issuer; this
is separate from the successful direct live API/OAuth checks. Retired provider
helpers and their obsolete tests were removed on 23 September 2026. See
[the Firebase runbook](FIREBASE_AUTH.md) for deployment and revocation details.

This review focused on the migration and API boundaries; passing tests do not
constitute an exhaustive audit of every possible upstream dataset.
