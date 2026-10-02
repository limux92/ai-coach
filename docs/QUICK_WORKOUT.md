# Quick Workout — retired

Magne retired the one-click recommendation feature on 29 September 2026 in favor
of conversational planning. The local source removes Quick Ride/Quick Run buttons,
both `/dashboard/api/quick-workout` POST routes, the OpenAI provider, workout schemas,
FIT/ZWO encoders and the two MCP rendering tools. The ten data/physiology tools
remain read-only. A local build does not remove the feature from production; that
requires a separately authorized release and verification of served assets.

Use [AI coach instructions](AI_COACH_SYSTEM_PROMPT.md),
[coaching setup and acceptance](COACH_CHAT_INSTRUCTIONS.md) and the
[story contract](stories/STORY_CONVERSATIONAL_COACH.md) for the replacement workflow.
Plans are discussed in chat; the connection cannot save or export them.

No new OpenAI runtime configuration is required. This code change does not remove
existing environment variables, Secret Manager secrets or IAM grants. Any cleanup
of those resources is a separate reviewed operation. Do not run the historical
activation story to restore a feature the user has retired.
