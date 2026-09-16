#!/usr/bin/env python3
"""Configure AI Coach Auth0 resources through an already authenticated CLI.

This does not log in, create users, bind an owner, or register ChatGPT itself.
Run `api`, then `client --callback <exact ChatGPT callback> --connection-id <id>`.
The optional `dcr` step enables open dynamic registration with read-only user grants.
Only the selected Auth0 tenant and this fixed AI Coach resource are supported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlencode, urlsplit


AUDIENCE = "https://ai-coach-chat-600465847441.europe-north1.run.app/mcp"
RESOURCE_NAME = "AI Coach Chat"
CLIENT_NAME = "ChatGPT AI Coach"
SCOPE = "coach:read"
MARKER = hashlib.sha256(AUDIENCE.encode()).hexdigest()[:24]
STATE_FIELDS = {"version", "tenant", "audience", "api_id", "client_id", "grant_id",
                "connection_id", "callback", "owner_binding_required"}


class SetupError(Exception):
    """Contains only messages safe to print; never raw CLI output."""


def object_result(value: Any) -> dict:
    if not isinstance(value, dict):
        raise SetupError("Auth0 returned an unexpected response shape.")
    return value


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise SetupError("Auth0 returned an invalid resource identifier.")
    return value


def validate_tenant(tenant: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.auth0\.com", tenant):
        raise SetupError("Supply the exact lowercase Auth0 tenant domain, without a URL path.")
    if ".." in tenant:
        raise SetupError("Invalid Auth0 tenant domain.")
    return tenant


def validate_callback(callback: str) -> str:
    try:
        parsed = urlsplit(callback)
        valid = (parsed.scheme == "https" and parsed.netloc == "chatgpt.com"
                 and not parsed.query and not parsed.fragment
                 and (parsed.path == "/connector_platform_oauth_redirect"
                      or re.fullmatch(r"/connector/oauth/[A-Za-z0-9_-]+", parsed.path)))
    except ValueError:
        valid = False
    if not valid:
        raise SetupError("Supply the exact HTTPS ChatGPT OAuth callback from its management page.")
    return callback


class Auth0CLI:
    def __init__(self, executable: str, tenant: str, runner=None):
        self.executable = executable
        self.tenant = validate_tenant(tenant)
        self.runner = runner or subprocess.run
        self.checked = False

    def _run(self, arguments: list[str], payload=None):
        # Never put JSON bodies or credentials in argv, and never relay CLI output.
        try:
            result = self.runner(
                [self.executable, *arguments],
                input="" if payload is None else json.dumps(payload),
                text=True, capture_output=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise SetupError("Auth0 CLI failed or timed out; its output was withheld. Rerun to reconcile.") from None
        if result.returncode:
            raise SetupError(f"Auth0 CLI exited with status {result.returncode}; its output was withheld. Check login and permissions.")
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            raise SetupError("Auth0 CLI returned invalid JSON; its output was withheld.") from None

    def preflight(self):
        # Do not pass --tenant here: it would mask a different active tenant.
        rows = self._run(["tenants", "list", "--json", "--no-input", "--no-color"])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SetupError("Cannot verify the active authenticated Auth0 tenant.")
        active = [row.get("name") for row in rows if row.get("active") is True]
        if active != [self.tenant]:
            raise SetupError("Active Auth0 tenant does not exactly match --tenant. No API calls were made.")
        self.checked = True

    def api(self, method: str, path: str, payload=None, query=None):
        if not self.checked:
            raise SetupError("Auth0 tenant preflight is required before API calls.")
        if method not in {"get", "post", "patch"}:
            raise SetupError("This setup tool does not support deletion.")
        # Auth0 CLI v1.35's --query uses pflag.StringToString, which trims
        # trailing quotes and switches to CSV parsing when values contain '='.
        # Its raw API path parser preserves standard URL-encoded query values.
        if query:
            path += ("&" if "?" in path else "?") + urlencode(query)
        arguments = ["api", method, path, "--tenant", self.tenant, "--no-input", "--no-color"]
        return self._run(arguments, payload)

    def collection(self, path: str, query=None) -> list[dict]:
        rows = []
        for page in range(100):
            result = self.api("get", path, query={**(query or {}), "page": page, "per_page": 100})
            if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                raise SetupError("Auth0 returned an unexpected collection response.")
            rows.extend(result)
            if len(result) < 100:
                return rows
        raise SetupError("Auth0 collection exceeds this setup tool's safety limit.")


def subset_matches(actual: dict, expected: dict) -> bool:
    for key, value in expected.items():
        if isinstance(value, dict):
            if not isinstance(actual.get(key), dict) or not subset_matches(actual[key], value):
                return False
        elif actual.get(key) != value:
            return False
    return True


def reconcile(cli: Auth0CLI, path: str, expected: dict) -> dict:
    current = object_result(cli.api("get", path))
    if not subset_matches(current, expected):
        cli.api("patch", path, expected)
        current = object_result(cli.api("get", path))
    if not subset_matches(current, expected):
        raise SetupError("Auth0 did not retain the requested setup. Stop and inspect configuration.")
    return current


def load_state(path: Path, tenant: str) -> dict:
    if not path.exists():
        return {"version": 1, "tenant": tenant, "audience": AUDIENCE, "owner_binding_required": True}
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        raise SetupError("Cannot read the nonsecret setup state file.") from None
    if (not isinstance(state, dict) or set(state) - STATE_FIELDS
            or state.get("version") != 1 or state.get("tenant") != tenant
            or state.get("audience") != AUDIENCE or state.get("owner_binding_required") is not True):
        raise SetupError("Setup state does not match this tenant/resource or has unexpected fields.")
    for key in ("api_id", "client_id", "grant_id", "connection_id"):
        if key in state:
            identifier(state[key])
    if "callback" in state:
        validate_callback(state["callback"])
    return state


def save_state(path: Path, state: dict):
    if set(state) - STATE_FIELDS:
        raise SetupError("Refusing to persist unexpected state fields.")
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        raise SetupError("Cannot save nonsecret setup state. Rerun to reconcile existing resources.") from None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def configure_api(cli: Auth0CLI, state: dict, checkpoint):
    expected = {
        "name": RESOURCE_NAME, "signing_alg": "RS256", "allow_offline_access": True,
        "token_lifetime": 3600, "enforce_policies": False,
        "skip_consent_for_verifiable_first_party_clients": False,
        "scopes": [{"value": SCOPE, "description": "Read private training context"}],
        "subject_type_authorization": {
            "user": {"policy": "require_client_grant"},
            "client": {"policy": "deny_all"}, "anonymous_user": {"policy": "deny_all"},
        },
    }
    matches = [row for row in cli.collection("resource-servers") if row.get("identifier") == AUDIENCE]
    if len(matches) > 1:
        raise SetupError("Multiple APIs match the AI Coach audience; refusing to choose one.")
    if matches:
        resource = matches[0]
        resource_id = identifier(resource.get("id"))
        scopes = resource.get("scopes", [])
        if (resource.get("name") != RESOURCE_NAME
                or (state.get("api_id") and state["api_id"] != resource_id)
                or not isinstance(scopes, list)
                or any(not isinstance(scope, dict) or scope.get("value") != SCOPE for scope in scopes)):
            raise SetupError("Existing audience belongs to an unexpected API or has additional scopes; no changes made.")
    else:
        if state.get("api_id"):
            raise SetupError("Recorded API is missing; refusing automatic replacement.")
        resource = object_result(cli.api("post", "resource-servers", {"identifier": AUDIENCE, **expected}))
        resource_id = identifier(resource.get("id"))
    state["api_id"] = resource_id
    checkpoint()
    reconcile(cli, f"resource-servers/{resource_id}", {**expected, "token_dialect": "rfc9068_profile"})
    # Patch only these two known settings; preserve CIMD/DCR and all other settings.
    reconcile(cli, "tenants/settings", {
        "resource_parameter_profile": "compatibility",
        "authorization_response_iss_parameter_supported": True,
    })


def configure_client(cli: Auth0CLI, state: dict, callback: str, connection_id: str, checkpoint):
    callback = validate_callback(callback)
    connection_id = identifier(connection_id)
    if not state.get("api_id"):
        raise SetupError("Run the api step successfully before creating the ChatGPT client.")
    resource = object_result(cli.api("get", f"resource-servers/{identifier(state['api_id'])}"))
    if not subset_matches(resource, {"identifier": AUDIENCE, "name": RESOURCE_NAME,
                                   "signing_alg": "RS256", "token_dialect": "rfc9068_profile",
                                   "allow_offline_access": True}):
        raise SetupError("AI Coach API preconditions changed; rerun the api step first.")
    connection = object_result(cli.api("get", f"connections/{connection_id}"))
    if connection.get("id") != connection_id or connection.get("strategy") != "auth0":
        raise SetupError("Select an existing Auth0 database connection by its exact ID.")
    expected = {
        "name": CLIENT_NAME, "app_type": "native", "is_first_party": False,
        "oidc_conformant": True, "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "callbacks": [callback],
        "jwt_configuration": {"alg": "RS256"},
        "client_metadata": {"ai_coach_setup": MARKER},
        "refresh_token": {
            "rotation_type": "rotating", "expiration_type": "expiring",
            "token_lifetime": 31536000, "idle_token_lifetime": 7776000,
            "infinite_token_lifetime": False, "infinite_idle_token_lifetime": False,
            "leeway": 10,
        },
    }
    rows = cli.collection("clients", {"fields": "client_id,name,client_metadata", "include_fields": "true"})
    matches = [row for row in rows if isinstance(row.get("client_metadata"), dict)
               and row["client_metadata"].get("ai_coach_setup") == MARKER]
    if len(matches) > 1:
        raise SetupError("Multiple managed ChatGPT clients found; refusing to choose one.")
    if matches:
        client_id = identifier(matches[0].get("client_id"))
        if state.get("client_id") and state["client_id"] != client_id:
            raise SetupError("Recorded client differs from the managed ChatGPT client.")
    else:
        if state.get("client_id") or any(row.get("name") == CLIENT_NAME for row in rows):
            raise SetupError("Existing client cannot be safely adopted or is missing; no replacement created.")
        client_id = identifier(object_result(cli.api("post", "clients", expected)).get("client_id"))
    state.update(client_id=client_id, callback=callback)
    checkpoint()
    reconcile(cli, f"clients/{client_id}", expected)
    reconcile(cli, f"connections/{connection_id}", {"is_domain_connection": True})
    state["connection_id"] = connection_id
    checkpoint()
    grants = cli.collection("client-grants", {"client_id": client_id, "audience": AUDIENCE})
    relevant = [grant for grant in grants if grant.get("client_id") == client_id
                and grant.get("audience") == AUDIENCE and grant.get("subject_type") == "user"]
    if len(relevant) > 1:
        raise SetupError("Multiple user grants found for the managed ChatGPT client.")
    grant_body = {"client_id": client_id, "audience": AUDIENCE, "subject_type": "user", "scope": [SCOPE]}
    if relevant:
        grant_id = identifier(relevant[0].get("id"))
        if relevant[0].get("scope") != [SCOPE] or relevant[0].get("allow_all_scopes") is True:
            cli.api("patch", f"client-grants/{grant_id}", {"scope": [SCOPE], "allow_all_scopes": False})
    else:
        grant_id = identifier(object_result(cli.api("post", "client-grants", {**grant_body, "allow_all_scopes": False})).get("id"))
    verified = [grant for grant in cli.collection("client-grants", {"client_id": client_id, "audience": AUDIENCE})
                if grant.get("id") == grant_id]
    if (len(verified) != 1 or not subset_matches(verified[0], grant_body)
            or verified[0].get("allow_all_scopes") is True):
        raise SetupError("Auth0 did not retain the restricted user grant.")
    state["grant_id"] = grant_id
    checkpoint()


def configure_dcr(cli: Auth0CLI, state: dict):
    """Enable strict registration with one read-only, user-delegated default grant.

    Auth0 open registration lets clients register, not read the owner's data.
    Tokens still require user authentication/consent and the deployed owner check.
    """
    if not state.get("api_id") or not state.get("connection_id"):
        raise SetupError("Complete the API and database-connection setup before enabling DCR.")
    resource = object_result(cli.api("get", f"resource-servers/{identifier(state['api_id'])}"))
    expected_resource = {
        "identifier": AUDIENCE, "name": RESOURCE_NAME, "signing_alg": "RS256",
        "token_dialect": "rfc9068_profile", "allow_offline_access": True,
        "skip_consent_for_verifiable_first_party_clients": False,
        "subject_type_authorization": {
            "user": {"policy": "require_client_grant"},
            "client": {"policy": "deny_all"}, "anonymous_user": {"policy": "deny_all"},
        },
    }
    if (not subset_matches(resource, expected_resource)
            or resource.get("scopes") != [{"value": SCOPE, "description": "Read private training context"}]):
        raise SetupError("Read-only API or consent preconditions changed; DCR was not enabled.")
    connection = object_result(cli.api("get", f"connections/{identifier(state['connection_id'])}"))
    if connection.get("strategy") != "auth0" or connection.get("is_domain_connection") is not True:
        raise SetupError("The selected database connection is not ready for third-party login.")
    settings = object_result(cli.api("get", "tenants/settings"))
    # New Auth0 tenants can omit this property: strict mode is fixed by the
    # provider. Verify actual registered clients separately instead of treating
    # an omitted optional metadata property as a failed enablement.
    if settings.get("dynamic_client_registration_security_mode") not in (None, "strict"):
        raise SetupError("DCR is not in strict mode; registration was not enabled.")
    expected_grant = {"default_for": "third_party_clients", "audience": AUDIENCE,
                      "scope": [SCOPE], "subject_type": "user", "allow_all_scopes": False}
    def defaults():
        return [row for row in cli.collection("client-grants", {"audience": AUDIENCE})
                if row.get("audience") == AUDIENCE and row.get("default_for") == "third_party_clients"]
    existing = defaults()
    if existing:
        if (len(existing) != 1 or not subset_matches(existing[0], expected_grant)
                or existing[0].get("client_id")):
            raise SetupError("Unexpected third-party defaults exist; no permissions were changed.")
    else:
        cli.api("post", "client-grants", expected_grant)
    verified = defaults()
    if len(verified) != 1 or not subset_matches(verified[0], expected_grant):
        raise SetupError("Read-only third-party default grant was not retained; DCR was not enabled.")
    # Keep unrelated tenant flags and the manually registered client unchanged.
    reconcile(cli, "tenants/settings", {"flags": {"enable_dynamic_client_registration": True}})
    return {"dcr_client_verification_required": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--cli", default=str(Path(__file__).resolve().parents[1] / ".tools" / "auth0"))
    parser.add_argument("--state", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".local" / "auth0-config.json")
    subparsers = parser.add_subparsers(dest="step", required=True)
    subparsers.add_parser("api", help="Create/reconcile the API and two tenant OAuth settings")
    subparsers.add_parser("dcr", help="Enable strict dynamic registration with user-delegated coach:read only")
    client_parser = subparsers.add_parser("client", help="Create/reconcile the supplied ChatGPT callback and user grant")
    client_parser.add_argument("--callback", required=True)
    client_parser.add_argument("--connection-id", required=True)
    args = parser.parse_args(argv)
    try:
        cli = Auth0CLI(args.cli, args.tenant)
        state = load_state(args.state, cli.tenant)
        if args.step == "client":
            validate_callback(args.callback)
            identifier(args.connection_id)
        cli.preflight()
        checkpoint = lambda: save_state(args.state, state)
        verification = {}
        if args.step == "api":
            configure_api(cli, state, checkpoint)
        elif args.step == "dcr":
            verification = configure_dcr(cli, state)
        else:
            configure_client(cli, state, args.callback, args.connection_id, checkpoint)
        print(json.dumps({"step": args.step, "status": "configured", "tenant": cli.tenant,
                          "audience": AUDIENCE, "owner_binding_required": True, **verification}))
        return 0
    except SetupError as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
