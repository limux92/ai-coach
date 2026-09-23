"""Durable OAuth state isolated from training records in its own Firestore database.

Bearer values are SHA-256 document keys, never stored raw. Single-use transitions
are transactions so retries/concurrent Cloud Run instances cannot redeem twice.
"""
import asyncio
from datetime import UTC, datetime
import hashlib
import time


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FirestoreOAuthStore:
    def __init__(self, project: str, database: str):
        if database != "ai-coach-auth":
            raise ValueError("OAuth state requires the isolated auth database")
        self.project, self.database = project, database
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.cloud import firestore
            self._client = firestore.Client(project=self.project, database=self.database)
        return self._client

    def ref(self, kind, key):
        return self.client.collection(kind).document(digest(key))

    async def get(self, kind, key):
        value = await asyncio.to_thread(lambda: self.ref(kind, key).get().to_dict())
        return value if value and value.get("expires_at", 0) > time.time() else None

    async def put(self, kind, key, value):
        await asyncio.to_thread(self.ref(kind, key).set, self._ttl(value))

    @staticmethod
    def _ttl(value):
        return {**value, "delete_after": datetime.fromtimestamp(value["expires_at"], UTC)}

    async def transition(self, kind, key, records, *, revoke_replay=False):
        """Atomically consume an unused record and write its successors.

        A replayed refresh token revokes the whole grant; a consumed marker lasts
        until the grant's absolute expiry, independent of eventual TTL deletion.
        """
        def commit():
            from google.cloud import firestore
            ref = self.ref(kind, key)
            @firestore.transactional
            def update(transaction):
                value = ref.get(transaction=transaction).to_dict()
                if not value or value.get("expires_at", 0) <= time.time():
                    return False
                grant = value.get("grant")
                if value.get("used"):
                    if revoke_replay and grant:
                        transaction.update(self.ref("grants", grant), {"revoked": True})
                    return False
                if grant:
                    family = self.ref("grants", grant).get(transaction=transaction).to_dict()
                    if not family or family.get("revoked") or family["expires_at"] <= time.time():
                        return False
                transaction.update(ref, {"used": True})
                for target_kind, target_key, record in records:
                    transaction.set(self.ref(target_kind, target_key), self._ttl(record))
                return True
            return update(self.client.transaction())
        return await asyncio.to_thread(commit)

    async def revoke(self, grant):
        await asyncio.to_thread(self.ref("grants", grant).update, {"revoked": True})
