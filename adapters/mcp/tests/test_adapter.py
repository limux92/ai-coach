import json
import time

import httpx
import pytest
from starlette.testclient import TestClient
import asyncio
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from conftest import MemoryOAuthStore

from ai_coach_mcp.app import DEFAULT_SAMPLE_FIELDS, create_app
from ai_coach_mcp.config import Settings


@pytest.fixture
def settings():
    return Settings(
        backend_url="https://ai-coach-data-123.europe-north1.run.app",
        backend_allowed_host="ai-coach-data-123.europe-north1.run.app",
        public_url="https://ai-coach-mcp-123.europe-north1.run.app/mcp",
        firebase_project_id="test-project",
        firebase_api_key="fake-api-key",
        owner_subject="owner-subject",
        oauth_redirect_uris=("https://chatgpt.com/connector_platform_oauth_redirect",))


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Tokens:
    calls = 0

    async def token(self):
        self.calls += 1
        return "private-backend-id-token"


def firebase_token(key, settings, **changes):
    claims = {"iss": settings.firebase_issuer, "sub": settings.owner_subject,
              "aud": settings.firebase_project_id, "iat": int(time.time()),
              "exp": int(time.time()) + 300, "auth_time": int(time.time()) - 30,
              "email_verified": True, "firebase": {"sign_in_provider": "google.com"}}
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def token(key, settings):
    return "A" * 43


def setup(settings, key, backend_handler):
    tokens = Tokens()
    store = MemoryOAuthStore()
    async def seed():
        await store.put("grants", "grant", {"expires_at": time.time() + 600, "revoked": False})
        await store.put("access", token(key, settings), {"client_id": "test-client", "subject": settings.owner_subject,
            "resource": settings.public_url, "scopes": [settings.read_scope], "grant": "grant", "expires_at": int(time.time()) + 300})
    asyncio.run(seed())
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="test-key", alg="RS256", use="sig")
    app = create_app(settings, backend_transport=httpx.MockTransport(backend_handler),
        jwks_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"keys": [jwk]})),
        token_provider=tokens, oauth_store=store)
    return app, tokens, []


def headers(bearer=None):
    result = {"Accept": "application/json, text/event-stream", "Mcp-Protocol-Version": "2025-11-25"}
    if bearer:
        result["Authorization"] = "Bearer " + bearer
    return result


def rpc(client, bearer, method="tools/list", params=None):
    return client.post("/mcp", headers=headers(bearer),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def invoke(client, bearer, name, args):
    response = rpc(client, bearer, "tools/call", {"name": name, "arguments": args})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_initialization_metadata_and_seven_readonly_tools(settings, key):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("No backend call expected"))
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        assert client.get("/healthz").json() == {"service": "ai-coach-mcp", "status": "ok"}
        assert client.get("/v1/health").json() == {"service": "ai-coach-mcp", "status": "ok"}
        metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert metadata["resource"] == settings.public_url
        assert metadata["authorization_servers"] == [settings.oauth_issuer]
        unauth = rpc(client, None)
        assert unauth.status_code == 401
        assert "resource_metadata=" in unauth.headers["www-authenticate"]
        bearer = token(key, settings)
        init = rpc(client, bearer, "initialize", {"protocolVersion": "2025-11-25",
            "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        assert init.status_code == 200
        tools = rpc(client, bearer).json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"get_coach_context", "list_completed_workouts",
            "get_workout_details", "list_planned_workouts", "list_wellness", "get_workout_samples", "get_training_summary"}
        for tool in tools:
            assert tool["annotations"]["readOnlyHint"] is True
            assert tool["annotations"]["destructiveHint"] is False
            assert tool["annotations"]["openWorldHint"] is False
            assert tool["_meta"]["securitySchemes"] == [{"type": "oauth2", "scopes": ["coach:read"]}]
        assert tokens.calls == 0


@pytest.mark.parametrize("name,args,path,query", [
    ("get_coach_context", {"days": 7, "upcoming": 3}, "/v1/context", {"days": "7", "upcoming": "3"}),
    ("get_training_summary", {"period": "week", "date": "2026-09-15"}, "/v1/summaries", {"period": "week", "date": "2026-09-15"}),
    ("list_completed_workouts", {"oldest": "2026-09-01", "newest": "2026-09-15", "after": "i-12"}, "/v1/workouts",
        {"oldest": "2026-09-01", "newest": "2026-09-15", "limit": "50", "after": "i-12"}),
    ("get_workout_details", {"workout_id": "i-123"}, "/v1/workouts/i-123", {}),
    ("list_planned_workouts", {"oldest": "2026-09-15", "newest": "2026-10-01"}, "/v1/planned-workouts",
        {"oldest": "2026-09-15", "newest": "2026-10-01", "limit": "50"}),
    ("list_wellness", {"oldest": "2026-09-01", "newest": "2026-09-15", "limit": 10}, "/v1/wellness",
        {"oldest": "2026-09-01", "newest": "2026-09-15", "limit": "10"}),
    ("get_workout_samples", {"workout_id": "i-123", "offset": 500, "limit": 100}, "/v1/workouts/i-123/samples",
        {"offset": "500", "limit": "100", "fields": DEFAULT_SAMPLE_FIELDS}),
    ("get_workout_samples", {"workout_id": "i-123"}, "/v1/workouts/i-123/samples",
        {"offset": "0", "limit": "100", "fields": DEFAULT_SAMPLE_FIELDS}),
    ("get_workout_samples", {"workout_id": "i-123", "fields": "timestamp,heart_rate"}, "/v1/workouts/i-123/samples",
        {"offset": "0", "limit": "100", "fields": "timestamp,heart_rate"}),
])
def test_tools_forward_only_private_get_and_preserve_pagination(settings, key, name, args, path, query):
    calls = []

    def backend(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == settings.backend_allowed_host
        assert request.url.path == path
        assert dict(request.url.params) == query
        assert request.headers["authorization"] == "Bearer private-backend-id-token"
        return httpx.Response(200, json={"items": [{"id": "i-124"}], "next_cursor": "i-124", "next_offset": 600})

    app, tokens, _ = setup(settings, key, backend)
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        result = invoke(client, token(key, settings), name, args)
        assert result.get("isError", False) is False
        data = json.loads(result["content"][0]["text"])
        assert data["next_cursor"] == "i-124"
        assert data["next_offset"] == 600
    assert len(calls) == tokens.calls == 1


@pytest.mark.parametrize("change", [{"subject": "another-user"}, {"resource": "https://wrong.example/mcp"},
    {"expires_at": 1}, {"scopes": ["coach:write"]}])
def test_mcp_rejects_invalid_stored_grants(settings, key, change):
    from ai_coach_mcp.oauth_store import digest
    app, tokens, _ = setup(settings, key, lambda r: pytest.fail("Unauthorized backend access"))
    app.state.oauth.store.rows["access", digest(token(key, settings))].update(change)
    with TestClient(app, base_url=settings.oauth_issuer) as client:
        assert rpc(client, token(key, settings)).status_code == 401
    assert tokens.calls == 0


def test_mcp_rejects_firebase_id_tokens_and_forged_tokens(settings, key):
    app, tokens, _ = setup(settings, key, lambda r: pytest.fail("Unauthorized backend access"))
    with TestClient(app, base_url=settings.oauth_issuer) as client:
        for value in ("not-a-token", "B" * 43, firebase_token(key, settings), '{"sub":"owner-subject"}'):
            assert rpc(client, value).status_code == 401
    assert tokens.calls == 0


@pytest.mark.parametrize("name,args", [
    ("get_coach_context", {"days": 91}),
    ("get_training_summary", {"period": "year", "date": "2026-09-15"}),
    ("get_training_summary", {"period": "month", "date": "2026-02-30"}),
    ("get_workout_samples", {"workout_id": "i-123", "limit": 1001}),
    ("get_workout_samples", {"workout_id": "i-123", "offset": -1}),
    ("get_workout_samples", {"workout_id": "i-123", "fields": "x" * 201}),
    ("get_workout_details", {"workout_id": "../internal/sync"}),
    ("get_workout_details", {"workout_id": "https://attacker.example/data"}),
    ("get_workout_details", {"workout_id": ".."}),
    ("list_completed_workouts", {"oldest": "2026-09-15", "newest": "2026-09-01"}),
    ("list_completed_workouts", {"oldest": "2025-01-01", "newest": "2026-09-15"}),
    ("list_completed_workouts", {"oldest": "2026-09-01", "newest": "2026-09-15", "after": "../secret"}),
    ("list_planned_workouts", {"oldest": "2026-09-01", "newest": "2026-09-15", "limit": 101}),
])
def test_bounded_queries_and_path_injection_do_not_reach_backend(settings, key, name, args):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Invalid query reached backend"))
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        assert invoke(client, token(key, settings), name, args)["isError"] is True
    assert tokens.calls == 0


@pytest.mark.parametrize("status", [302, 401, 403, 404, 409, 410, 422, 500])
def test_upstream_errors_are_sanitized_and_redirects_not_followed(settings, key, status, caplog):
    calls = []
    secret = "private-health-and-token-payload"

    def backend(request):
        calls.append(request)
        return httpx.Response(status, text=secret, headers={"Location": "https://attacker.example"})

    app, _, _ = setup(settings, key, backend)
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        result = invoke(client, token(key, settings), "get_workout_details", {"workout_id": "i-123"})
        assert result["isError"] is True
        assert secret not in json.dumps(result)
        assert secret not in caplog.text
    assert len(calls) == 1


def test_response_size_is_bounded(settings, key):
    app, _, _ = setup(settings, key, lambda request: httpx.Response(200, json={"payload": "x" * 64_001}))
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        result = invoke(client, token(key, settings), "get_coach_context", {})
        assert result["isError"] is True
        assert "too large" in result["content"][0]["text"]


def test_backend_rejects_unsupported_sample_fields_without_echoing_payload(settings, key):
    def backend(request):
        assert request.url.params["fields"] == "timestamp,unsupported_field"
        return httpx.Response(422, json={"detail": "private-provider-data"})
    app, _, _ = setup(settings, key, backend)
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        result = invoke(client, token(key, settings), "get_workout_samples",
                        {"workout_id": "i-123", "fields": "timestamp,unsupported_field"})
        assert result["isError"] is True
        assert "rejected these query parameters" in result["content"][0]["text"]
        assert "private-provider-data" not in json.dumps(result)


def test_server_rejects_unlisted_host_and_origin(settings, key):
    app, _, _ = setup(settings, key, lambda request: pytest.fail("Invalid host reached backend"))
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        bearer = token(key, settings)
        bad_host = client.post("/mcp", headers={**headers(bearer), "Host": "attacker.example"}, json={})
        assert bad_host.status_code == 421
        bad_origin = client.post("/mcp", headers={**headers(bearer), "Origin": "https://attacker.example"}, json={})
        assert bad_origin.status_code == 403


@pytest.mark.parametrize("change", [
    {"backend_url": "http://ai-coach-data-123.europe-north1.run.app"},
    {"backend_url": "https://attacker.example"},
    {"backend_url": "https://other.run.app"},
    {"backend_url": "https://ai-coach-data-123.europe-north1.run.app/path"},
    {"backend_url": "https://name:password@ai-coach-data-123.europe-north1.run.app"},
    {"firebase_project_id": "INVALID_PROJECT_ID!"},  # uppercase/special chars invalid
    {"public_url": "http://localhost:8080/mcp"}, {"owner_subject": ""},
])
def test_config_fails_closed(settings, change):
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, **change})
