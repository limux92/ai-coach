# AI Coach — local AI handoff

Updated 25 September 2026. Work only in AI-Coach, not JustInterval. Inspect Git
status before changes; the application uses Firebase Google sign-in.
Never print or send private `.local/` artifacts, credentials or personal training
data to a model. The deliberately curated `.local/worker/context.md` memo is the
only automatic exception: new harness jobs include its bounded snapshot.
For a new-conversation handoff, read [README_CURRENT_CONTEXT.md](README_CURRENT_CONTEXT.md)
first. It separates the verified ChatGPT OAuth repair from the locally implemented
Garmin/OpenAI feature and links the private receipts and next steps. This runbook
documents commands; it does not replace that current-state handoff.

## Architecture

Intervals.icu → scheduled private FastAPI importer → Firestore `(default)` + GCS.
The public gateway serves a Firebase Google-login dashboard and seven read-only
MCP tools. Hosted chat uses the gateway OAuth service with Firebase owner consent.
OAuth state is isolated in Firestore `ai-coach-auth`; the gateway IAM condition
must never permit direct access to the training database.

See [Firebase runbook](docs/FIREBASE_AUTH.md), [infrastructure](infra/README.md),
[summary semantics](docs/COMPUTED_SUMMARIES.md) and [chat setup](docs/CHAT_CONNECTION.md).
Retired identity-provider helpers are removed; use the Firebase runbook.

## Codex coordinator, GPT-oss and Gemini workers

The [bounded task harness](docs/HARNESS.md) adds saved task briefs, source
fingerprints, bounded attempts, and separate review/completion records
around the existing local worker. Use VS Code **Tasks: Run Task** and select an
`AI-Coach:` task. The private routing report is `.local/worker/harness.html`;
its workload link shows measured worker usage and Codex stages.
Gemini reviews now run through Antigravity in a fresh task workspace, with an
exact-source read hook, deadline and failure checks. Use **Create Gemini review**
then **Run worker job**. Codex retains the user conversation and verifies results.
**Harness status** includes saved deployment failures; worker prose cannot complete
a release. Nothing resumes automatically when quota refreshes. See the harness
guide for permissions, evidence and the separate deployment approval boundary.

`gpt-oss:20b` is the sole installed local worker. Codex analyzes and divides work,
provides acceptance criteria, reviews the generated code, and runs focused checks.
The local worker handles one file or function per coding draft. `AGENTS.md`
records this workflow for subsequent sessions.

Launch local-worker tasks in VS Code's visible integrated terminal so Magne can
see the prompt and follow progress. Leave its output available to inspect. If
terminal access is unavailable, explain that before using another route; do not
silently run GPT-oss in the background. Keep VS Code open without restarting or
reloading it unless explicitly requested.

```sh
ollama list
# Start only if the local service is not already running:
ollama serve
# In another terminal:
.venv/bin/python scripts/local_worker.py 'Explain the API methods and error handling briefly' \
  --task explain --file src/ai_coach/intervals_client.py:1:80 --label 'Explain API client'
ollama ps
```

The helper talks only to `127.0.0.1:11434`, bypasses proxies, accepts only tracked
source as file context and writes drafts only under ignored `.local/worker/`.
It never executes model output. Prompts must not contain secrets or training
records. API inference keeps the model loaded for 5 minutes after each request.
Task presets are `draft`, `implement`, `debug`, `refactor`, `docs`, and `explain`.
Implementation and refactoring request complete replacement code for a single
file or function, avoiding the repeated patch output seen in the comparison.
Codex reviews that code and prepares the actual diff. Each call gets a unique
draft filename unless `--output` is explicitly set. Output defaults to 2048
tokens; use `--max-output-tokens` (256–4096) to adjust it. Truncated or empty
responses are saved as incomplete and exit with code 2. The model is fixed;
`--reasoning low` is the default, with `medium` and `high` available when needed.
Allow at most one correction attempt before Codex reassesses the task. Direct terminal
use avoids hosted orchestration tokens; asking the hosted assistant to delegate
still consumes some hosted tokens for scoping and review.

The helper uses a 16,384-token context and rejects prompts plus source above
12,000 UTF-8 bytes before inference. Use `--file path:START:END` for an inclusive
line range. The request timeout is 120 seconds; `--timeout-seconds` accepts 30–600.
Reduce the task before increasing limits. Run one local request at a time and
avoid a simultaneous VS Code Chat request. A timeout ends the helper's wait;
it is not a guarantee about server-side cancellation.

For frontend work, start with [the dashboard file map](dashboard/README.md).
The entire dark palette is in `dashboard/src/styles/tokens.css`. New source
files must be tracked before the helper accepts them as `--file` context.
Do not send build output, the lockfile, the whole repository, or private data.

### VS Code Chat with Ollama

The workspace's `AI-Coach Local` custom agent provides focused read/search tools
and drafts for review. It does not edit or run code. Select that agent with
`gpt-oss:20b` and start a fresh chat for each small task, attaching only the
relevant source file. The editor adds instructions, conversation history, tool
definitions and tool results, so Chat can use more context than the helper.

Use the official Ollama extension (`ollama.ollama`, provider `ollama-models`).
The agent's model selector is `gpt-oss:20b (ollama-models)`. The older built-in
`ollama` provider is deprecated; remove or migrate its entry in VS Code's user
`chatLanguageModels.json` so the two providers do not offer duplicate models.

If Chat says "Sorry, no response was returned", check the Copilot and Ollama
logs before increasing limits. A full input context can leave no room for an
answer: on 25 September, a 16,366-token prompt filled almost all of the 16,384
available tokens and ended with `finish reason: [length]`. The official
extension version 0.0.9 reads the model's `num_ctx` and reserves 4,096 tokens
for output when advertising its input limit. After changing providers, run
`Ollama: Refresh Models` and start a new Chat session; do not retry the full
`/create-agent` conversation. Select `AI-Coach Local` to use the existing agent.

On this machine, the model defaults were reduced from 65,536 to 16,384 context
tokens, with 2,048 output tokens and temperature 0.2. The helper independently
sets its own 16K context, output limit, low reasoning and temperature 0. A client
can override model defaults. No editor restart is required for the source edits;
an existing Chat session may retain its previous model metadata or context.
These limits reduce demand; a 20B model still consumes substantial memory.

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
# Import measured Codex turns once (no API calls):
.venv/bin/python scripts/codex_usage.py
# Or keep counters current, including the final response; Ctrl-C stops it:
.venv/bin/python scripts/codex_usage.py --watch
# The assistant records its planning/review stages, reusing --id for updates:
.venv/bin/python scripts/workload.py record --agent codex \
  --title 'Review interval helper' --status running --id interval-review
```

All report data stays under ignored `.local/worker/`. Automatic tracking covers
requests made through this helper after tracking was added, including failures
and incomplete drafts. Direct `ollama` calls are not included. Codex stages are
manually recorded by the assistant. `codex_usage.py` reads local Codex session logs
under `${CODEX_HOME:-~/.codex}/sessions`, selecting only sessions whose working
directory is this repository or a subdirectory. It imports available history and
new sessions into a separate per-turn table. Only IDs, timestamps, model names,
status and measured counters are retained in `.local/worker/codex_usage.json`;
conversation bodies are neither stored there nor sent to the local model.

The importer uses explicit `token_usage_record.turn_token_usage` snapshots and
replaces each turn's counters on refresh, preventing duplicate counting. Older
logs without these records show unknown usage. This is a local Codex log format,
not a stable billing API; verify the importer after a Codex update. A row represents
a Codex turn, including its tool calls; automatic continuations can be separate
turns. Running rows are partial. Input includes cached input; output includes
reasoning where reported. Repeated context is counted on each model call. These
counts do not measure subscription allowance, money spent, or token savings.
The watcher polls every five seconds and discovers new project sessions. It must
remain running; it does not start automatically after a reboot. HTML auto-reload
alone does not update counters. Check the report rebuild timestamp for freshness.

Task counts are not equivalent effort or token savings. A completed local draft still requires
review. An interrupted process may remain marked running if forcibly terminated.
Earlier model comparisons and the original workload log are retained in
`.local/worker/archive/` as historical evidence; they are not active configuration.

## Checks

```sh
.venv/bin/python -m pytest -q
(cd adapters/mcp && .venv/bin/python -m pytest -q)
npm --prefix dashboard run check
git diff --check
```

Backend and gateway have separate virtual environments/lockfiles. Python 3.12+
is supported; containers use 3.14.7 for the backend and 3.12 for the gateway.
Node 24 is used for the frontend. Frontend assets build into ignored
`adapters/mcp/static/dashboard/` and must exist before gateway deployment.

## Routine release command

Use `scripts/release.py --check` for local validation. The reviewed
`--release --public-repo limux92/ai-coach --message "Change title"` command adds
GitHub branch/PR sync, exact-head CI and code-only deployments of both the
private backend and dashboard/MCP gateway, with candidate checks and traffic rollback. Run it with `.venv/bin/python` in the
visible VS Code terminal. Review and stage the public source first.

See [deployment instructions](docs/DEPLOYMENT.md) and the
[copyable local-agent prompt](docs/LOCAL_DEPLOY_PROMPT.md). The current read-only
AI-Coach Local chat and `local_worker.py` cannot execute commands; use a
terminal-capable agent or paste the command. Release receipts stay private in
`.local/releases/`. Infrastructure provisioning/configuration changes still need
separate review. Private backend probes call only the static `/health` route.

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
