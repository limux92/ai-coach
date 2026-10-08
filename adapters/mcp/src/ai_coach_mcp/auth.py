"""Firebase ID-token verification for the browser and OAuth consent step."""
import asyncio
import json
import time

import httpx
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .config import Settings

FIREBASE_JWKS_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"


class OwnerTokenVerifier(TokenVerifier):
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client
        self.keys = []
        self.fetched_at = -float("inf")
        self.lock = asyncio.Lock()

    async def _key(self, kid):
        async with self.lock:
            age = time.monotonic() - self.fetched_at
            matching = next((key for key in self.keys if key.get("kid") == kid), None)
            if age > 300 or (matching is None and age > 5):
                # Bound fetches and payloads; never follow an attacker-selected jku/x5u.
                self.fetched_at = time.monotonic()
                self.keys = []
                async with self.client.stream("GET", FIREBASE_JWKS_URL, follow_redirects=False) as response:
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
                matching = next((key for key in self.keys if key.get("kid") == kid), None)
            return matching

    async def verify_token(self, token: str) -> AccessToken | None:
        if not isinstance(token, str) or not 1 <= len(token) <= 16_384:
            return None
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
                return None
            key_data = await self._key(kid)
            if (not key_data or key_data.get("kty") != "RSA" or key_data.get("use", "sig") != "sig"
                    or key_data.get("alg", "RS256") != "RS256"):
                return None
            key = jwt.PyJWK.from_dict(key_data, algorithm="RS256").key
            claims = jwt.decode(token, key, algorithms=["RS256"],
                audience=self.settings.firebase_project_id, issuer=self.settings.firebase_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "auth_time"], "strict_aud": True})
            now = time.time()
            if any(type(claims[field]) is not int for field in ("exp", "iat", "auth_time")):
                return None
            if ((not self.settings.multi_tenant and claims["sub"] != self.settings.owner_subject)
                    or not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 128
                    or claims["email_verified"] is not True
                    or claims["firebase"]["sign_in_provider"] != "google.com"
                    or not 0 < claims["auth_time"] <= claims["iat"] <= now
                    or claims["exp"] <= claims["iat"]):
                return None
            return AccessToken(token=token, client_id=self.settings.firebase_project_id,
                scopes=[self.settings.read_scope], expires_at=claims["exp"],
                resource=self.settings.public_url, subject=claims["sub"],
                claims={"iss": self.settings.firebase_issuer})
        except Exception:
            # Invalid claims, provider outages and malformed keys fail closed.
            return None
