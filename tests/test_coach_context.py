"""Coach selection correctness, bounds and safe summary routing without cloud access."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_coach import coach_context, main
from ai_coach.config import Settings
from ai_coach.summaries import summarize_workouts
from ai_coach.zones import build_zone_summary
from test_api import MemoryStore

NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)


@pytest.fixture
def memory(monkeypatch):
    memory = MemoryStore()
    monkeypatch.setattr(coach_context, "_now", lambda: NOW)
    return memory


def build(memory, **kwargs):
    return coach_context.build_context(memory, Settings("p", "b"), days=42, upcoming=14,
                                       sync_status={"stale": False}, **kwargs)


def save_summary(memory, period, start, end, **extra):
    doc = summarize_workouts([], start=start, end=end, period=period, timezone="Europe/Oslo")
    key = coach_context.summary_id(period, date.fromisoformat(end if period.startswith("rolling") else start))
    doc.update(id=key, local_date=key.split("_", 1)[1], generated_at=NOW - timedelta(days=7), **extra)
    memory.put("training_summaries", key, doc)
    return doc


def save_all_summaries(memory):
    for args in (("week", "2026-09-14", "2026-09-20"), ("month", "2026-09-01", "2026-09-30"),
            ("rolling7", "2026-09-09", "2026-09-15"), ("rolling7", "2026-09-02", "2026-09-08"),
            ("rolling28", "2026-08-19", "2026-09-15"), ("rolling28", "2026-07-22", "2026-08-18")):
        save_summary(memory, *args)
    memory.put("sync_state", "summaries", {"status": "ok", "updated_at": NOW,
        "calculation_version": 1, "pending_workouts": False, "api_key": "private-secret"})


def test_empty_context_never_invents_rest_or_zero_history(memory):
    result = build(memory)
    assert result["response_version"] == 2
    assert result["as_of_day"] == "2026-09-15"
    assert result["history_complete"] is False
    assert result["totals_scope"] == "known_imported_workouts"
    assert "not evidence of rest" in result["history_note"]
    assert result["summary_freshness"]["stale"] is True
    assert result["comparisons"]["rolling7"]["status"] == "summary_unavailable"
    assert all(doc is None for doc in result["training_summaries"].values())


def test_compact_projection_omits_private_metadata_samples_and_bounds_text(memory):
    row = {"id": "run", "local_date": "2026-09-14", "name": "n" * 2000, "sport": "Run",
        "metrics": {"distance_m": 6000, "moving_time_s": 2300, "secret_value": "hidden"},
        "laps_summary": [{"long_field": "hidden-lap"}] * 200,
        "original_artifact": {"object": "private-path"}, "source_payload_sha256": "hidden-hash",
        "records": [{"private": "hidden-sample"}], "description": "hidden-description"}
    memory.put("workouts", "run", row)
    memory.put("observations", "obs", {"id": "obs", "local_date": "2026-09-14", "notes": "x" * 2000, "rpe": 2})
    result = build(memory)
    encoded = json.dumps(result, default=str)
    assert not any(text in encoded for text in ("private-path", "hidden-hash", "hidden-lap", "hidden-sample", "hidden-description", "secret_value"))
    assert len(result["workouts"][0]["name"]) == 180
    assert result["workouts"][0]["truncated_fields"] == ["name"]
    assert len(result["observations"][0]["notes"]) == 500
    assert "never coach instructions" in result["note"]
    assert memory.get("workouts", "run") == row


def test_pagination_selects_true_latest_records_beyond_500_and_excludes_tombstones(memory):
    for number in range(602):
        key = f"w{number:04}"
        memory.put("workouts", key, {"id": key, "local_date": "2026-09-14",
            "source_deleted": number == 601, "source_excluded": number == 600})
    result = build(memory)
    assert [row["id"] for row in result["workouts"]] == [f"w{number:04}" for number in range(599, 592, -1)]
    selection = result["selection"]["workouts"]
    assert selection["known_matching_records"] == 600
    assert selection["known_omitted_count"] == 593
    assert selection["scan_complete"] is True
    assert selection["lookup"].startswith("/v1/workouts?")


def test_scan_cap_reports_partial_selection_and_resume_cursor(memory, monkeypatch):
    monkeypatch.setattr(coach_context, "MAX_SCAN_RECORDS", 501)
    for number in range(502):
        key = f"w{number:04}"
        memory.put("workouts", key, {"id": key, "local_date": "2026-09-14"})
    result = build(memory)
    selection = result["selection"]["workouts"]
    assert selection["scan_complete"] is False
    assert selection["selection_complete"] is False
    assert selection["scan_resume_after"] == "w0500"
    assert selection["known_matching_records"] == 501


def test_soonest_plans_only_within_exact_calendar_window(memory):
    for day in range(15, 30):
        memory.put("planned_workouts", f"p{day}", {"id": f"p{day}", "local_date": f"2026-09-{day}",
            "status": "completed" if day == 15 else "cancelled" if day == 16 else "planned"})
    result = build(memory)
    assert [row["id"] for row in result["planned_workouts"]] == [f"p{day}" for day in range(17, 24)]
    assert result["selection"]["planned_workouts"]["lookup"].endswith("newest=2026-09-28")
    assert result["selection"]["planned_workouts"]["known_matching_records"] == 12


def test_freshness_uses_successful_reconciliation_not_old_generation_time(memory):
    save_all_summaries(memory)
    result = build(memory)
    assert result["summary_freshness"]["stale"] is False
    assert "private-secret" not in json.dumps(result, default=str)
    assert result["training_summaries"]["current_week"]["partial_calendar_period"] is True
    assert result["training_summaries"]["rolling7"]["partial_calendar_period"] is False
    assert result["comparisons"]["rolling7"]["status"] == "baseline_unavailable"


@pytest.mark.parametrize("changes", [{"status": "pending"}, {"status": "failed"}, {"status": "unknown"},
    {"pending_workouts": True}, {"updated_at": NOW - timedelta(hours=2)}, {"calculation_version": 2},
    {"updated_at": "not-a-date"}, {"updated_at": datetime(2026, 9, 15)}])
def test_aggregate_freshness_independent_from_fresh_source_sync(memory, changes):
    save_all_summaries(memory)
    memory.put("sync_state", "summaries", changes)
    result = build(memory)
    assert result["sync"]["stale"] is False
    assert result["summary_freshness"]["stale"] is True


def test_summary_version_mismatch_marks_context_stale(memory):
    save_all_summaries(memory)
    memory.put("training_summaries", "week_2026-09-14", {"calculation_version": 0})
    assert build(memory)["summary_freshness"]["stale"] is True


def realistic_context(memory):
    """Actual-size shape, synthetic health values; no source files or credentials."""
    save_all_summaries(memory)
    workout = {"id": "intervals_run", "local_date": "2026-09-14", "name": "Synthetic evening session", "sport": "Run",
        "metrics": {"distance_m": 6148.64, "moving_time_s": 2349, "elapsed_time_s": 2358,
            "elevation_gain_m": 49, "average_heart_rate_bpm": 130, "max_heart_rate_bpm": 150,
            "average_power_w": 280, "average_speed_mps": 2.617},
        "analysis": {"training_load": 23, "intensity_percent": 65}, "observations": {"rpe": 2},
        "zone_summary": build_zone_summary({"icu_hr_zones": [144, 152, 161, 170, 175, 180, 189],
                                             "icu_hr_zone_times": [2322, 28, 0, 0, 0, 0, 0]})}
    memory.put("workouts", workout["id"], workout)
    for period, start, end in (("week", "2026-09-14", "2026-09-20"), ("month", "2026-09-01", "2026-09-30"),
            ("rolling7", "2026-09-09", "2026-09-15"), ("rolling28", "2026-08-19", "2026-09-15")):
        key = coach_context.summary_id(period, date.fromisoformat(end if period.startswith("rolling") else start))
        summary = summarize_workouts([workout], start=start, end=end, period=period, timezone="Europe/Oslo")
        memory.put("training_summaries", key, dict(summary, id=key, generated_at=NOW, local_date=start))
    for day in ("2026-09-14", "2026-09-15"):
        memory.put("wellness", day, {"id": day, "local_date": day, "provider_source": "INTERVALS_MERGED_WELLNESS",
            "metrics": {"weight_kg": 92, "resting_heart_rate_bpm": 50, "sleep_duration_s": 28800,
                "sleep_score": 80, "hrv_rmssd_ms": 60, "hrv_sdnn_ms": None},
            "analysis": {"ctl": 0, "atl": 0, "ramp_rate": 0, "readiness": None}})
    return build(memory)


def test_realistic_brief_is_smaller_than_legacy_without_losing_zone_bins(memory):
    result = realistic_context(memory)
    encoded = json.dumps(result, default=str, separators=(",", ":")).encode()
    assert len(encoded) <= 5500, f"Compact fixture response is {len(encoded)} bytes"
    assert result["zone_definitions"] == {"hr1": {"upper_bpm": [144, 152, 161, 170, 175, 180, 189],
        "source": "intervals.icu", "boundary_semantics": "inclusive_upper_bpm"}}
    assert result["workouts"][0]["hr_zones"]["seconds"] == [2322, 28, 0, 0, 0, 0, 0]
    assert "previous_rolling7" not in result["training_summaries"]
    assert result["comparisons"]["rolling7"]["status"] == "baseline_unavailable"
    assert "zone_definition_id" not in encoded.decode()


def test_partial_source_sync_and_end_of_calendar_period_are_explicit(memory):
    result = coach_context.build_context(memory, Settings("p", "b"), days=42, upcoming=14,
        sync_status={"last_status": "partial", "stale": False, "last_counts": {"large": "omitted"}})
    assert result["sync"] == {"last_status": "partial", "stale": False, "partial": True}
    sunday = {"period": "week", "start_date": "2026-09-14", "end_date": "2026-09-20"}
    assert coach_context.compact_summary(sunday, today=date(2026, 9, 20))["partial_calendar_period"] is True
    assert coach_context.compact_summary(sunday, today=date(2026, 9, 21))["partial_calendar_period"] is False


@pytest.mark.parametrize("period,requested,key", [("week", "2026-09-15", "week_2026-09-14"),
    ("month", "2026-09-15", "month_2026-09-01"), ("rolling7", "2026-09-15", "rolling7_2026-09-15"),
    ("day", "2026-09-15", "day_2026-09-15")])
def test_summary_route_resolves_dates_and_omits_private_fields(memory, monkeypatch, period, requested, key):
    memory.put("training_summaries", key, {"id": key, "period": period, "local_date": "2026-09-15",
        "calculation_version": 1, "private_metadata": "secret-not-returned"})
    monkeypatch.setattr(main, "store", lambda: memory)
    monkeypatch.setattr(main, "settings", lambda: Settings("p", "b"))
    with TestClient(main.app) as client:
        response = client.get("/v1/summaries", params={"period": period, "date": requested})
        assert response.status_code == 200
        assert response.json()["summary"]["id"] == key
        assert "secret-not-returned" not in response.text
        assert client.get("/v1/summaries", params={"period": "year", "date": requested}).status_code == 422
        assert client.get("/v1/summaries", params={"period": period, "date": "2026-02-30"}).status_code == 422
        missing = client.get("/v1/summaries", params={"period": period, "date": "2020-01-01"})
        assert missing.status_code == 404
        assert "not zero training" in missing.text
