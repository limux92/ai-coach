"""Exercise archive immutability and lease ownership without Google connections."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import AlreadyExists, PreconditionFailed

from ai_coach import storage


class FakeBucket:
    name = "private-archive"

    def __init__(self):
        self.contents = {}
        self.uploads = []

    def blob(self, name):
        bucket = self
        class Blob:
            def upload_from_string(self, data, *, content_type, if_generation_match, checksum):
                bucket.uploads.append({"name": name, "precondition": if_generation_match, "checksum": checksum})
                if name in bucket.contents and if_generation_match == 0:
                    raise PreconditionFailed("Object already exists")
                bucket.contents[name] = data

            def download_as_bytes(self):
                return bucket.contents[name]
        return Blob()


def archive_store():
    store = storage.Store.__new__(storage.Store)
    store.bucket = FakeBucket()
    return store


def test_replayed_archive_is_idempotent_and_changed_file_keeps_original():
    store = archive_store()
    original = b"original-file-bytes"
    first = store.archive("raw/activity-123", original)
    replay = store.archive("raw/activity-123", original)
    changed = store.archive("raw/activity-123", b"updated-file-bytes")
    assert first == replay
    assert first["object"] != changed["object"]
    assert len(store.bucket.contents) == 2
    assert store.bucket.contents[first["object"]] == original
    assert all(upload["precondition"] == 0 for upload in store.bucket.uploads)
    assert all(upload["checksum"] != "none" for upload in store.bucket.uploads)


def test_archived_json_roundtrips_and_rejects_artifact_in_another_bucket():
    store = archive_store()
    payload = {"records": [{"heart_rate": 135}], "note": "økten"}
    artifact = store.archive_json("parsed/123", payload)
    assert store.read_json(artifact) == payload
    assert store.archive_json("parsed/123", payload) == artifact
    with pytest.raises(ValueError, match="outside"):
        store.read_json({**artifact, "bucket": "other-private-bucket"})


class FakeDB:
    def __init__(self):
        self.documents = {}

    def collection(self, name):
        database = self
        class Collection:
            def document(self, doc_id):
                class Reference:
                    key = (name, doc_id)
                    def collection(self, sub_name):
                        return database.collection(f"{name}/{doc_id}/{sub_name}")
                    def get(self, transaction=None):
                        value = database.documents.get(self.key)
                        return SimpleNamespace(to_dict=lambda: copy.deepcopy(value), exists=value is not None)
                return Reference()
        return Collection()

    def transaction(self):
        database = self
        class Transaction:
            def set(self, reference, updates, *, merge=False):
                before = database.documents.get(reference.key, {}) if merge else {}
                database.documents[reference.key] = {**before, **copy.deepcopy(updates)}

            def create(self, reference, updates):
                if reference.key in database.documents:
                    raise AlreadyExists("Immutable evidence exists")
                self.set(reference, updates)
        return Transaction()


def test_unexpired_lease_blocks_another_worker_and_old_owner_cannot_release_new_lease(monkeypatch):
    # The fake decorator executes the transaction body. Google provides actual
    # conflict retries; this test covers our ownership/expiration decisions.
    monkeypatch.setattr(storage.firestore, "transactional", lambda function: function)
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    current = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(storage, "utcnow", lambda: current)
    assert store.acquire_lease("worker-a", seconds=60) is True
    assert store.acquire_lease("worker-b", seconds=60) is False
    store.release_lease("worker-b", {"last_success_at": current})
    assert store.db.documents[("sync_state", "intervals")]["lease_owner"] == "worker-a"
    assert "last_success_at" not in store.db.documents[("sync_state", "intervals")]
    current += timedelta(seconds=61)
    assert store.acquire_lease("worker-b", seconds=60) is True
    store.release_lease("worker-a", {"last_success_at": current})
    assert store.db.documents[("sync_state", "intervals")]["lease_owner"] == "worker-b"
    store.release_lease("worker-b", {"last_success_at": current})
    state = store.db.documents[("sync_state", "intervals")]
    assert state["lease_owner"] is None
    assert state["last_success_at"] == current


def test_evidence_transaction_records_revision_job_and_head_without_backdating(monkeypatch):
    monkeypatch.setattr(storage.firestore, "transactional", lambda function: function)
    monkeypatch.setattr(storage.firestore, "SERVER_TIMESTAMP", "server-commit-time")
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    doc = {"id": "w", "local_date": "2020-01-01", "sport": "Ride", "imported_at": "2020-01-01"}
    store.commit_workout_evidence("w", doc, merge=False)
    first = store.get("workouts", "w")
    revision = store.get("physiology_revisions", first["physiology_revision_id"])
    assert revision["known_at"] == "server-commit-time"
    assert revision["sequence"] == first["physiology_revision_sequence"] == 1
    assert store.get("physiology_jobs", revision["id"])["pending"] is True
    assert store.get("sync_state", "physiology")["status"] == "pending"
    store.commit_workout_evidence("w", doc, merge=False)
    assert store.get("workouts", "w")["physiology_revision_id"] == revision["id"]
    store.commit_workout_evidence("w", {"source_deleted": True})
    second = store.get("workouts", "w")
    updated = store.get("physiology_revisions", second["physiology_revision_id"])
    assert updated["sequence"] == 2 and updated["previous_revision_id"] == revision["id"]
    assert store.get("physiology_revisions", revision["id"]) == revision


def test_expired_or_replaced_lease_cannot_publish_current_physiology(monkeypatch):
    monkeypatch.setattr(storage.firestore, "transactional", lambda function: function)
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(storage, "utcnow", lambda: now)
    assert store.acquire_lease("a", seconds=60)
    store.put_if_sync_owner("sync_state", "physiology", {"context_id": "a"}, "a")
    now += timedelta(seconds=60)
    with pytest.raises(RuntimeError, match="expired"):
        store.put_if_sync_owner("sync_state", "physiology", {"context_id": "stale-a"}, "a")
    assert store.acquire_lease("b")
    store.put_if_sync_owner("sync_state", "physiology", {"context_id": "b"}, "b")
    with pytest.raises(RuntimeError, match="expired"):
        store.put_if_sync_owner("sync_state", "physiology", {"context_id": "stale-a"}, "a")
    assert store.get("sync_state", "physiology")["context_id"] == "b"


def test_user_scoped_ref_targets_user_subcollection():
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    store.user_id = None
    user_store = store.for_user("athlete-123")
    assert user_store.user_id == "athlete-123"
    assert user_store.ref("workouts", "w1").key == ("users/athlete-123/workouts", "w1")
    assert user_store.ref("wellness", "2026-10-01").key == ("users/athlete-123/wellness", "2026-10-01")
    assert user_store.ref("physiology_models", "m1").key == ("users/athlete-123/physiology_models", "m1")


def test_user_scoped_global_collections_stay_at_root():
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    user_store = store.for_user("athlete-123")
    assert user_store.ref("schema", "v1").key == ("schema", "v1")
    assert user_store.ref("upstream_budgets", "intervals").key == ("upstream_budgets", "intervals")
    assert user_store.ref("users", "athlete-123").key == ("users", "athlete-123")


def test_unscoped_ref_targets_root_collection():
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    store.user_id = None
    assert store.ref("workouts", "w1").key == ("workouts", "w1")
    assert store.ref("wellness", "2026-10-01").key == ("wellness", "2026-10-01")
    assert store.ref("schema", "v1").key == ("schema", "v1")


def test_user_scoped_archive_prefixes_user_path():
    store = storage.Store.__new__(storage.Store)
    store.bucket = FakeBucket()
    store.user_id = None
    user_store = store.for_user("athlete-42")
    payload = b"athlete-activity-bytes"
    user_artifact = user_store.archive("raw/activity-99", payload)
    assert user_artifact["object"].startswith("users/athlete-42/raw/activity-99/")

    root_artifact = store.archive("raw/activity-99", payload)
    assert root_artifact["object"].startswith("raw/activity-99/")
    assert root_artifact["object"] != user_artifact["object"]


def test_invalid_user_id_rejected():
    store = storage.Store.__new__(storage.Store)
    store.db = FakeDB()
    store.user_id = None
    with pytest.raises(ValueError, match="Invalid document identifier"):
        store.for_user("../malicious")
    with pytest.raises(ValueError, match="Invalid document identifier"):
        store.for_user("")
    with pytest.raises(ValueError, match="Invalid document identifier"):
        store.for_user("bad user with spaces")
