import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ai_coach.config import Settings


def verify_stripe_signature(
    payload: bytes,
    sig_header: str | None,
    secret: str | None,
    tolerance: int = 300,
) -> bool:
    if not sig_header or not secret:
        return False
    pairs = {}
    for item in sig_header.split(","):
        parts = item.strip().split("=", 1)
        if len(parts) == 2:
            pairs[parts[0]] = parts[1]
    timestamp = pairs.get("t")
    signature = pairs.get("v1")
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
        if abs(time.time() - ts) > tolerance:
            return False
    except ValueError:
        return False

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected_sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, signature)


def create_checkout_session(
    user_id: str,
    email: str | None,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    if not settings.stripe_secret_key:
        raise RuntimeError("Stripe secret key is not configured.")
    if not settings.stripe_price_id:
        raise RuntimeError("Stripe price ID is not configured.")

    data = {
        "mode": "subscription",
        "payment_method_types[0]": "card",
        "line_items[0][price]": settings.stripe_price_id,
        "line_items[0][quantity]": "1",
        "client_reference_id": user_id,
        "metadata[user_id]": user_id,
        "subscription_data[metadata][user_id]": user_id,
        "success_url": f"{settings.dashboard_url}?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": settings.dashboard_url,
    }
    if email:
        data["customer_email"] = email

    headers = {
        "Authorization": f"Bearer {settings.stripe_secret_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    own_client = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        resp = c.post("https://api.stripe.com/v1/checkout/sessions", data=data, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Stripe error ({resp.status_code}): {resp.text}")
        payload = resp.json()
        return {
            "checkout_url": payload["url"],
            "session_id": payload["id"],
            "provider": "stripe",
        }
    finally:
        if own_client:
            c.close()


def create_portal_session(
    customer_id: str,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    if not settings.stripe_secret_key:
        raise RuntimeError("Stripe secret key is not configured.")

    data = {
        "customer": customer_id,
        "return_url": settings.dashboard_url,
    }
    headers = {
        "Authorization": f"Bearer {settings.stripe_secret_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    own_client = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        resp = c.post("https://api.stripe.com/v1/billing_portal/sessions", data=data, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Stripe portal error ({resp.status_code}): {resp.text}")
        payload = resp.json()
        return {"portal_url": payload["url"]}
    finally:
        if own_client:
            c.close()


def process_stripe_event(event: dict[str, Any], store: Any) -> dict[str, Any]:
    event_type = event.get("type")
    now_iso = datetime.now(timezone.utc).isoformat()

    if event_type == "checkout.session.completed":
        session = event.get("data", {}).get("object", {})
        user_id = session.get("client_reference_id") or session.get("metadata", {}).get("user_id")
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")

        if user_id:
            existing = store.get("users", user_id) or {}
            data = {
                "status": "active",
                "stripe_customer_id": customer_id,
                "stripe_subscription_id": subscription_id,
                "plan": "athlete_subscription",
                "billing_provider": "stripe",
                "updated_at": now_iso,
            }
            if not existing:
                data.update({
                    "id": user_id,
                    "role": "athlete",
                    "created_at": now_iso,
                })
            store.put("users", user_id, data, merge=True)
            return {"status": "activated", "user_id": user_id}

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub = event.get("data", {}).get("object", {})
        metadata = sub.get("metadata", {})
        user_id = metadata.get("user_id")
        if user_id:
            store.put("users", user_id, {"status": "inactive", "updated_at": now_iso}, merge=True)
            return {"status": "deactivated", "user_id": user_id}

    elif event_type == "customer.subscription.updated":
        sub = event.get("data", {}).get("object", {})
        metadata = sub.get("metadata", {})
        user_id = metadata.get("user_id")
        sub_status = sub.get("status")
        target_status = "active" if sub_status in ("active", "trialing") else "past_due"
        if user_id:
            store.put("users", user_id, {"status": target_status, "updated_at": now_iso}, merge=True)
            return {"status": target_status, "user_id": user_id}

    return {"received": True, "type": event_type}


# --- Vipps MobilePay Recurring Integration ---

def get_vipps_token(settings: Settings, client: httpx.Client | None = None) -> str:
    if not (settings.vipps_client_id and settings.vipps_client_secret and settings.vipps_subscription_key):
        raise RuntimeError("Vipps API credentials (client_id, client_secret, subscription_key) are not configured.")

    headers = {
        "client_id": settings.vipps_client_id,
        "client_secret": settings.vipps_client_secret,
        "Ocp-Apim-Subscription-Key": settings.vipps_subscription_key,
    }
    own_client = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        url = f"{settings.vipps_api_url.rstrip('/')}/accesstoken/get"
        resp = c.post(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Vipps authentication error ({resp.status_code}): {resp.text}")
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Vipps access token response missing access_token")
        return token
    finally:
        if own_client:
            c.close()


def create_vipps_agreement(
    user_id: str,
    email: str | None,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    if not settings.vipps_merchant_serial_number:
        raise RuntimeError("Vipps merchant serial number is not configured.")

    own_client = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        token = get_vipps_token(settings, client=c)
        headers = {
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": settings.vipps_subscription_key or "",
            "Merchant-Serial-Number": settings.vipps_merchant_serial_number,
            "Content-Type": "application/json",
            "Idempotency-Key": f"{user_id}-{int(time.time())}",
        }
        payload = {
            "pricing": {
                "type": "RECURRING",
                "amount": settings.vipps_price_amount,
                "currency": settings.vipps_currency,
            },
            "interval": {
                "unit": "MONTH",
                "count": 1,
            },
            "productName": "AI Coach Abonnement",
            "productDescription": "Månedlig personlig AI treningsveiledning og fysiologisk analyse",
            "merchantRedirectUrl": f"{settings.dashboard_url}?agreement_id={{agreementId}}",
            "merchantAgreementUrl": f"{settings.dashboard_url}?view=terms",
        }
        url = f"{settings.vipps_api_url.rstrip('/')}/recurring/v3/agreements"
        resp = c.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Vipps agreement creation error ({resp.status_code}): {resp.text}")
        data = resp.json()
        agreement_id = data.get("agreementId")
        conf_url = data.get("vippsConfirmationUrl") or f"{settings.vipps_api_url.rstrip('/')}/recurring/v3/agreements/{agreement_id}/redirect"
        return {
            "checkout_url": conf_url,
            "session_id": agreement_id,
            "provider": "vipps",
        }
    finally:
        if own_client:
            c.close()


def verify_vipps_agreement(
    agreement_id: str,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    own_client = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        token = get_vipps_token(settings, client=c)
        headers = {
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": settings.vipps_subscription_key or "",
            "Merchant-Serial-Number": settings.vipps_merchant_serial_number or "",
        }
        url = f"{settings.vipps_api_url.rstrip('/')}/recurring/v3/agreements/{agreement_id}"
        resp = c.get(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Vipps agreement lookup error ({resp.status_code}): {resp.text}")
        return resp.json()
    finally:
        if own_client:
            c.close()


def process_vipps_event(event: dict[str, Any], store: Any) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    agreement_id = event.get("agreementId") or event.get("agreement_id")
    status = (event.get("status") or event.get("agreementStatus") or "").upper()
    user_id = event.get("user_id")

    if status in ("ACTIVE", "ACCEPTED"):
        if user_id:
            store.put("users", user_id, {
                "status": "active",
                "vipps_agreement_id": agreement_id,
                "plan": "athlete_subscription",
                "billing_provider": "vipps",
                "updated_at": now_iso,
            }, merge=True)
            return {"status": "activated", "user_id": user_id, "agreement_id": agreement_id}
    elif status in ("STOPPED", "EXPIRED"):
        if user_id:
            store.put("users", user_id, {
                "status": "inactive",
                "updated_at": now_iso,
            }, merge=True)
            return {"status": "deactivated", "user_id": user_id, "agreement_id": agreement_id}

    return {"received": True, "agreement_id": agreement_id, "status": status or "unknown"}


def create_checkout(
    user_id: str,
    email: str | None,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Unified checkout router based on configured billing_provider."""
    if settings.billing_provider == "vipps":
        return create_vipps_agreement(user_id, email, settings, client=client)
    return create_checkout_session(user_id, email, settings, client=client)
