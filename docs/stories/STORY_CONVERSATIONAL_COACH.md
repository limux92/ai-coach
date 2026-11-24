# Story: conversational coaching and Quick Workout retirement

Status: released and public assets verified, 1 October 2026.
Design: Magne's 29 September conversation with Gemini. Implementation repair and
verification: Codex. No local worker or Gemini review ran during this repair.

## Outcome and scope

Magne wants to discuss a holistic plan with the coach using critical power,
workload and durability evidence. Remove the dashboard Quick Ride/Quick Run flow,
paid recommendation calls and both MCP workout exporters. Keep owner-authenticated
training reads, OAuth and existing deterministic physiology calculations intact.
Provide reusable coaching instructions and use saved summaries before sample reads.

The primary workspace starts at `44afae7` with mixed existing changes. The last
recorded production source is `3462024`; deployment is a separate approval step.
Do not alter the primary Git index. Prepare an isolated reviewed release payload.
Keep cloud configuration and secrets unchanged by this code retirement.

## Acceptance criteria

1. Dashboard build and rendered overview/calendar contain no Quick Workout controls.
2. Authenticated POSTs to both former recommendation routes return 404 without
   contacting the private backend or a model. Anonymous API requests still require
   authentication. MCP discovery lists exactly ten data tools, all read-only.
3. No executable feature imports, providers, schemas, exporters or projection remain.
4. `AI_COACH_SYSTEM_PROMPT.md` is usable instructions, with conversational planning,
   current versus historical evidence, status/provenance, CP/W′ and CS/D′ units,
   independent sport loads and conservative durability interpretation.
5. Existing saved physiology context retains current models, 7/28-day workload,
   coverage/missing counts, bounded events/sessions and evidence drill-down. No
   athlete-specific model or live coaching quality is declared validated by tests.
6. Root and adapter tests and dashboard format/tests/build/budgets pass on the exact
   release candidate. Static source and assets pass secret scans. Preserve OAuth,
   IAM and the private-backend boundary.
7. Only a successful new deployment receipt and a check of served assets establish
   that the live dashboard no longer offers Quick Workout.

## Findings when resuming

The pasted Gemini report claimed removal and 801/809 passing tests. The actual
workspace had deleted feature modules but retained imports/routes/UI bindings;
the new system prompt was zero bytes. Treat those claims as unverified. Existing
physiology summaries and pagination already implement the data flow above; this
story does not introduce a new physiological model or a universal payload-size claim.

## Validation and remaining acceptance

Run root pytest, adapter pytest, and `npm --prefix dashboard run check`. Inspect the
sealed release receipt and validate it separately before requesting publication.
The deployment must preserve existing Cloud Run configuration and IAM. Installing
the prompt in the private coaching workspace and evaluating real conversations are
separate user acceptance steps; repository edits do not change ChatGPT settings.

Prompt structure follows [OpenAI's prompt engineering guidance](https://developers.openai.com/api/docs/guides/prompt-engineering).
Scientific constraints and primary-source cards are recorded in
[PHYSIOLOGY.md](../PHYSIOLOGY.md); exact protocol behavior comes from the backend.

## Release evidence

Commit `c35e6cd3c51e4a79e3758516562a6cc46855b81e`, PR 3 open. Exact checked tree
`408c37915edbb10a7bf3b191ffe7d9698c4d08fb` passed all four remote CI jobs. Both
Cloud Run services serve the verified new revisions; IAM/runtime configuration
were preserved and the reviewed Scheduler settings verified. Deployment receipt:
`.local/release-conversational-coach/source/.local/deployments/261001-211327-585a/summary.json`.
All four public dashboard assets match the sealed hashes with no Quick Workout
markers; evidence is `.local/verification/20261001-conversational-coach/production-after.json`.
Browser UI automation was unavailable, so no owner session or training-data reads
were exercised. Private coaching-instruction installation and conversation quality
remain user acceptance steps. Qwen reviewed release instructions; Codex executed
after Magne approved using Codex's terminal for this attempt.
