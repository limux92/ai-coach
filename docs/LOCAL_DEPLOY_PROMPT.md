# Prompt for a checked backend and gateway deployment

Use this with a terminal-capable agent after the public source has been reviewed
and staged. The check and deployment are separate. Replace the receipt path and
commit title before authorizing publication.

## Local checks only

```text
Work in /Users/magnelima/Workspace/AI-Coach using the visible VS Code terminal.
Keep VS Code open. Run exactly:

.venv/bin/python scripts/release_check.py

Do not publish, deploy, stage files, change code, change IAM, or read training
records. Stop on failure and report the failed stage and printed receipt path.
On success, report the checked tree, publication file list, and receipt path.
```

## Read-only deployment validation

```text
Work in /Users/magnelima/Workspace/AI-Coach using the visible VS Code terminal.
Run exactly:

.venv/bin/python scripts/release_deploy.py --validate-only --receipt .local/release-checks/REPLACE_RUN_ID/summary.json

Do not publish, deploy, edit, stage, or rerun checks. Report the command exit and
the validated tree and services. A model statement without command output is not
validation evidence.
```

## Authorized GitHub and Google Cloud deployment

```text
Work in /Users/magnelima/Workspace/AI-Coach.

I have reviewed and staged the exact intended public source and reviewed the
successful check receipt below. I authorize publishing that checked tree to the
public GitHub repository limux92/ai-coach and deploying BOTH ai-coach-sync and
ai-coach-chat in Google Cloud project magne-ai-coach-20260915, europe-north1.
Preserve existing configuration and IAM.

Use the visible VS Code integrated terminal and keep VS Code open. Run exactly:

.venv/bin/python scripts/release_deploy.py --release --receipt .local/release-checks/REPLACE_RUN_ID/summary.json --public-repo limux92/ai-coach --message "REPLACE WITH A SHORT COMMIT TITLE"

The deployment command must consume the existing receipt. Do not rerun tests,
npm, builds, or secret scans inside the deployment phase. Do not edit the scripts,
stage additional files, skip gates, force-push, merge the PR, change credentials
or IAM, or read training records.

On a nonzero exit, stop. Report the failed stage and deployment summary.json path;
do not retry or improvise manual Git or Cloud commands. On success, report only
the check tree, commit, PR URL, both serving revisions, public dashboard URL,
verification status, and deployment receipt path. The PR stays open. Do not claim
owner login, training-data reads, or a physical Garmin import were verified.
```

If the reviewed release includes the exact physiology Scheduler migration, add
`--physiology-scheduler-migration` to both the check and deployment commands.
For the separately reviewed first static-health migration or failed-candidate
recovery, add the exact existing revision flag only to the deployment command.
