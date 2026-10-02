# Routine backend and gateway release

The release has two explicit commands:

- `scripts/release_check.py` runs tests, builds, scans, and creates a sealed source bundle.
- `scripts/release_deploy.py` consumes that receipt and performs GitHub publication, exact-head CI verification, Cloud Run deployment, and production verification.

The deploy command never runs pytest, npm, a build, or Gitleaks. The compatibility
entry point `scripts/release.py --check` forwards to the check command;
the old combined `scripts/release.py --release` path is retired.

The fixed destinations are the public `limux92/ai-coach` repository and both
`ai-coach-sync` and `ai-coach-chat` in Google Cloud project
`magne-ai-coach-20260915`, region `europe-north1`. Every routine deployment
ships both services. It preserves existing Cloud Run configuration and IAM.

## Prepare the exact source

Use VS Code's visible integrated terminal and keep VS Code open. Review and stage
the exact public files first. The workspace must contain no conflicts, unstaged
changes, or untracked nonignored files. Neither command stages files, stashes,
force-pushes, merges a PR, changes IAM, or reads training records.

The local setup requires the root and adapter Python environments, Node/npm,
the local Gitleaks binary, authenticated `gh`, `scripts/gcloud`, and the existing
private Firebase configuration receipt. Git history must be complete and the
local `origin/main` ref must exist. The check phase does not fetch or contact
GitHub or Google Cloud; `npm ci` may use the package registry when its cache is
insufficient.

Changes under `infra/` stop the routine workflow. The one-time
`--physiology-scheduler-migration` exception accepts only the recorded, separately
reviewed `infra/deploy.py` diff. The same flag must be used in both phases.

## 1. Check and seal

Run:

```sh
.venv/bin/python scripts/release_check.py
```

This command:

1. Validates the repository, public path scope, staging area, local `origin/main`, and staged Git tree.
2. Installs the locked dashboard packages and runs root, adapter, and dashboard checks.
3. Scans staged source, full Git history, generated assets, and the final source bundle with Gitleaks.
4. Creates normalized bundles directly from the staged Git tree and inserts the exact checked dashboard assets.
5. Writes a schema-v2 receipt under `.local/release-checks/<run-id>/summary.json` with file hashes, modes, sizes, service bundle digests, publication paths, tree SHA, and an integrity digest.

A receipt is deployable only when its status is exactly `checked` and
`local_checks_passed` is true. Artifact tampering, symlinks, extra or missing
files, unsafe modes, path escapes, or a changed index tree fail validation.

## 2. Validate the deployment input

Any terminal-capable agent can prove it can consume the receipt without network
or external mutation:

```sh
.venv/bin/python scripts/release_deploy.py \
  --validate-only \
  --receipt .local/release-checks/REPLACE_RUN_ID/summary.json
```

This verifies the sealed receipt, all bundle contents, the clean workspace, and
the current index tree. Add `--commit HEAD` only when the checked tree has already
been committed and its commit tree should also be verified.

## 3. Publish and deploy

After the exact public payload and destination are authorized, run:

```sh
.venv/bin/python scripts/release_deploy.py \
  --release \
  --receipt .local/release-checks/REPLACE_RUN_ID/summary.json \
  --public-repo limux92/ai-coach \
  --message "Describe the reviewed change"
```

The deploy phase revalidates the receipt and tree before any external command.
It then fetches main, rechecks publication scope, reads current Cloud Run state,
commits the exact checked tree, pushes a branch, opens or reuses a PR, and waits
for all four named CI jobs on the exact commit. After CI passes it revalidates
the commit tree and sealed bundle, uploads zero-traffic candidates for both
services, probes them, rechecks CI, promotes them in order, and verifies
production, configuration, IAM, private backend health, and asset hashes.

GitHub CI never deploys. The local command performs the Cloud Run operations
after exact-head CI passes. The PR remains open and is not merged automatically.

Deployment evidence is written under
`.local/deployments/<run-id>/summary.json`. It binds the deployment to the check
receipt hash, receipt integrity digest, and checked tree. A failed run keeps its
stage, error, command logs, Cloud snapshots, and any zero-traffic candidates for
review. Reusing the same successful check receipt is permitted only while its
artifacts and exact tree still validate.

## Failure and recovery rules

Stop on a nonzero exit and inspect the printed receipt path. Do not bypass a
failed gate or replace it with manual commands. Source commits and PRs are not
rolled back after a Cloud failure. If promotion fails, the workflow attempts to
restore each previous traffic allocation and records whether that recovery was
verified.

`--recover-failed-backend-candidate REVISION` is only for a reviewed retry of the
exact latest zero-traffic backend candidate with a confirmed startup health
failure while the previous ready revision still serves 100 percent. The
`--bootstrap-backend-health REVISION` option is only for the reviewed first
migration from an existing backend revision that lacks the static `/health`
route. Both options retain every normal CI, IAM, configuration, candidate, and
production gate.

A successful deployment proves the recorded revisions and static service checks.
It does not prove a fresh owner sign-in, a real workout read, data freshness, or
a physical Garmin import; run those acceptance checks separately when relevant.
