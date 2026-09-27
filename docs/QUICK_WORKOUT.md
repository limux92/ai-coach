# Quick Workout

The owner dashboard uses OpenAI for two separate recommendations for today in the
athlete's timezone:

- **Quick Ride · Zwift**: a cycling session and `.zwo` file using Zwift FTP.
- **Quick Run · Garmin**: a running session and `.fit` workout. Warm-up and cool-down
  end only on LAP press; run and recovery intervals are timed.

Both show the reason and uncertainty. A rest recommendation produces no file.
Running never reuses the cycling recommendation. Caches are separated by sport;
the in-flight lock, 60-second interval and daily quota are shared across both.
Neither action changes imported training or planned workouts.

## Contract and data flow

1. Browser sends an empty authenticated POST to `/dashboard/api/quick-workout`
   for cycling or `/dashboard/api/quick-workout/run` for running. Existing Firebase
   owner verification applies to both. No arbitrary prompts, model names, dates,
   query parameters or URLs are accepted.
2. Gateway uses its existing identity to read `/v1/context?days=42&upcoming=7`.
   The bounded context includes summaries, recent workouts, wellness, provider
   load estimates when available, upcoming plans, attribution and freshness.
   It contains no full activity files. Running instructions assess running history
   separately; cycling fitness is not treated as running tolerance.
3. Missing/stale freshness, partial scans or a mismatched local day stop generation.
   Missing history is unknown, never zero training or proof of rest.
4. The fixed OpenAI Responses endpoint receives the sport-specific prompt and
   [strict structured-output schema](https://developers.openai.com/api/docs/guides/structured-outputs),
   no tools, `store=false`, at most 3,000 output tokens, 24 KB context and 64 KB response.
   HTTP timeout is 45 seconds; the service adds a 50-second model timeout.
5. Server-side validators check the response before deterministic file encoding.
   The frontend rejects a response for the wrong sport. Product limits validate
   structure and size; they are not a medical safety guarantee.

### Cycling

`quick_workout_schema.py` defines `QuickWorkout`: warmup, steady blocks and cooldown,
30–1800 seconds each, 30–120% of Zwift FTP. The dashboard permits 10–60 minutes;
explicit chat-authored cycling prescriptions permit up to 90 minutes. Powers are
fractions, so `0.65` is 65% FTP. No FTP in watts is invented.

### Running

`running_workout_schema.py` defines `RunningWorkout`, with `sport: running`,
`day`, `decision`, `title`, `rationale`, `caveats` and `steps`. Every step has
`kind`, `duration_type`, `duration_s` and `effort`:

| Step | Duration | Effort |
| --- | --- | --- |
| First warmup | `lap_press`, seconds `null` | easy |
| Main run intervals | `time`, 30–1800 seconds | easy, steady or hard |
| Main recovery intervals | `time`, 30–1800 seconds | easy |
| Last cooldown | `lap_press`, seconds `null` | easy |

The main set totals 5–40 minutes, with at most 50 steps including warmup/cooldown.
Each interval is explicit. The returned `duration_s` counts only timed steps;
`open_duration: true` means there is no fixed total session duration. The dashboard
labels this as the timed main set plus warm-up/cool-down until LAP press.
No running pace, heart-rate zones or cycling power targets are invented; effort
instructions appear in the FIT step name/notes. The model can recommend easy
run/walk or rest when running history is insufficient.

## Garmin file behavior

The FIT workout has sport **running**. Warmup/cooldown use FIT duration **open**,
which Garmin's [SDK example](https://github.com/garmin/fit-java-sdk/blob/main/src/main/java/com/garmin/fit/examples/EncodeWorkout.java)
uses for steps ending on a lap-button press. Both steps have open targets and easy
effort instructions. Main run/recovery steps retain their exact timed order.

This is a structured workout, not an activity or a route. It does not upload to
Garmin Connect. Transfer it using the workflow supported by the target device;
workout capabilities and step limits vary. Physical device import remains a
separate acceptance test.

The small encoder follows [Garmin's workout profile](https://developer.garmin.com/fit/articles/file-types/workout.html)
and adds no runtime package. Tests independently decode sport, duration modes,
step order, targets and CRCs with `fitdecode`. Synthetic fixtures contain no
personal activities. File identities distinguish different prescriptions.

## Chat-authored exports

After reading `get_coach_context`, a chat can fill the corresponding schema:

- `render_quick_workout(plan)` returns cycling `filename` and `zwo`.
- `render_running_workout(plan)` returns `garmin_filename` and base64 `fit_base64`.

Each response includes `sport`, `plan`, `duration_s` and `open_duration`. The other
sport's file fields are null. All four file fields are null for rest. These pure
MCP tools do not call another model, save a plan or upload a file. Refresh the
connector tool list after deployment. Validation checks format, not suitability.

## Runtime setup

OpenAI is the selected provider. The gateway needs `OPENAI_API_KEY` and
`QUICK_WORKOUT_MODEL` server-side. Choose a model supporting Responses structured
output. Keep the key in Secret Manager, bound to the gateway runtime with access
to that secret only; the model name is ordinary configuration. Never put the key
in frontend code, public config, source control, prompts or real-valued fixtures.
Without both settings, the authenticated endpoint returns 503.

Antigravity development authentication and a ChatGPT subscription do not configure
this API. Google Cloud spending caps do not cover OpenAI API spending. One active
request, a 60-second interval, ten attempts per UTC day and a ten-minute cache
reduce repeated calls. These limits are per process and reset on restart; they
are not a global cost cap. There is no automatic paid retry or provider fallback.
`store=false` is not a promise of zero provider retention.

The last read-only deployment diagnostic found both recommendation settings
absent. Provider selection is resolved; secret/model configuration and exact
public-payload approval remain pending. Do not use `infra/deploy_chat.py` to
activate this feature: that older bootstrap path rebuilds environment/secrets.
Use separately reviewed minimal configuration updates, followed by the routine
release workflow that preserves runtime settings and IAM.

## Verification and agent roles

Focused checks cover schema cross-field rules, sport/cache separation, shared
quotas, owner access, refusal/provider failures, rest, FIT decoding and browser
binary downloads. Dashboard checks include formatting, build and size budgets.
A paid model call, live owner download and physical Garmin import remain unverified.

GPT-oss drafted the running schema in the visible VS Code terminal. Codex reviewed
and integrated it, updated provider/API/FIT/frontend behavior and ran final tests.
Gemini's infrastructure diagnostic produced a valid receipt even though its CLI
subsequently timed out; its fresh health-migration review completed. Neither
worker output nor a passed local check proves cloud deployment.
