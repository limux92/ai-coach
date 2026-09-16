#!/usr/bin/env python3
"""Verify an official Auth0 CLI login before binding the coach OAuth owner.

Read private login/bootstrap files; never log, refresh, forward, or save their
credentials. Both access and ID tokens must be signed by the configured tenant,
bound to the bootstrap client, and identify the same user as user_info. A signed
verified email is required before writing the three non-secret adapter settings.
This verifies token evidence, not the CLI's earlier callback/state handling.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

import httpx
import jwt


ROOT = Path(__file__).resolve().parent.parent
MCP_AUDIENCE = "https://ai-coach-chat-600465847441.europe-north1.run.app/mcp"
MAX_JWKS_BYTES = 100_000
MAX_FILE_BYTES = 131_072
MAX_TOKEN_LENGTH = 16_384


class VerificationError(ValueError):
    """Stable error code only, never token claims or an upstream response body."""


@dataclass(frozen=True, repr=False)
class VerifiedOwner:
    issuer: str
    jwks_url: str
    subject: str
    email: str
    email_verified: bool

    def configuration(self):
        if not self.email_verified:
            raise VerificationError("email_verification_required")
        return {"oauth_issuer": self.issuer, "oauth_jwks_url": self.jwks_url,
                "owner_subject": self.subject}


def read_private_json(path: Path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                    or metadata.st_uid != os.getuid() or metadata.st_size > MAX_FILE_BYTES):
                raise VerificationError("input_file_must_be_private_and_bounded")
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise VerificationError("input_file_must_be_private_and_bounded")
        result = json.loads(raw)
    except VerificationError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise VerificationError("private_input_file_invalid") from None
    if not isinstance(result, dict):
        raise VerificationError("private_input_file_invalid")
    return result


def tenant_configuration(bootstrap):
    if not isinstance(bootstrap, dict):
        raise VerificationError("bootstrap_invalid")
    tenant, client_id = bootstrap.get("tenant"), bootstrap.get("client_id")
    if (not isinstance(tenant, str)
            or not re.fullmatch(r"(?:[a-z0-9][a-z0-9-]*\.)+auth0\.com", tenant)
            or not isinstance(client_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", client_id)):
        raise VerificationError("bootstrap_tenant_or_client_invalid")
    issuer = f"https://{tenant}/"
    return issuer, issuer + ".well-known/jwks.json", client_id


def fetch_keys(client, jwks_url):
    try:
        with client.stream("GET", jwks_url, follow_redirects=False) as response:
            if response.status_code != 200:
                raise VerificationError("jwks_http_failure")
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_JWKS_BYTES:
                    raise VerificationError("jwks_response_too_large")
        document = json.loads(payload)
    except VerificationError:
        raise
    except (httpx.HTTPError, ValueError, UnicodeError, OSError):
        raise VerificationError("jwks_fetch_or_decode_failed") from None
    keys = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
        raise VerificationError("jwks_keys_invalid")
    return keys


def verify_token(token, *, keys, issuer, audience):
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise VerificationError("token_missing_or_invalid")
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
            raise VerificationError("token_algorithm_or_key_invalid")
        matching = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
        if len(matching) != 1:
            raise VerificationError("token_signing_key_ambiguous_or_missing")
        jwk = matching[0]
        if (jwk.get("kty") != "RSA" or jwk.get("alg", "RS256") != "RS256"
                or jwk.get("use", "sig") != "sig"
                or any(field in jwk for field in ("d", "p", "q", "dp", "dq", "qi", "k"))):
            raise VerificationError("token_signing_key_invalid")
        key = jwt.PyJWK.from_dict(jwk, algorithm="RS256").key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=issuer,
                            options={"require": ["iss", "sub", "aud", "iat", "exp"]}, leeway=0)
    except VerificationError:
        raise
    except Exception:
        raise VerificationError("token_signature_or_claims_invalid") from None
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 256 or any(char.isspace() for char in subject):
        raise VerificationError("token_subject_invalid")
    for name in ("iat", "exp"):
        value = claims.get(name)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise VerificationError("token_time_claims_invalid")
    if claims["exp"] <= claims["iat"]:
        raise VerificationError("token_time_claims_invalid")
    return claims


def _email(value):
    if (not isinstance(value, str) or value != value.strip() or len(value) > 320
            or value.count("@") != 1 or any(char.isspace() for char in value)
            or not all(value.split("@"))):
        raise VerificationError("owner_email_missing_or_invalid")
    return value


def verify_owner(login, bootstrap, *, expected_email=None, client=None):
    issuer, jwks_url, client_id = tenant_configuration(bootstrap)
    if not isinstance(login, dict) or not isinstance(login.get("tokens"), dict) or not isinstance(login.get("user_info"), dict):
        raise VerificationError("login_result_invalid")
    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False)
    try:
        keys = fetch_keys(client, jwks_url)
    finally:
        if owned_client:
            client.close()
    tokens, user_info = login["tokens"], login["user_info"]
    access = verify_token(tokens.get("access_token"), keys=keys, issuer=issuer, audience=MCP_AUDIENCE)
    identity = verify_token(tokens.get("id_token"), keys=keys, issuer=issuer, audience=client_id)
    # Auth0 access tokens can contain both the MCP resource and /userinfo audience.
    # JWT audience verification requires the exact MCP URL, never a prefix or the
    # backend URL. An ID token must be issued specifically to this bootstrap client.
    if identity.get("aud") != client_id:
        raise VerificationError("id_token_client_audience_invalid")
    access_clients = [access[field] for field in ("client_id", "azp") if field in access]
    if not access_clients or any(value != client_id for value in access_clients):
        raise VerificationError("access_token_client_binding_invalid")
    if "azp" in identity and identity["azp"] != client_id:
        raise VerificationError("id_token_client_binding_invalid")
    if not isinstance(access.get("scope"), str) or "coach:read" not in access["scope"].split():
        raise VerificationError("access_token_read_scope_missing")
    if not (access["sub"] == identity["sub"] == user_info.get("sub")):
        raise VerificationError("owner_subject_binding_mismatch")
    email = _email(identity.get("email"))
    if _email(user_info.get("email")).casefold() != email.casefold():
        raise VerificationError("owner_email_binding_mismatch")
    if expected_email is not None and _email(expected_email).casefold() != email.casefold():
        raise VerificationError("unexpected_owner_email")
    verified = identity.get("email_verified") is True and user_info.get("email_verified") is True
    return VerifiedOwner(issuer, jwks_url, access["sub"], email, verified)


def write_configuration(path: Path, owner: VerifiedOwner):
    config = owner.configuration()  # Must fail before touching the output path.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            json.dump(config, temporary, sort_keys=True, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login-result", type=Path, default=ROOT / ".local/auth0-owner-login.json")
    parser.add_argument("--bootstrap", type=Path, default=ROOT / ".local/auth0-bootstrap.json")
    parser.add_argument("--oauth-config", type=Path, default=ROOT / ".local/chat-oauth.json")
    parser.add_argument("--expected-email", help="Require this owner email; never inferred from an administrator profile")
    parser.add_argument("--show-email", action="store_true", help="Include verified token email in the safe result")
    args = parser.parse_args(argv)
    try:
        login = read_private_json(args.login_result)
        bootstrap = read_private_json(args.bootstrap)
        owner = verify_owner(login, bootstrap, expected_email=args.expected_email)
        result = {"status": "owner_bound" if owner.email_verified else "email_verification_required",
                  "tokens_verified": True, "email_verified": owner.email_verified,
                  "owner_bound": owner.email_verified, "config_written": False}
        if owner.email_verified:
            write_configuration(args.oauth_config, owner)
            result["config_written"] = True
        if args.show_email:
            result["email"] = owner.email
        print(json.dumps(result, sort_keys=True))
        return 0 if owner.email_verified else 2
    except Exception as exc:
        code = str(exc) if isinstance(exc, VerificationError) else "owner_verification_failed"
        print(json.dumps({"status": "verification_failed", "reason": code,
                          "tokens_verified": False, "email_verified": False,
                          "owner_bound": False, "config_written": False}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
