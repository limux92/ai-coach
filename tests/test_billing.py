import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_coach.billing import (
    create_checkout_session,
    create_portal_session,
    process_stripe_event,
    verify_stripe_signature,
)
from ai_coach.config import Settings
from ai_coach.main import app
import copy
from ai_coach.storage import safe_id


class MemoryStore:
    def __init__(self):
        self.documents = {}

    def get(self, collection, doc_id):
        safe_id(doc_id)
        return copy.deepcopy(self.documents.get((collection, doc_id)))

    def put(self, collection, doc_id, data, *, merge=True):
        safe_id(doc_id)
        if merge:
            old = self.documents.get((collection, doc_id), {})
            self.documents[(collection, doc_id)] = {**old, **copy.deepcopy(data)}
        else:
            self.documents[(collection, doc_id)] = copy.deepcopy(data)


def _test_settings(**kwargs):
    defaults = {
        "project": "test-project",
        "bucket": "test-bucket",
        "stripe_secret_key": "sk_test_123",
        "stripe_webhook_secret": "whsec_test_abc",
        "stripe_price_id": "price_test_monthly",
        "dashboard_url": "http://localhost:8000/dashboard",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def test_verify_stripe_signature():
    secret = "whsec_secret_123"
    payload = b'{"type": "checkout.session.completed"}'
    now = int(time.time())

    # Valid signature
    signed_payload = f"{now}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    header = f"t={now},v1={sig}"
    assert verify_stripe_signature(payload, header, secret) is True

    # Bad signature
    bad_header = f"t={now},v1=bad_signature"
    assert verify_stripe_signature(payload, bad_header, secret) is False

    # Expired timestamp (older than tolerance)
    old_time = now - 400
    old_signed = f"{old_time}.".encode() + payload
    old_sig = hmac.new(secret.encode(), old_signed, hashlib.sha256).hexdigest()
    assert verify_stripe_signature(payload, f"t={old_time},v1={old_sig}", secret, tolerance=300) is False

    # Missing parameters
    assert verify_stripe_signature(payload, None, secret) is False
    assert verify_stripe_signature(payload, header, None) is False


def test_create_checkout_session():
    settings = _test_settings()

    def handler(request: httpx.Request):
        assert request.url == "https://api.stripe.com/v1/checkout/sessions"
        assert request.headers["authorization"] == "Bearer sk_test_123"
        return httpx.Response(200, json={"id": "cs_test_999", "url": "https://checkout.stripe.com/c/pay/cs_test_999"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    result = create_checkout_session("user-42", "athlete@example.com", settings, client=client)
    assert result["session_id"] == "cs_test_999"
    assert result["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_test_999"


def test_create_checkout_session_missing_config():
    settings = _test_settings(stripe_secret_key=None)
    with pytest.raises(RuntimeError, match="Stripe secret key is not configured"):
        create_checkout_session("user-42", "athlete@example.com", settings)


def test_create_portal_session():
    settings = _test_settings()

    def handler(request: httpx.Request):
        assert request.url == "https://api.stripe.com/v1/billing_portal/sessions"
        return httpx.Response(200, json={"url": "https://billing.stripe.com/p/session/portal_123"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    result = create_portal_session("cus_test_123", settings, client=client)
    assert result["portal_url"] == "https://billing.stripe.com/p/session/portal_123"


def test_process_stripe_event_activation():
    store = MemoryStore()
    user_id = "athlete-123"
    store.put("users", user_id, {
        "id": user_id,
        "email": "athlete@example.com",
        "status": "pending_payment",
    })

    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": user_id,
                "customer": "cus_stripe_123",
                "subscription": "sub_stripe_123",
            }
        }
    }

    res = process_stripe_event(event, store)
    assert res["status"] == "activated"
    updated = store.get("users", user_id)
    assert updated["status"] == "active"
    assert updated["stripe_customer_id"] == "cus_stripe_123"
    assert updated["stripe_subscription_id"] == "sub_stripe_123"


def test_billing_api_endpoints(monkeypatch):
    store = MemoryStore()
    settings = _test_settings()
    monkeypatch.setattr("ai_coach.main.store", lambda: store)
    monkeypatch.setattr("ai_coach.main.settings", lambda: settings)

    client = TestClient(app)

    # 401 without X-User-Id
    res = client.post("/v1/billing/checkout")
    assert res.status_code == 401

    user_id = "athlete-abc"
    store.put("users", user_id, {
        "id": user_id,
        "email": "abc@example.com",
        "status": "pending_payment",
    })

    # Mock checkout creation
    monkeypatch.setattr(
        "ai_coach.main.create_checkout",
        lambda uid, email, stg: {"checkout_url": "https://stripe.com/checkout", "session_id": "cs_abc"},
    )
    res = client.post("/v1/billing/checkout", headers={"X-User-Id": user_id})
    assert res.status_code == 200
    assert res.json()["checkout_url"] == "https://stripe.com/checkout"

    # Portal without customer id -> 400
    res = client.post("/v1/billing/portal", headers={"X-User-Id": user_id})
    assert res.status_code == 400

    # User with customer id
    store.put("users", user_id, {
        "id": user_id,
        "stripe_customer_id": "cus_abc_999",
        "status": "active",
    })
    monkeypatch.setattr(
        "ai_coach.main.create_portal_session",
        lambda cus, stg: {"portal_url": "https://stripe.com/portal"},
    )
    res = client.post("/v1/billing/portal", headers={"X-User-Id": user_id})
    assert res.status_code == 200
    assert res.json()["portal_url"] == "https://stripe.com/portal"

    # Webhook endpoint test with valid signature
    event_payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": user_id,
                "customer": "cus_abc_999",
                "subscription": "sub_abc_999",
            }
        }
    }).encode("utf-8")

    now = int(time.time())
    signed = f"{now}.".encode() + event_payload
    sig = hmac.new(settings.stripe_webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={now},v1={sig}"

    res = client.post(
        "/v1/webhook/stripe",
        content=event_payload,
        headers={"Stripe-Signature": header, "Content-Type": "application/json"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "activated"


def test_vipps_token_and_agreement():
    settings = _test_settings(
        billing_provider="vipps",
        vipps_client_id="test_client_id",
        vipps_client_secret="test_client_secret",
        vipps_subscription_key="test_sub_key",
        vipps_merchant_serial_number="123456",
        vipps_api_url="https://apitest.vipps.no",
        vipps_price_amount=19900,
        vipps_currency="NOK",
    )

    def handler(request: httpx.Request):
        if request.url == "https://apitest.vipps.no/accesstoken/get":
            assert request.headers["client_id"] == "test_client_id"
            assert request.headers["client_secret"] == "test_client_secret"
            return httpx.Response(200, json={"token_type": "Bearer", "access_token": "vipps_token_xyz"})
        if request.url == "https://apitest.vipps.no/recurring/v3/agreements":
            assert request.headers["authorization"] == "Bearer vipps_token_xyz"
            assert request.headers["merchant-serial-number"] == "123456"
            body = json.loads(request.content)
            assert body["pricing"]["amount"] == 19900
            assert body["pricing"]["currency"] == "NOK"
            return httpx.Response(200, json={
                "agreementId": "agr_test_888",
                "vippsConfirmationUrl": "https://api.vipps.no/recurring/v3/agreements/agr_test_888/redirect",
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    from ai_coach.billing import create_checkout, create_vipps_agreement, process_vipps_event

    res = create_vipps_agreement("athlete-99", "ath@example.no", settings, client=client)
    assert res["session_id"] == "agr_test_888"
    assert "agr_test_888" in res["checkout_url"]
    assert res["provider"] == "vipps"

    # Unified router returns Vipps
    unified = create_checkout("athlete-99", "ath@example.no", settings, client=client)
    assert unified["provider"] == "vipps"

    # Vipps activation
    store = MemoryStore()
    store.put("users", "athlete-99", {"id": "athlete-99", "status": "pending_payment"})
    act_res = process_vipps_event({"agreementId": "agr_test_888", "status": "ACTIVE", "user_id": "athlete-99"}, store)
    assert act_res["status"] == "activated"
    assert store.get("users", "athlete-99")["status"] == "active"
    assert store.get("users", "athlete-99")["billing_provider"] == "vipps"


def test_registration_with_terms_accepted(monkeypatch):
    store = MemoryStore()
    settings = _test_settings()
    monkeypatch.setattr("ai_coach.main.store", lambda: store)
    monkeypatch.setattr("ai_coach.main.settings", lambda: settings)

    client = TestClient(app)

    # Register with terms accepted
    res = client.post(
        "/v1/user/register",
        headers={"X-User-Id": "athlete-terms-1"},
        json={"email": "athlete@example.com", "display_name": "Test Athlete", "terms_accepted": True},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["terms_accepted"] is True
    assert data["terms_accepted_at"] is not None
    assert data["status"] == "pending_payment"

    # Vipps activate endpoint test
    act_res = client.post(
        "/v1/billing/vipps/activate",
        headers={"X-User-Id": "athlete-terms-1"},
        json={"agreement_id": "agr_live_123"},
    )
    assert act_res.status_code == 200
    assert act_res.json()["status"] == "activated"
    assert store.get("users", "athlete-terms-1")["status"] == "active"
