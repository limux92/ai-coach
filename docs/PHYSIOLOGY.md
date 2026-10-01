# Traceable physiological modelling

Updated 27 September 2026. The backend capability is implemented locally with synthetic numerical,
lifecycle, polling and adapter tests. Production behavior, private athlete results and deployment are
**unverified**. Final full-suite checks passed: **541 root tests plus 41 subtests, and 272 adapter tests**.
The root suite includes 40 focused physiology cases. Existing dependency/test-fixture warnings remain.
No frontend code was changed for this epic. Whitespace checks passed.

The backend calculates and selects evidence. AI explains supplied results and cited research; it cannot
upgrade evidence quality or equate a model prediction with observed exhaustion. Cycling CP/W′ and running
CS/D′ remain separate. Running D′ balance is deferred.

## Modules and actual work

All backend modules below are in `src/ai_coach/`.

| Module | Responsibility |
| --- | --- |
| `physiology_samples.py` | Native FIT normalization, continuity/quality flags and time-integrated best efforts. |
| `physiology_regression.py` | Pure work–time OLS, output-unit residual error and parameter standard errors. |
| `physiology_models.py` | Recent-window, chronology, independence, duration-spread and support gates. |
| `physiology_balance.py` | Cycling differential W′ balance and ordered above-threshold events. |
| `physiology_evidence.py` | Immutable revision chain, evidence fingerprints, known-at chronology and publication helpers. |
| `physiology_service.py` | Migration, versioned extraction jobs, bounded analysis and current pointers under the sync lease. |
| `physiology_workload.py`, `physiology_protocols.py` | Mechanical work, custom running index and explicit matched maximal-effort annotations. |
| `physiology_context.py`, `physiology_api.py` | Stored context projections, coverage, evidence reads, pagination and lease-protected internal writes. |
| `upstream_budget.py` | Shared durable request counts and provider cooldowns. |

GPT-oss ran two bounded visible-terminal jobs: the regression kernel draft and numerical-test draft.
Both were rejected after review: the first had a dimensional error and the second contained incorrect tests.
Codex implemented/corrected the production code and tests, integrated the pipeline and performed reviews.
Gemini's corrected infrastructure review was accepted; its reviewed polling/Scheduler changes were
integrated and checked offline. No worker completion constitutes deployment evidence.

## Ingestion, budgets and coverage

The importer retains source restrictions, content-addressed artifacts, duplicate-safe source IDs,
explicit deletion reconciliation and resumable history. Absence from a list alone never proves deletion.
Recent refresh covers 42 days and extends across an outage; historical edits/deletions are reconciled in
rotating windows. A separate lightweight full-history index discovers new old-dated uploads.

The index requests `id,start_date_local` from the configured history floor through today, with `limit=20001`.
At most 20,000 entries are accepted; reaching the sentinel cap fails closed. Only unseen activity IDs trigger
detail/file import. Synthetic tests establish next-successful-poll discovery within this bounded contract.
A five-minute schedule is not proof of a five-minute completion SLA: upstream failures, budget exhaustion,
a busy lease or an oversized index can defer completion. Live provider/Scheduler behavior is unverified.

A Firestore transaction reserves each request attempt, including retries. Defaults are 4,500 requests per
UTC day, 2,300 per rolling 15 minutes, 80 per run and 0.2-second spacing within the client. Remaining-budget
headers and `Retry-After` can impose a durable cooldown. Budgets count requests before execution; a crash
may conservatively consume an allowance without issuing the request. Failed windows do not advance.

Runtime authentication remains the personal API key. `INTERVALS_BUDGET_POOL` defaults to `personal`.
Future OAuth consumers sharing a client must all share the same central pool; this implementation does
not account for unrelated callers that bypass it. The official guide documents different personal/OAuth
limits and a separate IP limit; live headers and current guidance remain authoritative.
[Intervals.icu API access and rate limits](https://forum.intervals.icu/t/api-access-to-intervals-icu/609)

Coverage deliberately separates:

- `history_complete`: completed backfill for the recorded `backfill_scope_start`, matching configured history
  scope. Expanding/changing that scope resets the required backfill. This is not lifetime completeness.
- `recent_window_complete`: successful enumeration/reconciliation across the requested dates, with a
  non-future coverage observation no older than 600 seconds.
- Per-session workload coverage, known/unknown seconds, quality flags and analysis IDs: source enumeration
  can succeed while samples or physiological analysis remain unavailable.
- `totals_scope="known_imported_workouts"`: missing workouts do not establish rest days. Partial known
  totals and missing-contributor counts must accompany interpretation.

## Normalization and effort evidence

Policy `native_step_v1` uses offset-aware UTC timestamps and native FIT fields; developer fields cannot
silently replace native measurements. Non-monotonic or invalid timestamps reject the recording.
Identical duplicate timestamps collapse; conflicting duplicates make the corresponding interval unknown.
Multiple FIT files or native sessions are rejected for physiology rather than joined across boundaries.
Timer events preserve equal-time source order; stops/transitions create unknown intervals.

A record is held constant over `[timestamp_i, timestamp_next)`. Eligible spacing is positive and at most
five seconds by default. No final sample is extrapolated. Missing, negative, non-finite or implausible
values remain unknown; measured zero remains zero. Current quality bounds exclude power above 2,500 W
and speed above 15 m/s, without clipping. These are versioned software guardrails, not physiological limits.
Running uses native `enhanced_speed` when present, falling back to native `speed` when absent.

The service reconciles source elapsed time with a native single-session elapsed time, using the latter
when source duration is absent. Conflicts are flagged; known leading/trailing gaps are inserted explicitly.
Unknown workout extent leaves full-workout totals unavailable even when the recorded span is usable.
Pauses and unknown gaps never imply recovery, and best-effort windows cannot bridge them.

Cycling mean power is `integral(power_watts * dt) / duration_seconds`; running mean speed is
`integral(speed_mps * dt) / duration_seconds`. Sliding integrals include fractional boundary segments and
both start/end boundary candidates. Running remains in `speed_mps`; presentation alone may convert
positive speed with `pace_seconds_per_km = 1000 / speed_mps`.

Efforts retain workout/revision/extraction IDs, normalization version, UTC start/end, duration, mean output,
quality flags, known-at time and maximal-intent status. Revision references resolve exact parsed/original
artifacts. They also retain preceding elapsed time and known cycling work. Explicit protocol annotations
can supply body mass for kJ/kg; absent mass or incomplete preceding work leaves that value null.

## CP/W′ and CS/D′ fitting

`PHYSIOLOGY_LOOKBACK_DAYS` defaults to 42; accepted configuration is 14–90 days. The fitting range is
strictly `120 < duration_seconds < 900`. Default best-effort durations are
`150, 180, 300, 420, 600, 720, 840` seconds. Endpoints, sprints and hour-long efforts are excluded.

`critical_work_time_ols_v1` fits work/distance against duration, not an OLS fit of power against reciprocal time:

```text
cycling: work_joules      = CP_watts * duration_seconds + W_prime_joules
running: distance_metres  = CS_mps   * duration_seconds + D_prime_metres
```

The equivalent prediction is `mean_output = critical_output + capacity / duration`. Invalid/nonphysical
parameters or degenerate duration spread are rejected. Parameter standard errors use residual work/distance
variance with `n-2` degrees of freedom; `rmse_output` is calculated separately in watts or m/s.
Two-point standard errors are null. These are conditional statistical errors, not physiological confidence intervals.

Selection retains up to 12 distinct workout alternatives per duration, bounded to 16 duration groups.
It selects at most three efforts, trying three before two, with one effort per workout. Longest/shortest
must be at least 3; adjacent selected durations must differ by a ratio of at least 1.4. Among feasible
sets it maximizes summed output relative to each duration's strongest candidate. Nested windows from
one workout cannot become independent tests. The bounded selection is a product policy, not universal validation.

| Status | Implemented meaning |
| --- | --- |
| `insufficient_data` | No valid independent/spread-constrained fit; parameters remain null with reasons. |
| `provisional` | Valid fit with at least two independent efforts, but supported requirements fail. |
| `supported` | Three selected efforts marked `verified_maximal`, on three distinct UTC dates, with output RMSE/critical output <= 0.05. |
| `stale` | The latest selected effort ended more than 21 days before the cutoff. Expired lookback evidence is excluded altogether. |

Maximal intent is never inferred from a high value. The internal protocol endpoint accepts up to eight
owner-supplied effort annotations bound to the exact current parsed-artifact hash. Source replacements
cannot inherit their claims for different bytes. A small fit error does not remove measurement error,
selection bias, terrain effects or model mismatch.

## Immutable chronology and bounded processing

`Store.commit_workout_evidence` atomically writes the immutable evidence revision, current workout/head,
and pending job. `known_at` uses Firestore commit time. Content fingerprints exclude processing clocks;
unchanged replay creates no new contribution. Edit/revert transitions retain their predecessor even if
content returns to an earlier hash. Pending parse metadata is not eligible simply because old bytes remain archived.

Canonical legacy evidence is known when this capability first indexes it. It is never backdated to workout
start, provider update time or inferred archive age. Older workouts may therefore have no supported
as-known model. That limitation is retained rather than repaired using future knowledge.

Extraction IDs include evidence revision, pipeline and normalization versions. Bounded upgrade jobs build
new immutable extractions when versions change, preserving old outputs. Migration and extraction batches
are 25; all-date revision scanning is capped at 20,000 and fails explicitly rather than declaring completeness.
The service resolves the latest revision applicable at the cutoff before filtering dates, so moves/deletions
cannot disappear from historical selection. Per-run extraction caching avoids repeated storage reads.

Snapshots include `snapshot_id`, algorithm version, `calculated_at`, cutoff, lookback, selected evidence,
eligible/independent counts, parameters, diagnostics and warnings. Analyses bind target revision, model,
mode and algorithm versions. Curves/events remain in immutable artifacts; compact records retain references.
Replay repairs the mutable target pointer after an interrupted publication without replacing the saved analysis.

For `as_known_before_workout`, both `known_at` and effort end must be **strictly before** target start;
the target's own effort is excluded. A reconstructed analysis may be calculated later. Its basis explicitly
states reconstruction; it does not prove the application supplied that model at the original time.
`retrospective` uses an explicit later cutoff and separate ID. The original analysis pointer is retained.

Refresh recalculates window eligibility without requiring a new workout. Incomplete migration, future-to-cutoff
commit times or pending jobs cannot publish a completed projection. Internal mutations acquire the existing
importer lease before reading/validating their target. Deadline checks, cached reads and lease checks bound
processing; reanalysis has a 240-second deadline. Readers do not mutate snapshots or fit models.
Same-time evidence revisions use a per-workout monotonic sequence as the tie-breaker. Current physiology
state and target pointers publish only inside a transaction that verifies unexpired sync lease ownership;
an expired/replaced worker cannot overwrite its successor's current result. Offline transaction-body
tests verify our decisions; real Firestore conflict retries remain a live acceptance check.

## Cycling W′ balance

Default version: `skiba_2015_differential_exact_v1`. For constant power `P`, critical power `CP`, capacity
`W` in joules, current balance `B` and duration `dt` in seconds:

```text
P > CP: B_next = B - (P - CP) * dt
P = CP: B_next = B
P < CP: B_next = W - (W - B) * exp(-(CP - P) * dt / W)
```

The solution is exact for the chosen constant-segment representation. Default recovery tau is
`W/(CP-P)` below CP, with `recovery_parameter_source="population_estimate"` and warning
`recovery_not_individually_calibrated`. Initial balance is `W`, warning `initial_full_capacity_assumed`.
This does not substitute the empirical tau from the 2012 integral model.
[Skiba et al., 2015](https://pubmed.ncbi.nlm.nih.gov/25425258/),
[Skiba et al., 2012](https://pubmed.ncbi.nlm.nih.gov/22382171/)

The pure kernel also accepts a positive fixed individual tau with a calibration ID. Its version gains
`_fixed_calibrated_tau`; output includes `recovery_tau_seconds`, `recovery_calibration_id` and
`recovery_parameter_source="individually_calibrated"`. The normal service uses the default population
assumption; no individual athlete calibration has been demonstrated.

Unknown intervals invalidate all subsequent balance values, warning
`unknown_gap_invalidates_subsequent_balance`; later measured output/work/events remain reportable.
No usable prefix means no minimum balance. Negative values remain negative with
`negative_balance_model_consistency_breach`; they never become confirmed task failure.

Ordered `above_threshold_events` include sequence, UTC/elapsed start/end, duration, mean power,
above-CP work, balance before/after, modeled depletion and recovery since the previous event.
Unknown recovery is null. Event count is separate; pagination preserves sequence and omission information.

## Workload, durability and running

Cycling workload is `mechanical_work_kj = integral(power_watts * dt) / 1000`. It is external mechanical
work, not metabolic expenditure or a direct fatigue measurement. Full totals require complete known extent;
partial `known_mechanical_work_kj` and unknown seconds remain available separately.

Durability version `matched_300s_2000kj_v1` compares verified maximal 300-second cycling efforts from
distinct workouts within 42 days, with identical explicit comparison protocol/context. Fresh preceding
work must be <=100 kJ; fatigued preceding work must be 2,000–2,200 kJ. Context selection excludes
future effort ends and future observations. Only then return `100*(fresh-tired)/fresh`; otherwise return
`insufficient_evidence` and null decline. A supported observation is not causal proof or a harder-training prescription.

Custom running index `speed_ratio_squared_v1` is:

```text
total_running_stress = (100 / 3600) * integral((speed_mps / critical_speed_mps)^2 * dt)
```

It requires complete speed/extent coverage and a supported `as_known_before_workout` CS model, whose
snapshot ID is retained. Otherwise it is null. It is not validated physiological fatigue, does not adjust
for grade, must not be called TSS and cannot be added to cycling kJ. Running D′ balance remains deferred.

## AI context, retrieval and interfaces

`physiology_context.v1` includes current models, the model used for the latest target, analysis IDs,
7/28-day per-session evidence, coverage, workload, ordered events, durability and limitations.
The compact context returns seven sessions per window and ten target events, with lookup/omission metadata.
Stored sessions are bounded to 500 per window; excess counts are explicit.

Conversational coaching uses this saved context and focused evidence reads directly.
The separate Quick Workout provider projection has been retired with that feature.
[AI coach instructions](AI_COACH_SYSTEM_PROMPT.md) specify summary-first retrieval,
status/provenance interpretation and collaborative planning. The adapter's 64 KB
response bound still applies; bounded item counts are not a universal 15 KB payload
guarantee. Full evidence remains available through the read interfaces.

Read-only routes provide context, model/analysis evidence, per-workout pointers, sessions for exactly 7 or
28 days, and paginated events. Sessions default to 25/max 50 per page; events default to 50/max 100.
The MCP adapter exposes `get_physiology_evidence`, `get_physiology_sessions` and `get_physiology_events`.
Internal reanalysis/protocol routes remain separate from read-only coaching tools.

The library `curated_physiology_cards_v1` uses deterministic retrieval of three primary-source cards,
with claims and population/method limitations. It does not use an external vector database:
[Galbraith et al., critical speed field test](https://pubmed.ncbi.nlm.nih.gov/24622815/),
[Skiba et al., 2015 recovery model](https://pubmed.ncbi.nlm.nih.gov/25425258/),
[Skiba et al., 2012 CP/W′ model](https://pubmed.ncbi.nlm.nih.gov/22382171/).
Research explains deterministic outputs; it cannot supply missing athlete measurements or upgrade support.

Context/prompt rules distinguish low estimated balance from observed exhaustion; event counts from pacing
judgments; workload association from causation; and matched durability findings from automatic training
prescriptions. Missing nutrition stays null without an inadequate-fueling accusation. Missing workouts
never become rest days. Local fixtures check these contracts; hosted AI adherence has not been observed.

## Local acceptance ledger and remaining verification

AC1–8 are locally implemented and tested. The paths below identify the evidence suites; they do not
establish deployment, live provider behavior, athlete-specific validity or scientific validation of every policy.

| Criterion | Local evidence | Production |
| --- | --- | --- |
| AC1: polling, budgets, lifecycle, coverage | `tests/test_physiology_polling.py`, `tests/test_sync.py`, `tests/test_summary_service.py`, `tests/test_physiology_lifecycle.py` | Unverified |
| AC2: normalization and efforts | `tests/test_physiology_numerics.py`, `tests/test_physiology_lifecycle.py` | Unverified |
| AC3: fit, selection and status | `tests/test_physiology_numerics.py` | Unverified |
| AC4: immutable temporal analysis | `tests/test_physiology_lifecycle.py`, `tests/test_physiology_numerics.py`, `tests/test_storage.py` | Unverified |
| AC5: balance, negative values and gaps | `tests/test_physiology_numerics.py` | Unverified |
| AC6: comparable durability/null fallback | `tests/test_physiology_numerics.py` | Unverified |
| AC7: workload units and sport separation | `tests/test_physiology_numerics.py`, `tests/test_physiology_lifecycle.py` | Unverified |
| AC8: context, pagination and coaching contract | `tests/test_physiology_lifecycle.py`, `adapters/mcp/tests/test_adapter.py`, `adapters/mcp/tests/test_physiology_projection.py` | Unverified |

Final local regressions and complete suites passed. No private athlete data or live drilldown was read
for this implementation. Detailed local evidence is `.local/worker/physiology-verification.md`.
No deployment, IAM change or public publication was performed. Production acceptance requires the separately
reviewed release, successful current receipt and authenticated user-visible evidence; live numerical results
and any individual calibration remain unverified until then.
