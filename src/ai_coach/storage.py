"""Private Cloud Storage artifacts and Firestore metadata, accessed through IAM."""
import gzip
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from google.api_core.exceptions import PreconditionFailed
from google.cloud import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTIONS = {"workouts", "planned_workouts", "wellness", "observations", "sync_state", "sync_runs", "athletes", "schema", "training_summaries", "summary_jobs"}


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
    def __init__(self, settings):
        self.db = firestore.Client(project=settings.project, database=settings.database)
        self.bucket = storage.Client(project=settings.project).bucket(settings.bucket)

    def ref(self, collection, doc_id):
        if collection not in COLLECTIONS:
            raise ValueError("Unknown collection")
        return self.db.collection(collection).document(safe_id(doc_id))

    def get(self, collection, doc_id):
        snapshot = self.ref(collection, doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def put(self, collection, doc_id, data, *, merge=True):
        self.ref(collection, doc_id).set(data, merge=merge)

    def list(self, collection, oldest, newest, *, limit=200, after=None):
        if collection not in COLLECTIONS:
            raise ValueError("Unknown collection")
        query = (self.db.collection(collection)
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
        name = f"{path}/{digest}"
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0, checksum="auto")
        except PreconditionFailed:
            # A matching content-addressed name already exists, so this is a replay.
            pass
        return {"bucket": self.bucket.name, "object": name, "sha256": digest, "bytes": len(data)}

    def scan(self, collection, *, limit=200, after=None, pending=None):
        """Bounded ID pagination for migrations and durable rebuild jobs."""
        if collection not in COLLECTIONS:
            raise ValueError("Unknown collection")
        query = self.db.collection(collection)
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
