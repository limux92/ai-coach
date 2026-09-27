"""Synthetic tests of replay, provenance, durable cursors and plan preservation."""
import copy
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from ai_coach import sync as module
from ai_coach.fit_parser import InvalidActivityFile, ParsedActivity
from ai_coach.intervals_client import IntervalsError, OriginalFile
from test_normalize import garmin_fit_metadata


class MemoryStore:
    def __init__(self):
        self.docs = {}
        self.objects = {}
        self.lease = None
        self.fail_archive = False

    def get(self, collection, doc_id):
        return copy.deepcopy(self.docs.get((collection, doc_id)))

    def put(self, collection, doc_id, data, *, merge=True):
        old = self.docs.get((collection, doc_id), {}) if merge else {}
        self.docs[(collection, doc_id)] = copy.deepcopy({**old, **data})

    def scan(self, collection, *, limit=200, after=None, pending=None):
        rows = [dict(copy.deepcopy(doc), id=identifier)
                for (name, identifier), doc in self.docs.items()
                if name == collection and (pending is None or doc.get("pending") is pending)]
        rows.sort(key=lambda row: row["id"])
        if after:
            if (collection, after) not in self.docs:
                raise ValueError("Unknown pagination cursor")
            rows = [row for row in rows if row["id"] > after]
        return rows[:limit]

    def list(self, collection, oldest, newest, *, limit=200, after=None):
        rows = [dict(copy.deepcopy(doc), id=identifier)
                for (name, identifier), doc in self.docs.items()
                if name == collection and oldest <= doc.get("local_date", "") <= newest]
        rows.sort(key=lambda row: (row["local_date"], row["id"]))
        if after:
            cursor = self.get(collection, after)
            if cursor is None:
                raise ValueError("Unknown pagination cursor")
            key = (cursor["local_date"], after)
            rows = [row for row in rows if (row["local_date"], row["id"]) > key]
        return rows[:limit]

    def read_json(self, artifact):
        if artifact["bucket"] != "test":
            raise ValueError("Artifact is outside this archive")
        return json.loads(gzip.decompress(self.objects[artifact["object"]]))

    def archive(self, path, data, *, content_type="application/octet-stream"):
        if self.fail_archive:
            self.fail_archive = False
            raise OSError("synthetic storage outage")
        digest = hashlib.sha256(data).hexdigest()
        name = f"{path}/{digest}"
        self.objects[name] = data
        return {"bucket": "test", "object": name, "sha256": digest, "bytes": len(data)}

    def archive_json(self, path, value):
        return self.archive(path, gzip.compress(json.dumps(value, sort_keys=True).encode(), mtime=0))

    def acquire_lease(self, owner, *, seconds):
        assert seconds == 840
        if self.lease:
            return False
        self.lease = owner
        return True

    def release_lease(self, owner, updates):
        assert self.lease == owner
        self.put("sync_state", "intervals", updates)
        self.lease = None


class FakeClient:
    def __init__(self, activities=(), events=()):
        self.activities = list(activities)
        self.events = list(events)
        self.details = 0
        self.downloads = 0
        self.plan_fetches = 0
        self.fail_history = False
        self.auth_failure = False

    def athlete(self):
        return {"id": "i42"}

    def list_activities(self, oldest, newest):
        if self.fail_history and oldest < TODAY - timedelta(days=module.RECENT_DAYS - 1):
            raise IntervalsError("upstream_unavailable", retryable=True, status_code=503)
        return [copy.deepcopy(p) for p in self.activities
                if oldest.isoformat() <= p["start_date_local"][:10] <= newest.isoformat()]

    def get_activity(self, activity_id):
        self.details += 1
        found = next((p for p in self.activities if p["id"] == activity_id), None)
        if found is None:
            raise IntervalsError("not_found", status_code=404)
        return copy.deepcopy(found)

    def download_original(self, activity_id):
        self.downloads += 1
        if self.auth_failure:
            raise IntervalsError("authentication", status_code=401)
        data = b"synthetic-fixture-original"
        return OriginalFile(data, "application/octet-stream", "test.fit", None,
                            hashlib.sha256(data).hexdigest())

    def list_wellness(self, oldest, newest):
        return []

    def list_events(self, oldest, newest):
        self.plan_fetches += 1
        return copy.deepcopy(self.events)


TODAY = datetime.now(ZoneInfo("Europe/Oslo")).date()


@pytest.fixture
def settings():
    return SimpleNamespace(project="test", bucket="test", athlete_id="0",
                           timezone="Europe/Oslo", history_start_date="2000-01-01")


@pytest.fixture(autouse=True)
def successful_parser(monkeypatch):
    monkeypatch.setattr(module, "parse_activity_file", lambda *args, **kwargs: ParsedActivity(
        records=[{"timestamp": "2026-09-15T10:00:00+00:00", "heart_rate": 140}],
        laps=[{"total_distance": 1000, "avg_heart_rate": 140}], metadata={"crc_verified": True}))


def activity(identifier="a1", days_ago=1, source="GARMIN_CONNECT"):
    return {"id": identifier, "source": source, "type": "Run", "name": "Synthetic run",
            "start_date_local": f"{TODAY - timedelta(days=days_ago)}T10:00:00",
            "start_date": f"{TODAY - timedelta(days=days_ago)}T08:00:00Z"}


def upload(identifier="a1", days_ago=1):
    return {**activity(identifier, days_ago, source="UPLOAD"), "file_type": "fit",
            "device_name": "GARMIN FR970", "strava_only": None}


def verified_parser(*args, **kwargs):
    return ParsedActivity(records=[{"heart_rate": 140}], laps=[{"total_distance": 1000}],
                          metadata=garmin_fit_metadata())


def test_manual_upload_is_archived_then_verified_before_publication_and_replayed(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([upload()])
    def inspect_original(data, **kwargs):
        assert store.get("workouts", "intervals_a1") is None
        pending = store.get("sync_state", "garmin_upload_intervals_a1")
        assert pending["verification_status"] == "pending"
        assert store.objects[pending["original_artifact"]["object"]] == data
        return verified_parser()
    monkeypatch.setattr(module, "parse_activity_file", inspect_original)
    first = module.run_sync(store, settings, client=client, backfill=False)
    doc = store.get("workouts", "intervals_a1")
    assert first["status"] == "ok" and first["counts"]["uploads_verified"] == 1
    assert doc["provider_source"] == "UPLOAD" and doc["import_method"] == "manual_upload"
    assert doc["source_verification_sha256"] == doc["original_artifact"]["sha256"]
    assert doc["parse_status"] == "parsed" and doc["record_count"] == 1
    objects = dict(store.objects)
    second = module.run_sync(store, settings, client=client, backfill=False)
    assert second["status"] == "ok" and second["counts"]["workouts_unchanged"] == 1
    assert client.downloads == client.details == 1 and objects == store.objects


def test_rejected_upload_retains_original_without_workout_and_is_not_redownloaded(settings):
    # Default mocked parser intentionally has no Garmin file_id proof.
    store, client = MemoryStore(), FakeClient([upload()])
    first = module.run_sync(store, settings, client=client, backfill=False)
    assert first["status"] == "ok_with_warnings"
    assert first["counts"]["uploads_rejected"] == 1
    assert first["warnings"][0]["code"] == "garmin_activity_file_id_not_verified"
    pending = store.get("sync_state", "garmin_upload_intervals_a1")
    assert pending["verification_status"] == "rejected" and pending["original_artifact"]
    assert store.get("workouts", "intervals_a1") is None
    second = module.run_sync(store, settings, client=client, backfill=False)
    assert second["counts"]["uploads_rejected"] == 1 and client.downloads == 1
    assert store.get("workouts", "intervals_a1") is None


def test_upload_parse_errors_keep_original_quarantined_and_stop_after_three(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([upload()])
    def reject(data, **kwargs):
        pending = store.get("sync_state", "garmin_upload_intervals_a1")
        assert store.objects[pending["original_artifact"]["object"]] == data
        raise InvalidActivityFile("synthetic corrupt upload")
    monkeypatch.setattr(module, "parse_activity_file", reject)
    for attempt in range(1, 4):
        result = module.run_sync(store, settings, client=client, backfill=False)
        assert result["status"] == "partial"
        assert result["counts"]["workouts_imported"] == 0
        assert store.get("sync_state", "garmin_upload_intervals_a1")["parse_attempts"] == attempt
        assert store.get("workouts", "intervals_a1") is None
    result = module.run_sync(store, settings, client=client, backfill=False)
    assert result["status"] == "ok_with_warnings" and result["counts"]["parse_terminal"] == 1
    assert client.downloads == 3


def test_upload_download_failure_retains_pending_evidence_then_recovers(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([upload()])
    monkeypatch.setattr(module, "parse_activity_file", verified_parser)
    client.auth_failure = True
    with pytest.raises(IntervalsError):
        module.run_sync(store, settings, client=client, backfill=False)
    assert store.get("workouts", "intervals_a1") is None
    pending = store.get("sync_state", "garmin_upload_intervals_a1")
    assert pending["file_status"] == "error" and pending["file_error"] == "authentication"
    client.auth_failure = False
    result = module.run_sync(store, settings, client=client, backfill=False)
    assert result["status"] == "ok" and store.get("workouts", "intervals_a1")["parse_status"] == "parsed"


def test_upload_detail_changing_to_strava_is_excluded_before_archival(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([upload()])
    monkeypatch.setattr(client, "get_activity", lambda _: {"source": "STRAVA"})
    result = module.run_sync(store, settings, client=client, backfill=False)
    assert result["counts"]["activities_excluded"] == 1
    assert client.downloads == 0 and not store.objects
    assert store.get("workouts", "intervals_a1") is None


def test_source_edit_retries_previously_rejected_upload(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([upload()])
    module.run_sync(store, settings, client=client, backfill=False)
    client.activities[0]["updated"] = "2026-09-15T12:00:00Z"
    monkeypatch.setattr(module, "parse_activity_file", verified_parser)
    result = module.run_sync(store, settings, client=client, backfill=False)
    assert result["status"] == "ok" and result["counts"]["uploads_verified"] == 1
    assert client.downloads == 2


def test_replay_reuses_original_and_parsed_artifacts(settings):
    store, client = MemoryStore(), FakeClient([activity()])
    first = module.run_sync(store, settings, client=client, backfill=False)
    artifacts = dict(store.objects)
    original = store.get("workouts", "intervals_a1")["original_artifact"]
    second = module.run_sync(store, settings, client=client, backfill=False)
    assert first["status"] == second["status"] == "ok"
    assert client.downloads == client.details == 1
    assert artifacts == store.objects
    assert second["counts"]["workouts_unchanged"] == 1
    assert store.get("workouts", "intervals_a1")["original_artifact"] == original
    assert client.plan_fetches == 1


def test_strava_and_unknown_sources_excluded_before_any_archive(settings):
    store = MemoryStore()
    client = FakeClient([activity(source="STRAVA"), activity("a2", source="UNKNOWN")])
    result = module.run_sync(store, settings, client=client, backfill=False)
    assert result["counts"]["activities_excluded"] == 2
    assert client.downloads == client.details == 0
    assert not store.objects
    assert not any(collection == "workouts" for collection, _ in store.docs)


@pytest.mark.parametrize("failure", ["fetch", "archive"])
def test_failed_history_window_does_not_advance_and_retries(settings, failure):
    store, client = MemoryStore(), FakeClient([activity(days_ago=module.RECENT_DAYS + 6)])
    initial_cursor = (TODAY - timedelta(days=module.RECENT_DAYS)).isoformat()
    store.put("sync_state", "intervals", {"backfill_cursor": initial_cursor})
    client.fail_history = failure == "fetch"
    store.fail_archive = failure == "archive"
    failed = module.run_sync(store, settings, client=client)
    assert failed["status"] == "partial"
    assert store.get("sync_state", "intervals")["backfill_cursor"] == initial_cursor
    assert store.lease is None
    client.fail_history = False
    success = module.run_sync(store, settings, client=client)
    assert success["status"] == "ok"
    assert store.get("sync_state", "intervals")["backfill_cursor"] < initial_cursor
    assert store.get("workouts", "intervals_a1")["parse_status"] == "parsed"


def test_parse_failure_retains_original_and_stops_retrying_after_three(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([activity()])
    def reject(data, **kwargs):
        # Verify archive durability before the parser is entered.
        original = store.get("workouts", "intervals_a1")["original_artifact"]
        assert store.objects[original["object"]] == data
        raise InvalidActivityFile("synthetic corrupt input")
    monkeypatch.setattr(module, "parse_activity_file", reject)
    for attempt in range(1, 4):
        result = module.run_sync(store, settings, client=client, backfill=False)
        assert result["status"] == "partial"
        assert store.get("workouts", "intervals_a1")["parse_attempts"] == attempt
    result = module.run_sync(store, settings, client=client, backfill=False)
    assert result["status"] == "ok_with_warnings"
    assert result["counts"]["parse_terminal"] == 1
    assert client.downloads == 3
    assert store.get("workouts", "intervals_a1")["parse_status"] == "error"


def test_plan_import_links_completion_and_preserves_it_on_source_edit(settings):
    completed = {**activity(), "paired_event_id": 12}
    event = {"id": 12, "category": "WORKOUT", "name": "Planned intervals",
             "start_date_local": completed["start_date_local"],
             "workout_doc": {"steps": [{"duration": 120}], "duration": 120}}
    store, client = MemoryStore(), FakeClient([completed], [event])
    module.run_sync(store, settings, client=client, backfill=False)
    plan = store.get("planned_workouts", "intervals_12")
    assert plan["status"] == "completed" and plan["completed_workout_id"] == "intervals_a1"
    client.events[0]["name"] = "Edited planned intervals"
    store.put("sync_state", "intervals", {"last_plans_success_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    module.run_sync(store, settings, client=client, backfill=False)
    plan = store.get("planned_workouts", "intervals_12")
    assert plan["name"] == "Edited planned intervals"
    assert plan["status"] == "completed" and plan["completed_workout_id"] == "intervals_a1"


def test_authentication_failure_propagates_and_releases_lease(settings):
    store, client = MemoryStore(), FakeClient([activity()])
    client.auth_failure = True
    with pytest.raises(IntervalsError) as error:
        module.run_sync(store, settings, client=client, backfill=False)
    assert error.value.status_code == 401
    assert store.lease is None
    state = store.get("sync_state", "intervals")
    assert state["last_status"] == "partial"
    assert "last_success_at" not in state
    assert store.get("workouts", "intervals_a1")["file_status"] == "error"


def test_busy_lease_does_not_touch_provider(settings):
    store = MemoryStore()
    store.lease = "other-run"
    result = module.run_sync(store, settings, client=FakeClient(), backfill=False)
    assert result["status"] == "already_running"
    assert store.lease == "other-run"


def test_terminal_bad_fit_cannot_block_historical_cursor_forever(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([activity(days_ago=module.RECENT_DAYS + 6)])
    initial_cursor = (TODAY - timedelta(days=module.RECENT_DAYS)).isoformat()
    store.put("sync_state", "intervals", {"backfill_cursor": initial_cursor})
    def reject(*args, **kwargs):
        raise InvalidActivityFile("synthetic bad FIT")
    monkeypatch.setattr(module, "parse_activity_file", reject)
    for attempt in range(3):
        result = module.run_sync(store, settings, client=client)
        assert result["status"] == "partial"
        cursor = store.get("sync_state", "intervals")["backfill_cursor"]
        assert (cursor == initial_cursor) if attempt < 2 else (cursor < initial_cursor)
    assert store.get("workouts", "intervals_a1")["original_artifact"]
    assert store.get("workouts", "intervals_a1")["parse_attempts"] == 3


def test_budget_exhaustion_keeps_cursor_and_releases_lease(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([activity(days_ago=20)])
    cursor = (TODAY - timedelta(days=14)).isoformat()
    store.put("sync_state", "intervals", {"backfill_cursor": cursor})
    monkeypatch.setattr(module, "RUN_BUDGET_SECONDS", 0)
    result = module.run_sync(store, settings, client=client)
    assert result["status"] == "partial"
    assert store.get("sync_state", "intervals")["backfill_cursor"] == cursor
    assert store.lease is None and client.downloads == 0


def test_absent_calendar_event_never_deletes_imported_plan(settings):
    event = {"id": 12, "category": "WORKOUT", "name": "Planned intervals",
             "start_date_local": f"{TODAY}T10:00:00"}
    store, client = MemoryStore(), FakeClient(events=[event])
    module.run_sync(store, settings, client=client, backfill=False)
    client.events.clear()
    store.put("sync_state", "intervals", {"last_plans_success_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    module.run_sync(store, settings, client=client, backfill=False)
    assert store.get("planned_workouts", "intervals_12")["status"] == "planned"


@pytest.mark.parametrize("iso_watermark", [False, True])
def test_outage_longer_than_lookback_recovers_after_backfill_finished(settings, iso_watermark):
    store, client = MemoryStore(), FakeClient([activity(days_ago=40)])
    watermark = datetime.now(timezone.utc) - timedelta(days=45)
    value = watermark.isoformat().replace("+00:00", "Z") if iso_watermark else watermark
    store.put("sync_state", "intervals", {"last_success_at": value, "backfill_complete": True})
    result = module.run_sync(store, settings, client=client)
    assert result["status"] == "ok"
    assert store.get("workouts", "intervals_a1")["parse_status"] == "parsed"
    assert client.downloads == 1
    assert store.get("sync_state", "intervals")["last_success_at"] > watermark


def test_failed_outage_recovery_does_not_advance_watermark(settings):
    store, client = MemoryStore(), FakeClient([activity(days_ago=40)])
    watermark = datetime.now(timezone.utc) - timedelta(days=45)
    store.put("sync_state", "intervals", {"last_success_at": watermark, "backfill_complete": True})
    client.fail_history = True
    failed = module.run_sync(store, settings, client=client)
    assert failed["status"] == "partial"
    assert store.get("sync_state", "intervals")["last_success_at"] == watermark
    client.fail_history = False
    module.run_sync(store, settings, client=client)
    assert store.get("workouts", "intervals_a1")["parse_status"] == "parsed"
    assert store.get("sync_state", "intervals")["last_success_at"] > watermark
