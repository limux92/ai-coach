# Prompt for a backend and gateway release

Use this with a **terminal-capable local agent** after reviewing and staging the
public source. The existing AI-Coach Local chat agent and `local_worker.py` have
no command-execution tools; they can display the command for you to paste.
Replace the commit title before sending the prompt. Do not use this prompt as a
substitute for any still-pending approval of the exact public file payload.

## First deployment of the static health route

Cloud Run's frontend can intercept `/healthz`; release probes use `/health`.
The old backend revision may not contain this route. After reviewing its live
anonymous 403 and authenticated application `{"detail":"Not Found"}` response,
the release command can explicitly include
`--bootstrap-backend-health EXACT_EXISTING_BACKEND_REVISION`.
That revision must still receive exactly 100% of backend traffic at preflight.
This permits only the old application's missing health route; candidate and
production health remain mandatory 200 responses with the expected static JSON.
It does not permit IAM/configuration changes or a Google frontend 404.
Include this migration flag in the exact release command submitted for approval;
omit it from subsequent releases after `/health` is available.

For the separately reviewed physiology Scheduler change, also include
`--physiology-scheduler-migration`. This accepts only the fixed reviewed
infrastructure diff and applies the narrow existing-job retry/deadline migration.
It cannot authorize unrelated infrastructure edits or IAM changes.

The path choice is also documented in Google's
[Scion Cloud Run deployment guide](https://googlecloudplatform.github.io/scion/hosted/ha/setup-gcp/#3f-verify-hub-health).

## Reviewed release prompt

```text
Work in /Users/magnelima/Workspace/AI-Coach.

I have reviewed and staged the intended public source, including this branch's
unpublished commits. I authorize publishing that source to public GitHub repo
limux92/ai-coach and deploying BOTH the private backend ai-coach-sync and the
dashboard/MCP gateway ai-coach-chat in Google Cloud project
magne-ai-coach-20260915, europe-north1. Preserve existing configuration and IAM.

Use the visible VS Code integrated terminal and keep VS Code open. Run the
reviewed command below once; it includes checks, GitHub sync and cloud release:

.venv/bin/python scripts/release.py --release --public-repo limux92/ai-coach --message "REPLACE WITH A SHORT COMMIT TITLE"

Do not edit the script, stage additional files, skip checks, force-push, merge
the PR, change credentials/IAM or read training records. Let the script perform
its static private-backend health probe using the existing Google Cloud login.
Do not rescan the workspace or load full logs into your context. Follow the
numbered terminal stages. If execution tools are unavailable, show the command
and say it has not run.

On a nonzero exit, stop. Report the failed stage and the printed summary.json
path; do not retry or improvise manual Git/cloud commands. On success, report
only the commit, PR URL, BOTH serving revisions, public dashboard URL, check status
and receipt path. The PR stays open. Do not claim owner login or training-data
reads were verified.
```

For checks without publication or cloud access, use this shorter prompt:

```text
In the AI-Coach workspace, use VS Code's visible terminal to run:
.venv/bin/python scripts/release.py --check
Keep VS Code open. Stop on failure and report the stage and receipt path.
Do not publish, deploy, stage files or change code.
```
