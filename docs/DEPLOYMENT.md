# Routine backend and gateway release

Entry point: `scripts/release.py`. It uses the existing Python environments,
GitHub CLI, Gitleaks and project Google Cloud wrapper. There are no new runtime
dependencies. Git operations live in `scripts/release_git.py`; Cloud Run checks
and rollback live in `scripts/release_cloud.py`. Static health and public
endpoint probes live in `scripts/release_health.py`.

The fixed destinations are the **public** `limux92/ai-coach` repository and
both `ai-coach-sync` (private training backend) and `ai-coach-chat` (dashboard/MCP
gateway) in project `magne-ai-coach-20260915`, region `europe-north1`.
Every release deploys both services. It does not import training data, provision
resources or change IAM. Changes under `infra/` relative to `origin/main` stop
the routine release and need a separate infrastructure review.

Use this for compatible application updates. The two traffic switches are
sequential, backend first, so API changes must work with the previous gateway
during that interval. Schema migrations and coordinated breaking changes need
a separate plan. Traffic rollback cannot undo writes made by running code.

## First use

Use VS Code's visible integrated terminal in the AI-Coach workspace. Keep
VS Code open. The existing setup must have:

- Both Python virtual environments installed from their respective lockfiles,
  including test dependencies: `.venv/` and `adapters/mcp/.venv/`.
- Node 24/npm, Git and authenticated `gh` access to `limux92/ai-coach`.
- `.tools/gitleaks/gitleaks` and `scripts/gcloud`, with the existing project
  authentication under `.local/gcloud/`. That existing operator needs permission
  to invoke the private backend as well as deploy both services. The script does
  not grant permissions if the health probe is denied.
- The existing `.local/firebase-auth.json` configuration receipt and live
  gateway/backend services. No credentials belong in the prompt or Git.

Review the intended diff and stage those files in VS Code Source Control.
Include the release script, helpers, tests and documentation on its first release.
Inspect existing branch commits too: pushing a branch publishes its history,
not just the newest staged diff. Secret scanning cannot identify every piece
of personal data; the public-source review is still necessary.

A release requires all intended source staged, no leftover unstaged or untracked
non-ignored files, and a branch containing current `origin/main`. The script
never stages everything, stashes changes, force-pushes, resolves conflicts or
merges a PR. If main has moved, update the branch deliberately before retrying.

## Commands

Show staged/unstaged/untracked scope without tests or external writes:

```sh
.venv/bin/python scripts/release.py --plan
```

Run local Python tests, frontend format/tests/build/size checks, staged and
full-history secret scans, and a generated-assets secret scan:

```sh
.venv/bin/python scripts/release.py --check
```

After the exact public source payload has been reviewed and authorized, run:

```sh
.venv/bin/python scripts/release.py --release \
  --public-repo limux92/ai-coach \
  --message "Describe the reviewed change"
```

`--release` includes all checks, so a separate `--check` run is optional.
The destination flag acknowledges the public target; it does not override an
agent's approval rules or an earlier rejected publication request.

## What a release does

1. Validates the repository, staging area and scope; fetches main; freezes the
   staged tree. Installs frontend dependencies with `npm ci`.
2. Runs the local checks and scans. Stops if source changes during the run.
3. Reads current cloud configuration/IAM for both services. Verifies gateway
   public metadata and access denials, and private backend static `/healthz`.
   Saves both previous traffic allocations and recovery commands.
4. Commits staged files, pushes the current branch and opens/reuses a PR. On
   main it creates a release branch. Waits up to 30 minutes for all four named
   CI jobs on that PR head. Failed, skipped or cancelled jobs cannot pass.
5. Packages committed source and the exact checked dashboard assets into an
   isolated directory. Scans it and records each service's upload inventory.
6. Deploys a tagged revision of each service with zero serving traffic. Checks
   that environment, secrets bindings, resources, service identity and IAM are
   preserved. Verifies private backend static health, gateway OAuth metadata,
   access denials and SHA-256 hashes of every dashboard asset.
7. After **both** candidates pass, rechecks the PR and CI. Promotes the backend,
   verifies it, then promotes and verifies the gateway. Removes the release tags
   and verifies production HTTPS, redirects, assets and access denials.
   If promotion or production verification fails, attempts to restore both
   previous traffic allocations, gateway first. It still attempts backend
   recovery if gateway recovery fails; another operator's traffic is not overwritten.

The **local script** deploys after CI passes. GitHub CI itself does not deploy.
The PR remains open; production may therefore be ahead of main until it is
merged. Source commits and PRs are not rolled back when a cloud stage fails.
Cloud rollback restores traffic only, not data, IAM or environment configuration.
It records the recovery outcome separately for each service.

Cloud Run's [zero-traffic deployment flag](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy#--no-traffic)
and [GitHub check states](https://cli.github.com/manual/gh_pr_checks) are the
provider interfaces used by this workflow.

## Results and failures

The terminal prints numbered stages and a heartbeat during long commands.
Detailed logs, upload inventory, before/after descriptions, asset hashes and
`summary.json` remain under ignored `.local/releases/<run-id>/`, created with
private filesystem permissions. These files can contain configuration details:
report the summary fields, not complete logs or service descriptions to a model.

Exit 0 means the requested mode completed. Any failure exits nonzero. Stop and
report the failed stage and receipt path. Do not blindly rerun or substitute
manual cloud commands. A failed candidate may leave a zero-traffic revision and
its tag for inspection. If interrupted, a build may still finish remotely;
inspect the receipt and live traffic before retrying. The saved
`manual_traffic_rollback` command is for a reviewed recovery if automatic rollback
cannot be verified. Concurrent changes by another operator require review.

The backend probe uses a short-lived identity token from the existing Google
Cloud login solely for `/healthz`, whose response is static, following
[Google's private-service test flow](https://docs.cloud.google.com/run/docs/authenticating/developers#test_your_private_service). The token stays in
memory and is never written to the release logs. It also checks that anonymous
requests are denied. No authenticated `/v1/status`, workout, database or sync
routes are called. Gateway probes use no owner credentials.

A successful release proves both serving revisions, static backend health,
public deployment and the listed guards. It does **not** prove fresh owner
sign-in, real workout reads or data freshness.

The current **AI-Coach Local** chat agent and `local_worker.py` are draft-only.
They cannot execute a release. Use the [copyable prompt](LOCAL_DEPLOY_PROMPT.md)
with a terminal-capable local agent, or paste the command into the terminal.
