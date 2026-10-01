# AI Coach — release handoff

Prepared 1 October 2026. This checkout is a local release candidate on
`release/conversational-coach`, based on previously released `3462024`.
Read `AGENTS.md` and `docs/stories/STORY_CONVERSATIONAL_COACH.md`.

## Current product decision

Magne retired Quick Workout on 29 September in favor of collaborative coaching.
This candidate removes dashboard recommendations, both recommendation POST routes,
paid provider calls and MCP FIT/ZWO exporters. The ten read-only data/physiology
tools remain available. Use `docs/AI_COACH_SYSTEM_PROMPT.md` for coaching behavior
and `docs/COACH_CHAT_INSTRUCTIONS.md` for setup and acceptance examples. These are
instructions, not trained model weights or changes to a ChatGPT workspace.

Existing deterministic physiology context supplies current CP/W-prime and CS/D-prime
models, historical model provenance, 7/28-day workload, bounded session/event lists,
durability and missing-evidence limits. Do not claim live athlete models validated
from synthetic tests. Running balance remains deferred.

## Verification and publication

Local check and deployment phases are separate: run `scripts/release_check.py`,
then validate the exact receipt with `scripts/release_deploy.py --validate-only`.
A changed payload needs a new receipt. Only an authorized `--release` plus a
successful deployment receipt and served-asset verification establishes production
removal. The existing Scheduler diff against main requires the reviewed
`--physiology-scheduler-migration` flag; its settings are preserved when already
correct. Never weaken a failed gate or rerun the historical activation story.

Public destination is `limux92/ai-coach`; routine services are `ai-coach-sync` and
`ai-coach-chat` in `magne-ai-coach-20260915`, `europe-north1`. Preserve IAM, OAuth
issuer/PKCE protection and runtime settings. Unused OpenAI secrets/configuration
are outside this code change; cleanup requires separate review. No publication
or deployment is authorized by this handoff. Keep the PR open.

## Workspace preservation

The primary workspace has separate staged/unstaged work and unsaved editor buffers.
Do not save all buffers, reset, stash, clean or auto-stage it. The private candidate
manifest identifies copied/deleted files in this isolated checkout. Unrelated
worker/collaborator changes and private transcripts are excluded.

Magne separately requested Qwen 3.8 and task splitting on each prompt. That local
workflow setup is maintained in the primary workspace, outside this product release.
Historical worker jobs stay dormant. Never send secrets, training records or raw
logs/transcripts to worker prompts or public source.
