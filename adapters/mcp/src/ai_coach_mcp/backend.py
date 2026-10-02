"""A bounded read-only HTTP client; incoming OAuth tokens never leave the adapter."""
import asyncio
import json
import logging
import re
import threading
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from .config import Settings

logger = logging.getLogger("ai_coach_mcp")
MAX_RESPONSE_BYTES = 64_000
ALLOWED_ROUTES = re.compile(
    r"/v1/(?:context|summaries|status|workouts|planned-workouts|wellness|"
    r"physiology/(?:sessions|models/[a-zA-Z0-9_.-]{1,180}|analyses/[a-zA-Z0-9_.-]{1,180}(?:/events)?)|"
    r"dashboard/(?:workouts|planned-workouts|workouts/[a-zA-Z0-9_.-]{1,180})|"
    r"workouts/[a-zA-Z0-9_.-]{1,180}(?:/samples|/physiology)?|"
    r"user/(?:register|profile|intervals-credentials|sync)|"
    r"billing/(?:checkout|portal|vipps/activate))\Z")


class BackendError(Exception):
    """Contains only a fixed safe message, never an upstream response body."""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class GoogleIDTokenProvider:
    def __init__(self, audience: str):
        self.audience = audience
        self.credentials = None
        self.lock = threading.Lock()

    def _token(self) -> str:
        with self.lock:
            request = Request()
            if self.credentials is None:
                # Cloud Run's attached service account supplies credentials through
                # the metadata service. No downloaded service-account key is needed.
                self.credentials = id_token.fetch_id_token_credentials(self.audience, request=request)
            if not self.credentials.valid:
                self.credentials.refresh(request)
            return self.credentials.token

    async def token(self) -> str:
        return await asyncio.to_thread(self._token)


class BackendClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient, token_provider=None):
        self.settings = settings
        self.client = client
        self.token_provider = token_provider or GoogleIDTokenProvider(settings.backend_url)

    async def get(self, path: str, params: dict[str, Any] | None = None, *, user_id: str | None = None) -> dict:
        if not ALLOWED_ROUTES.fullmatch(path) or "/../" in path or path.endswith("/.."):
            raise BackendError("Unsupported backend operation")
        try:
            token = await self.token_provider.token()
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            if user_id:
                headers["X-User-Id"] = user_id
            async with self.client.stream(
                "GET", self.settings.backend_url + path,
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers=headers,
                follow_redirects=False,
            ) as response:
                if response.status_code == 404:
                    raise BackendError("Training record or summary not found; missing history is not zero training", status_code=404)
                if response.status_code == 410:
                    raise BackendError("Workout no longer available in active training data", status_code=410)
                if response.status_code == 409:
                    raise BackendError("Workout samples are unavailable; inspect workout parse status", status_code=409)
                if response.status_code in (401, 403):
                    raise BackendError("The coach service cannot access its private backend")
                if response.status_code == 422:
                    raise BackendError("The backend rejected these query parameters", status_code=422)
                if response.status_code != 200:
                    raise BackendError("The coach backend is temporarily unavailable")
                if response.headers.get("content-type", "").split(";")[0] != "application/json":
                    raise BackendError("The coach backend returned an unexpected response")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise BackendError("Response is too large; request fewer days or a smaller page")
                parsed = json.loads(data)
                if not isinstance(parsed, dict):
                    raise BackendError("The coach backend returned an unexpected response")
                return parsed
        except BackendError:
            raise
        except Exception:
            # Never log path parameters, response bodies, health data or credentials.
            logger.warning("backend_read_failed")
            raise BackendError("The coach backend is temporarily unavailable") from None

    async def post(self, path: str, json_data: dict[str, Any] | None = None, *, user_id: str | None = None) -> dict:
        if not ALLOWED_ROUTES.fullmatch(path) or "/../" in path or path.endswith("/.."):
            raise BackendError("Unsupported backend operation")
        try:
            token = await self.token_provider.token()
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"}
            if user_id:
                headers["X-User-Id"] = user_id
            async with self.client.stream(
                "POST", self.settings.backend_url + path,
                json=json_data or {},
                headers=headers,
                follow_redirects=False,
            ) as response:
                if response.status_code in (401, 403):
                    raise BackendError("The coach service cannot access its private backend")
                if response.status_code == 422:
                    raise BackendError("The backend rejected these request parameters", status_code=422)
                if response.status_code not in (200, 201):
                    raise BackendError("The coach backend is temporarily unavailable", status_code=response.status_code)
                if response.headers.get("content-type", "").split(";")[0] != "application/json":
                    raise BackendError("The coach backend returned an unexpected response")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise BackendError("Response is too large")
                parsed = json.loads(data)
                if not isinstance(parsed, dict):
                    raise BackendError("The coach backend returned an unexpected response")
                return parsed
        except BackendError:
            raise
        except Exception:
            logger.warning("backend_post_failed")
            raise BackendError("The coach backend is temporarily unavailable") from None
