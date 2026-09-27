"""Read-only deployment probes; authentication is used only for static backend health."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from release_git import ReleaseError, require

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra"))
from deploy_chat import (BACKEND_SERVICE, BACKEND_URL, BUILDER, PROJECT, REGION,
                         SERVICE, DeploymentError, NoRedirect, Response, load_settings,
                         probe_adapter as adapter_probe)

DOMAIN = "https://aiworkoutbuilder.app"


def probe_adapter(settings, **kwargs):
    try:
        adapter_probe(settings, **kwargs)
    except DeploymentError as error:
        raise ReleaseError(str(error)) from None


def traffic(service):
    result = {}
    for item in service["status"].get("traffic", []):
        if item.get("percent", 0):
            revision = item.get("revisionName")
            require(bool(revision), "Cloud Run traffic has no resolved revision.")
            result[revision] = result.get(revision, 0) + item["percent"]
    require(sum(result.values()) == 100, "Cloud Run must have a resolved 100% traffic allocation.")
    return result


def runtime_config(service):
    spec = deepcopy(service["spec"]["template"]["spec"])
    require(len(spec.get("containers", [])) == 1, "Routine release supports one container per service.")
    spec["containers"][0].pop("image", None)
    annotations = service.get("metadata", {}).get("annotations", {})
    return {"spec": spec, "template_annotations": service["spec"]["template"].get("metadata", {}).get("annotations", {}),
            "access": {key: annotations.get(key) for key in (
                "run.googleapis.com/ingress", "run.googleapis.com/invoker-iam-disabled",
                "run.googleapis.com/default-url-disabled", "run.googleapis.com/custom-audiences",
                "run.googleapis.com/minScale", "run.googleapis.com/maxScale")}}


def bindings(policy):
    return sorted(json.dumps(row, sort_keys=True) for row in policy.get("bindings", []))


def http(url, *, method="GET", headers=None, body=None):
    """TLS verified; no redirects or cookies; callers restrict probes to health and public metadata."""
    payload = None if body is None else json.dumps(body).encode()
    headers = {"User-Agent": "AI-Coach-Release/1.0", **(headers or {})}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, method=method, headers=headers, data=payload)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=45)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ReleaseError("Public HTTPS probe failed (certificate/network); see release stage.") from None
    with response:
        data = response.read(2_000_001)
        require(len(data) <= 2_000_000, "Public probe response exceeds size limit.")
        return Response(response.status, {k.lower(): v for k, v in response.headers.items()}, data)



def identity_token():
    """Keep the existing operator's short-lived token in memory, never in logs."""
    try:
        result = subprocess.run([str(ROOT / "scripts/gcloud"), "auth", "print-identity-token",
                                 f"--project={PROJECT}", "--quiet"],
                                capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError("Could not obtain the existing operator identity for the private health probe.") from None
    require(result.returncode == 0 and bool(result.stdout.strip()),
            "Existing Google Cloud login cannot obtain an identity token. No IAM changes were made.")
    return result.stdout.strip()


def probe_backend(origin, *, allow_missing_health=False):
    """Only static /health; candidates and production never allow a missing route."""
    url = urlsplit(origin)
    require(url.scheme == "https" and bool(url.hostname) and url.hostname.endswith(".run.app")
            and not url.path and not url.query and not url.fragment and not url.username and not url.password,
            "Unexpected private health origin.")
    require(http(origin + "/health").status == 403, "Backend must deny anonymous access.")
    response = http(origin + "/health", headers={"Authorization": "Bearer " + identity_token()})
    if allow_missing_health and response.status == 404:
        # Only the application's exact missing-route response qualifies, not a
        # Google frontend 404, denied invocation, timeout or unhealthy container.
        require(response.body == b'{"detail":"Not Found"}', "Unexpected missing-health response.")
        return "missing-route"
    require(response.status == 200, "Existing operator lacks backend invocation access, or backend health failed.")
    try:
        body = json.loads(response.body)
    except (ValueError, UnicodeError):
        raise ReleaseError("Backend health returned invalid JSON.") from None
    require(isinstance(body, dict) and set(body) == {"service", "status", "version"}
            and body["service"] == "ai-coach-data" and body["status"] == "ok"
            and isinstance(body["version"], str) and 0 < len(body["version"]) <= 64,
            "Private backend static health response is not the expected service.")
    return "healthy"


def probe_gateway(settings, origin, assets, *, production=False):
    canonical = settings.public_url.removesuffix("/mcp")
    def route(url, **kwargs):
        return http(origin + url.removeprefix(canonical), **kwargs)
    probe_adapter(settings, http=route)
    for path, digest in assets.items():
        response = http(origin + "/dashboard/" + path)
        require(response.status == 200 and hashlib.sha256(response.body).hexdigest() == digest,
                "Published dashboard asset does not match tested build: " + path)
    if production:
        root = http(DOMAIN + "/")
        require(root.status in {301, 302, 307, 308} and root.headers.get("location", "").endswith("/dashboard/"),
                "Primary domain dashboard redirect failed.")
        www = http("https://www.aiworkoutbuilder.app/")
        require(www.status in {301, 302, 307, 308}
                and www.headers.get("location", "").startswith(DOMAIN + "/"), "www redirect failed.")
