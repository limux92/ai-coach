from __future__ import annotations

import copy
from typing import Any
from fastapi.testclient import TestClient

from ai_coach.config import Settings
from ai_coach.main import app
from ai_coach.sync import run_sync_for_user, run_multi_tenant_sync


class ScopedMemoryStore:
    def __init__(self, user_id: str | None = None, docs: dict | None = None):
        self.user_id = user_id
        self.docs: dict[tuple[str, str], dict[str, Any]] = docs if docs is not None else {}
        self.lease = None

    def for_user(self, user_id: str | None) -> ScopedMemoryStore:
        return ScopedMemoryStore(user_id=user_id, docs=self.docs)

    def _key(self, collection: str, doc_id: str):
        if self.user_id and collection not in {"schema", "upstream_budgets", "users"}:
            return (f"users/{self.user_id}/{collection}", doc_id)
        return (collection, doc_id)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        key = self._key(collection, doc_id)
        val = self.docs.get(key)
        return copy.deepcopy(val) if val is not None else None

    def put(self, collection: str, doc_id: str, data: dict[str, Any], *, merge: bool = True) -> None:
        key = self._key(collection, doc_id)
        if merge:
            old = self.docs.get(key, {})
            self.docs[key] = {**old, **copy.deepcopy(data)}
        else:
            self.docs[key] = copy.deepcopy(data)

    def list_active_users(self) -> list[dict[str, Any]]:
        active = []
        for (col, doc_id), data in self.docs.items():
            if col == "users" and data.get("status") == "active":
                active.append(copy.deepcopy(data))
        return active

    def acquire_lease(self, owner, seconds=840):
        self.lease = owner
        return True

    def release_lease(self, owner, updates=None):
        self.lease = None


def test_user_intervals_credentials_api(monkeypatch):
    store = ScopedMemoryStore()
    settings = Settings(project="test", bucket="test")
    monkeypatch.setattr("ai_coach.main.store", lambda: store)
    monkeypatch.setattr("ai_coach.main.settings", lambda: settings)

    client = TestClient(app)

    # 401 without auth
    res = client.post("/v1/user/intervals-credentials", json={"api_key": "mock-api-key", "athlete_id": "i9999"})
    assert res.status_code == 401

    uid = "athlete-sync-1"
    store.put("users", uid, {"id": uid, "status": "pending_payment"})

    # 403 when not active
    res = client.post(
        "/v1/user/intervals-credentials",
        headers={"X-User-Id": uid},
        json={"api_key": "mock-api-key", "athlete_id": "i9999"},
    )
    assert res.status_code == 403

    # Activate athlete
    store.put("users", uid, {"status": "active"})

    # Successful save
    res = client.post(
        "/v1/user/intervals-credentials",
        headers={"X-User-Id": uid},
        json={"api_key": "mock-api-key", "athlete_id": "i9999"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "configured", "athlete_id": "i9999"}

    # Get credentials (does not leak secret)
    get_res = client.get("/v1/user/intervals-credentials", headers={"X-User-Id": uid})
    assert get_res.status_code == 200
    assert get_res.json() == {"configured": True, "athlete_id": "i9999"}
    assert "api_key" not in get_res.json()

    # Stored under scoped tenant subcollection
    stored_cred = store.for_user(uid).get("credentials", "intervals")
    assert stored_cred["api_key"] == "mock-api-key"
    assert stored_cred["athlete_id"] == "i9999"


def test_run_sync_for_user_and_multi_tenant(monkeypatch):
    store = ScopedMemoryStore()
    settings = Settings(project="test", bucket="test")

    # Inactive athlete
    store.put("users", "inactive-user", {"id": "inactive-user", "status": "inactive"})
    res_inactive = run_sync_for_user("inactive-user", store, settings)
    assert res_inactive["status"] == "subscription_inactive"

    # Active athlete without configured credentials
    store.put("users", "unconfigured-user", {"id": "unconfigured-user", "status": "active"})
    res_unconfigured = run_sync_for_user("unconfigured-user", store, settings)
    assert res_unconfigured["status"] == "not_configured"

    # Active athlete with configured credentials
    uid = "active-configured"
    store.put("users", uid, {"id": uid, "status": "active"})
    store.for_user(uid).put("credentials", "intervals", {"api_key": "test_api_key_valid", "athlete_id": "i123"})

    class MockSyncClient:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    # Mock run_sync
    monkeypatch.setattr(
        "ai_coach.sync.run_sync",
        lambda st, sg, run_id=None, client=None, backfill=True: {"status": "ok", "counts": {"new": 2}},
    )

    mock_client = MockSyncClient()
    res = run_sync_for_user(uid, store, settings, client=mock_client)
    assert res["status"] == "ok"
    assert res["user_id"] == uid
    assert res["counts"]["new"] == 2

    # Multi-tenant iteration
    mt_results = run_multi_tenant_sync(store, settings)
    # Only active users should be synced (active-configured, unconfigured-user)
    synced_ids = {r["user_id"] for r in mt_results}
    assert uid in synced_ids
    assert "unconfigured-user" in synced_ids
    assert "inactive-user" not in synced_ids
