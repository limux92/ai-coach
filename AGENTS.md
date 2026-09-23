# Working with Magne

Conserve hosted model tokens. Keep explanations practical and concise, and avoid
repeated approval questions for work already authorized.

## Codex architect, GPT-oss worker

The sole local worker is `gpt-oss:20b` through `scripts/local_worker.py`.
Codex analyzes the task, defines acceptance criteria, reviews the draft, and
integrates the result. Delegate bounded implementation, debugging, refactoring,
documentation, scripts, and test generation to the local worker.

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

## Show the workload split

Use `--label` on worker calls so the local workload report identifies each task.
Record substantial Codex planning and review stages with
`scripts/workload.py record --agent codex --title 'Short label' --status running --id <stable-id>`;
update the same ID to `done` or `failed` when finished. Use a unique ID per stage
and task so later work does not replace earlier history. GPT-oss calls through the
helper record themselves. Keep the labels concise and free of sensitive data.
The report is `.local/worker/workload.html`. Do not equate task counts or local tokens
with measured Codex token savings.
