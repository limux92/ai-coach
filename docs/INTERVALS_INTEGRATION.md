# Intervals.icu import component

## Interface

`IntervalsClient(api_key, athlete_id="0")` is a synchronous context manager using
HTTPX. Default request timeout is 30 seconds, connection timeout 10 seconds, and
maximum attempts 3. Only GET requests exist. Redirects are rejected to keep Basic
credentials bound to the configured origin. The key is never included in an
exception. Do not log HTTP request headers or the client internals.

Optional `deadline_monotonic` bounds importer execution. Requests, received chunks
and retry waits check this monotonic deadline. Per-operation network timeouts are
capped by the remaining budget; exhausted budgets raise retryable
`deadline_exceeded`. Keep the caller's lease longer than its request deadline.

Methods:

- `athlete()` returns the authenticated profile. Retain its actual ID as provenance.
- `list_activities(oldest, newest)` returns all source summaries, unfiltered.
- `get_activity(activity_id)` includes Intervals analysis intervals in the JSON.
- `download_original(activity_id)` returns `OriginalFile` with exact entity bytes,
  content type/encoding, sanitized filename metadata, SHA256 and `is_gzip`.
- `list_wellness(oldest, newest)` returns daily payloads.
- `list_events(oldest, newest)` returns calendar events of all categories.
- `get_event(event_id)` retrieves one event for reconciliation.

`IntervalsError` exposes `code`, `status_code`, `retryable`, and
`retry_after_seconds`. Network, 408, 429 and 5xx failures are retryable. A
Retry-After above the 30-second local wait cap is returned immediately so a durable
job can defer appropriately. Authentication and ordinary 4xx failures are not
retried. No body or request details are copied into errors.

## Persistence contract

1. Accept automatic records with `is_direct_garmin`; reject `strava_only`, STRAVA
   and unknown sources before archival. Do not reject a direct Garmin record merely
   because `strava_id` is also present. `UPLOAD` + FIT + a Garmin or Zwift device label is
   only a manual-upload candidate. Its original is archived with pending state;
   every chained FIT file must pass CRC checks and have native file_id fields for
   the expected manufacturer and type activity before a workout is published. Native
   fields must be distinguished from developer fields and accessory device_info.
   The document retains provider_source UPLOAD and manual_upload attribution,
   verification metadata and the verified original SHA256.
2. Recheck source on the activity detail. Preserve complete JSON with
   `serialize_payload`; use its SHA256 for immutable paths and idempotent writes.
3. Store exact original entity bytes. Some responses are gzip compressed. Keep
   encoding metadata and decompress only when parsing, with decompression limits.
   This endpoint returns the file received by Intervals, not a guaranteed identical
   copy of a manually exported Garmin FIT. Do not use `/fit-file` for raw archives.
4. Normalize with `normalize_activity`, `normalize_wellness`, or
   `normalize_planned_workout`. `normalize_calendar_event` handles every category.
   Only WORKOUT events enter `planned_workouts`; other calendar categories are
   excluded by the current importer.
5. Completed and planned document IDs are `intervals_{source_id}`. Wellness uses
   local day (`YYYY-MM-DD`) as the document ID in this single-athlete application.
6. Projection fields are replacements, not patches: None clears fields removed
   upstream. Preserve local notes and overrides separately before replacing source
   projections. Particularly do not reset user-modified plan status on every import.
7. A completed workout's `paired_planned_workout_id` links to its plan. A past plan
   date is not evidence of completion. The projection leaves imported status as
   `planned`; join source pairing or apply a local status overlay separately.
8. `structured_workout_json` retains native relative targets and nested steps as
   JSON text, avoiding Firestore nested-array/index issues. Above 256 KiB it is
   omitted and `structure_stored_in_raw_payload` is true: serve from raw JSON then.
9. No UTC time is invented from local dates. Source UTC timestamps must include an
   offset. Wellness provenance says merged Intervals data; not every value can be
   asserted to originate from Garmin.

## Range/reconciliation details

Dates are explicit ISO local dates. Range calls split into up to 31-day windows
with one boundary-day overlap and deduplicate by source ID. No offset pagination
or `limit` is assumed. Source maintainer recommends month/quarter date paging.

Calendar range queries can expand for multiday events and return only enabled
calendars. Do not delete a plan merely because it is absent from a single range
response: it might have moved, its calendar might have been disabled, or its
category might have changed. A robust reconciler can GET known missing event IDs
and track explicit remote-not-found separately from local deletions.

Source API rate limits may change; consult the provider references below. The
importer should maintain a modest call rate, respect retry instructions and
checkpoint backfills. Persist failure state rather than marking a sync
complete when only a subset succeeds. Daily wellness data may update retroactively;
re-fetch a recent window. Periodically reconcile historical edits as well.

## Validation

Install the project dependencies and test extras, then run from the repository root:

```sh
.venv/bin/python -m pytest -q
```

Tests use synthetic data and HTTPX MockTransport. They check source gating,
compressed-byte fidelity, safe bounded retries, range de-duplication, timezone
handling, category separation, nested plan targets and merged wellness provenance.
These are not live account or Garmin file-fidelity tests.

## Primary references

- [API cookbook](https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090)
- [Planned workout downloads and native structure](https://forum.intervals.icu/t/downloading-planned-workouts-from-the-api/93737)
- [API access, limits and calendar semantics](https://forum.intervals.icu/t/api-access-to-intervals-icu/609)
- [Maintainer on date-range paging](https://forum.intervals.icu/t/api-access-to-intervals-icu/609?page=28)
- [Source enum and field units](https://forum.intervals.icu/t/server-side-data-model-for-scripts/25781)
- [Source field versus Strava metadata ID](https://forum.intervals.icu/t/icusync-claude-ai-mcp-connector-for-intervals-icu-no-technical-setup-required/126632?page=4)
- [Planned event pairing](https://forum.intervals.icu/t/paired-event-id/40322)
