"""Zwift upload boundaries, archival publication, replay and coach projections."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from ai_coach import coach_context
from ai_coach import sync as sync_module
from ai_coach.fit_parser import ParsedActivity
from ai_coach.normalize import (
    NormalizationError, fit_upload_manufacturer, is_direct_garmin,
    is_garmin_upload_candidate, is_verified_activity_fit, normalize_activity,
)
from test_sync import FakeClient, MemoryStore, TODAY, activity


def zwift_activity(identifier="z1", **changes):
    return {**activity(identifier, source="UPLOAD"), "type": "VirtualRide",
            "name": "Synthetic Zwift ride", "device_name": "Zwift", "file_type": "fit",
            "strava_only": None, "distance": 32000, "icu_distance": 31980,
            "moving_time": 3600, "elapsed_time": 3620, "total_elevation_gain": 210,
            "average_heartrate": 132, "icu_average_watts": 185,
            "icu_training_load": 48, "icu_hr_zones": [140, 160, 190],
            "icu_hr_zone_times": [2700, 850, 50], **changes}


def fit_metadata(manufacturer=260, file_count=1):
    return {"format": "FIT", "crc_verified": True, "fit_file_count": file_count,
            "other_messages": {"file_id": [
                {"_file_index": index, "_fields": [
                    {"definition_number": 0, "raw_value": 4, "developer_data_index": None},
                    {"definition_number": 1, "raw_value": manufacturer, "developer_data_index": None}]}
                for index in range(file_count)]}}


def parsed_zwift():
    return ParsedActivity(records=[{"heart_rate": 132, "power": 185}],
                          laps=[{"total_distance": 32000}], metadata=fit_metadata())


@pytest.fixture
def settings():
    return SimpleNamespace(project="test", bucket="test", athlete_id="0",
                           timezone="Europe/Oslo", history_start_date="2000-01-01")


@pytest.mark.parametrize("device", ["Zwift", "ZWIFT", " Zwift Windows "])
def test_zwift_fit_label_is_candidate_but_requires_native_proof(device):
    payload = zwift_activity(device_name=device, file_type="FIT")
    assert fit_upload_manufacturer(payload) == "zwift"
    assert not is_direct_garmin(payload) and not is_garmin_upload_candidate(payload)
    with pytest.raises(NormalizationError):
        normalize_activity(payload, athlete_id="i42")
    with pytest.raises(NormalizationError):
        normalize_activity(payload, athlete_id="i42", upload_fit_metadata=fit_metadata(1))
    assert normalize_activity(payload, athlete_id="i42", upload_fit_metadata=fit_metadata())


@pytest.mark.parametrize("changes", [
    {"source": "STRAVA"}, {"source": "UNKNOWN"}, {"strava_only": True},
    {"device_name": "Zwiftish"}, {"device_name": "Tacx"}, {"device_name": None},
    {"file_type": "gpx"}, {"file_type": None},
])
def test_zwift_admission_rejects_strava_false_labels_and_non_fit(changes):
    payload = zwift_activity(**changes)
    assert fit_upload_manufacturer(payload) is None
    with pytest.raises(NormalizationError):
        normalize_activity(payload, athlete_id="i42", upload_fit_metadata=fit_metadata())


@pytest.mark.parametrize("invalid", [
    "no_crc", "not_fit", "no_files", "boolean_file_count", "wrong_manufacturer",
    "developer_manufacturer", "developer_type", "non_activity", "accessory_only",
    "duplicate_manufacturer", "missing_segment_id", "wrong_segment_manufacturer",
    "negative_segment", "boolean_segment",
])
def test_every_segment_needs_crc_and_native_zwift_activity_identity(invalid):
    metadata = fit_metadata(file_count=2)
    assert is_verified_activity_fit(metadata, "zwift")
    message = metadata["other_messages"]["file_id"][1]
    fields = message["_fields"]
    if invalid == "no_crc":
        metadata["crc_verified"] = False
    elif invalid == "not_fit":
        metadata["format"] = "GPX"
    elif invalid == "no_files":
        metadata["fit_file_count"] = 0
    elif invalid == "boolean_file_count":
        metadata["fit_file_count"] = True
    elif invalid in {"wrong_manufacturer", "wrong_segment_manufacturer"}:
        fields[1]["raw_value"] = 1
    elif invalid == "developer_manufacturer":
        fields[1]["developer_data_index"] = 0
    elif invalid == "developer_type":
        fields[0]["developer_data_index"] = 0
    elif invalid == "non_activity":
        fields[0]["raw_value"] = 6
    elif invalid == "accessory_only":
        metadata["other_messages"]["device_info"] = metadata["other_messages"].pop("file_id")
    elif invalid == "duplicate_manufacturer":
        fields.append(deepcopy(fields[1]))
    elif invalid == "missing_segment_id":
        metadata["other_messages"]["file_id"].pop()
    elif invalid == "negative_segment":
        message["_file_index"] = -1
    elif invalid == "boolean_segment":
        message["_file_index"] = True
    assert not is_verified_activity_fit(metadata, "zwift")


def test_virtual_ride_metrics_and_zones_preserve_units_and_zwift_attribution():
    doc = normalize_activity(zwift_activity(), athlete_id="i42", upload_fit_metadata=fit_metadata())
    assert doc["source"] == "intervals.icu" and doc["source_id"] == "z1"
    assert doc["provider_source"] == "UPLOAD" and doc["recording_platform"] == "zwift"
    assert doc["sport"] == "VirtualRide" and doc["distance_type"] == "virtual"
    assert "Zwift" in doc["source_attribution"] and doc["garmin_attribution"] is None
    assert doc["source_verification"]["manufacturer"] == "zwift"
    assert doc["metrics"]["distance_m"] == 31980
    assert doc["metrics"]["recorded_distance_m"] == 32000
    assert doc["metrics"]["moving_time_s"] == 3600
    assert doc["metrics"]["average_power_w"] == 185
    assert doc["metrics"]["average_heart_rate_bpm"] == 132
    assert doc["analysis"]["training_load"] == 48
    assert doc["zone_summary"]["heart_rate"]["seconds"] == [2700, 850, 50]


def test_zwift_original_is_durable_before_parse_and_publication_and_replay(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([zwift_activity()])

    def inspect(data, **kwargs):
        assert store.get("workouts", "intervals_z1") is None
        pending = store.get("sync_state", "zwift_upload_intervals_z1")
        assert pending["verification_status"] == "pending"
        assert pending["file_status"] == "archived"
        assert store.objects[pending["original_artifact"]["object"]] == data
        return parsed_zwift()

    monkeypatch.setattr(sync_module, "parse_activity_file", inspect)
    first = sync_module.run_sync(store, settings, client=client, backfill=False)
    doc = store.get("workouts", "intervals_z1")
    assert first["status"] == "ok" and first["counts"]["uploads_verified"] == 1
    assert doc["parse_status"] == "parsed" and doc["record_count"] == 1
    assert doc["source_verification_sha256"] == doc["original_artifact"]["sha256"]
    assert store.read_json(doc["parsed_artifact"])["records"] == [{"heart_rate": 132, "power": 185}]
    objects = dict(store.objects)
    second = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert second["status"] == "ok" and second["counts"]["workouts_unchanged"] == 1
    assert client.downloads == client.details == 1 and store.objects == objects


def test_rejected_zwift_original_is_retained_without_publish_or_repeat_download(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([zwift_activity()])
    monkeypatch.setattr(sync_module, "parse_activity_file", lambda *a, **kw:
                        ParsedActivity(records=[], laps=[], metadata=fit_metadata(1)))
    first = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert first["status"] == "ok_with_warnings"
    assert first["counts"]["uploads_rejected"] == 1
    assert first["warnings"][0]["code"] == "zwift_activity_file_id_not_verified"
    state = store.get("sync_state", "zwift_upload_intervals_z1")
    assert state["verification_status"] == "rejected"
    assert store.objects[state["original_artifact"]["object"]]
    assert store.get("workouts", "intervals_z1") is None
    second = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert second["counts"]["uploads_rejected"] == 1 and client.downloads == 1
    assert store.get("workouts", "intervals_z1") is None


@pytest.mark.parametrize("detail_change,retired_flag", [
    ({"source": "STRAVA"}, "source_excluded"),
    ({"device_name": "Garmin FR970"}, "source_excluded"),
    ({"strava_only": True}, "source_excluded"),
    ({"deleted": True}, "source_deleted"),
])
def test_changed_detail_retires_prior_zwift_and_refreshes_summary(settings, monkeypatch,
                                                                detail_change, retired_flag):
    store, client = MemoryStore(), FakeClient([zwift_activity()])
    monkeypatch.setattr(sync_module, "parse_activity_file", lambda *a, **kw: parsed_zwift())
    sync_module.run_sync(store, settings, client=client, backfill=False)
    original = store.get("workouts", "intervals_z1")["original_artifact"]
    client.activities[0]["updated"] = "synthetic-source-revision-2"
    monkeypatch.setattr(client, "get_activity", lambda _: detail_change)
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert result["counts"]["workouts_retired"] == 1
    assert result["counts"]["activities_excluded"] == 1
    assert client.downloads == 1
    doc = store.get("workouts", "intervals_z1")
    assert doc[retired_flag] is True and doc["original_artifact"] == original
    day = (TODAY - timedelta(days=1)).isoformat()
    assert store.get("training_summaries", f"day_{day}")["workout_count"] == 0


def test_saved_summary_and_coach_context_keep_virtual_riding_distinct(settings, monkeypatch):
    outdoor = {**activity("r1"), "type": "Ride", "distance": 25000, "moving_time": 3000}
    run = {**activity("r2"), "type": "Run", "distance": 5000, "moving_time": 1800}
    store, client = MemoryStore(), FakeClient([zwift_activity(), outdoor, run])
    monkeypatch.setattr(sync_module, "parse_activity_file", lambda *a, **kw: parsed_zwift())
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert result["status"] == "ok"
    day = (TODAY - timedelta(days=1)).isoformat()
    saved = store.get("training_summaries", f"day_{day}")
    sports = {row["sport"]: row for row in saved["by_sport"]}
    assert set(sports) == {"Ride", "Run", "VirtualRide"}
    assert sports["VirtualRide"]["totals"]["distance_m"] == 31980
    assert sports["VirtualRide"]["heart_rate_zones"]["groups"][0]["seconds"] == [2700, 850, 50]
    assert sports["Ride"]["totals"]["distance_m"] == 25000
    assert sports["Run"]["totals"]["distance_m"] == 5000
    context = coach_context.build_context(store, settings, days=42, upcoming=14,
                                           sync_status={"stale": False})
    zwift = next(row for row in context["workouts"] if row["id"] == "intervals_z1")
    assert zwift["recording_platform"] == "zwift" and zwift["distance_type"] == "virtual"
    assert "Zwift" in zwift["source_attribution"] and "Garmin" not in zwift["source_attribution"]
    assert "Zwift distances are virtual" in context["attribution"]


@pytest.mark.parametrize("origin,expected_source,expected_platform,expected_distance_type", [
    ({"source_attribution": "Zwift virtual activity, uploaded to Intervals.icu",
      "garmin_attribution": None, "recording_platform": "zwift", "distance_type": "virtual"},
     "Zwift virtual activity, uploaded to Intervals.icu", "zwift", "virtual"),
    ({"garmin_attribution": "Garmin device data via Intervals.icu"},
     "Garmin device data via Intervals.icu", None, None),
    ({}, "Intervals.icu", None, None),
])
def test_sample_api_uses_actual_source_and_virtual_distance_context(monkeypatch, origin,
                                         expected_source, expected_platform, expected_distance_type):
    from fastapi.testclient import TestClient
    from ai_coach import main
    from test_api import MemoryStore as ApiMemoryStore

    store = ApiMemoryStore()
    store.put("workouts", "intervals_z1", {"id": "intervals_z1", "local_date": "2026-09-14",
              "parse_status": "parsed", "parsed_artifact": {"object": "parsed-z1"}, **origin})
    store.artifacts["parsed-z1"] = {"records": [{"power": 185, "heart_rate": 132},
                                               {"power": 200, "heart_rate": 140}]}
    monkeypatch.setattr(main, "store", lambda: store)
    with TestClient(main.app) as client:
        response = client.get("/v1/workouts/intervals_z1/samples", params={"limit": 1, "fields": "power"})
    assert response.status_code == 200
    page = response.json()
    assert page["items"] == [{"power": 185}] and page["next_offset"] == 1
    assert page["source"] == expected_source
    assert page.get("recording_platform") == expected_platform
    assert page.get("distance_type") == expected_distance_type


def oversized_parser(*args, **kwargs):
    from ai_coach.fit_parser import ActivityFileTooLarge
    raise ActivityFileTooLarge("Synthetic decoded field limit")


def inspected_zwift():
    return {"metadata": fit_metadata(), "record_count": 125000, "lap_count": 12}


def test_oversized_zwift_publishes_verified_summary_preserves_original_and_replays(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from ai_coach import main

    store, client = MemoryStore(), FakeClient([zwift_activity()])
    inspection_calls = []

    def inspect(data, **kwargs):
        inspection_calls.append(data)
        assert store.get("workouts", "intervals_z1") is None
        pending = store.get("sync_state", "zwift_upload_intervals_z1")
        assert pending["file_status"] == "archived"
        assert store.objects[pending["original_artifact"]["object"]] == data
        return inspected_zwift()

    monkeypatch.setattr(sync_module, "parse_activity_file", oversized_parser)
    monkeypatch.setattr(sync_module, "inspect_activity_file", inspect)
    first = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert first["status"] == "ok_with_warnings"
    assert first["counts"]["uploads_verified"] == 1
    assert first["counts"]["workouts_imported"] == 1
    assert first["counts"]["files_parsed"] == 0
    assert first["counts"]["summary_only_imported"] == 1
    assert first["counts"]["parse_errors"] == 0
    assert first["warnings"] == [{"stage": "parse", "code": "summary_only_large_fit"}]
    doc = store.get("workouts", "intervals_z1")
    assert doc["parse_status"] == "summary_only"
    assert doc["sample_availability"] == "original_only"
    assert doc["sample_limit_reason"] == "activity_file_too_large"
    assert doc.get("parsed_artifact") is None
    assert doc["original_artifact"] and doc["inspected_artifact"]
    assert store.read_json(doc["inspected_artifact"]) == inspected_zwift()
    assert doc["source_verification"]["manufacturer"] == "zwift"
    assert doc["source_verification_sha256"] == doc["original_artifact"]["sha256"]
    assert doc["record_count"] == 125000 and doc["lap_count"] == 12
    assert doc["laps_summary"] == [] and doc["laps_summary_truncated"] is True
    assert doc["metrics"]["distance_m"] == 31980
    assert doc["metrics"]["moving_time_s"] == 3600
    day = (TODAY - timedelta(days=1)).isoformat()
    saved = store.get("training_summaries", f"day_{day}")
    assert saved["workout_count"] == 1
    assert saved["by_sport"][0]["sport"] == "VirtualRide"
    assert saved["totals"]["distance_m"] == 31980

    context = coach_context.build_context(store, settings, days=42, upcoming=14,
                                         sync_status={"stale": False})
    projected = next(row for row in context["workouts"] if row["id"] == "intervals_z1")
    assert projected["parse_status"] == "summary_only"
    assert projected["sample_availability"] == "original_only"
    assert projected["metrics"]["distance_m"] == 31980
    monkeypatch.setattr(main, "store", lambda: store)
    with TestClient(main.app) as api:
        details = api.get("/v1/workouts/intervals_z1")
        samples = api.get("/v1/workouts/intervals_z1/samples")
    assert details.status_code == 200
    assert samples.status_code == 409
    assert "summary is available" in samples.json()["detail"]
    assert "archived in full" in samples.json()["detail"]
    assert "items" not in samples.json()

    objects = dict(store.objects)
    second = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert second["counts"]["workouts_unchanged"] == 1
    assert second["counts"]["summary_only_imported"] == 0
    assert client.downloads == client.details == len(inspection_calls) == 1
    assert store.objects == objects


def test_previously_terminal_oversized_zwift_is_retried_once_for_new_inspector(settings, monkeypatch):
    from ai_coach.normalize import source_hash

    payload = zwift_activity()
    store, client = MemoryStore(), FakeClient([payload])
    original = store.archive("originals/intervals_z1", b"synthetic-fixture-original")
    store.put("sync_state", "zwift_upload_intervals_z1", {
        "source_summary_sha256": source_hash(payload),
        "parser_version": sync_module.PARSER_VERSION,
        "upload_verification_version": sync_module.UPLOAD_VERIFICATION_VERSION,
        "original_artifact": original, "parse_status": "error", "parse_attempts": 3,
        "parse_error": "activity_file_too_large", "verification_status": "pending"})
    monkeypatch.setattr(sync_module, "parse_activity_file", oversized_parser)
    monkeypatch.setattr(sync_module, "inspect_activity_file", lambda *a, **kw: inspected_zwift())
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert result["counts"]["parse_terminal"] == 0
    assert result["counts"]["summary_only_imported"] == 1
    assert client.downloads == 1
    doc = store.get("workouts", "intervals_z1")
    assert doc["parse_status"] == "summary_only" and doc["original_artifact"] == original
    assert doc["parse_attempts"] == 4
    state = store.get("sync_state", "zwift_upload_intervals_z1")
    assert state["inspection_version"] == sync_module.OVERSIZE_INSPECTION_VERSION
    assert state["verification_status"] == "verified"


@pytest.mark.parametrize("failure", ["crc_error", "crc_unverified", "garmin_proof"])
def test_oversized_inspection_never_bypasses_corruption_or_source_proof(settings, monkeypatch, failure):
    from ai_coach.fit_parser import InvalidActivityFile

    store, client = MemoryStore(), FakeClient([zwift_activity()])

    def inspect(*args, **kwargs):
        if failure == "crc_error":
            raise InvalidActivityFile("Synthetic CRC mismatch")
        inspected = inspected_zwift()
        if failure == "crc_unverified":
            inspected["metadata"]["crc_verified"] = False
        else:
            inspected["metadata"] = fit_metadata(1)
        return inspected

    monkeypatch.setattr(sync_module, "parse_activity_file", oversized_parser)
    monkeypatch.setattr(sync_module, "inspect_activity_file", inspect)
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert result["counts"]["workouts_imported"] == 0
    assert result["counts"]["summary_only_imported"] == 0
    assert store.get("workouts", "intervals_z1") is None
    state = store.get("sync_state", "zwift_upload_intervals_z1")
    assert store.objects[state["original_artifact"]["object"]]
    if failure == "crc_error":
        assert result["status"] == "partial" and result["counts"]["parse_errors"] == 1
        assert state["parse_error"] == "invalid_activity_file"
    else:
        assert result["counts"]["uploads_rejected"] == 1
        assert state["verification_status"] == "rejected"


def test_oversized_garmin_upload_does_not_use_zwift_summary_fallback(settings, monkeypatch):
    store, client = MemoryStore(), FakeClient([zwift_activity(device_name="Garmin FR970")])
    monkeypatch.setattr(sync_module, "parse_activity_file", oversized_parser)

    def forbidden_inspection(*args, **kwargs):
        pytest.fail("The oversized summary fallback is only enabled for Zwift")

    monkeypatch.setattr(sync_module, "inspect_activity_file", forbidden_inspection)
    result = sync_module.run_sync(store, settings, client=client, backfill=False)
    assert result["status"] == "partial"
    assert result["counts"]["parse_errors"] == 1 and result["counts"]["summary_only_imported"] == 0
    assert store.get("workouts", "intervals_z1") is None
    state = store.get("sync_state", "garmin_upload_intervals_z1")
    assert state["parse_error"] == "activity_file_too_large" and state["original_artifact"]
