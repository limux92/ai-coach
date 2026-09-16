#!/usr/bin/env python3
"""Deploy the owner-authorized MCP resource server; never expose the data backend.

Requires a real, configured OAuth issuer/JWKS/owner subject. This script cannot
create an OAuth provider, establish ownership from a subject string, complete
consent, or register the chat connection. Public deployment is not a successful
owner login: its result deliberately leaves ready_for_chat false.

Cloud Run custom authentication header and scoped public IAM documentation:
https://docs.cloud.google.com/run/docs/authenticating/service-to-service
https://docs.cloud.google.com/run/docs/authenticating/public
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cloud import Cloud

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "adapters" / "mcp" / "src"))
from ai_coach_mcp.config import Settings, https_url

PROJECT = "magne-ai-coach-20260915"
PROJECT_NUMBER = "600465847441"
REGION = "europe-north1"
SERVICE = "ai-coach-chat"
BACKEND_SERVICE = "ai-coach-sync"
BACKEND_URL = "https://ai-coach-sync-wws5xmx2wa-lz.a.run.app"
PUBLIC_URL = "https://ai-coach-chat-600465847441.europe-north1.run.app/mcp"
RUNTIME = f"ai-coach-chat@{PROJECT}.iam.gserviceaccount.com"
BUILDER = f"ai-coach-build@{PROJECT}.iam.gserviceaccount.com"
MAX_RESPONSE_BYTES = 100_000
PUBLIC_MEMBERS = frozenset({"allUsers", "allAuthenticatedUsers"})


class DeploymentError(RuntimeError):
    """Fixed operational message; never includes HTTP bodies or credentials."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, method="GET", headers=None, body=None):
    """Bounded direct requests; authorization is never forwarded on redirects."""
    request_headers = {"User-Agent": "AI-Coach-Deployment/1.0", **(headers or {})}
    encoded = None if body is None else json.dumps(body).encode()
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=request_headers, data=encoded)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(req, timeout=45)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DeploymentError("HTTP preflight request failed") from None
    with response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise DeploymentError("HTTP preflight response exceeds size limit")
        return Response(response.status, {key.lower(): value for key, value in response.headers.items()}, data)


def json_response(response, label):
    if response.status != 200:
        raise DeploymentError(f"{label} did not return HTTP 200")
    try:
        value = json.loads(response.body)
    except (ValueError, UnicodeError):
        raise DeploymentError(f"{label} returned invalid JSON") from None
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} returned an invalid document")
    return value


def load_settings(path: Path) -> Settings:
    try:
        if path.stat().st_size > 16_384:
            raise DeploymentError("OAuth config exceeds size limit")
        config = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise DeploymentError("Read a valid non-secret OAuth configuration file") from None
    expected = {"oauth_issuer", "oauth_jwks_url", "owner_subject"}
    if not isinstance(config, dict) or set(config) != expected:
        raise DeploymentError("OAuth config requires exactly oauth_issuer, oauth_jwks_url and owner_subject")
    for key, value in config.items():
        if (not isinstance(value, str) or not value.strip() or value != value.strip()
                or any(char.isspace() for char in value)
                or any(marker in value.casefold() for marker in ("your-", "placeholder", "changeme", "exact-immutable", "<", ">", "${"))):
            raise DeploymentError("OAuth configuration must contain real issuer, JWKS and owner subject values")
    for key in ("oauth_issuer", "oauth_jwks_url"):
        hostname = urlsplit(config[key]).hostname or ""
        if (hostname in {"localhost", "example.com", "example.org", "example.net"}
                or hostname.endswith((".localhost", ".invalid", ".example", ".example.com", ".example.org", ".example.net"))):
            raise DeploymentError("OAuth configuration must use the real provider hostname")
    try:
        return Settings(backend_url=BACKEND_URL, backend_allowed_host=urlsplit(BACKEND_URL).hostname,
                        public_url=PUBLIC_URL, **config)
    except (TypeError, ValueError):
        raise DeploymentError("OAuth configuration does not satisfy adapter Settings") from None



def load_dashboard_config(path: Path, settings: Settings) -> Settings:
    """Read only our dedicated SPA's nonsecret deployment receipt."""
    try:
        if path.stat().st_size > 16_384:
            raise DeploymentError("Dashboard configuration exceeds size limit")
        config = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise DeploymentError("Read a valid non-secret dashboard configuration file") from None
    expected = {"version", "tenant", "audience", "client_id", "grant_id", "connection_id", "callback"}
    if not isinstance(config, dict) or set(config) != expected:
        raise DeploymentError("Dashboard config requires exactly version, tenant, audience, client_id, grant_id, connection_id and callback")
    if (type(config["version"]) is not int or config["version"] != 1
            or config["tenant"] != urlsplit(settings.oauth_issuer).netloc
            or config["audience"] != settings.public_url
            or config["callback"] != settings.public_url.removesuffix("/mcp") + "/dashboard/"):
        raise DeploymentError("Dashboard configuration must match this OAuth tenant, API audience and callback")
    for key in ("client_id", "grant_id", "connection_id"):
        value = config[key]
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", value):
            raise DeploymentError("Dashboard configuration contains an invalid public identifier")
    return replace(settings, dashboard_client_id=config["client_id"])


def preserve_dashboard_config(settings: Settings, existing) -> Settings:
    """A later MCP-only deployment must not accidentally remove dashboard login."""
    if settings.dashboard_client_id or existing is None:
        return settings
    containers = existing.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        return settings
    entries = [entry for entry in containers[0].get("env", []) if entry.get("name") == "DASHBOARD_CLIENT_ID"]
    if not entries:
        return settings
    if len(entries) != 1 or set(entries[0]) != {"name", "value"}:
        raise DeploymentError("Existing dashboard client configuration must be a single non-secret environment value")
    try:
        return replace(settings, dashboard_client_id=entries[0]["value"])
    except (ValueError, TypeError):
        raise DeploymentError("Existing dashboard client configuration is invalid") from None


def _same_origin_url(value, issuer):
    if not isinstance(value, str):
        return False
    try:
        https_url(value, "Provider endpoint")
    except (ValueError, TypeError):
        return False
    return urlsplit(value).netloc == urlsplit(issuer).netloc


def _public_signing_key(key):
    if not isinstance(key, dict) or any(name in key for name in ("d", "p", "q", "dp", "dq", "qi", "k")):
        return False
    if (not isinstance(key.get("kid"), str) or not 1 <= len(key["kid"]) <= 128
            or key.get("use", "sig") != "sig"):
        return False
    if key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256":
        fields = ("n", "e")
    elif (key.get("kty") == "EC" and key.get("crv") == "P-256"
          and key.get("alg", "ES256") == "ES256"):
        fields = ("x", "y")
    else:
        return False
    for name in fields:
        value = key.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return False
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except ValueError:
            return False
        if (name == "n" and len(decoded) < 256) or (name in ("x", "y") and len(decoded) != 32) or not decoded:
            return False
    return True


def verify_provider(settings: Settings, http=request):
    issuer = urlsplit(settings.oauth_issuer)
    origin = f"https://{issuer.netloc}"
    issuer_path = issuer.path.rstrip("/")
    oauth_url = origin + "/.well-known/oauth-authorization-server" + issuer_path
    oidc_url = settings.oauth_issuer.rstrip("/") + "/.well-known/openid-configuration"
    response = http(oauth_url)
    if response.status == 404:
        response = http(oidc_url)
    metadata = json_response(response, "OAuth provider discovery")
    if metadata.get("issuer") != settings.oauth_issuer:
        raise DeploymentError("Provider discovery issuer does not match configuration")
    if metadata.get("jwks_uri") != settings.oauth_jwks_url:
        raise DeploymentError("Provider discovery JWKS URL does not match configuration")
    if any(not _same_origin_url(metadata.get(key), settings.oauth_issuer)
           for key in ("authorization_endpoint", "token_endpoint")):
        raise DeploymentError("Provider authorization/token endpoints must use its HTTPS origin")
    if ("code" not in metadata.get("response_types_supported", [])
            or "authorization_code" not in metadata.get("grant_types_supported", [])
            or "S256" not in metadata.get("code_challenge_methods_supported", [])):
        raise DeploymentError("Provider discovery must support authorization code and PKCE S256")
    jwks = json_response(http(settings.oauth_jwks_url), "OAuth provider JWKS")
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 32 or not any(_public_signing_key(key) for key in keys):
        raise DeploymentError("Provider must publish supported public RS256 or ES256 signing keys")
    kids = [key.get("kid") for key in keys if isinstance(key, dict) and key.get("kid") is not None]
    if len(set(kids)) != len(kids):
        raise DeploymentError("Provider JWKS contains ambiguous key identifiers")


def policy(cloud, service):
    value = cloud.json("run", "services", "get-iam-policy", service, f"--region={REGION}")
    if not isinstance(value, dict) or not isinstance(value.get("bindings", []), list):
        raise DeploymentError("Cloud Run IAM policy could not be verified")
    return value


def public_bindings(value):
    return [(binding["role"], member) for binding in value.get("bindings", [])
            for member in binding.get("members", []) if member in PUBLIC_MEMBERS]


def verify_backend_private(cloud, http=request):
    service = cloud.json("run", "services", "describe", BACKEND_SERVICE, f"--region={REGION}")
    if not isinstance(service, dict):
        raise DeploymentError("Existing private backend is missing")
    annotations = service.get("metadata", {}).get("annotations", {})
    if annotations.get("run.googleapis.com/invoker-iam-disabled") == "true":
        raise DeploymentError("Existing backend IAM invocation check is disabled")
    if public_bindings(policy(cloud, BACKEND_SERVICE)):
        raise DeploymentError("Existing backend has a public IAM binding")
    if http(BACKEND_URL + "/v1/status").status != 403:
        raise DeploymentError("Existing backend must deny anonymous requests with HTTP 403")


def preflight(cloud, settings, source, http=request):
    if cloud.project != PROJECT:
        raise DeploymentError("This deployment is restricted to the existing AI Coach project")
    if not (source / "Dockerfile").is_file() or not (source / "src" / "ai_coach_mcp" / "app.py").is_file():
        raise DeploymentError("MCP adapter source directory is incomplete")
    verify_provider(settings, http)
    project = cloud.json("projects", "describe", PROJECT)
    if str((project or {}).get("projectNumber")) != PROJECT_NUMBER:
        raise DeploymentError("Unexpected AI Coach project number")
    verify_backend_private(cloud, http)
    if cloud.json("iam", "service-accounts", "describe", BUILDER, allow_missing=True) is None:
        raise DeploymentError("Existing AI Coach build service account is missing")
    member = f"serviceAccount:{RUNTIME}"
    project_policy = cloud.json("projects", "get-iam-policy", PROJECT)
    if any(member in binding.get("members", []) for binding in project_policy.get("bindings", [])):
        raise DeploymentError("Chat runtime must not have project-wide IAM grants")


def make_private(cloud):
    current = policy(cloud, SERVICE)
    for role, member in public_bindings(current):
        if role != "roles/run.invoker":
            raise DeploymentError("Unexpected public role on chat service requires review")
        cloud.command("run", "services", "remove-iam-policy-binding", SERVICE,
                      f"--region={REGION}", f"--member={member}", f"--role={role}", "--condition=None")
    if public_bindings(policy(cloud, SERVICE)):
        raise DeploymentError("Chat service did not become private")


def probe_adapter(settings, *, transport_token=None, http=request):
    origin = settings.public_url.removesuffix("/mcp")
    headers = {"X-Serverless-Authorization": f"Bearer {transport_token}"} if transport_token else {}
    health = json_response(http(origin + "/v1/health", headers=headers), "Adapter health")
    if health != {"service": "ai-coach-mcp", "status": "ok"}:
        raise DeploymentError("Adapter health response was not the expected static response")
    metadata_url = origin + "/.well-known/oauth-protected-resource/mcp"
    discovery = json_response(http(metadata_url, headers=headers), "Protected-resource discovery")
    if (discovery.get("resource") != settings.public_url
            or discovery.get("authorization_servers") != [settings.oauth_issuer]):
        raise DeploymentError("Protected-resource discovery does not match the configured resource and issuer")
    if discovery.get("scopes_supported") is not None and "coach:read" not in discovery["scopes_supported"]:
        raise DeploymentError("Protected-resource discovery omits the required read scope")
    for authorization in (None, "Bearer invalid-deployment-probe"):
        mcp_headers = {**headers, "Accept": "application/json, text/event-stream"}
        if authorization:
            mcp_headers["Authorization"] = authorization
        response = http(settings.public_url, method="POST", headers=mcp_headers,
                        body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        challenge = response.headers.get("www-authenticate", "")
        match = re.search(r'\bresource_metadata="([^"]+)"', challenge)
        if response.status != 401 or not challenge.lower().startswith("bearer ") or not match or match[1] != metadata_url:
            raise DeploymentError("Adapter must deny missing/invalid OAuth with the correct HTTP 401 challenge")


def deploy(cloud, settings, source, *, http=request, check_only=False):
    source = source.resolve()
    preflight(cloud, settings, source, http)
    existing = cloud.json("run", "services", "describe", SERVICE, f"--region={REGION}", allow_missing=True)
    settings = preserve_dashboard_config(settings, existing)
    if settings.dashboard_client_id and not (source / "static" / "dashboard" / "index.html").is_file():
        raise DeploymentError("Build the dashboard before deploying its configured login")
    if check_only:
        return {"project": PROJECT, "preflight_verified": True, "deployed": False,
                "owner_login_verified": False, "ready_for_chat": False}
    if cloud.json("iam", "service-accounts", "describe", RUNTIME, allow_missing=True) is None:
        cloud.command("iam", "service-accounts", "create", "ai-coach-chat",
                      "--display-name=AI Coach read-only chat adapter")
    cloud.command("run", "services", "add-iam-policy-binding", BACKEND_SERVICE,
                  f"--region={REGION}", f"--member=serviceAccount:{RUNTIME}",
                  "--role=roles/run.invoker", "--condition=None")
    if existing is not None:
        make_private(cloud)
    env = {"BACKEND_URL": settings.backend_url, "BACKEND_ALLOWED_HOST": settings.backend_allowed_host,
           "MCP_PUBLIC_URL": settings.public_url, "OAUTH_ISSUER": settings.oauth_issuer,
           "OAUTH_JWKS_URL": settings.oauth_jwks_url, "OAUTH_OWNER_SUBJECT": settings.owner_subject}
    if settings.dashboard_client_id:
        env["DASHBOARD_CLIENT_ID"] = settings.dashboard_client_id
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as temporary:
        json.dump(env, temporary)
        temporary.flush()
        cloud.command("run", "deploy", SERVICE, f"--source={source}", f"--region={REGION}",
                      f"--service-account={RUNTIME}",
                      f"--build-service-account=projects/{PROJECT}/serviceAccounts/{BUILDER}",
                      "--no-allow-unauthenticated", "--invoker-iam-check", "--ingress=all",
                      "--min=0", "--max=2", "--cpu=1", "--memory=512Mi", "--concurrency=8",
                      "--timeout=120s", "--port=8080", "--clear-secrets",
                      f"--env-vars-file={temporary.name}", capture=False)
    if public_bindings(policy(cloud, SERVICE)):
        make_private(cloud)
        raise DeploymentError("Deployment unexpectedly retained public access; chat service was made private")
    token = cloud.command("auth", "print-identity-token").strip()
    if not token:
        raise DeploymentError("A Google identity token is required for private transport probes")
    probe_adapter(settings, transport_token=token, http=http)
    verify_backend_private(cloud, http)
    try:
        cloud.command("run", "services", "add-iam-policy-binding", SERVICE,
                      f"--region={REGION}", "--member=allUsers", "--role=roles/run.invoker", "--condition=None")
        if public_bindings(policy(cloud, SERVICE)) != [("roles/run.invoker", "allUsers")]:
            raise DeploymentError("Unexpected public IAM bindings on chat adapter")
        probe_adapter(settings, http=http)
        verify_backend_private(cloud, http)
    except Exception:
        try:
            make_private(cloud)
        except Exception:
            raise DeploymentError("Final probes failed; public binding removal could not be verified") from None
        raise DeploymentError("Final probes failed; chat service restored to private IAM") from None
    return {"project": PROJECT, "region": REGION, "service": SERVICE, "mcp_url": settings.public_url,
            "deployed": True, "provider_metadata_verified": True, "private_backend_verified": True,
            "oauth_denial_verified": True, "protected_resource_metadata_verified": True,
            "owner_login_verified": False, "ready_for_chat": False,
            "remaining": ["Verify a real owner OAuth login: exact issuer, owner subject, /mcp audience and coach:read scope",
                          "Verify a valid different-user token is denied", "Register and verify the chosen chat connection"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oauth-config", required=True, type=Path,
                        help="Non-secret JSON containing actual oauth_issuer, oauth_jwks_url and owner_subject")
    parser.add_argument("--dashboard-config", type=Path, help="Optional non-secret receipt from configure_dashboard.py; otherwise preserve existing dashboard login")
    parser.add_argument("--source", type=Path, default=ROOT / "adapters" / "mcp")
    parser.add_argument("--check-only", action="store_true", help="Read-only provider, cloud and private-backend checks")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.oauth_config)
        if args.dashboard_config is not None:
            settings = load_dashboard_config(args.dashboard_config, settings)
        result = deploy(Cloud(PROJECT), settings, args.source, check_only=args.check_only)
    except Exception as exc:
        # Cloud errors can include provider bodies; print only our curated errors.
        message = str(exc) if isinstance(exc, DeploymentError) else "Chat deployment failed; inspect the operation without logging tokens or payloads"
        raise SystemExit(message) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
