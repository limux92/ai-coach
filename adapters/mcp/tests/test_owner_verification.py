"""Signed synthetic token evidence for the owner bootstrap; no live account IO."""
import copy
import json
from pathlib import Path
import stat
import sys
import time

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra"))
import verify_auth0_owner as verifier

TENANT = "dev-owner-test.us.auth0.com"
CLIENT = "synthetic-bootstrap-client-1234"
ISSUER = f"https://{TENANT}/"
EMAIL = "owner@training.test"
SUBJECT = "auth0|synthetic-owner"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def bundle(signing_key):
    current = int(time.time())
    common = {"iss": ISSUER, "iat": current - 10, "exp": current + 600, "sub": SUBJECT}
    access = {**common, "aud": [verifier.MCP_AUDIENCE, ISSUER + "userinfo"], "azp": CLIENT,
              "scope": "openid profile email coach:read"}
    identity = {**common, "aud": CLIENT, "email": EMAIL, "email_verified": True}
    user_info = {"sub": SUBJECT, "email": EMAIL, "email_verified": True}
    def make(access_changes=None, identity_changes=None, user_changes=None):
        access_claims = {**access, **(access_changes or {})}
        id_claims = {**identity, **(identity_changes or {})}
        sign = lambda claims: jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "owner-signing-key"})
        return {"user_info": {**user_info, **(user_changes or {})}, "tokens": {
            "access_token": sign(access_claims), "id_token": sign(id_claims),
            "refresh_token": "synthetic-refresh-token-must-never-be-written"}}
    return make


@pytest.fixture
def bootstrap():
    return {"tenant": TENANT, "client_id": CLIENT, "connection_id": "unused", "grant_id": "unused"}


@pytest.fixture
def client(signing_key):
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update(kid="owner-signing-key", use="sig", alg="RS256")
    def handler(request):
        assert str(request.url) == ISSUER + ".well-known/jwks.json"
        assert request.method == "GET" and "authorization" not in request.headers
        return httpx.Response(200, json={"keys": [key]})
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as value:
        yield value


def write_private(path, payload):
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


def test_signed_access_id_and_userinfo_bind_one_owner_and_save_only_config(bundle, bootstrap, client, tmp_path):
    login = bundle()
    owner = verifier.verify_owner(login, bootstrap, expected_email=EMAIL.upper(), client=client)
    assert owner.subject == SUBJECT and owner.email_verified is True
    output = tmp_path / "private" / "chat-oauth.json"
    verifier.write_configuration(output, owner)
    assert json.loads(output.read_text()) == {"oauth_issuer": ISSUER,
        "oauth_jwks_url": ISSUER + ".well-known/jwks.json", "owner_subject": SUBJECT}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert all(token not in output.read_text() for token in login["tokens"].values())


@pytest.mark.parametrize("access_changes,identity_changes,user_changes", [
    ({"sub": "auth0|different-signed-user"}, {}, {}),
    ({}, {"sub": "auth0|different-signed-user"}, {}),
    ({}, {}, {"sub": "auth0|tampered-user-info"}),
    ({"azp": "another-client"}, {}, {}),
    ({"client_id": "another-client"}, {}, {}),
    ({}, {"aud": "another-client"}, {}),
    ({"aud": "https://ai-coach-sync-wws5xmx2wa-lz.a.run.app"}, {}, {}),
    ({"scope": "openid email coach:read:other"}, {}, {}),
    ({}, {}, {"email": "different@training.test"}),
])
def test_individually_signed_tokens_cannot_bind_mixed_user_client_or_resource(bundle, bootstrap, client,
                                                                            access_changes, identity_changes, user_changes):
    with pytest.raises(verifier.VerificationError):
        verifier.verify_owner(bundle(access_changes, identity_changes, user_changes), bootstrap, client=client)


def test_expected_email_is_enforced_without_inferred_admin_identity(bundle, bootstrap, client):
    with pytest.raises(verifier.VerificationError, match="unexpected_owner_email"):
        verifier.verify_owner(bundle(), bootstrap, expected_email="another@training.test", client=client)


def test_unverified_signed_email_does_not_bind_or_touch_existing_config(bundle, bootstrap, client, tmp_path):
    owner = verifier.verify_owner(bundle(identity_changes={"email_verified": False}), bootstrap, client=client)
    assert owner.email_verified is False
    path = tmp_path / "chat-oauth.json"
    path.write_text("existing configuration")
    with pytest.raises(verifier.VerificationError, match="email_verification_required"):
        verifier.write_configuration(path, owner)
    assert path.read_text() == "existing configuration"
    # Userinfo cannot override a signed false claim, and stale false userinfo also blocks.
    assert verifier.verify_owner(bundle(user_changes={"email_verified": False}), bootstrap, client=client).email_verified is False


def test_tampered_signature_is_rejected_before_owner_binding(bundle, bootstrap, client):
    login = bundle()
    header, payload, signature = login["tokens"]["access_token"].split(".")
    replacement = ("A" if signature[0] != "A" else "B") + signature[1:]
    login["tokens"]["access_token"] = ".".join((header, payload, replacement))
    with pytest.raises(verifier.VerificationError, match="token_signature_or_claims_invalid"):
        verifier.verify_owner(login, bootstrap, client=client)


@pytest.mark.parametrize("status,body,reason", [
    (302, b"", "jwks_http_failure"),
    (200, b"x" * 100_001, "jwks_response_too_large"),
])
def test_jwks_fetch_is_bounded_and_does_not_follow_redirects(bundle, bootstrap, status, body, reason):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(status, content=body, headers={"Location": "https://other.auth0.com/jwks"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(verifier.VerificationError, match=reason):
            verifier.verify_owner(bundle(), bootstrap, client=client)
    assert requests == [ISSUER + ".well-known/jwks.json"]


def test_cli_reports_booleans_without_tokens_subject_or_email_by_default(bundle, bootstrap, client, tmp_path, monkeypatch, capsys):
    login = bundle()
    login_file, bootstrap_file, output = (tmp_path / name for name in ("login.json", "bootstrap.json", "oauth.json"))
    write_private(login_file, login)
    write_private(bootstrap_file, bootstrap)
    verify = verifier.verify_owner
    monkeypatch.setattr(verifier, "verify_owner", lambda data, config, **kwargs: verify(data, config, client=client, **kwargs))
    code = verifier.main(["--login-result", str(login_file), "--bootstrap", str(bootstrap_file),
                          "--oauth-config", str(output), "--expected-email", EMAIL])
    text = capsys.readouterr().out
    assert code == 0 and json.loads(text)["config_written"] is True
    assert SUBJECT not in text and EMAIL not in text
    assert all(token not in text for token in login["tokens"].values())


def test_cli_unverified_email_reports_action_without_writing(bundle, bootstrap, client, tmp_path, monkeypatch, capsys):
    login = bundle(identity_changes={"email_verified": False}, user_changes={"email_verified": False})
    login_file, bootstrap_file, output = (tmp_path / name for name in ("login.json", "bootstrap.json", "oauth.json"))
    write_private(login_file, login)
    write_private(bootstrap_file, bootstrap)
    verify = verifier.verify_owner
    monkeypatch.setattr(verifier, "verify_owner", lambda data, config, **kwargs: verify(data, config, client=client, **kwargs))
    code = verifier.main(["--login-result", str(login_file), "--bootstrap", str(bootstrap_file), "--oauth-config", str(output)])
    result = json.loads(capsys.readouterr().out)
    assert code == 2 and result["status"] == "email_verification_required"
    assert result["tokens_verified"] is True and result["owner_bound"] is False
    assert result["config_written"] is False and not output.exists()


def test_shared_readable_credentials_are_rejected(tmp_path):
    path = tmp_path / "login.json"
    path.write_text('{}')
    path.chmod(0o644)
    with pytest.raises(verifier.VerificationError, match="input_file_must_be_private"):
        verifier.read_private_json(path)
