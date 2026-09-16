"""OAuth resource-server verification. The identity provider owns login/PKCE/refresh."""
import asyncio
import json
import time

import httpx
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .config import Settings


class OwnerTokenVerifier(TokenVerifier):
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.keys = []
        self.fetched_at = -float("inf")
        self.lock = asyncio.Lock()

    async def _key(self, kid: str):
        async with self.lock:
            age = time.monotonic() - self.fetched_at
            matching = next((key for key in self.keys if key.get("kid") == kid), None)
            if age > 300 or (matching is None and age > 5):
                async with self.client.stream("GET", self.settings.oauth_jwks_url, follow_redirects=False) as response:
                    if response.status_code != 200:
                        return None
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > 100_000:
                            return None
                keys = json.loads(payload).get("keys")
                if not isinstance(keys, list) or len(keys) > 32:
                    return None
                self.keys = [key for key in keys if isinstance(key, dict)]
                self.fetched_at = time.monotonic()
                matching = next((key for key in self.keys if key.get("kid") == kid), None)
            return matching

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or len(token) > 16_384:
            return None
        try:
            header = jwt.get_unverified_header(token)
            kid, algorithm = header.get("kid"), header.get("alg")
            if not isinstance(kid, str) or not 1 <= len(kid) <= 128 or algorithm not in ("RS256", "ES256"):
                return None
            key_data = await self._key(kid)
            if not key_data or key_data.get("use", "sig") != "sig":
                return None
            if key_data.get("alg", algorithm) != algorithm:
                return None
            key = jwt.PyJWK.from_dict(key_data, algorithm=algorithm).key
            claims = jwt.decode(token, key, algorithms=[algorithm],
                audience=self.settings.public_url, issuer=self.settings.oauth_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=0)
            if claims["sub"] != self.settings.owner_subject:
                return None
            scope = claims.get("scope")
            if not isinstance(scope, str) or self.settings.read_scope not in scope.split():
                return None
            client_id = claims.get("client_id") or claims.get("azp")
            if not isinstance(client_id, str) or not client_id:
                return None
            return AccessToken(token=token, client_id=client_id, scopes=scope.split(),
                expires_at=int(claims["exp"]), resource=self.settings.public_url,
                subject=claims["sub"])
        except Exception:
            # Fail closed without disclosing token claims, identifiers or provider payloads.
            return None
