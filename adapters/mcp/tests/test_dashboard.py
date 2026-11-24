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
    for path in ("/v1/dashboard/workouts", "/v1/dashboard/planned-workouts", "/v1/dashboard/workouts/i-123",
                 "/v1/user/register", "/v1/user/profile", "/v1/billing/checkout", "/v1/billing/portal",
                 "/v1/billing/vipps/activate"):
        assert ALLOWED_ROUTES.fullmatch(path)
    for path in ("/v1/dashboard/status", "/v1/dashboard/workouts/i-123/samples", "/v1/dashboard/internal/sync",
                 "/v1/dashboard/workouts/https://attacker.example", "/v1/dashboard/workouts/i-123/anything"):
        assert not ALLOWED_ROUTES.fullmatch(path)


def test_dashboard_client_config_rejects_injection(settings):
    with pytest.raises(ValueError):
        Settings(**{**settings.__dict__, "firebase_api_key": "client\nsecret"})


def test_user_registration_and_profile_gateway_forwarding(settings, key):
    from dataclasses import replace
    mt_settings = replace(settings, multi_tenant=True)
    calls = []

    def backend(request):
        calls.append(request)
        if request.url.path == "/v1/user/register":
            return httpx.Response(201, json={"id": "new-athlete-sub", "status": "pending_payment"}, headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/user/profile":
            return httpx.Response(200, json={"id": "new-athlete-sub", "status": "pending_payment"}, headers={"Content-Type": "application/json"})
        return httpx.Response(404, json={"error": "not found"}, headers={"Content-Type": "application/json"})

    app, _, _ = setup(mt_settings, key, backend)
    with client_for(app, mt_settings) as client:
        token = bearer(key, settings, sub="new-athlete-sub", email_verified=True)
        # Register
        reg_res = client.post("/dashboard/api/user/register", headers=token, json={"email": "new@example.com"})
        assert reg_res.status_code == 200
        assert reg_res.json()["status"] == "pending_payment"
        assert calls[0].headers.get("x-user-id") == "new-athlete-sub"
        assert json.loads(calls[0].content) == {"email": "new@example.com"}

        # Profile
        prof_res = client.get("/dashboard/api/user/profile", headers=token)
        assert prof_res.status_code == 200
        assert prof_res.json()["status"] == "pending_payment"
        assert calls[1].headers.get("x-user-id") == "new-athlete-sub"


def test_billing_gateway_forwarding(settings, key):
    from dataclasses import replace
    mt_settings = replace(settings, multi_tenant=True)
    calls = []

    def backend(request):
        calls.append(request)
        if request.url.path == "/v1/billing/checkout":
            return httpx.Response(200, json={"checkout_url": "https://stripe.com/checkout"}, headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/billing/portal":
            return httpx.Response(200, json={"portal_url": "https://stripe.com/portal"}, headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/billing/vipps/activate":
            return httpx.Response(200, json={"status": "activated", "user_id": "athlete-sub-1"}, headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/user/intervals-credentials":
            if request.method == "POST":
                return httpx.Response(200, json={"status": "configured", "athlete_id": "i45678"}, headers={"Content-Type": "application/json"})
            return httpx.Response(200, json={"configured": True, "athlete_id": "i45678"}, headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/user/sync":
            return httpx.Response(200, json={"status": "ok", "user_id": "athlete-sub-1"}, headers={"Content-Type": "application/json"})
        return httpx.Response(404, json={"error": "not found"}, headers={"Content-Type": "application/json"})

    app, _, _ = setup(mt_settings, key, backend)
    with client_for(app, mt_settings) as client:
        token = bearer(key, settings, sub="athlete-sub-1", email_verified=True)

        # Checkout
        checkout_res = client.post("/dashboard/api/billing/checkout", headers=token)
        assert checkout_res.status_code == 200
        assert checkout_res.json()["checkout_url"] == "https://stripe.com/checkout"
        assert calls[0].headers.get("x-user-id") == "athlete-sub-1"

        # Portal
        portal_res = client.post("/dashboard/api/billing/portal", headers=token)
        assert portal_res.status_code == 200
        assert portal_res.json()["portal_url"] == "https://stripe.com/portal"
        assert calls[1].headers.get("x-user-id") == "athlete-sub-1"

        # Vipps activate
        vipps_res = client.post("/dashboard/api/billing/vipps/activate", headers=token, json={"agreement_id": "agr_123"})
        assert vipps_res.status_code == 200
        assert vipps_res.json()["status"] == "activated"
        assert calls[2].headers.get("x-user-id") == "athlete-sub-1"
        assert json.loads(calls[2].content) == {"agreement_id": "agr_123"}

        # Intervals credentials save
        save_cred_res = client.post(
            "/dashboard/api/user/intervals-credentials",
            headers=token,
            json={"api_key": "mock-intervals-key", "athlete_id": "i45678"},
        )
        assert save_cred_res.status_code == 200
        assert calls[3].headers.get("x-user-id") == "athlete-sub-1"
        assert json.loads(calls[3].content) == {"api_key": "mock-intervals-key", "athlete_id": "i45678"}

        # Intervals credentials get
        get_cred_res = client.get("/dashboard/api/user/intervals-credentials", headers=token)
        assert get_cred_res.status_code == 200
        assert calls[4].headers.get("x-user-id") == "athlete-sub-1"

        # User sync
        sync_res = client.post("/dashboard/api/user/sync", headers=token, json={"backfill": False})
        assert sync_res.status_code == 200
        assert calls[5].headers.get("x-user-id") == "athlete-sub-1"


def test_chat_gateway_forwarding_and_list_support(settings, key):
    from dataclasses import replace
    mt_settings = replace(settings, multi_tenant=True)
    calls = []

    def backend(request):
        calls.append(request)
        if request.url.path == "/v1/chat/history":
            return httpx.Response(200, json=[
                {"id": "msg_1", "role": "user", "content": "What is my CP?", "created_at": "2026-10-07T10:00:00Z"},
                {"id": "msg_2", "role": "model", "content": "Your CP is 290W.", "created_at": "2026-10-07T10:00:05Z"}
            ], headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/chat/model":
            return httpx.Response(200, json={"model": "gemini-2.5-flash"}, headers={"Content-Type": "application/json"})
        if request.url.path == "/v1/user/goal":
            if request.method == "POST":
                return httpx.Response(200, json={"goal": "Marathon sub-3", "word_count": 2}, headers={"Content-Type": "application/json"})
            return httpx.Response(200, json={"goal": "Marathon sub-3", "word_count": 2}, headers={"Content-Type": "application/json"})
        return httpx.Response(404, json={"error": "not found"}, headers={"Content-Type": "application/json"})

    app, _, _ = setup(mt_settings, key, backend)
    with client_for(app, mt_settings) as client:
        token = bearer(key, settings, sub="athlete-sub-2", email_verified=True)

        # Chat history returns list through gateway
        hist_res = client.get("/dashboard/api/chat/history", headers=token)
        assert hist_res.status_code == 200
        items = hist_res.json()
        assert isinstance(items, list)
        assert len(items) == 2
        assert items[0]["content"] == "What is my CP?"
        assert calls[0].headers.get("x-user-id") == "athlete-sub-2"

        # Chat model returns model dict
        model_res = client.get("/dashboard/api/chat/model", headers=token)
        assert model_res.status_code == 200
        assert model_res.json()["model"] == "gemini-2.5-flash"
        assert calls[1].headers.get("x-user-id") == "athlete-sub-2"

        # Goal get and save
        goal_res = client.get("/dashboard/api/user/goal", headers=token)
        assert goal_res.status_code == 200
        assert goal_res.json()["goal"] == "Marathon sub-3"
        assert calls[2].headers.get("x-user-id") == "athlete-sub-2"

        save_goal = client.post("/dashboard/api/user/goal", headers=token, json={"goal": "Marathon sub-3"})
        assert save_goal.status_code == 200
        assert calls[3].headers.get("x-user-id") == "athlete-sub-2"
