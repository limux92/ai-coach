"""Synthetic persistence, temporal selection and real private route integration."""
from datetime import UTC, datetime, timedelta
import copy

import pytest
from fastapi.testclient import TestClient

from ai_coach.config import Settings
from ai_coach import main
from ai_coach.physiology_evidence import commit_workout, select_revisions
from ai_coach.physiology_service import extract_revision, scan_all, analyze_target, refresh_physiology, available_efforts
from ai_coach.physiology_context import read_context
from test_sync import MemoryStore

T = datetime(2026, 9, 1, tzinfo=UTC)
SETTINGS = Settings("test", "test", history_start_date="2026-01-01")


def add_workout(store, monkeypatch, identifier, day, duration=150, power=370, known_day=None):
    start = T + timedelta(days=day)
    known = T + timedelta(days=known_day if known_day is not None else day, hours=2)
    monkeypatch.setattr("ai_coach.physiology_evidence.utcnow", lambda: known)
    parsed = {"records": [{"timestamp": (start + timedelta(seconds=i)).isoformat(), "power": power}
                          for i in range(duration + 1)], "events": [], "sessions": []}
    artifact = store.archive_json("parsed/" + identifier, parsed)
    doc = {"id": identifier, "local_date": start.date().isoformat(), "start_date_utc": start.isoformat(),
           "sport": "Ride", "parse_status": "parsed", "parsed_artifact": artifact,
           "metrics": {"elapsed_time_s": duration}, "source_payload_sha256": identifier,
           "physiology_protocol_artifact_sha256": artifact["sha256"],
           "physiology_protocols": [{"start_utc": start.isoformat(), "duration_seconds": duration,
                                     "comparison_protocol_id": "max_test", "comparison_context": "same_trainer"}]}
    commit_workout(store, identifier, doc, merge=False)
    saved = store.get("workouts", identifier)
    revision = store.get("physiology_revisions", saved["physiology_revision_id"])
    extract_revision(store, revision)
    return saved, revision


def revisions(store):
    return scan_all(store, "physiology_revisions")


def test_unchanged_replay_edits_deletions_and_original_analysis_immutable(monkeypatch):
    store = MemoryStore()
    for day, duration in ((1, 150), (2, 300), (3, 600)):
        add_workout(store, monkeypatch, f"w{day}", day, duration, 250 + 18000 / duration)
    target, target_rev = add_workout(store, monkeypatch, "target", 8, 300, 330)
    now = T + timedelta(days=10)
    original = analyze_target(store, revisions(store), target_rev, now=now)
    model = store.get("physiology_models", original["model_snapshot_id"])
    assert model["status"] == "supported"
    assert model["critical_power_watts"] == pytest.approx(250)
    original_copy = copy.deepcopy(original)
    count = len(revisions(store))
    commit_workout(store, "target", target, merge=False)
    assert len(revisions(store)) == count
    # An old-dated effort first acquired after the target is never historical evidence.
    add_workout(store, monkeypatch, "late", 4, 150, 600, known_day=9)
    replay = analyze_target(store, revisions(store), target_rev, now=now)
    assert replay == original_copy
    # Post-workout deletion is effective for present/retrospective, not original interpretation.
    monkeypatch.setattr("ai_coach.physiology_evidence.utcnow", lambda: T + timedelta(days=9))
    commit_workout(store, "w1", {"source_deleted": True})
    past = available_efforts(store, revisions(store), T + timedelta(days=8))
    present = available_efforts(store, revisions(store), now)
    assert any(e["workout_id"] == "w1" for e in past)
    assert all(e["workout_id"] != "w1" for e in present)
    retrospective = analyze_target(store, revisions(store), target_rev, now=now, mode="retrospective")
    assert retrospective["analysis_id"] != original["analysis_id"]
    assert store.get("physiology_analyses", original["analysis_id"]) == original_copy
    pointer = store.get("sync_state", "physiology_target_target")
    assert pointer["original_analysis_id"] == original["analysis_id"]
    assert pointer["latest_retrospective"] == retrospective["analysis_id"]


def test_moved_revision_selected_before_date_filter_and_pending_artifact_not_eligible(monkeypatch):
    store = MemoryStore()
    doc, first = add_workout(store, monkeypatch, "w", 1)
    monkeypatch.setattr("ai_coach.physiology_evidence.utcnow", lambda: T + timedelta(days=3))
    commit_workout(store, "w", {"local_date": "2025-01-01", "parse_status": "pending"})
    latest = select_revisions(revisions(store), known_before=T + timedelta(days=4))[0]
    extracted = extract_revision(store, latest)
    assert not extracted["eligible"] and extracted["efforts"] == []
    assert available_efforts(store, revisions(store), T + timedelta(days=4)) == []
    assert available_efforts(store, revisions(store), T + timedelta(days=2))


def test_interruption_after_immutable_analysis_repairs_pointer(monkeypatch):
    store = MemoryStore()
    _, revision = add_workout(store, monkeypatch, "w", 1)
    put = store.put
    def interrupted(collection, identifier, data, **kwargs):
        if identifier == "physiology_target_w":
            raise OSError("synthetic interruption")
        return put(collection, identifier, data, **kwargs)
    store.put = interrupted
    with pytest.raises(OSError):
        analyze_target(store, revisions(store), revision, now=T + timedelta(days=3))
    store.put = put
    restored = analyze_target(store, revisions(store), revision, now=T + timedelta(days=4))
    assert store.get("sync_state", "physiology_target_w")["original_analysis_id"] == restored["analysis_id"]


def test_full_source_duration_gap_blocks_total_work(monkeypatch):
    store = MemoryStore()
    doc, _ = add_workout(store, monkeypatch, "w", 1)
    commit_workout(store, "w", {"metrics": {"elapsed_time_s": 500}})
    revision = store.get("physiology_revisions", store.get("workouts", "w")["physiology_revision_id"])
    output = extract_revision(store, revision)
    assert output["workload"]["mechanical_work_kj"] is None
    assert "unknown_trailing_gap" in output["recording_quality_flags"]


def test_refresh_context_coverage_api_and_clock_only_expiry(monkeypatch):
    store = MemoryStore()
    for day, duration in ((1, 150), (2, 300), (3, 600)):
        add_workout(store, monkeypatch, f"w{day}", day, duration, 250 + 18000 / duration)
    add_workout(store, monkeypatch, "target", 8, 300, 310)
    now = T + timedelta(days=10)
    store.put("sync_state", "intervals", {"recent_coverage_at": now, "recent_coverage_oldest": "2026-08-01",
              "recent_coverage_newest": "2026-09-11", "backfill_complete": False})
    assert refresh_physiology(store, SETTINGS, now=now)["status"] == "ok"
    context = read_context(store, SETTINGS, now)
    assert not context["data_coverage"]["history_complete"]
    assert context["data_coverage"]["recent_window_complete"]
    assert context["latest_session_target_analysis"]["fueling_carbs_grams_per_hour"] is None
    assert context["durability"]["observed_decline_percent"] is None
    assert context["latest_session_target_analysis"]["above_threshold_events"]
    monkeypatch.setattr(main, "store", lambda: store)
    monkeypatch.setattr(main, "settings", lambda: SETTINGS)
    with TestClient(main.app) as client:
        aid = context["latest_session_target_analysis"]["analysis_id"]
        response = client.get(f"/v1/physiology/analyses/{aid}/events?limit=1")
        assert response.status_code == 200 and response.json()["total"] == 1
        assert client.get("/v1/physiology/sessions?days=7&limit=1").status_code == 200
        assert client.get("/v1/physiology/sessions?days=8").status_code == 422
        assert "analysis_artifact" not in client.get(f"/v1/physiology/analyses/{aid}").json()
    saved = copy.deepcopy(store.get("physiology_contexts", store.get("sync_state", "physiology")["context_id"]))
    refresh_physiology(store, SETTINGS, now=T + timedelta(days=60))
    latest = read_context(store, SETTINGS, T + timedelta(days=60))
    assert latest["current_models"]["cycling"]["status"] == "insufficient_data"
    assert saved["current_models"]["cycling"]["status"] == "supported"


def test_newly_migrated_evidence_is_not_published_early(monkeypatch):
    store = MemoryStore()
    add_workout(store, monkeypatch, "w", 10)
    result = refresh_physiology(store, SETTINGS, now=T + timedelta(days=9))
    assert result["status"] == "pending"
    assert store.get("sync_state", "physiology").get("context_id") is None


def test_equal_commit_timestamp_selects_revision_sequence_not_hash_order(monkeypatch):
    store = MemoryStore()
    add_workout(store, monkeypatch, "w", 1)
    commit_workout(store, "w", {"source_deleted": True})
    rows = revisions(store)
    assert rows[0]["known_at"] == rows[1]["known_at"]
    for order in (rows, list(reversed(rows))):
        selected = select_revisions(order, known_before=T + timedelta(days=2))
        assert selected[0]["sequence"] == 2 and selected[0]["evidence"]["source_deleted"]


def test_version_upgrade_reextracts_without_mutating_original_evidence(monkeypatch):
    from ai_coach import physiology_service as service
    store = MemoryStore()
    _, revision = add_workout(store, monkeypatch, "w", 1)
    old_id = service.extraction_id(revision["id"])
    old = copy.deepcopy(store.get("physiology_efforts", old_id))
    refresh_physiology(store, SETTINGS, now=T + timedelta(days=2))
    monkeypatch.setattr(service, "SAMPLE_VERSION", "synthetic_new_normalizer")
    assert refresh_physiology(store, SETTINGS, now=T + timedelta(days=3))["status"] == "ok"
    assert service.extraction_id(revision["id"]) != old_id
    assert store.get("physiology_efforts", service.extraction_id(revision["id"]))
    assert store.get("physiology_efforts", old_id) == old


def test_retired_target_is_rechecked_after_acquiring_write_lease(monkeypatch):
    store = MemoryStore()
    doc, _ = add_workout(store, monkeypatch, "w", 1)
    acquire = store.acquire_lease
    def retiring_acquire(owner, **kwargs):
        result = acquire(owner, **kwargs)
        store.put("workouts", "w", {"source_deleted": True})
        return result
    store.acquire_lease = retiring_acquire
    monkeypatch.setattr(main, "store", lambda: store)
    monkeypatch.setattr(main, "settings", lambda: SETTINGS)
    with TestClient(main.app) as client:
        assert client.post("/internal/physiology/workouts/w/reanalyze").status_code == 410
        assert client.post("/internal/physiology/workouts/w/protocols", json={
            "parsed_artifact_sha256": doc["parsed_artifact"]["sha256"], "efforts": []}).status_code == 410


def test_unknown_extent_cannot_report_whole_workout_energy_in_balance(monkeypatch):
    store = MemoryStore()
    for day, duration in ((1, 150), (2, 300), (3, 600)):
        add_workout(store, monkeypatch, f"w{day}", day, duration, 250 + 18000 / duration)
    add_workout(store, monkeypatch, "target", 8, 300, 310)
    commit_workout(store, "target", {"metrics": {}})
    target = store.get("physiology_revisions", store.get("workouts", "target")["physiology_revision_id"])
    extract_revision(store, target)
    analysis = analyze_target(store, revisions(store), target, now=T + timedelta(days=10))
    assert analysis["workload"]["mechanical_work_kj"] is None
    assert analysis["balance"]["mechanical_work_kj"] is None
    assert "unknown_workout_extent" in analysis["balance"]["warnings"]
