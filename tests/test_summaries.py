"""Calculation tests exercise coverage, dates, replay and heterogeneous bins."""

import json
from copy import deepcopy

import pytest

from ai_coach.summaries import compare_summaries, period_bounds, summarize_workouts


def workout(identifier="a", day="2026-09-14", sport="Run", **values):
    return {"id": identifier, "local_date": day, "sport": sport,
            "metrics": values, "analysis": {"training_load": 23}}


def summarize(rows, start="2026-09-14", end="2026-09-20", period="week"):
    return summarize_workouts(rows, start=start, end=end, period=period, timezone="Europe/Oslo")


def zone(definition="d1", boundaries=None, seconds=None):
    seconds = seconds if seconds is not None else [120, 60]
    return {"heart_rate": {"status": "available", "source": "intervals.icu",
                           "zone_definition_id": definition, "boundaries_bpm": boundaries or [130, 160],
                           "seconds": seconds, "classified_seconds": sum(seconds)}}


def test_known_uploaded_workout_metrics_and_missing_are_distinct():
    result = summarize([workout(distance_m=6148.64, moving_time_s=2349, elapsed_time_s=2370,
                               elevation_gain_m=49)])
    assert result["workout_count"] == 1
    assert result["active_days"] == 1
    assert result["totals"] == {"distance_m": 6148.64, "moving_time_s": 2349,
                                "elapsed_time_s": 2370, "elevation_gain_m": 49, "training_load": 23}
    assert result["by_sport"][0]["pace_s_per_km"] == pytest.approx(2349 / 6.14864)
    assert result["history_complete"] is False
    assert result["totals_scope"] == "known_imported_workouts"
    assert result["by_sport"][0]["heart_rate_zones"]["missing_workout_count"] == 1
    json.dumps(result, allow_nan=False)


def test_empty_history_does_not_manufacture_zero_training():
    result = summarize([])
    assert result["workout_count"] == 0
    assert result["active_days"] == 0
    assert result["longest_distance_m"] is None
    assert all(total is None for total in result["totals"].values())
    assert all(count == 0 for count in result["contributors"].values())
    assert result["by_sport"] == []
    assert result["history_complete"] is False


def test_measurement_coverage_is_per_metric_and_zero_is_known():
    result = summarize([workout("a", distance_m=0, moving_time_s=0, elevation_gain_m=0),
                        workout("b", moving_time_s=600), workout("c", distance_m=1000)])
    assert result["totals"]["distance_m"] == 1000
    assert result["contributors"]["distance_m"] == 2
    assert result["totals"]["moving_time_s"] == 600
    assert result["contributors"]["moving_time_s"] == 2
    assert result["totals"]["elevation_gain_m"] == 0
    assert result["contributors"]["elevation_gain_m"] == 1
    assert result["totals"]["elapsed_time_s"] is None
    assert result["contributors"]["elapsed_time_s"] == 0
    assert result["by_sport"][0]["pace_s_per_km"] is None


def test_replay_deduplicates_and_last_snapshot_wins():
    before = workout(distance_m=1000, moving_time_s=400)
    after = workout(distance_m=2000, moving_time_s=600)
    result = summarize([before, before, after])
    assert result["workout_count"] == 1
    assert result["totals"]["distance_m"] == 2000
    assert result["duplicate_records_removed"] == 2
    # Deletions and date edits must suppress the previous in-window snapshot.
    assert summarize([before, {**after, "source_deleted": True}])["workout_count"] == 0
    assert summarize([before, {**after, "local_date": "2026-09-21"}])["workout_count"] == 0
    assert summarize([before, {**after, "source_excluded": True}])["workout_count"] == 0


def test_inclusive_dates_use_original_local_date_even_if_utc_differs():
    rows = [workout("a", "2026-09-13", distance_m=1000),
            workout("b", "2026-09-14", distance_m=2000),
            workout("c", "2026-09-20", distance_m=3000),
            workout("d", "2026-09-21", distance_m=4000)]
    rows[1]["start_date_utc"] = "2026-09-13T22:15:00Z"
    result = summarize(rows)
    assert result["workout_count"] == 2
    assert result["totals"]["distance_m"] == 5000
    assert result["active_days"] == 2


def test_pace_is_ratio_of_paired_totals_not_mean_of_workout_paces():
    rows = [workout("a", distance_m=1000, moving_time_s=240),
            workout("b", distance_m=10000, moving_time_s=3600),
            workout("c", moving_time_s=1000), workout("d", distance_m=9000)]
    run = summarize(rows)["by_sport"][0]
    assert run["pace_s_per_km"] == pytest.approx(3840 / 11)
    assert run["contributors"]["pace_s_per_km"] == 2
    assert run["totals"]["moving_time_s"] == 4840
    assert run["totals"]["distance_m"] == 20000
    assert run["longest_distance_m"] == 10000
    assert run["contributors"]["longest_distance_m"] == 3


def test_running_and_cycling_stay_separate_and_unknown_sport_is_explicit():
    result = summarize([workout("a", sport="Run", distance_m=5000, moving_time_s=1800),
                        workout("b", sport="Ride", distance_m=30000, moving_time_s=3600),
                        workout("c", sport=None, moving_time_s=400)])
    assert [sport["sport"] for sport in result["by_sport"]] == ["Ride", "Run", "Unknown"]
    assert result["by_sport"][1]["totals"]["distance_m"] == 5000
    assert result["by_sport"][0]["totals"]["distance_m"] == 30000
    assert result["active_days"] == 1
    assert "pace_s_per_km" not in result


@pytest.mark.parametrize("day,period,expected", [
    ("2026-09-20", "week", ("2026-09-14", "2026-09-20")),
    ("2026-09-21", "week", ("2026-09-21", "2026-09-27")),
    ("2026-01-01", "week", ("2025-12-29", "2026-01-04")),
    ("2026-12-31", "month", ("2026-12-01", "2026-12-31")),
    ("2028-02-20", "month", ("2028-02-01", "2028-02-29")),
    ("2026-02-20", "month", ("2026-02-01", "2026-02-28")),
    ("2026-01-03", "rolling7", ("2025-12-28", "2026-01-03")),
    ("2026-03-15", "rolling28", ("2026-02-16", "2026-03-15")),
    ("2026-09-15", "day", ("2026-09-15", "2026-09-15")),
])
def test_calendar_and_rolling_bounds(day, period, expected):
    assert period_bounds(day, period) == expected


def test_matching_zone_definitions_sum_seconds_and_weight_percentages():
    first, second = workout("a"), workout("b")
    first["zone_summary"] = zone(seconds=[60, 0])
    second["zone_summary"] = zone(seconds=[0, 180])
    result = summarize([first, second, workout("c")])["by_sport"][0]["heart_rate_zones"]
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["seconds"] == [60, 180]
    assert group["percentages"] == [25, 75]
    assert group["classified_seconds"] == 240
    assert group["boundary_semantics"] == "inclusive_upper_bpm"
    assert group["percentage_basis"] == "classified_seconds"
    assert group["workout_count"] == 2
    assert result["missing_workout_count"] == 1
    assert result["contributing_workout_count"] == 2
    assert result["unclassified_seconds"] is None
    assert result["duration_denominator_seconds"] is None


def test_different_definitions_and_sports_do_not_merge_even_if_labels_match():
    rows = [workout("a"), workout("b"), workout("c"), workout("d", sport="Ride")]
    rows[0]["zone_summary"] = zone("first")
    rows[1]["zone_summary"] = zone("second", boundaries=[135, 165])
    # Corrupt reused IDs must still not merge different actual boundaries.
    rows[2]["zone_summary"] = zone("first", boundaries=[140, 170])
    rows[3]["zone_summary"] = zone("first")
    result = summarize(rows)
    ride, run = result["by_sport"]
    assert len(run["heart_rate_zones"]["groups"]) == 3
    assert len(ride["heart_rate_zones"]["groups"]) == 1
    assert all(group["seconds"] == [120, 60] for group in run["heart_rate_zones"]["groups"])


def test_zero_zone_duration_is_known_but_has_no_percentage():
    item = workout()
    item["zone_summary"] = zone(seconds=[0, 0])
    result = summarize([item])["by_sport"][0]["heart_rate_zones"]
    assert result["groups"][0]["classified_seconds"] == 0
    assert result["groups"][0]["percentages"] == [None, None]
    assert result["contributing_workout_count"] == 1


@pytest.mark.parametrize("change", [
    {"status": "missing"}, {"status": "invalid"}, {"status": "ignored"},
    {"seconds": [20]}, {"seconds": [-1, 1]}, {"seconds": [True, 179]},
    {"boundaries_bpm": [160, 130]}, {"boundaries_bpm": [130, 130]},
    {"boundaries_bpm": [0, 130]}, {"classified_seconds": 200},
    {"zone_definition_id": None}, {"seconds": [float("nan"), 1]},
])
def test_invalid_zones_do_not_pollute_totals(change):
    item = workout()
    item["zone_summary"] = zone()
    item["zone_summary"]["heart_rate"].update(change)
    result = summarize([item])["by_sport"][0]["heart_rate_zones"]
    assert result["groups"] == []
    assert result["missing_workout_count"] == 1
    json.dumps(result, allow_nan=False)


def test_malformed_records_and_numbers_are_ignored_without_nan_or_bool_totals():
    item = workout(distance_m=float("nan"), moving_time_s=True, elapsed_time_s=-1,
                   elevation_gain_m="20")
    item["analysis"]["training_load"] = float("inf")
    rows = [item, None, {"id": "b", "local_date": "bad"}, {"local_date": "2026-09-14"}]
    result = summarize(rows)
    assert result["workout_count"] == 1
    assert result["ignored_malformed_records"] == 3
    assert all(value is None for value in result["totals"].values())
    assert all(count == 0 for count in result["contributors"].values())
    json.dumps(result, allow_nan=False)


def test_overflowing_total_remains_json_safe():
    result = summarize([workout("a", distance_m=1e308), workout("b", distance_m=1e308)])
    assert result["totals"]["distance_m"] is None
    assert result["contributors"]["distance_m"] == 2
    json.dumps(result, allow_nan=False)


def test_inputs_are_not_mutated():
    items = [workout()]
    items[0]["zone_summary"] = zone()
    snapshot = deepcopy(items)
    summarize(items)
    assert items == snapshot


def test_bad_range_or_configuration_fails_clearly():
    for start, end in [("20260914", "2026-09-20"), ("2026-09-21", "2026-09-20")]:
        with pytest.raises(ValueError):
            summarize([], start=start, end=end)
    with pytest.raises(ValueError):
        summarize([], period="year")
    with pytest.raises(ValueError):
        summarize_workouts([], start="2026-09-14", end="2026-09-20", period="week", timezone="Mars/Run")
    with pytest.raises(ValueError):
        period_bounds("2026-09-14", "custom")


def test_comparison_uses_known_values_and_no_missing_training_inference():
    current = summarize([workout(distance_m=15000, moving_time_s=5400)])
    previous = summarize([workout(day="2026-09-07", distance_m=10000, moving_time_s=3600)],
                         start="2026-09-07", end="2026-09-13")
    compared = compare_summaries(current, previous)
    assert compared["comparable"] is True
    assert compared["changes"]["distance_m"] == {
        "current": 15000, "previous": 10000, "absolute_change": 5000, "percent_change": 50}
    assert compared["changes"]["elapsed_time_s"]["absolute_change"] is None
    assert compared["changes"]["elapsed_time_s"]["percent_change"] is None
    assert compared["by_sport"][0]["changes"]["distance_m"]["percent_change"] == 50
    assert compared["history_complete"] is False
    missing = compare_summaries(current, summarize([], start="2026-09-07", end="2026-09-13"))
    assert missing["changes"]["distance_m"]["previous"] is None
    assert missing["changes"]["distance_m"]["percent_change"] is None


def test_zero_baseline_has_no_percentage_change():
    previous = summarize([workout(day="2026-09-07", distance_m=0)], start="2026-09-07", end="2026-09-13")
    current = summarize([workout(distance_m=1000)])
    compared = compare_summaries(current, previous)
    assert compared["changes"]["distance_m"]["absolute_change"] == 1000
    assert compared["changes"]["distance_m"]["percent_change"] is None


def test_comparison_keeps_unmatched_sports_unknown():
    previous = summarize([workout(day="2026-09-07", sport="Ride", distance_m=20000)],
                         start="2026-09-07", end="2026-09-13")
    current = summarize([workout(sport="Run", distance_m=5000)])
    compared = compare_summaries(current, previous)
    ride, run = compared["by_sport"]
    assert ride["changes"]["distance_m"]["current"] is None
    assert run["changes"]["distance_m"]["previous"] is None
    assert all(row["changes"]["distance_m"]["percent_change"] is None for row in compared["by_sport"])


def test_comparison_rejects_partial_week_against_full_week():
    current = summarize([], end="2026-09-15")
    previous = summarize([], start="2026-09-07", end="2026-09-13")
    result = compare_summaries(current, previous)
    assert result["comparable"] is False
    assert result["reason"] == "unequal_window_lengths"
    assert result["changes"] is None


def test_comparison_rejects_incompatible_timezones_and_invalid_dates():
    current = summarize([])
    previous = {**current, "timezone": "UTC"}
    assert compare_summaries(current, previous)["reason"] == "incompatible_timezones"
    assert compare_summaries(current, {**current, "start_date": None})["reason"] == "invalid_dates"


def test_comparison_refuses_different_calculation_versions():
    current = summarize([])
    previous = {**current, "calculation_version": 2}
    result = compare_summaries(current, previous)
    assert result["comparable"] is False
    assert result["reason"] == "incompatible_calculation_versions"
    assert result["changes"] is None
