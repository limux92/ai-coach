# Bounded local and Gemini task workflow

Codex owns design, architecture, integration review and final checks. Local GPT-oss
owns bounded application code and unit-test drafts; Gemini owns infrastructure and
infrastructure reviews and change/test proposals. Codex scopes each handoff. Local
GPT-oss drafts through `scripts/local_worker.py`; Gemini runs through the installed
Antigravity CLI with the existing sign-in. The harness invokes Gemini for bounded
reviews, not cloud commands or deployments. No credits are purchased and no task
resumes automatically at a quota refresh time.

Keep the user conversation with Codex. Workers receive a fresh bounded packet;
they do not inherit the full chat. Saved goals, acceptance criteria, source hashes,
drafts, failures and review/check evidence provide continuity across conversations.
At resumption, Codex runs `status`, inspects relevant job records, and reconciles
the actual files and release receipt before reporting progress. This is not a
VS Code Agent Host adapter or automatic chat-history synchronization.

## Shared context file

Codex maintains ignored `.local/worker/context.md` as the current working memo.
Keep it under 4000 UTF-8 bytes, with the objective, decisions, verified state and
open work. Keep it across chat restarts; it is working state, not a file in the
OS temporary directory that may disappear. Replace outdated details as work moves
forward rather than appending an unlimited transcript.

Every new job automatically captures the memo's text and SHA-256 fingerprint in
its saved record. Both GPT-oss and Gemini receive that frozen text in their brief,
alongside their task and selected source. The entire input still must fit 12 KB;
an oversized packet fails before inference. Use `create --without-context` for
unrelated tasks. Existing jobs created before this feature receive no new context.

Run `harness.py context` to inspect the current memo and `show JOB_ID` to see which
snapshot a job uses. `handoff JOB_ID` includes the snapshot for manual handoffs.
Updating the memo affects newly created jobs only. If scope or authorization
changes, Codex must stop/reassess affected jobs and create fresh briefs. The memo
does not grant permissions or override each worker's tool policy.

Workers return their existing draft/result artifacts. Codex checks those against
the source, command results and deployment receipts, then updates the shared memo.
Do not let multiple workers overwrite it. This explicitly curated text is the
exception to excluding `.local/` from prompts: never load neighboring private
files, credentials, personal training records, raw transcripts or full logs.
It is a lightweight shared notebook, not automatic access to other agents' chats.

## Use it in VS Code

Run **Tasks: Run Task** and select an `AI-Coach:` task:

| Task | Action |
| --- | --- |
| Create local job | Collect a brief and one source selection; no inference |
| Run worker job | Run a local or Gemini job ID in a visible dedicated terminal |
| Create Gemini review | Save a ready job; inference starts only with Run worker job |
| Harness status | Show job failures and the latest saved release outcome |
| Prepare handoff | Save a bounded packet locally; send nothing |
| Record review | Record acceptance/rejection and checks actually performed |
| Inspect harness setup | Inspect local executables/extension metadata only |

Keep the terminal available for inspection. The runner enforces a VS Code
terminal/TTY check; this is a workflow guard, not a security sandbox. One harness
mutation runs at a time. Direct helper/chat requests are outside this lock;
avoid launching them while a harness worker is running.

## Command workflow

Run from the repository root, using the ID printed by `create` in later commands:

```sh
.venv/bin/python scripts/harness.py create --title 'Explain source selection' \
  --goal 'Explain the source validation in three bullets' \
  --acceptance 'Ground each claim in the supplied function' \
  --file scripts/local_worker.py:28:59 --task explain
.venv/bin/python scripts/harness.py run JOB_ID
# Inspect the draft; replace the evidence below with checks actually performed.
.venv/bin/python scripts/harness.py review JOB_ID --verdict accepted --checks 'Review evidence'
# Codex applies any reviewed changes and runs appropriate checks separately.
.venv/bin/python scripts/harness.py complete JOB_ID --checks 'Actual integration/check results'
```

Explanation jobs can complete without changing code. Acceptance checks the original
draft hash and the full source-file hash before integration. A change anywhere in
that source file requires a fresh job. Completion records the post-integration hash
and evidence; it never applies code or executes the `--checks` text.

At most two local attempts or three Gemini attempts are allowed per job. A correction uses
`run JOB_ID --feedback 'Concise correction'`. There are no automatic retries.
Incomplete, failed, changed or stale drafts cannot be accepted. After an interrupted
job, inspect its terminal and artifacts; a hard kill can leave its status running.
Use `recover JOB_ID` to record an interrupted run as failed; Gemini recovery refuses
while its saved process ID is still present. Inspect the terminal if termination
cannot be confirmed. Codex must reassess at the attempt limit, not recreate a retry loop.

## Gemini worker

Create a job with `--task review` (Gemini is the default), then run its ID in the
visible terminal. Default model: `gemini-3.7-flash-medium`, verified on 25 September
2026. An intentional model change uses `run JOB_ID --model MODEL_SLUG`. There is no
silent model switch or quota fallback. Default timeout is 120 seconds; `--timeout`
accepts 15–300 seconds and the supervisor adds five seconds for process cleanup.

Each run copies only the selected context to `source.md` in a temporary workspace.
A task-local Antigravity `PreToolUse` hook permits only that exact file read. Shell,
writes, browser tools and delegation are denied. It changes no global permissions.
This is a provider tool policy, not OS-level isolation of the CLI process; use only
reviewed public source. The runner checks the hook audit against completed tool
events before accepting a response. Missing hooks, denied/failed tools, malformed
events, nonzero exit, deadline, excessive output and empty answers all fail.
`SUCCESS` or exit code zero alone is insufficient.

Prompts, stream events, process ID, hook decisions and results are retained under
the job's `gemini-N/` directory. These private diagnostics can contain source.
The terminal shows the brief, tool progress and outcome. A valid response becomes
`needs-review`; it cannot mark the job completed. Gemini may draft infrastructure
changes or test plans as text. Codex reviews and applies them and runs appropriate
checks separately. Direct, explicitly scoped infrastructure execution remains a
separate workflow; it must return actual command results and release receipts.

## Deployment evidence

`status` and the HTML report show the latest saved release attempt, including its
failure stage. They do not query the live service. A worker's statement that a
deployment succeeded is never sufficient evidence.

A Codex `release` job can complete only with `--release-receipt
.local/releases/RUN_ID/summary.json`. The receipt must report successful local/CI,
candidate/production, IAM and private-backend checks, plus both revisions and a PR.
Its commit must match current HEAD and its start time must follow job creation.
This prevents a failed run or old success from completing a new release job.
The existing release approval boundary still applies. User-visible behavior and
authenticated owner data require separate live checks before claiming they work.

## Routing and context

`--task` defaults to `implement`. `implement`, `debug`, `refactor`, `docs`, `explain`
and `tests` default to local. `review` defaults to Gemini. `architecture`,
`security`, `iam` and `release` stay with Codex and reject worker overrides.
`--provider local|gemini|codex` overrides other defaults. Codex classifies the work;
these labels do not detect sensitive work hidden inside a routine task.
Codex jobs stay in the primary conversation; use `complete` to record that work
and its validation directly, without a worker draft.

Select one tracked public source file or inclusive line range. The combined brief
and context must fit the helper's 12 KB input limit. Private directories, credential
filenames and symlink selections are rejected. Never put secrets or training records
in a goal, criteria, feedback or review evidence; filenames are not a secret scanner.

`show JOB_ID`, `list`, `report` and `doctor` make no inference calls. `handoff JOB_ID`
saves only the bounded brief and selected source. It is not sent to Antigravity.
`status` summarizes current jobs and saved release evidence without inference.
After a CLI/extension upgrade, rerun a small read-only smoke test before larger tasks.
`doctor` also checks the official extension's `~/.gemini/bin/agy` installation
location when `agy` is absent from PATH; it does not launch that executable.

## Records and usage

Jobs, briefs and drafts stay in ignored `.local/worker/jobs/`. Open
`.local/worker/harness.html` for routing/review metadata; its `workload.html` link
shows measured local usage and Codex stages. Worker labels include a short job ID
and attempt number. No source or prompt is shown in the routing report.
The HTML reloads the last generated report; run `report` to refresh it after an
external release. It does not poll providers. Counters measure neither remaining quota
nor proven savings; Codex scoping and review still consume hosted tokens.

Research and platform limitations are in [AGENT_INTEGRATION_RESEARCH.md](AGENT_INTEGRATION_RESEARCH.md).
