"""Private Cloud Storage artifacts and Firestore metadata, accessed through IAM."""
import gzip
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from google.cloud import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTIONS = {"workouts", "planned_workouts", "wellness", "observations", "sync_state", "sync_runs", "athletes", "schema", "training_summaries", "summary_jobs", "credentials"}
COLLECTIONS |= {"physiology_revisions", "physiology_jobs", "physiology_efforts", "physiology_models",
                "physiology_analyses", "physiology_contexts", "upstream_budgets", "activity_discovery", "users"}
GLOBAL_COLLECTIONS = frozenset({"schema", "upstream_budgets", "users"})
TENANT_COLLECTIONS = frozenset(COLLECTIONS - GLOBAL_COLLECTIONS)


def utcnow():
    return datetime.now(timezone.utc)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False, separators=(",", ":")).encode()


def fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, default=str, allow_nan=False,
                           sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def safe_id(value: Any) -> str:
    text = str(value)
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,180}", text) or text in {".", ".."} or (text.startswith("__") and text.endswith("__")):
        raise ValueError("Invalid document identifier")
    return text


class Store:
    user_id: str | None = None
    settings: Any = None
    db: Any = None
    bucket: Any = None

    def __init__(self, settings, *, user_id: str | None = None):
        self.settings = settings
        self.user_id = safe_id(user_id) if user_id is not None else None
        self.db = firestore.Client(project=settings.project, database=settings.database)
        self.bucket = storage.Client(project=settings.project).bucket(settings.bucket)

    def for_user(self, user_id: str | None) -> "Store":
        store = Store.__new__(Store)
        store.settings = getattr(self, "settings", None)
        store.user_id = safe_id(user_id) if user_id is not None else None
        store.db = getattr(self, "db", None)
        store.bucket = getattr(self, "bucket", None)
        return store

    def _collection_ref(self, collection: str):
        if collection not in COLLECTIONS:
            raise ValueError("Unknown collection")
        if self.user_id is not None and collection in TENANT_COLLECTIONS:
            return self.db.collection("users").document(self.user_id).collection(collection)
        return self.db.collection(collection)

    def list_active_users(self) -> list[dict[str, Any]]:
        """Retrieve all registered users with active subscription status."""
        query = self.db.collection("users").where(filter=FieldFilter("status", "==", "active"))
        return [dict(s.to_dict(), id=s.id) for s in query.stream()]

    def ref(self, collection, doc_id):
        return self._collection_ref(collection).document(safe_id(doc_id))

    def get(self, collection, doc_id):
        snapshot = self.ref(collection, doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def put(self, collection, doc_id, data, *, merge=True):
        self.ref(collection, doc_id).set(data, merge=merge)

    def create(self, collection, doc_id, data):
        """Create-only deterministic objects; never replace a historical result."""
        try:
            self.ref(collection, doc_id).create(data)
        except AlreadyExists:
            pass

    def reserve_upstream_request(self, pool):
        from .upstream_budget import reserve_state
        ref = self.ref("upstream_budgets", pool)
        @firestore.transactional
        def reserve(transaction):
            state = ref.get(transaction=transaction).to_dict() or {}
            transaction.set(ref, reserve_state(state, utcnow()))
        reserve(self.db.transaction())

    def defer_upstream_requests(self, pool, until):
        ref = self.ref("upstream_budgets", pool)
        @firestore.transactional
        def defer(transaction):
            state = ref.get(transaction=transaction).to_dict() or {}
            previous = state.get("cooldown_until")
            transaction.set(ref, {"cooldown_until": max(previous, until) if previous else until}, merge=True)
        defer(self.db.transaction())

    def commit_workout_evidence(self, doc_id, data, *, merge=True):
        from .physiology_evidence import merged_workout, transition
        ref = self.ref("workouts", doc_id)
        @firestore.transactional
        def commit(transaction):
            old = ref.get(transaction=transaction).to_dict() or {}
            new = merged_workout(old, data, doc_id, merge)
            revision = transition(old, new, known_at=firestore.SERVER_TIMESTAMP)
            if revision:
                transaction.create(self.ref("physiology_revisions", revision["id"]), revision)
                transaction.create(self.ref("physiology_jobs", revision["id"]), {"id": revision["id"], "pending": True})
                transaction.set(self.ref("sync_state", "physiology"), {"status": "pending"}, merge=True)
                new.update(physiology_revision_id=revision["id"], physiology_evidence_sha256=revision["evidence_sha256"],
                           physiology_revision_sequence=revision["sequence"])
            transaction.set(ref, new)
        commit(self.db.transaction())

    def list(self, collection, oldest, newest, *, limit=200, after=None):
        query = (self._collection_ref(collection)
                 .where(filter=FieldFilter("local_date", ">=", oldest))
                 .where(filter=FieldFilter("local_date", "<=", newest))
                 .order_by("local_date").order_by("__name__"))
        if after:
            cursor = self.ref(collection, after).get()
            if not cursor.exists:
                raise ValueError("Unknown pagination cursor")
            query = query.start_after(cursor)
        return [dict(s.to_dict(), id=s.id) for s in query.limit(limit).stream()]

    def archive(self, path, data: bytes, *, content_type="application/octet-stream"):
        """Content-addressed objects are immutable. Replays never replace originals."""
        digest = hashlib.sha256(data).hexdigest()
        prefix = f"users/{self.user_id}/" if self.user_id else ""
        name = f"{prefix}{path}/{digest}"
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0, checksum="auto")
        except PreconditionFailed:
            # A matching content-addressed name already exists, so this is a replay.
            pass
        return {"bucket": self.bucket.name, "object": name, "sha256": digest, "bytes": len(data)}

    def scan(self, collection, *, limit=200, after=None, pending=None):
        """Bounded ID pagination for migrations and durable rebuild jobs."""
        query = self._collection_ref(collection)
        if pending is not None:
            query = query.where(filter=FieldFilter("pending", "==", pending))
        query = query.order_by("__name__")
        if after:
            cursor = self.ref(collection, after).get()
            if not cursor.exists:
                raise ValueError("Unknown pagination cursor")
            query = query.start_after(cursor)
        return [dict(s.to_dict(), id=s.id) for s in query.limit(limit).stream()]

    def archive_json(self, path, value):
        return self.archive(path, gzip.compress(json_bytes(value), mtime=0), content_type="application/gzip")

    def read_json(self, artifact):
        if artifact["bucket"] != self.bucket.name:
            raise ValueError("Artifact is outside this archive")
        return json.loads(gzip.decompress(self.bucket.blob(artifact["object"]).download_as_bytes()))

    def acquire_lease(self, owner, *, seconds=840):
        ref = self.ref("sync_state", "intervals")
        @firestore.transactional
        def acquire(transaction):
            state = ref.get(transaction=transaction).to_dict() or {}
            expires = state.get("lease_expires_at")
            if expires and expires > utcnow():
                return False
            transaction.set(ref, {"lease_owner": owner, "lease_expires_at": utcnow() + timedelta(seconds=seconds)}, merge=True)
            return True
        return acquire(self.db.transaction())

    def assert_sync_lease(self, owner):
        state = self.get("sync_state", "intervals") or {}
        expires = state.get("lease_expires_at")
        if not owner or state.get("lease_owner") != owner or not expires or expires <= utcnow():
            raise RuntimeError("Sync lease ownership expired")

    def put_if_sync_owner(self, collection, doc_id, data, owner, *, merge=True):
        """Fence current physiology pointers against expired/replaced workers."""
        lease = self.ref("sync_state", "intervals")
        destination = self.ref(collection, doc_id)
        @firestore.transactional
        def publish(transaction):
            state = lease.get(transaction=transaction).to_dict() or {}
            expires = state.get("lease_expires_at")
            if not owner or state.get("lease_owner") != owner or not expires or expires <= utcnow():
                raise RuntimeError("Sync lease ownership expired")
            transaction.set(destination, data, merge=merge)
        publish(self.db.transaction())

    def release_lease(self, owner, updates):
        ref = self.ref("sync_state", "intervals")
        @firestore.transactional
        def release(transaction):
            state = ref.get(transaction=transaction).to_dict() or {}
            if state.get("lease_owner") == owner:
                transaction.set(ref, {**updates, "lease_owner": None, "lease_expires_at": None}, merge=True)
        release(self.db.transaction())

    def initialize_schema(self):
        self.put("schema", "v1", {
            "version": 1,
            "collections": sorted(COLLECTIONS),
            "workouts": "Completed Garmin-origin activities; raw files are in Cloud Storage.",
            "planned_workouts": "Scheduled workouts, including imported Intervals WORKOUT events and locally authored plans.",
            "wellness": "One document per local calendar day.",
            "training_summaries": "Versioned known-imported totals by day, week, month and rolling window; history completeness is explicit.",
            "summary_jobs": "Private durable invalidations; originals are never deleted by summary reconciliation.",
            "source_attribution": "Activity data from Garmin Forerunner 970 / Garmin, via Intervals.icu.",
        })
