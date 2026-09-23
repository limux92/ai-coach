# Training dashboard custom domain

`https://aiworkoutbuilder.app/` uses Firebase Hosting to route `/dashboard`
and `/dashboard/**` to the public `ai-coach-chat` Cloud Run gateway in
`europe-north1`, project `magne-ai-coach-20260915`. The root redirects to
`/dashboard/`; `www.aiworkoutbuilder.app` redirects to the canonical domain.
There are no uploaded static files: the existing gateway serves the dashboard
and its assets. Other application routes are not proxied.

Google sign-in and the exact owner UID protect training data. The dashboard is
read-only, responses use `no-store`, and the `ai-coach-sync` backend remains
private. Firebase Auth's authorized domains include both custom hostnames
additively, preserving the existing domains. The Firebase authDomain remains
`magne-ai-coach-20260915.firebaseapp.com`.

The MCP endpoint remains
`https://ai-coach-chat-600465847441.europe-north1.run.app/mcp`; its OAuth issuer
and callback configuration have not changed.

## DNS and HTTPS

GoDaddy manages DNS. Firebase Hosting site `magne-ai-coach-20260915` manages
the custom domains and HTTPS certificates. Records supplied by Firebase on
2026-09-23:

| Name | Type | Value |
| --- | --- | --- |
| `@` | A | `199.36.158.100` |
| `@` | TXT | `hosting-site=magne-ai-coach-20260915` |
| `www` | CNAME | `magne-ai-coach-20260915.web.app` |

Keep the ownership TXT record for certificate maintenance. Preserve the
existing NS, SOA, `_domainconnect`, and `_dmarc` records. For subsequent changes,
read Firebase's live `customDomains.requiredDnsUpdates` instead of assuming
these values remain current. Certificate provisioning is asynchronous.

## Hosting configuration and maintenance

[`infra/firebase-hosting.json`](../infra/firebase-hosting.json) contains the
Firebase Hosting REST `ServingConfig`, **not** Firebase CLI `firebase.json`.
It was deployed through the Hosting API by creating a version with this
`config`, finalizing the version, and creating a release. The private receipt
is `.local/dashboard-hosting-release.json`.

Use Firebase Hosting's release history to roll back routing changes. Changing
the local JSON alone does not deploy or roll back anything. A Hosting rollback
does not reverse DNS, Firebase Auth settings, or Cloud Run deployments.
Cloud Run traffic remains on the existing service revision.

Verify valid HTTPS on both hostnames, root and www redirects, loaded dashboard
assets, and HTTP 401 from `/dashboard/api/status` without a token. Then verify
Google sign-in and an actual owner data read in the browser. Public HTML alone
does not establish successful authentication. The existing run.app dashboard
URL remains available while DNS or certificates propagate.
