# Working with Magne

Conserve hosted model tokens. Keep explanations practical and concise, and avoid
repeated approval questions for work already authorized.

On a fresh conversation, read `README_CURRENT_CONTEXT.md` and the local handoff it
references before investigating prior work. Preserve staged, unstaged and untracked
changes. Keep the coordinator handoff current after meaningful verified outcomes;
the smaller worker memo below serves a different audience and must stay bounded.

## Codex design, GPT-oss code, Gemini infrastructure

The sole local worker is `gpt-oss:20b` through `scripts/local_worker.py`.
Codex owns design, architecture, integration review and final testing. Local GPT-oss
owns bounded application code and unit-test drafts. Gemini/Antigravity owns
infrastructure investigation, infrastructure changes and infrastructure testing.
Keep each delegation bounded and report which agent actually completed it.
Codex reviews drafts before integration; preserve the existing deployment approval boundary.

Run GPT-oss task commands in VS Code's visible integrated terminal so Magne can
see the prompt and follow progress. Use `scripts/local_worker.py` there and keep
the terminal/output available for inspection. Do not silently run local-model
tasks in a hidden/background terminal. If integrated-terminal access is
unavailable, explain the limitation before choosing another route. Never close,
restart, or reload VS Code to run a task unless Magne explicitly requests it.

1. Scope each coding draft to one file or function. Supply only relevant tracked
   source with `--file` and concrete acceptance criteria in the prompt.
2. Select `--task implement`, `debug`, `refactor`, `docs`, or `explain`.
   Use low reasoning by default. Implementation/refactoring returns replacement
   code; Codex prepares the actual diff after review.
3. The helper saves uniquely named drafts under `.local/worker/`. Never apply or
   execute its output automatically. An incomplete draft exits with code 2.
4. Run focused checks after review. Give the worker concise failure feedback and
   allow at most one correction attempt; then narrow the task or handle the hard
   part directly. Avoid sending full logs and rescanning the whole repository.

Keep architecture, authentication/security, IAM, destructive operations, and
release decisions with the primary assistant. Local inference does not make
generated code trustworthy. Never execute model output automatically or include
credentials or personal training records in prompts. The helper produces drafts;
it is not an autonomous editing or testing agent.

Use `README_LOCAL_AI.md` for the stack, commands, and operational boundaries.

## Bounded task routing

For new delegated work, prefer `scripts/harness.py` and `docs/HARNESS.md`.
Codex creates a job with one tracked source selection, a goal and acceptance
criteria, then runs its ID in VS Code's visible integrated terminal. The runner
calls the existing local worker; it does not replace the worker's limits.
Classify architecture and final review as Codex work. Gemini may prepare and test
infrastructure/IAM changes; security-sensitive access and release decisions remain
with Codex and the user. Task labels are routing instructions, not semantic detection.

The harness invokes Antigravity for bounded Gemini reviews/drafts in a fresh packet
workspace with a task-local exact-source read hook. Run it visibly, inspect the saved
result/tool evidence, and disclose failures. It does not execute infrastructure
commands or deploy. Direct, explicitly scoped Antigravity infrastructure tasks stay
separate. Never purchase credits, silently change models or schedule quota resumes.

Keep the user conversation with Codex; workers receive only their bounded brief.
Codex maintains `.local/worker/context.md` as a concise worker-safe memo of the
objective, decisions, verified state and open work (maximum 4000 UTF-8 bytes).
New harness jobs freeze that memo into their brief for both workers; the combined
12 KB input limit still applies. This curated memo is the only automatic exception
to excluding `.local/` content from prompts. Never include secrets, private training
data, raw transcripts or logs. Workers return results separately; only Codex merges
verified findings into the memo. A memo is not permission to run commands.
Read it on resumption and update it after reviewed outcomes. When it changes scope
or records a revoked authorization, stop/reassess affected jobs; existing snapshots
are intentionally immutable and are not updated automatically.
On resumption and before a completion claim, inspect `scripts/harness.py status`
and the relevant saved job/release evidence. A queue entry, worker SUCCESS, accepted
draft or GitHub push does not prove deployment. Release completion requires the
current successful `--release-receipt`; check live user-visible behavior separately.
Report which worker actually ran, what it returned, and what Codex verified.

Review a complete draft before applying it, recording `review --checks` evidence.
Source or draft changes invalidate acceptance. Apply reviewed changes and run
appropriate checks separately, then record `complete --checks` with the actual
results. An accepted draft is not an applied or tested change. Preserve existing
user edits; job files and reports remain ignored under `.local/worker/`.

## Lightweight frontend

Use plain JavaScript modules, semantic HTML, native controls and system fonts.
Keep source readable: one feature per file, source files below 12 KB, and lines
below 240 characters. Keep dark colors in `dashboard/src/styles/tokens.css`;
use `dashboard/README.md` to find the relevant view or stylesheet. Do not send
the whole frontend to the worker. Select one file or `--file path:START:END`.
Keep authentication and data semantics intact. Avoid new runtime dependencies
unless the task needs them. Run `npm --prefix dashboard run check` after a
frontend change; it checks formatting, behavior, the build and gzip budgets.

## Show the workload split

Use `--label` on worker calls so the local workload report identifies each task.
Record substantial Codex planning and review stages with
`scripts/workload.py record --agent codex --title 'Short label' --status running --id <stable-id>`;
update the same ID to `done` or `failed` when finished. Use a unique ID per stage
and task so later work does not replace earlier history. GPT-oss calls through the
helper record themselves. Keep the labels concise and free of sensitive data.
The report is `.local/worker/workload.html`. Do not equate task counts or local tokens
with measured Codex token savings.

## Reviewed routine releases

When the user authorizes the exact public source payload and routine backend/gateway
release, a terminal-capable agent may run the reviewed `scripts/release.py`
command from `docs/LOCAL_DEPLOY_PROMPT.md` in the visible VS Code terminal.
This is execution of a reviewed workflow, not permission to execute model drafts,
auto-stage files, bypass a failed check or expand cloud access. Keep release
scope and recovery decisions with the primary assistant. The draft-only local
worker and read-only custom chat agent still cannot execute commands.
