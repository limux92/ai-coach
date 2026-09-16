import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from ai_coach_mcp.app import DEFAULT_SAMPLE_FIELDS, create_app
from ai_coach_mcp.config import Settings


@pytest.fixture
def settings():
    return Settings(
        backend_url="https://ai-coach-data-123.europe-north1.run.app",
        backend_allowed_host="ai-coach-data-123.europe-north1.run.app",
        public_url="https://ai-coach-mcp-123.europe-north1.run.app/mcp",
        oauth_issuer="https://owner.auth0.com/",
        oauth_jwks_url="https://owner.auth0.com/.well-known/jwks.json",
        owner_subject="owner-subject")


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Tokens:
    calls = 0

    async def token(self):
        self.calls += 1
        return "private-backend-id-token"


def token(key, settings, **changes):
    claims = {"iss": settings.oauth_issuer, "sub": settings.owner_subject,
        "aud": settings.public_url, "iat": int(time.time()), "exp": int(time.time()) + 300,
        "scope": "coach:read", "azp": "chat-client"}
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def setup(settings, key, backend_handler):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    auth_calls = []

    def jwks(request):
        auth_calls.append(request)
        assert str(request.url) == settings.oauth_jwks_url
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"keys": [jwk]})

    tokens = Tokens()
    app = create_app(settings, backend_transport=httpx.MockTransport(backend_handler),
        jwks_transport=httpx.MockTransport(jwks), token_provider=tokens)
    return app, tokens, auth_calls


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


@pytest.mark.parametrize("changes", [
    {"sub": "another-user"}, {"aud": "https://other.example/mcp"}, {"iss": "https://evil.example/"},
    {"scope": "coach:write"}, {"exp": 1}, {"nbf": 4_000_000_000}, {"azp": None},
])
def test_oauth_rejects_wrong_owner_audience_issuer_scope_or_time(settings, key, changes):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Unauthorized backend access"))
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        result = rpc(client, token(key, settings, **changes))
        assert result.status_code == 401
    assert tokens.calls == 0


def test_oauth_rejects_bad_signature_and_unsigned_token(settings, key):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Unauthorized backend access"))
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with TestClient(app, base_url=settings.public_url.removesuffix("/mcp")) as client:
        assert rpc(client, token(other_key, settings)).status_code == 401
        assert rpc(client, "not-a-token").status_code == 401
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
    {"oauth_jwks_url": "https://attacker.example/jwks"},
    {"public_url": "http://localhost:8080/mcp"}, {"owner_subject": ""},
])
def test_config_fails_closed(settings, change):
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, **change})
