"""Offline durable-store double. JWT verification is NEVER mocked."""
from copy import deepcopy
import time

from ai_coach_mcp.oauth_store import digest


class MemoryOAuthStore:
    def __init__(self):
        self.rows = {}

    async def get(self, kind, key):
        row = deepcopy(self.rows.get((kind, digest(key))))
        return row if row and row['expires_at'] > time.time() else None

    async def put(self, kind, key, row):
        self.rows[kind, digest(key)] = deepcopy(row)

    async def transition(self, kind, key, records, *, revoke_replay=False):
        row = self.rows.get((kind, digest(key)))
        if not row or row['expires_at'] <= time.time():
            return False
        grant = row.get('grant')
        if row.get('used'):
            if revoke_replay and grant:
                await self.revoke(grant)
            return False
        if grant:
            family = await self.get('grants', grant)
            if not family or family.get('revoked'):
                return False
        row['used'] = True
        for target_kind, target_key, record in records:
            await self.put(target_kind, target_key, record)
        return True

    async def revoke(self, grant):
        self.rows['grants', digest(grant)]['revoked'] = True
