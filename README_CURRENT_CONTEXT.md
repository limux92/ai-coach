# AI Coach — fresh-chat handoff

Updated 27 September 2026. Read this with `AGENTS.md`. Work in **AI-Coach**, not
JustInterval. This records decisions and saved evidence, not a transcript or new
deployment permission. Replace stale state after verified outcomes.

## Resume here

1. Read this file, `.local/worker/README_HANDOFF.md` and `.local/worker/context.md`.
2. Inspect `git status --short`, branch/HEAD, and
   `.venv/bin/python scripts/harness.py status`.
3. Preserve all staged, unstaged and untracked changes. Load only relevant source
   and evidence; never send this whole handoff or private logs to workers.
4. Summarize the state in at most five bullets, then continue the requested task.
   Recheck live state before a new deployment or current-health claim.

Saved on branch `release/lightweight-dark-dashboard`, HEAD `44afae7`, with
substantial uncommitted source. Closing the chat preserves local files; a fresh
clone will lack unpublished code and ignored evidence. Resume in this workspace.
No commit, push or deployment is part of saving this handoff.

## Current task: traceable physiological modelling

The physiology epic is implemented and verified locally. Read
[architecture and acceptance ledger](docs/PHYSIOLOGY.md) and
`.local/worker/physiology-verification.md` before changing it. Root checks passed
541 tests plus 41 subtests; adapter checks passed 272 tests on 27 September.
No cloud deployment, live athlete model validation or hosted AI evaluation was
performed. Existing Garmin/OpenAI activation and health migration remain pending.

The pipeline now retains immutable acquired-evidence revisions, recent independent
CP/W-prime and CS/D-prime fits, explicitly historical/retrospective analyses,
cycling balance, sport-specific workload and conservative durability evidence.
Context includes coverage, provenance, 7/28-day details, events and curated sources.
Private read APIs/MCP tools and a bounded Quick Workout projection are integrated.
Normalization gaps stay unknown; running balance remains deferred.

GPT-oss ran visible regression/test drafts; both were rejected after the allowed
correction attempt. Codex implemented and tested the final code. Gemini's corrected
polling/Scheduler review was accepted and its changes checked offline. Job IDs and
evidence are in the private verification note. No relevant jobs remain running;
do not resume the older deferred wording review automatically.

Before publication/release, prepare the exact combined source payload, preserve
the OAuth repair and existing user edits, and obtain the existing final release
authorization. A deployment must apply/review Scheduler configuration separately
from an image-only routine release, then prove actual authenticated behavior.

## Completed versus pending

| Work | Last verified state on 25 September |
| --- | --- |
| ChatGPT database connection | Repaired and deployed; actual hosted context, workout, lap and paginated sample reads passed |
| Replacement connection | **Magne Training Database — Google** installed; obsolete Auth0 connection uninstalled with user approval |
| OAuth hotfix | Gateway `ai-coach-chat-oauth-iss-260925` serving 100%; backend, runtime configuration and IAM preserved |
| Garmin running and Zwift buttons | Implemented/tested locally; feature release, live OpenAI call and owner download pending |
| Agent integration | Bounded GPT-oss/Gemini jobs, shared context snapshots and evidence checks implemented locally |
| Full backend/gateway release | Earlier attempt failed health preflight; `/health` migration prepared locally, not deployed |

These are saved verification results, not a cloud check made while writing this
README. The successful OAuth-only hotfix is separate from the failed full release.
Live MCP has seven read-only tools; new workout-rendering tools remain local.

## Retain the ChatGPT OAuth repair

The old connection cached retired Auth0 issuer/client settings; reconnect and
refresh did not migrate them. Google-backed replacement registration then failed
because the gateway allowed ChatGPT's stable callback without declaring RFC 9207
issuer identification.

`adapters/mcp/src/ai_coach_mcp/oauth.py` now advertises
`authorization_response_iss_parameter_supported: true`, preserves the exact issuer
in discovery and includes `iss` in SDK authorization error redirects. Successful
consent already included it. Callback allowlists, PKCE and `coach:read` remain
strict. Never restore Auth0 or allow arbitrary callbacks. Regression tests are in
`adapters/mcp/tests/test_oauth.py`.

The approved hotfix reused the exact serving image and replaced only `oauth.py`.
Zero-traffic checks passed before promotion. Codex deployed it and verified real
hosted reads. **Any full gateway build must include this local OAuth fix.**

The combined result is `.local/verification/mcp-repair/hosted-verification.json`.
Its linked deployment and standalone OAuth test receipts retain false hosted-read
flags because those earlier steps did not perform the later ChatGPT reads. Do not
rewrite historical receipts or mistake those flags for the final outcome.
Separate live-client token rotation/replay revocation passed and test grants were
revoked. ChatGPT's own refresh after access-token expiry was not observed.

See [chat connection](docs/CHAT_CONNECTION.md) and
[Firebase authentication](docs/FIREBASE_AUTH.md). Private identifiers, verification
chat, rollback revision and evidence paths are in `.local/worker/README_HANDOFF.md`.

## Next unfinished task: OpenAI-powered Garmin running download

Magne selected **OpenAI** and a **downloadable workout file**, not Garmin Connect
upload. Buttons: **Quick Ride · Zwift** (`.zwo`) and **Quick Run · Garmin** (`.fit`).

- Running warm-up/cool-down end on **LAP press**: `duration_type=lap_press`,
  `duration_s=null`, easy effort. FIT encodes these as open-duration steps.
- Run/recovery intervals are timed, 30–1800 seconds each. Main set is 5–40 minutes,
  maximum 50 steps including bookends. Display the timed set plus open bookends.
- FIT sport is running; preserve explicit interval order. Use effort cues, never
  invented pace/HR zones or cycling FTP. Running has its own schema/prompt/cache.
- Both sports share a request lock, 60-second interval and ten attempts per UTC
  day per process. A rest recommendation has no file.
- Owner-authenticated empty POST routes: `/dashboard/api/quick-workout` and
  `/dashboard/api/quick-workout/run`. Pure MCP render tools export supplied plans
  without another model call or writing training records.

Read [Quick Workout](docs/QUICK_WORKOUT.md) for the full contract and constraints.

| Concern | Source |
| --- | --- |
| OpenAI, validation, caching | `adapters/mcp/src/ai_coach_mcp/quick_workout_{provider,schema,service}.py` |
| Running prescription and FIT | Same directory: `running_workout_schema.py`, `garmin_workout.py` |
| Owner routes and MCP exports | Same directory: `dashboard.py`, `app.py` |
| Download UI | `dashboard/src/quick-workout.js`; related files in `dashboard/README.md` |
| Tests | `adapters/mcp/tests/test_{running_workout,garmin_workout,quick_workout_flow}.py`, `dashboard/test/quick-workout.test.js` |

Remaining steps:

1. Recheck gateway settings. Last diagnostic found no `OPENAI_API_KEY` or
   `QUICK_WORKOUT_MODEL`. Confirm an existing Secret Manager secret **name** and
   model name, or prepare setup if absent; never request a key value in chat.
   No OpenAI-named secret was found, but another name may exist.
2. Prepare minimal runtime configuration and the exact public source payload.
   `.local/worker/garmin-public-files.json` is stale after OAuth/docs changes;
   regenerate and review before final publication/release approval.
3. Follow `docs/LOCAL_DEPLOY_PROMPT.md` and `docs/DEPLOYMENT.md`. `/healthz` was
   intercepted by Cloud Run's frontend. The local `/health` migration permits
   only a verified exact-old-revision missing-route preflight. Candidate and
   production checks remain strict. Never relax IAM or bypass a failed gate.
4. After an authorized release, verify a real owner OpenAI recommendation, FIT
   download and cycling behavior; refresh MCP tools if needed. Physical Garmin
   import is still a separate device acceptance test.

Avoid `infra/deploy_chat.py` for feature activation: its older bootstrap flow
rebuilds environment/secrets. Routine releases preserve settings; configuration
changes must be prepared and reviewed separately.

## Agents and continuity

Codex keeps the conversation, design, security, integration and final checks.
GPT-oss drafts bounded application code/tests; Gemini drafts/reviews infrastructure.
Run workers in VS Code's visible terminal and leave it open. Use fresh small
packets, not accumulated chat history. Do not silently switch models or buy credits.

`scripts/harness.py` manages jobs; `harness_context.py` freezes the curated
`.local/worker/context.md` (maximum **4000 UTF-8 bytes**) into new briefs. Total
input remains capped at 12 KB. Codex alone updates the memo after reviewing worker
results. Existing job snapshots remain immutable; a memo grants no execution access.

`gemini_worker.py`, `gemini_jobs.py` and `gemini_gate.py` provide fresh Antigravity
review packets with exact-source access checks. The harness does not execute cloud
commands. Direct scoped Gemini execution is separate and must return command and
release evidence. A prior cloud diagnostic succeeded but its CLI timed out; that
was not a deployment. The original conversation crash remains unexplained; the
user ruled out spending caps and a fresh conversation worked.

GPT-oss supplied running-schema and dashboard drafts. Its FIT encoder draft was
rejected; Codex implemented the encoder and integration. Gemini completed readiness
and health reviews. No jobs were running at handoff; one wording review is deferred.
Do not automatically resume deferred jobs. Codex completed the auth repair.

See [HARNESS](docs/HARNESS.md), [research](docs/AGENT_INTEGRATION_RESEARCH.md) and
[local AI runbook](README_LOCAL_AI.md). `harness_release.py` requires a successful
current routine release receipt; worker SUCCESS or a Git push proves no deployment.

## Checks and boundaries

Last recorded checks: **499 root tests + 41 subtests**, **265 adapter tests** after
the OAuth fix, and **25 dashboard tests** plus format/build/gzip checks. Synthetic
browser FIT download matched encoder bytes; independent `fitdecode` verified CRC,
running sport, open bookends and timed interval order. These are prior implementation
results, not suites rerun to write this handoff. Paid generation, live owner FIT
download and physical watch import remain unverified.

Run appropriate checks after source changes:

```sh
.venv/bin/python -m pytest -q
(cd adapters/mcp && .venv/bin/python -m pytest -q)
npm --prefix dashboard run check
git diff --check
```

Backend remains private behind Cloud Run IAM. Firebase authenticates the owner;
MCP uses separate gateway OAuth tokens. Never use Firebase ID tokens as MCP access
tokens. OAuth state is isolated from training records. Public source excludes
credentials, activity files and operational artifacts. The curated worker memo is
the sole automatic exception to excluding `.local/` from worker prompts.

The user approved the now-completed OAuth-only gateway deployment and obsolete
connection removal. Do not ask again for those actions or extend that permission
to a full feature/backend release, IAM changes or source publication. Prepare any
new action concretely before seeking final approval.

## Paste into a new chat

```text
Continue AI-Coach in this workspace. Read AGENTS.md and README_CURRENT_CONTEXT.md,
then the local handoff and worker memo they reference. Inspect git status and
harness status; preserve all staged, unstaged and untracked work.
The ChatGPT Google connection repair is complete. The next unfinished task is
OpenAI-powered Quick Run Garmin download with LAP-press warm-up/cool-down.
Summarize the state in at most five bullets, then continue preparing that feature
for release. Respect recorded approvals; do not repeat completed repair work.
Prepare any new publication/deployment scope for final approval.
```
