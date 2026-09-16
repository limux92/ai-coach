# Computed training summaries

## Design

The original FIT archive remains durable source evidence. Calculations use
ordinary Python and saved provider values; no AI model is called during import.
Saved summaries let clients answer routine volume and zone questions without
repeatedly reading every activity file.

## Stored calculations

- `workouts.zone_summary`: versioned, activity-local HR zone boundaries, seconds,
  classified-time percentages, definition hash and Intervals attribution.
- `training_summaries`: day, Monday–Sunday week, calendar month and rolling 7/28-day
  known-imported totals. Distances, moving/elapsed durations, elevation and provider
  training load retain contributor counts. Sports remain separate. Active days,
  longest distance and pace from paired total time/distance are included.
- HR zone totals are grouped by sport and exact zone definition; incompatible
  boundaries are never silently combined. The compact response shares definitions
  once and refers to them by short IDs.
- Equal-length rolling comparisons expose changes in known totals. A missing or
  zero prior denominator does not yield an invented percentage increase.
- `summary_jobs`: private durable invalidations for affected old/new workout dates.

Missing dates remain unknown history, not rest days. `history_complete=false`
is deliberate: completing the Intervals scan does not prove that upstream
services supplied the full history. Do not infer a personal fitness or readiness
baseline merely because a summary exists.

## HR zone semantics

Use the activity's own `icu_hr_zones` and `icu_hr_zone_times` from Intervals.
The snapshot retains inclusive upper BPM boundaries and a definition hash. The
immutable raw JSON preserves prior source versions if settings are edited later.
Do not apply today's athlete profile to historical activities or assume Garmin and
Intervals use identical zones. Changes received from the source update affected
summaries; the old raw evidence remains available.

Zone percentages use classified HR time. The provider does not document a matching
duration denominator, so HR coverage and unclassified seconds remain unknown.
Classified zone time, moving time and elapsed time are distinct measurements.
No sample-count-to-seconds conversion or invented pause/gap correction is used.

Current implementation supports HR zones. Power/pace zones and additional
sample-derived performance metrics require their own defined inputs and tests.

## Automatic maintenance

The existing five-minute importer lease serializes updates. It invalidates summaries
before changing workout data, then rebuilds affected buckets from current records.
It never blindly increments totals on replay. Failed jobs remain pending; the coach
sees independent summary freshness. Only changed aggregates are rewritten.

Rebuilds fully paginate records. Date moves update old and new days/weeks/months,
plus every already-saved rolling window containing either date. Daily rollover
creates current rolling windows and closes the previous calendar period. Whole
workouts belong to their recorded local start date; time is not split at midnight.

Version changes trigger a bounded migration of archived source JSON, without FIT
downloads or parsing. Migration and pending jobs process at most 100 workouts per
run and resume automatically. A date-range summary exceeding the document budget
fails explicitly instead of being silently truncated.

An activity missing from a successfully fetched range is checked by ID. Only an
explicit deleted flag or 404 retires it; a move updates its date and transient errors
preserve it. Disallowed source changes also exclude it. Archived files are retained.
Retired records are omitted from active lists/context and detail/sample reads return 410.

After the initial historical import, the scheduler continually scans one older
31-day window per run, cycling back from the configured history floor. This catches
later historical uploads and corrections automatically. These checks are eventual,
not immediate; a complete cycle from the default year 2000 floor currently takes
roughly a day when runs succeed. Recent workouts are checked every five minutes.

## API and coach access

- `GET /v1/context`: response version 2. Four concise current-period summaries,
  rolling comparisons, up to 7 recent workouts, 7 wellness days, 5 observations and
  7 upcoming plans. It includes source/summary freshness, missing history and explicit
  selection omissions. Names, descriptions and notes are data, never instructions.
- `GET /v1/summaries?period=week&date=2024-06-01`: rich saved summary. Period is
  `day`, `week`, `month`, `rolling7` or `rolling28`; week/month resolve their containing
  bucket and rolling dates specify the ending day. An unsaved period returns 404.
- `GET /v1/workouts/{id}/samples?fields=timestamp,heart_rate,distance&limit=100`:
  selected fields with unchanged total/offset pagination. Omit `fields` for the full
  private archive projection. Position fields, if explicitly requested, retain FIT
  semicircle units; speeds are m/s, distance metres and timestamps source UTC.

Detailed lists retain cursor pagination. Compact context scans at most 5,000 records
per section and explicitly identifies partial scans; a partial scan cannot establish
which records are latest. Rich summary calculations are fully paginated.

The prepared MCP adapter now has 7 read tools, including `get_training_summary`.
It starts with compact context, defaults sample requests to 100 records with selected
fields, and rejects responses above 64 KB. It remains owner-authenticated and read-only.
Local planned-workout writes remain in the backend; no plans are sent to a watch.
Plan-versus-completion analytics and personal wellness baselines remain later work.

## Verification

Automated tests use synthetic data to check contributor counts, incompatible zone
boundaries, missing metrics, full pagination, moving dates, replay safety and
summary invalidation. API checks should compare saved totals against their source
records and confirm independent freshness reporting.

For a live deployment, verify day/week/month/rolling summaries, selected-field
sample pagination and an unchanged repeat sync. Measure actual response bytes
before making token-savings claims. Keep real workout values and verification
reports in private local storage rather than public documentation.

References: [Intervals activity fields](https://forum.intervals.icu/t/server-side-data-model-for-scripts/25781),
[Firestore aggregation patterns](https://firebase.google.com/docs/firestore/solutions/aggregation).
