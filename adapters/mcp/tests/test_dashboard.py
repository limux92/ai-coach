"""Dashboard privacy boundaries, bounded queries, and coexistence with MCP."""
from dataclasses import replace
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from ai_coach_mcp.backend import ALLOWED_ROUTES
from ai_coach_mcp.config import Settings
from test_adapter import key, settings, setup, firebase_token as token, token as mcp_token, rpc


def client_for(app, settings):
    return TestClient(app, base_url=settings.public_url.removesuffix("/mcp"))


def bearer(key, settings, **claims):
    return {"Authorization": "Bearer " + token(key, settings, **claims)}


def test_shell_and_config_are_public_but_contain_no_training_data(settings, key, tmp_path, monkeypatch):
    static = tmp_path / "dashboard"
    static.mkdir()
    (static / "index.html").write_text('<!doctype html><main id="root"></main>')
    (static / "app.js").write_text('document.querySelector("main");')
    (static / ".env").write_text("private-data")
    (static / "secret.py").write_text("private-data")
    outside = tmp_path / "private.html"
    outside.write_text("private-data")
    (static / "linked.html").symlink_to(outside)
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(static))
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Public route accessed private backend"))
    with client_for(app, settings) as client:
        config = client.get("/dashboard/config")
        assert config.status_code == 200
        assert config.json() == {
            "apiKey": "fake-api-key",
            "authDomain": "test-project.firebaseapp.com",
            "projectId": "test-project",
            "timezone": "Europe/Oslo",
            "configured": True}
        for path in ("/dashboard/", "/dashboard/calendar", "/dashboard/app.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert "private-data" not in response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert "test-project.firebaseapp.com" in response.headers["content-security-policy"]
        for path in ("/dashboard/.env", "/dashboard/secret.py", "/dashboard/linked.html",
                     "/dashboard/assets/missing.js", "/dashboard/%2e%2e/private.html",
                     "/dashboard/%5cprivate.html"):
            response = client.get(path)
            assert response.status_code != 200
            assert "private-data" not in response.text
        assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/dashboard/"
        assert rpc(client, mcp_token(key, settings)).status_code == 200
    assert tokens.calls == 0


def test_missing_dashboard_client_does_not_disable_mcp(settings, key):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Unexpected backend access"))
    with client_for(app, settings) as client:
        config = client.get("/dashboard/config").json()
        assert config["configured"] is True
        assert rpc(client, mcp_token(key, settings)).status_code == 200
    assert tokens.calls == 0


@pytest.mark.parametrize("path", ["/quick-workout", "/quick-workout/run"])
def test_retired_recommendations_are_unavailable_after_owner_auth(settings, key, path):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Retired route accessed backend"))
    with client_for(app, settings) as client:
        assert client.post("/dashboard/api" + path).status_code == 401
        response = client.post("/dashboard/api" + path, headers=bearer(key, settings))
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
    assert tokens.calls == 0


@pytest.mark.parametrize("path", ["/status", "/context", "/summaries?period=week&date=2026-09-16",
    "/workouts?oldest=2026-09-01&newest=2026-09-16", "/planned-workouts?oldest=2026-09-01&newest=2026-09-16",
    "/workouts/i-123", "/workouts/i-123/samples", "/not-a-route"])
@pytest.mark.parametrize("claims", [None, {"sub": "another-person"}, {"aud": "other-api"},
    {"email_verified": False}, {"exp": 1}, {"iss": "https://attacker.example/"}])
def test_all_api_routes_reject_missing_or_invalid_owner_token(settings, key, path, claims):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Unauthorized backend access"))
    with client_for(app, settings) as client:
        response = client.get("/dashboard/api" + path, headers=bearer(key, settings, **claims) if claims is not None else {})
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "access-control-allow-origin" not in response.headers
    assert tokens.calls == 0


@pytest.mark.parametrize("path,backend_path,params", [
    ("/workouts?oldest=2026-09-01&newest=2026-09-16&after=i-15", "/v1/dashboard/workouts",
     {"oldest": "2026-09-01", "newest": "2026-09-16", "limit": "50", "after": "i-15"}),
    ("/planned-workouts?oldest=2026-09-01&newest=2026-09-16&limit=10", "/v1/dashboard/planned-workouts",
     {"oldest": "2026-09-01", "newest": "2026-09-16", "limit": "10"}),
    ("/workouts/i-123", "/v1/dashboard/workouts/i-123", {}),
    ("/workouts/i-123/samples?offset=500&limit=20&fields=timestamp,heart_rate,power", "/v1/workouts/i-123/samples",
     {"offset": "500", "limit": "20", "fields": "timestamp,heart_rate,power"}),
    ("/status", "/v1/status", {}),
    ("/context?days=28&upcoming=7", "/v1/context", {"days": "28", "upcoming": "7"}),
    ("/summaries?date=2026-09-16&period=week", "/v1/summaries", {"date": "2026-09-16", "period": "week"}),
])
def test_reads_use_allowlisted_routes_service_identity_and_preserve_cursors(settings, key, path, backend_path, params):
    def backend(request):
        assert request.method == "GET"
        assert request.url.path == backend_path
        assert dict(request.url.params) == params
        assert request.headers["authorization"] == "Bearer private-backend-id-token"
        assert "cookie" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, json={"items": [{"id": "i-123"}], "next_cursor": "i-123", "next_offset": 520})
    app, tokens, _ = setup(settings, key, backend)
    with client_for(app, settings) as client:
        response = client.get("/dashboard/api" + path,
            headers={**bearer(key, settings), "Cookie": "private-cookie", "X-API-Key": "private-key"})
        assert response.status_code == 200
        assert response.json()["next_cursor"] == "i-123"
        assert response.json()["next_offset"] == 520
        assert response.headers["cache-control"] == "no-store"
    assert tokens.calls == 1


@pytest.mark.parametrize("path", [
    "/workouts", "/workouts?oldest=2026-09-16&newest=2026-09-01",
    "/workouts?oldest=2024-01-01&newest=2026-09-16",
    "/workouts?oldest=2026-09-01&newest=2026-09-16&limit=51",
    "/workouts?oldest=2026-09-01&newest=2026-09-16&limit=0",
    "/workouts?oldest=2026-09-01&newest=2026-09-16&limit=1&limit=2",
    "/workouts?oldest=2026-09-01&newest=2026-09-16&after=../secrets",
    "/workouts?oldest=2026-09-01&newest=2026-09-16&url=https://attacker.example",
    "/workouts/__internal__", "/workouts/%2e%2e%2Finternal%2Fsync",
    "/workouts/i-123?access_token=private", "/workouts/i-123/samples?fields=secret",
    "/workouts/i-123/samples?fields=timestamp,timestamp", "/workouts/i-123/samples?limit=501",
    "/workouts/i-123/samples?offset=-1", "/workouts/i-123/samples?offset=1000001",
    "/status?url=https://attacker.example", "/context?days=91", "/context?upcoming=0",
    "/summaries?date=2026-02-30&period=week", "/summaries?date=2026-09-16&period=year",
    "/summaries?date=20260916&period=week", "/internal/sync", "/https://attacker.example",
])
def test_malformed_unbounded_or_injected_queries_never_reach_backend(settings, key, path):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Invalid query reached backend"))
    with client_for(app, settings) as client:
        response = client.get("/dashboard/api" + path, headers=bearer(key, settings))
        assert response.status_code in (404, 422), response.text
    assert tokens.calls == 0


def test_duplicate_authorization_and_mutations_rejected(settings, key):
    app, tokens, _ = setup(settings, key, lambda request: pytest.fail("Unexpected backend call"))
    with client_for(app, settings) as client:
        auth = bearer(key, settings)["Authorization"]
        assert client.get("/dashboard/api/status", headers=[("Authorization", auth), ("Authorization", auth)]).status_code == 401
        assert client.post("/dashboard/api/workouts", headers=bearer(key, settings), json={}).status_code == 405
        assert client.post("/dashboard/api/workouts", json={}).status_code == 401
    assert tokens.calls == 0


@pytest.mark.parametrize("code,expected", [(404,404), (409,409), (410,410), (422,422), (401,503), (403,503), (302,503), (500,503)])
def test_upstream_errors_preserve_safe_status_without_data_or_redirects(settings, key, code, expected):
    calls = []
    def backend(request):
        calls.append(request)
        return httpx.Response(code, text="private-token-and-health-data", headers={"Location": "https://attacker.example"})
    app, _, _ = setup(settings, key, backend)
    with client_for(app, settings) as client:
        response = client.get("/dashboard/api/workouts/i-123/samples", headers=bearer(key, settings))
        assert response.status_code == expected
        assert "private-token-and-health-data" not in response.text
        assert "location" not in response.headers
    assert len(calls) == 1


def test_gateway_keeps_existing_response_size_bound(settings, key):
    app, _, _ = setup(settings, key, lambda request: httpx.Response(200, json={"data": "x" * 64_001}))
    with client_for(app, settings) as client:
        response = client.get("/dashboard/api/status", headers=bearer(key, settings))
        assert response.status_code == 503
        assert "too large" in response.json()["error"]


def test_backend_allowlist_has_only_specific_dashboard_routes():
    for path in ("/v1/dashboard/workouts", "/v1/dashboard/planned-workouts", "/v1/dashboard/workouts/i-123"):
        assert ALLOWED_ROUTES.fullmatch(path)
    for path in ("/v1/dashboard/status", "/v1/dashboard/workouts/i-123/samples", "/v1/dashboard/internal/sync",
                 "/v1/dashboard/workouts/https://attacker.example", "/v1/dashboard/workouts/i-123/anything"):
        assert not ALLOWED_ROUTES.fullmatch(path)


def test_dashboard_client_config_rejects_injection(settings):
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, "firebase_api_key": "client\nsecret"})
