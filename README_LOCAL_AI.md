# AI Coach — local AI handoff

Updated 23 September 2026. Work only in AI-Coach, not JustInterval. Inspect Git
status before changes; the application uses Firebase Google sign-in.
Never print or send `.local/`, credentials or personal training data to a model.

## Architecture

Intervals.icu → scheduled private FastAPI importer → Firestore `(default)` + GCS.
The public gateway serves a Firebase Google-login dashboard and seven read-only
MCP tools. Hosted chat uses the gateway OAuth service with Firebase owner consent.
OAuth state is isolated in Firestore `ai-coach-auth`; the gateway IAM condition
must never permit direct access to the training database.

See [Firebase runbook](docs/FIREBASE_AUTH.md), [infrastructure](infra/README.md),
[summary semantics](docs/COMPUTED_SUMMARIES.md) and [chat setup](docs/CHAT_CONNECTION.md).
Retired identity-provider helpers are removed; use the Firebase runbook.

## Codex architect, local GPT-oss worker

`gpt-oss:20b` is the sole installed local worker. Codex analyzes and divides work,
provides acceptance criteria, reviews the generated code, and runs focused checks.
The local worker handles one file or function per coding draft. `AGENTS.md`
records this workflow for subsequent sessions.

```sh
ollama list
# Start only if the local service is not already running:
ollama serve
# In another terminal:
.venv/bin/python scripts/local_worker.py 'Explain the API methods and error handling briefly' \
  --task explain --file src/ai_coach/intervals_client.py --label 'Explain API client'
ollama ps
```

The helper talks only to `127.0.0.1:11434`, bypasses proxies, accepts only tracked
source as file context and writes drafts only under ignored `.local/worker/`.
It never executes model output. Prompts must not contain secrets or training
records. API inference keeps the model loaded for 30 minutes after each request.
Task presets are `draft`, `implement`, `debug`, `refactor`, `docs`, and `explain`.
Implementation and refactoring request complete replacement code for a single
file or function, avoiding the repeated patch output seen in the comparison.
Codex reviews that code and prepares the actual diff. Each call gets a unique
draft filename unless `--output` is explicitly set. Output defaults to 3072
tokens; use `--max-output-tokens` (256–4096) to adjust it. Truncated or empty
responses are saved as incomplete and exit with code 2. The model is fixed;
`--reasoning low` is the default, with `medium` and `high` available when needed.
Allow at most one correction attempt before Codex reassesses the task. Direct terminal
use avoids hosted orchestration tokens; asking the hosted assistant to delegate
still consumes some hosted tokens for scoping and review.

### Workload view

Open `.local/worker/workload.html` in a browser to see Codex stages and local drafts,
their status, actual model, request duration, and measured input/output tokens. The page
refreshes every five seconds and the helper rebuilds it when a request starts or
finishes. Use `--label 'Short task name'` to identify a local request without
logging its prompt. Raw prompts, source context, and responses are not stored in
the workload log; labels must not contain sensitive information.

```sh
.venv/bin/python scripts/workload.py report
open .local/worker/workload.html
# The assistant records its planning/review stages, reusing --id for updates:
.venv/bin/python scripts/workload.py record --agent codex \
  --title 'Review interval helper' --status running --id interval-review
```

All report data stays under ignored `.local/worker/`. Automatic tracking covers
requests made through this helper after tracking was added, including failures
and incomplete drafts. Direct `ollama` calls are not included. Codex stages are
manually recorded by the assistant; Codex token usage is not measured. Task counts
are not equivalent effort or token savings. A completed local draft still requires
review. An interrupted process may remain marked running if forcibly terminated.
Earlier model comparisons and the original workload log are retained in
`.local/worker/archive/` as historical evidence; they are not active configuration.

## Checks

```sh
.venv/bin/python -m pytest -q
(cd adapters/mcp && .venv/bin/python -m pytest -q)
npm --prefix dashboard test
npm --prefix dashboard run build
git diff --check
```

Backend and gateway have separate virtual environments/lockfiles. Python 3.12+
is supported; containers use 3.14.7 for the backend and 3.12 for the gateway.
Node 24 is used for the frontend. Frontend assets build into ignored
`adapters/mcp/static/dashboard/` and must exist before gateway deployment.

## Operations

Existing project: `magne-ai-coach-20260915`, region `europe-north1`.
Backend: `ai-coach-sync`; gateway: `ai-coach-chat`.
The project CLI wrapper is `scripts/gcloud`; it uses ignored `.local/gcloud/`.
Set `GCLOUD_BIN="$PWD/scripts/gcloud"` for Python operator helpers.

Preserve Cloud Run service descriptions in `.local/verification/` before release.
The Firebase receipt is `.local/firebase-auth.json`. Do not infer a new owner
from an email lookup; use the real signed-login binding helper. Hosted clients
must reconnect after changing issuers. Never make the data backend public.

GitHub is public. No push deploys to Cloud Run. Do not commit receipts, database
exports, FIT files or token files. Review the diff and run the local Gitleaks
scanner before publishing; use a branch/PR for substantial changes.

Preserve import provenance, leases/checkpoints, timezones, pagination and unknown
metrics. Local plans and imported plans remain separate. Large FIT files may be
summary-only; do not claim all sessions have decoded samples. Neither public UI
nor MCP may start syncs or modify training records.
