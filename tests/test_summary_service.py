"""Integration coverage for durable aggregate maintenance and source corrections."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from ai_coach import summary_service as service
from ai_coach import sync as sync_module
from ai_coach.fit_parser import ParsedActivity
from ai_coach.intervals_client import IntervalsError
from ai_coach.normalize import SCHEMA_VERSION
from ai_coach.summaries import CALCULATION_VERSION, period_bounds
from ai_coach.zones import build_zone_summary
from test_sync import FakeClient, MemoryStore, TODAY, activity, upload, verified_parser


DAY = date(2026, 9, 15)


@pytest.fixture
def settings():
    return SimpleNamespace(project="test", bucket="test", athlete_id="0",
                           timezone="Europe/Oslo", history_start_date="2000-01-01")


@pytest.fixture(autouse=True)
def parser(monkeypatch):
    monkeypatch.setattr(sync_module, "parse_activity_file", lambda *args, **kwargs: ParsedActivity(
        records=[{"heart_rate": 140}], laps=[], metadata={"crc_verified": True}))


class TrackingStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.reads = []
        self.date_queries = []
        self.fail_next_summary = False

    def put(self, collection, doc_id, data, *, merge=True):
        if collection == "training_summaries" and self.fail_next_summary:
            self.fail_next_summary = False
            raise OSError("synthetic aggregate write failure")
        self.writes.append((collection, doc_id))
        super().put(collection, doc_id, data, merge=merge)

    def read_json(self, artifact):
        self.reads.append(artifact["object"])
        return super().read_json(artifact)

    def list(self, collection, oldest, newest, *, limit=200, after=None):
        self.date_queries.append((collection, oldest, newest, limit, after))
        return super().list(collection, oldest, newest, limit=limit, after=after)


def doc(identifier="one", day="2026-09-14", distance=1000):
    return {"id": identifier, "local_date": day, "sport": "Run", "schema_version": SCHEMA_VERSION,
            "source": "intervals.icu", "source_id": identifier.removeprefix("intervals_"),
            "metrics": {"distance_m": distance, "moving_time_s": 360},
            "analysis": {"training_load": 10}, "zone_summary": build_zone_summary({})}


def aggregate(store, period, day):
    start, _ = period_bounds(day, period)
    return store.get("training_summaries", f"{period}_{day if period.startswith('rolling') else start}")


def test_initial_and_replay_are_idempotent_without_aggregate_rewrites(settings):
    store = TrackingStore()
    item = doc()
    service.save_workout(store, item["id"], item, merge=False)
    first = service.refresh_summaries(store, settings, DAY)
    assert first["updated"] > 0 and first["pending"] is False
    assert aggregate(store, "week", item["local_date"])["totals"]["distance_m"] == 1000
    assert store.get("summary_jobs", item["id"])["pending"] is False
    store.writes.clear()
    service.save_workout(store, item["id"], item, merge=False)
    second = service.refresh_summaries(store, settings, DAY)
    assert second == {"updated": 0, "pending": False}
    assert not any(collection == "training_summaries" for collection, _ in store.writes)
    assert aggregate(store, "week", item["local_date"])["workout_count"] == 1


def test_date_edit_rebuilds_old_and_new_day_week_month(settings):
    store = TrackingStore()
    item = doc()
    service.save_workout(store, item["id"], item, merge=False)
    service.refresh_summaries(store, settings, DAY)
    moved = {**item, "local_date": "2026-08-31"}
    service.save_workout(store, item["id"], moved, merge=False)
    assert store.get("summary_jobs", item["id"])["dates"] == ["2026-08-31", "2026-09-14"]
    service.refresh_summaries(store, settings, DAY)
    for period in ("day", "week", "month"):
        old = aggregate(store, period, item["local_date"])
        new = aggregate(store, period, moved["local_date"])
        assert old["workout_count"] == 0 and old["totals"]["distance_m"] is None
        assert new["workout_count"] == 1 and new["totals"]["distance_m"] == 1000


def test_deleted_workout_is_excluded_but_original_evidence_is_retained(settings):
    store = TrackingStore()
    original = store.archive("originals/one", b"original fit evidence")
    item = {**doc(), "original_artifact": original}
    service.save_workout(store, item["id"], item, merge=False)
    service.refresh_summaries(store, settings, DAY)
    service.save_workout(store, item["id"], {"source_deleted": True})
    service.refresh_summaries(store, settings, DAY)
    assert aggregate(store, "month", item["local_date"])["workout_count"] == 0
    assert store.get("workouts", item["id"])["original_artifact"] == original
    assert store.objects[original["object"]] == b"original fit evidence"


def test_failed_aggregate_after_workout_save_recovers_from_durable_job(settings):
    store = TrackingStore()
    item = doc()
    service.save_workout(store, item["id"], item, merge=False)
    store.fail_next_summary = True
    with pytest.raises(OSError):
        service.refresh_summaries(store, settings, DAY)
    state = store.get("sync_state", "summaries")
    assert state["status"] == "failed" and state["pending_workouts"] is True
    assert state["last_error_code"] == "OSError"
    assert store.get("summary_jobs", item["id"])["pending"] is True
    assert store.get("workouts", item["id"])["metrics"]["distance_m"] == 1000
    recovered = service.refresh_summaries(store, settings, DAY)
    assert recovered["pending"] is False
    assert store.get("sync_state", "summaries")["status"] == "ok"
    assert aggregate(store, "week", item["local_date"])["totals"]["distance_m"] == 1000


def test_aggregate_pagination_includes_all_records_above_page_limit(settings):
    store = TrackingStore()
    for index in range(450):
        identifier = f"run_{index:04d}"
        store.put("workouts", identifier, doc(identifier))
    first = service.refresh_summaries(store, settings, DAY)
    assert first["pending"] is True  # Migration is independently bounded at100.
    summary = aggregate(store, "week", "2026-09-14")
    assert summary["workout_count"] == 450 and summary["totals"]["distance_m"] == 450000
    assert any(after is not None for collection, _, _, _, after in store.date_queries if collection == "workouts")
    for _ in range(5):
        result = service.refresh_summaries(store, settings, DAY)
        if not result["pending"]:
            break
    assert result["pending"] is False
    assert aggregate(store, "week", "2026-09-14")["workout_count"] == 450
    assert not store.scan("summary_jobs", pending=True)


def test_daily_rollover_updates_rolling_totals_without_new_activity(settings):
    store = TrackingStore()
    item = doc(day=(DAY - timedelta(days=6)).isoformat())
    service.save_workout(store, item["id"], item, merge=False)
    service.refresh_summaries(store, settings, DAY)
    assert aggregate(store, "rolling7", DAY.isoformat())["totals"]["distance_m"] == 1000
    tomorrow = DAY + timedelta(days=1)
    service.refresh_summaries(store, settings, tomorrow)
    new = aggregate(store, "rolling7", tomorrow.isoformat())
    assert new["workout_count"] == 0 and new["totals"]["distance_m"] is None
    assert aggregate(store, "rolling28", tomorrow.isoformat())["totals"]["distance_m"] == 1000


def test_migration_uses_archived_json_zone_snapshot_without_original_read(settings):
    store = TrackingStore()
    raw = store.archive_json("raw/one", {"summary": {"icu_hr_zones": [130, 160],
                              "icu_hr_zone_times": [10, 20]}, "detail": {"icu_hr_zone_times": [60, 120]}})
    original = store.archive("originals/one", b"must not be read or parsed")
    item = {**doc(), "schema_version": 1, "raw_payload_artifact": raw, "original_artifact": original}
    item.pop("zone_summary")
    store.put("workouts", item["id"], item)
    service.refresh_summaries(store, settings, DAY)
    migrated = store.get("workouts", item["id"])
    assert migrated["schema_version"] == SCHEMA_VERSION
    assert migrated["zone_summary"]["heart_rate"]["seconds"] == [60, 120]
    assert migrated["original_artifact"] == original
    assert store.reads == [raw["object"]]
    assert store.get("sync_state", "summary_migration")["version"] == (
        f"{CALCULATION_VERSION}.{SCHEMA_VERSION}.{service.ZONE_VERSION}")
    group = aggregate(store, "week", item["local_date"])["by_sport"][0]["heart_rate_zones"]["groups"][0]
    assert group["seconds"] == [60, 120]
    service.refresh_summaries(store, settings, DAY)
    assert store.reads == [raw["object"]]


def test_late_historical_upload_is_discovered_after_backfill_complete(settings, monkeypatch):
    store = TrackingStore()
    store.put("sync_state", "intervals", {"backfill_complete": True})
    late = {**upload(days_ago=40), "distance": 5000, "moving_time": 1800}
    monkeypatch.setattr(sync_module, "parse_activity_file", verified_parser)
    result = sync_module.run_sync(store, settings, client=FakeClient([late]))
    assert result["status"] == "ok" and result["counts"]["uploads_verified"] == 1
    imported = store.get("workouts", "intervals_a1")
    assert imported["local_date"] == (TODAY - timedelta(days=40)).isoformat()
    assert imported["metrics"]["distance_m"] == 5000
    state = store.get("sync_state", "intervals")
    assert state["backfill_complete"] is True
    assert state["historical_reconcile_cursor"] == (TODAY - timedelta(days=sync_module.RECENT_DAYS + 31)).isoformat()
    assert aggregate(store, "month", imported["local_date"])["totals"]["distance_m"] == 5000


def test_absent_activity_is_fetched_and_moved_without_false_deletion(settings):
    store, client = TrackingStore(), FakeClient([{**activity(), "distance": 1000}])
    assert sync_module.run_sync(store, settings, client=client, backfill=False)["status"] == "ok"
    old_day = client.activities[0]["start_date_local"][:10]
    client.activities[0] = {**activity(days_ago=40), "distance": 2000}
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    moved = store.get("workouts", "intervals_a1")
    assert result["status"] == "ok" and result["counts"]["workouts_retired"] == 0
    assert not moved.get("source_deleted") and not moved.get("source_excluded")
    assert moved["local_date"] == client.activities[0]["start_date_local"][:10]
    assert aggregate(store, "day", old_day)["workout_count"] == 0
    assert aggregate(store, "day", moved["local_date"])["totals"]["distance_m"] == 2000


def test_absent_source_404_retires_workout_and_preserves_archived_original(settings):
    store, client = TrackingStore(), FakeClient([{**activity(), "distance": 1000}])
    sync_module.run_sync(store, settings, client=client, backfill=False)
    before = store.get("workouts", "intervals_a1")
    client.activities.clear()
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    retired = store.get("workouts", "intervals_a1")
    assert result["status"] == "ok" and result["counts"]["workouts_retired"] == 1
    assert retired["source_deleted"] is True
    assert retired["original_artifact"] == before["original_artifact"]
    assert retired["original_artifact"]["object"] in store.objects
    assert aggregate(store, "day", before["local_date"])["workout_count"] == 0


def test_absent_source_transient_failure_retains_last_verified_workout(settings, monkeypatch):
    store, client = TrackingStore(), FakeClient([{**activity(), "distance": 1000}])
    sync_module.run_sync(store, settings, client=client, backfill=False)
    before = store.get("workouts", "intervals_a1")
    client.activities.clear()
    def unavailable(_):
        raise IntervalsError("upstream_unavailable", retryable=True, status_code=503)
    monkeypatch.setattr(client, "get_activity", unavailable)
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    retained = store.get("workouts", "intervals_a1")
    assert result["status"] == "partial" and result["counts"]["workouts_retired"] == 0
    assert not retained.get("source_deleted") and not retained.get("source_excluded")
    assert retained["original_artifact"] == before["original_artifact"]
    assert any(error["stage"] == "activity_reconcile" for error in result["errors"])
    assert aggregate(store, "day", before["local_date"])["totals"]["distance_m"] == 1000


def test_historical_correction_repairs_saved_noncurrent_rolling_windows(settings):
    store = TrackingStore()
    item = doc(day="2026-09-14", distance=1000)
    service.save_workout(store, item["id"], item, merge=False)
    service.refresh_summaries(store, settings, date(2026, 9, 15))
    before = {period: aggregate(store, period, "2026-09-15")
              for period in ("rolling7", "rolling28")}
    service.save_workout(store, item["id"], {"metrics": {"distance_m": 2500, "moving_time_s": 900}})
    service.refresh_summaries(store, settings, date(2026, 9, 20))
    # Sept15 is neither today's ending date nor either previous comparison date.
    for period in ("rolling7", "rolling28"):
        repaired = aggregate(store, period, "2026-09-15")
        assert repaired["totals"]["distance_m"] == 2500
        assert repaired["totals"]["moving_time_s"] == 900
        assert repaired["workout_count"] == 1
        assert repaired["source_revision"] != before[period]["source_revision"]


@pytest.mark.parametrize("last_day,period", [
    (date(2026, 9, 20), "week"),  # Sunday remains an in-progress local day.
    (date(2026, 9, 30), "month"),  # Last day of a month is not complete early.
])
def test_last_calendar_day_is_partial_then_previous_bucket_closes(settings, last_day, period):
    store = TrackingStore()
    item = doc(day=last_day.isoformat())
    service.save_workout(store, item["id"], item, merge=False)
    service.refresh_summaries(store, settings, last_day)
    assert aggregate(store, period, last_day.isoformat())["period_in_progress"] is True
    next_day = last_day + timedelta(days=1)
    service.refresh_summaries(store, settings, next_day)
    previous = aggregate(store, period, last_day.isoformat())
    current = aggregate(store, period, next_day.isoformat())
    assert previous["period_in_progress"] is False
    assert previous["totals"]["distance_m"] == 1000
    assert current["period_in_progress"] is True
    assert current["workout_count"] == 0


def test_reconciliation_reads_next_page_before_boundary_workout_moves(settings):
    import time

    store = TrackingStore()
    old_day = (TODAY - timedelta(days=1)).isoformat()
    for index in range(201):
        identifier = f"intervals_run_{index:04d}"
        store.put("workouts", identifier, doc(identifier, day=old_day))
    # The200th snapshot is the date/id cursor of page1. Move it to a later date.
    # The201st record is absent upstream and must still be checked and retired.
    client = FakeClient([activity("run_0199", days_ago=-40)])
    importer = sync_module._Importer(store, settings, client, time.monotonic() + 60, {})
    seen = [{"id": f"run_{index:04d}"} for index in range(199)]
    assert importer.reconcile_missing(seen, TODAY - timedelta(days=1), TODAY) is True
    assert importer.missing_checks == 2
    assert store.get("workouts", "intervals_run_0199")["local_date"] == (TODAY + timedelta(days=40)).isoformat()
    assert store.get("workouts", "intervals_run_0200")["source_deleted"] is True
    assert importer.counts["workouts_retired"] == 1
