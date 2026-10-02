"""Private API behavior tests; no Google/Intervals services are contacted."""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ai_coach import main
from ai_coach.config import Settings
from ai_coach.storage import safe_id


def test_static_health_never_reads_training_store(monkeypatch):
    monkeypatch.setattr(main, "store", lambda: pytest.fail("Static health must not read data"))
    with TestClient(main.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "ai-coach-data", "status": "ok", "version": "0.1.0"}


class MemoryStore:
    def __init__(self):
        self.documents = {}
        self.artifacts = {}

    def get(self, collection, doc_id):
        safe_id(doc_id)
        return copy.deepcopy(self.documents.get((collection, doc_id)))

    def put(self, collection, doc_id, data, *, merge=True):
        safe_id(doc_id)
        if merge:
            old = self.documents.get((collection, doc_id), {})
            self.documents[(collection, doc_id)] = {**old, **copy.deepcopy(data)}
        else:
            self.documents[(collection, doc_id)] = copy.deepcopy(data)

    def list(self, collection, oldest, newest, *, limit=200, after=None):
        rows = [dict(copy.deepcopy(row), id=doc_id)
                for (name, doc_id), row in self.documents.items()
                if name == collection and oldest <= row.get("local_date", "") <= newest]
        rows.sort(key=lambda item: (item["local_date"], item["id"]))
        if after:
            cursor = self.get(collection, after)
            if cursor is None:
                raise ValueError("Unknown pagination cursor")
            key = (cursor["local_date"], after)
            rows = [row for row in rows if (row["local_date"], row["id"]) > key]
        return rows[:limit]

    def read_json(self, artifact):
        return copy.deepcopy(self.artifacts[artifact["object"]])


@pytest.fixture
def api(monkeypatch):
    memory = MemoryStore()
    monkeypatch.setattr(main, "store", lambda: memory)
    monkeypatch.setattr(main, "settings", lambda: Settings("test-project", "test-bucket"))
    with TestClient(main.app) as client:
        yield client, memory


def plan_body(**changes):
    return {
        "local_date": "2026-10-01", "name": "Threshold repetitions", "sport": "Run",
        "start_time": "17:30", "steps": [{"duration_seconds": 360, "repetitions": 5}],
        "targets": {"effort": "threshold"}, **changes,
    }


def test_create_query_update_preserves_plan_identity_and_created_time(api):
    client, memory = api
    response = client.post("/v1/planned-workouts", json=plan_body())
    assert response.status_code == 201
    created = response.json()
    assert created["id"].startswith("local-")
    assert created["source"] == "ai_coach"
    assert created["timezone"] == "Europe/Oslo"
    assert created["start_date_local"] == "2026-10-01T17:30:00"
    assert json.loads(created["structured_workout_json"])["steps"] == plan_body()["steps"]
    response = client.get("/v1/planned-workouts", params={"oldest": "2026-10-01", "newest": "2026-10-01"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [created["id"]]
    response = client.put(f"/v1/planned-workouts/{created['id']}",
                          json=plan_body(name="Easy run", steps=[], targets={}, start_time=None))
    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == created["id"]
    assert updated["created_at"] == created["created_at"]
    assert updated["name"] == "Easy run"
    assert updated["start_date_local"] == "2026-10-01T00:00:00"
    assert json.loads(updated["structured_workout_json"]) == {"steps": [], "targets": {}}
    assert len(memory.documents) == 1


def test_imported_plan_is_readable_but_cannot_be_overwritten(api):
    client, memory = api
    imported = {"id": "intervals-123", "source": "intervals", "local_date": "2026-10-01",
                "name": "Original imported plan", "provider_payload": "preserve"}
    memory.put("planned_workouts", imported["id"], imported)
    response = client.put("/v1/planned-workouts/intervals-123", json=plan_body())
    assert response.status_code == 409
    assert memory.get("planned_workouts", imported["id"]) == imported
    page = client.get("/v1/planned-workouts", params={"oldest": "2026-10-01", "newest": "2026-10-01"}).json()
    assert page["items"][0]["name"] == imported["name"]


def test_completed_workout_link_requires_an_existing_workout(api):
    client, memory = api
    payload = plan_body(status="completed", completed_workout_id="garmin-123")
    response = client.post("/v1/planned-workouts", json=payload)
    assert response.status_code == 422
    assert memory.documents == {}
    memory.put("workouts", "garmin-123", {"id": "garmin-123", "local_date": "2026-10-01"})
    response = client.post("/v1/planned-workouts", json=payload)
    assert response.status_code == 201
    assert response.json()["completed_workout_id"] == "garmin-123"


@pytest.mark.parametrize("bad_id", ["bad/id", "space id", "x" * 181, "", ".", "..", "__reserved__"])
def test_invalid_completed_links_are_rejected_before_writing(api, bad_id):
    client, memory = api
    response = client.post("/v1/planned-workouts", json=plan_body(completed_workout_id=bad_id))
    assert response.status_code == 422
    assert memory.documents == {}


@pytest.mark.parametrize("changes", [
    {"local_date": "2026-02-30"}, {"local_date": "not-a-date"},
    {"name": ""}, {"sport": ""}, {"start_time": "24:00"},
    {"start_time": "12:60"}, {"start_time": "7:30"},
    {"status": "unknown"}, {"steps": [{}] * 501},
    {"targets": {"text": "x" * 200_001}},
])
def test_invalid_or_oversized_plans_do_not_write(api, changes):
    client, memory = api
    assert client.post("/v1/planned-workouts", json=plan_body(**changes)).status_code == 422
    assert memory.documents == {}


@pytest.mark.parametrize("path", ["/v1/workouts", "/v1/planned-workouts", "/v1/wellness"])
def test_date_query_rejects_invalid_or_reversed_dates(api, path):
    client, _ = api
    assert client.get(path, params={"oldest": "2026-10-02", "newest": "2026-10-01"}).status_code == 422
    assert client.get(path, params={"oldest": "2026-02-30", "newest": "2026-10-01"}).status_code == 422
    assert client.get(path, params={"oldest": "2026-10-01", "newest": "2026-10-02", "limit": 0}).status_code == 422
    assert client.get(path, params={"oldest": "2026-10-01", "newest": "2026-10-02", "limit": 501}).status_code == 422


@pytest.mark.parametrize("collection,path", [
    ("workouts", "/v1/workouts"), ("planned_workouts", "/v1/planned-workouts"),
    ("wellness", "/v1/wellness"),
])
def test_pagination_with_same_day_ties_has_no_duplicates_or_omissions(api, collection, path):
    client, memory = api
    for identifier, day in [("c", "2026-10-02"), ("b", "2026-10-01"),
                            ("a", "2026-10-01"), ("outside", "2026-09-30")]:
        memory.put(collection, identifier, {"id": identifier, "local_date": day})
    params = {"oldest": "2026-10-01", "newest": "2026-10-02", "limit": 1}
    ids = []
    for _ in range(4):
        response = client.get(path, params=params)
        assert response.status_code == 200
        page = response.json()
        ids.extend(item["id"] for item in page["items"])
        if page["next_cursor"] is None:
            break
        params["after"] = page["next_cursor"]
    assert ids == ["a", "b", "c"]
    assert page["next_cursor"] is None
    params["after"] = "unknown"
    assert client.get(path, params=params).status_code == 422
    params["after"] = "bad/id"
    assert client.get(path, params=params).status_code == 422


def test_missing_plan_and_workout_have_clear_404(api):
    client, _ = api
    assert client.put("/v1/planned-workouts/missing", json=plan_body()).status_code == 404
    assert client.get("/v1/workouts/missing").status_code == 404
    assert client.get("/v1/workouts/bad%20id").status_code == 422


def test_samples_require_parsed_file_and_paginate_without_false_empty_success(api):
    client, memory = api
    memory.put("workouts", "abc", {"id": "abc", "local_date": "2026-10-01", "parse_status": "failed"})
    assert client.get("/v1/workouts/abc/samples").status_code == 409
    memory.put("workouts", "abc", {"parsed_artifact": {"object": "parsed-file"}, "garmin_attribution": "Garmin"})
    memory.artifacts["parsed-file"] = {"records": [{"timestamp": number} for number in range(3)]}
    response = client.get("/v1/workouts/abc/samples", params={"limit": 2})
    assert response.status_code == 200
    assert response.json() == {"items": [{"timestamp": 0}, {"timestamp": 1}], "total": 3,
                               "next_offset": 2, "source": "Garmin"}
    response = client.get("/v1/workouts/abc/samples", params={"limit": 2, "offset": 2})
    assert response.json()["items"] == [{"timestamp": 2}]
    assert response.json()["next_offset"] is None
    assert client.get("/v1/workouts/abc/samples", params={"offset": -1}).status_code == 422


def test_status_marks_fresh_and_stale_sync_without_mutating_stored_state(api):
    client, memory = api
    assert client.get("/v1/status").json()["stale"] is True
    state = {"last_success_at": datetime.now(timezone.utc), "lease_owner": "internal-owner"}
    memory.put("sync_state", "intervals", state)
    response = client.get("/v1/status")
    assert response.status_code == 200
    assert response.json()["stale"] is False
    assert "lease_owner" not in response.json()
    assert memory.get("sync_state", "intervals")["lease_owner"] == "internal-owner"
    memory.put("sync_state", "intervals", {"last_success_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    assert client.get("/v1/status").json()["stale"] is True


def test_status_does_not_expose_unknown_internal_diagnostics_or_credentials(api):
    client, memory = api
    # Defensive contract: new importer fields must not become public by default.
    memory.put("sync_state", "intervals", {
        "last_success_at": datetime.now(timezone.utc),
        "api_key": "test-secret-value", "authorization": "Basic test-credential",
        "upstream_response": {"athlete_private_payload": "never-show-this"},
    })
    response = client.get("/v1/status")
    assert response.status_code == 200
    assert "test-secret-value" not in response.text
    assert "test-credential" not in response.text
    assert "never-show-this" not in response.text


def test_sync_exception_does_not_leak_secret_or_payload_in_response_or_logs(api, monkeypatch, caplog):
    client, _ = api
    def fail(*args, **kwargs):
        raise RuntimeError("test-secret-value private-lactate-note")
    monkeypatch.setattr(main, "run_sync", fail)
    with caplog.at_level(logging.ERROR, logger="ai_coach"):
        response = client.post("/internal/sync", json={"backfill": False})
    assert response.status_code == 503
    assert "test-secret-value" not in response.text + caplog.text
    assert "private-lactate-note" not in response.text + caplog.text
    assert "RuntimeError" in caplog.text


def test_partial_sync_signals_retry_and_passes_backfill_choice(api, monkeypatch):
    client, memory = api
    calls = []
    def partial(store, settings, *, backfill):
        calls.append((store, backfill))
        return {"status": "partial", "retry": True}
    monkeypatch.setattr(main, "run_sync", partial)
    response = client.post("/internal/sync", json={"backfill": False})
    assert response.status_code == 503
    assert calls == [(memory, False)]


@pytest.mark.parametrize("identifier", [".", "..", "__reserved__", "bad/id", "space id"])
def test_storage_rejects_firestore_invalid_document_ids(identifier):
    with pytest.raises(ValueError):
        safe_id(identifier)


def test_settings_contain_resource_configuration_without_loading_secrets(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCS_BUCKET", "test-bucket")
    monkeypatch.setenv("INTERVALS_API_KEY", "test-secret-value")
    settings = Settings.from_env()
    assert settings.project == "test-project"
    assert settings.bucket == "test-bucket"
    assert "test-secret-value" not in repr(settings)


def test_observation_links_a_measurement_to_a_real_workout_and_can_be_queried(api):
    client, memory = api
    payload = {"local_date": "2026-10-01", "workout_id": "garmin-abc", "lap_index": 2,
               "elapsed_seconds": 1400.5, "lactate_mmol_l": 2.4, "rpe": 6.0, "notes": "After repetition"}
    assert client.post("/v1/observations", json=payload).status_code == 422
    assert memory.documents == {}
    memory.put("workouts", "garmin-abc", {"id": "garmin-abc", "local_date": "2026-10-01"})
    response = client.post("/v1/observations", json=payload)
    assert response.status_code == 201
    assert response.json()["source"] == "user"
    response = client.get("/v1/observations", params={"oldest": "2026-10-01", "newest": "2026-10-01"})
    assert response.status_code == 200
    assert response.json()["items"][0]["lactate_mmol_l"] == 2.4
    assert response.json()["items"][0]["lap_index"] == 2


@pytest.mark.parametrize("changes", [
    {"rpe": 11}, {"lactate_mmol_l": -1}, {"elapsed_seconds": -1}, {"lap_index": -1},
    {"workout_id": ""}, {"workout_id": "bad/id"}, {"local_date": "not-a-date"},
])
def test_invalid_observation_measurements_do_not_write(api, changes):
    client, memory = api
    response = client.post("/v1/observations", json={"local_date": "2026-10-01", **changes})
    assert response.status_code == 422
    assert memory.documents == {}


def test_observation_rejects_a_numeric_overflow_in_elapsed_time(api):
    client, memory = api
    response = client.post("/v1/observations", content='{"local_date":"2026-10-01","elapsed_seconds":1e309}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert memory.documents == {}


@pytest.mark.parametrize("path,payload", [
    ("/v1/planned-workouts", '{"local_date":"2026-10-01","name":"Run","sport":"Run","targets":{"pace":1e309}}'),
    ("/v1/observations", '{"local_date":"2026-10-01","rpe":1e309}'),
])
def test_numeric_validation_error_is_a_safe_422_response(api, path, payload):
    client, memory = api
    response = client.post(path, content=payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert memory.documents == {}


def test_status_reports_missing_and_configured_key_without_exposing_it(api, monkeypatch):
    client, _ = api
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    assert client.get("/v1/status").json()["source_connection"] == "awaiting_api_key"
    monkeypatch.setenv("INTERVALS_API_KEY", "synthetic-private-key")
    response = client.get("/v1/status")
    assert response.json()["source_connection"] == "configured"
    assert "synthetic-private-key" not in response.text


# Drafted by local Qwen, reviewed against the actual fixture and byte limit.
@pytest.mark.parametrize("text", ["a" * 210000, "😊" * 40000])
def test_large_targets_rejects(api, text):
    client, memory = api
    response = client.post("/v1/planned-workouts", json=plan_body(targets={"text": text}))
    assert response.status_code == 422
    assert memory.documents == {}


def test_small_valid_targets_accepts(api):
    client, memory = api
    response = client.post("/v1/planned-workouts", json=plan_body(targets={"effort": "moderate"}))
    assert response.status_code == 201
    assert len(memory.documents) == 1


def test_user_register_creates_pending_payment_and_profile_retrieves_it(api):
    client, memory = api
    headers = {"x-user-id": "athlete-test-1"}
    body = {"email": "athlete@example.com", "display_name": "New Runner"}
    response = client.post("/v1/user/register", json=body, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "athlete-test-1"
    assert data["email"] == "athlete@example.com"
    assert data["display_name"] == "New Runner"
    assert data["status"] == "pending_payment"
    assert data["role"] == "athlete"

    # Profile retrieves it
    prof_res = client.get("/v1/user/profile", headers=headers)
    assert prof_res.status_code == 200
    assert prof_res.json()["id"] == "athlete-test-1"

    # Idempotent call returns existing 200
    idemp_res = client.post("/v1/user/register", json=body, headers=headers)
    assert idemp_res.status_code == 200
    assert idemp_res.json()["id"] == "athlete-test-1"


def test_user_register_owner_is_active(api, monkeypatch):
    client, memory = api
    monkeypatch.setenv("FIREBASE_OWNER_UID", "owner-athlete-id")
    headers = {"x-user-id": "owner-athlete-id"}
    body = {"email": "owner@example.com", "display_name": "Coach Magne"}
    response = client.post("/v1/user/register", json=body, headers=headers)
    assert response.status_code == 201
    assert response.json()["status"] == "active"


def test_user_routes_require_user_id(api):
    client, _ = api
    assert client.post("/v1/user/register", json={"email": "a@b.com"}).status_code == 401
    assert client.get("/v1/user/profile").status_code == 401
