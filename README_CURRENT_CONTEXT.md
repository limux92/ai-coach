# AI Coach — fresh-chat context

Use this file to start a new Codex conversation without carrying old chat history.
It summarizes decisions and boundaries; inspect the repository and live services
before treating operational details as current.

## Project

AI Coach is Magne's private training archive and coaching interface. It imports
workouts from Intervals.icu, preserves raw and structured history, and exposes a
private dashboard plus seven read-only MCP tools for training analysis.

```text
Intervals.icu
    → scheduled private FastAPI importer (`ai-coach-sync`)
    → Firestore `(default)` + Google Cloud Storage
    → public login gateway (`ai-coach-chat`)
        → Firebase Google-login dashboard
        → gateway OAuth for hosted MCP clients
        → private backend through Cloud Run IAM
```

Google Cloud project: `magne-ai-coach-20260915`
Primary Cloud Run region: `europe-north1`
Dashboard target: `https://aiworkoutbuilder.app/dashboard/`
Canonical MCP endpoint: `https://ai-coach-chat-600465847441.europe-north1.run.app/mcp`

The custom domain routes the dashboard through Firebase Hosting. It does not
replace the canonical MCP issuer/endpoint. Verify DNS and certificate state live
before describing the custom domain as active.

## Authentication and security

- Firebase Google sign-in protects the dashboard and owner consent.
- The gateway implements OAuth authorization code + PKCE for hosted MCP clients.
- Auth0 is retired. Do not restore its helpers, configuration, tests, or docs.
- OAuth state lives in the separate Firestore database `ai-coach-auth`.
- The gateway service account must not read the training database directly; it
  invokes the private backend through narrowly scoped Cloud Run IAM.
- The backend must remain private. The public dashboard and MCP tools are
  read-only and cannot trigger syncs or change training records.
- Never print, commit, or send credentials, `.local/`, private exports, FIT files,
  or personal training records to any model.

Read `docs/FIREBASE_AUTH.md`, `docs/CHAT_CONNECTION.md`,
`docs/CUSTOM_DOMAIN.md`, and `infra/README.md` before auth or deployment work.

## Coding workflow

Codex is the architect and reviewer. The sole local worker is `gpt-oss:20b`
through `scripts/local_worker.py`.

Magne's preference: launch GPT-oss tasks through VS Code's visible integrated
terminal, with the prompt/command and progress visible. Keep the terminal output
available for inspection. Do not silently substitute a hidden/background run;
explain any terminal-access limitation first. Keep VS Code open, without restarting
or reloading it unless explicitly requested. This preference is also recorded in
the project `AGENTS.md` and global Codex `~/.codex/AGENTS.md`.

1. Codex analyzes the request, sets acceptance criteria, and keeps architecture,
   authentication, IAM, destructive operations, and release decisions.
2. Delegate bounded implementation, debugging, refactoring, docs, and test drafts
   to GPT-oss, one file or function at a time with only relevant tracked source.
3. GPT-oss writes drafts under ignored `.local/worker/`; never execute or apply
   them automatically. Codex reviews and integrates accepted work.
4. Use low local reasoning by default. Allow one correction attempt, then narrow
   the task or let Codex handle the difficult part.
5. Run focused checks after integration and broader checks only when justified.

For hosted usage, start a fresh chat for each substantial objective. Prefer a
lighter model and standard speed for routine planning/review; reserve Astra or
high reasoning for architecture, security, difficult debugging, and releases.
Batch several independent local drafts under one Codex plan, then review them
together. Send compact failure summaries instead of full logs.

Use concise `--label` values for local tasks. Record meaningful Codex stages with
`scripts/workload.py`. The report is `.local/worker/workload.html`.

## Recent changes

The dashboard uses small JavaScript and CSS modules with a JustInterval-inspired
dark theme. `dashboard/README.md` maps each feature to its source file;
`dashboard/src/styles/tokens.css` holds the palette. Frontend checks enforce
formatting, behavior, 12 KB source-file limits, 240-character line limits, and
production gzip budgets of 50 KB JavaScript and 6 KB CSS.

The local worker accepts tracked file ranges (`--file path:START:END`), limits
prompt plus source to 12 KB, uses 16K context, and defaults to 2,048 output tokens,
a 120-second timeout and five-minute idle retention. The workload dashboard also
includes measured Codex turn counters through `scripts/codex_usage.py`.

Release preparation on 23 September 2026 passed 364 backend tests plus 41 subtests,
191 adapter tests and 12 frontend tests. These are historical results; inspect
`git status` and rerun relevant checks after changes. Git publication and Cloud
Run deployment are separate operations; verify both live before claiming either.

The token importer stores only IDs, timestamps, model names, status, and usage
counters in ignored `.local/worker/codex_usage.json`. It does not copy prompt or
response bodies. Input includes cached context and can count repeated context on
every model call, so raw Codex and GPT-oss totals are not a fair model comparison.

## Verification

```sh
# Token-dashboard work
.venv/bin/python -m pytest -q tests/test_codex_usage.py

# Backend and gateway
.venv/bin/python -m pytest -q
(cd adapters/mcp && .venv/bin/python -m pytest -q)

# Dashboard
npm --prefix dashboard run check

git diff --check
```

GitHub is public. A Git push does not deploy Google Cloud. Before publishing,
inspect the diff and scan for secrets. Before deploying, preserve live Cloud Run
configuration and verify actual traffic, IAM, owner login, training-data reads,
and freshness. Health endpoints and old deployment receipts are insufficient.

## New-chat starter

Paste this into a new Codex chat opened in this repository:

```text
Read AGENTS.md and README_CURRENT_CONTEXT.md completely, then inspect git status.
Preserve existing uncommitted work. Use Codex as the architect/reviewer and
gpt-oss:20b through scripts/local_worker.py in the visible VS Code terminal for
bounded drafts. Keep hosted-model context small, never send secrets or training records
to a model, and do not deploy or push unless I explicitly request it.

First, summarize the current working tree in at most five bullets and wait for my
next task.
```
