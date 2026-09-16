#!/usr/bin/env python3
"""Reconcile one first-party dashboard SPA in the existing AI Coach tenant.

Uses the authenticated Auth0 CLI. Does not change tenant-wide settings, the API,
ChatGPT clients, users, or the owner binding. Persists only nonsecret identifiers.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from configure_auth0 import (AUDIENCE, SCOPE, Auth0CLI, SetupError, identifier,
                             object_result, reconcile, subset_matches)

ROOT = Path(__file__).resolve().parents[1]
TENANT = "dev-1o1ivpmjhvam14ve.us.auth0.com"
ORIGIN = AUDIENCE.removesuffix("/mcp")
CALLBACK = ORIGIN + "/dashboard/"
NAME = "Magne Training Dashboard"
MARKER = "magne-training-dashboard-v1"
FIELDS = {"version", "tenant", "audience", "client_id", "grant_id", "connection_id", "callback"}


def load_state(path):
    if not path.exists():
        return {"version": 1, "tenant": TENANT, "audience": AUDIENCE, "callback": CALLBACK}
    state = json.loads(path.read_text())
    if (not isinstance(state, dict) or set(state) - FIELDS
            or not subset_matches(state, {"version": 1, "tenant": TENANT,
                                          "audience": AUDIENCE, "callback": CALLBACK})):
        raise SetupError("Dashboard state does not match the fixed tenant and callback.")
    for key in ("client_id", "grant_id", "connection_id"):
        if key in state:
            identifier(state[key])
    return state


def save_state(path, state):
    if set(state) - FIELDS:
        raise SetupError("Unexpected dashboard state fields.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        os.chmod(out.name, 0o600)
        json.dump(state, out, indent=2, sort_keys=True)
        out.write("\n")
    os.replace(out.name, path)


def connection_clients(cli, connection_id):
    """Read the current cursor-paginated membership API, never legacy fields.

    Auth0 docs: /docs/api/management/v2/connections/get-connection-clients
    Cursor tokens are opaque; the transport URL-encodes them as query values.
    """
    path = f"connections/{identifier(connection_id)}/clients"
    members, seen, cursor = set(), set(), None
    for _ in range(100):
        query = {"take": 100}
        if cursor is not None:
            query["from"] = cursor
        page = object_result(cli.api("get", path, query=query))
        clients = page.get("clients")
        if not isinstance(clients, list) or len(clients) > 1000:
            raise SetupError("Unexpected connection membership response.")
        for item in clients:
            members.add(identifier(object_result(item).get("client_id")))
        cursor = page.get("next")
        if cursor is None:
            return members
        if (not isinstance(cursor, str) or not 1 <= len(cursor) <= 1000
                or any(ord(char) < 32 or ord(char) == 127 for char in cursor)
                or cursor in seen):
            raise SetupError("Connection membership pagination did not advance safely.")
        seen.add(cursor)
    raise SetupError("Connection membership exceeds this setup tool's safety limit.")


def configure(cli, state, connection_id, checkpoint):
    if cli.tenant != TENANT:
        raise SetupError("This dashboard belongs to the dedicated AI Coach tenant.")
    connection_id = identifier(connection_id)
    resources = [r for r in cli.collection("resource-servers") if r.get("identifier") == AUDIENCE]
    if (len(resources) != 1 or not subset_matches(resources[0], {
            "signing_alg": "RS256", "allow_offline_access": True,
            "subject_type_authorization": {"user": {"policy": "require_client_grant"},
                "client": {"policy": "deny_all"}, "anonymous_user": {"policy": "deny_all"}}})
            or SCOPE not in [s.get("value") for s in resources[0].get("scopes", [])]):
        raise SetupError("Existing AI Coach read-only API preconditions changed.")
    connection = object_result(cli.api("get", f"connections/{connection_id}"))
    if connection.get("id") != connection_id or connection.get("strategy") != "auth0":
        raise SetupError("Select the existing Auth0 database connection.")
    if state.get("connection_id", connection_id) != connection_id:
        raise SetupError("Refusing to replace the recorded dashboard login connection.")
    connection_clients(cli, connection_id)  # Verify membership read access before creating anything.
    expected = {
        "name": NAME, "app_type": "spa", "is_first_party": True,
        "oidc_conformant": True, "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "callbacks": [CALLBACK], "allowed_logout_urls": [CALLBACK],
        "web_origins": [ORIGIN], "allowed_origins": [ORIGIN],
        "jwt_configuration": {"alg": "RS256"},
        "client_metadata": {"ai_coach_dashboard": MARKER},
        "refresh_token": {"rotation_type": "rotating", "expiration_type": "expiring",
            "token_lifetime": 31536000, "idle_token_lifetime": 7776000,
            "infinite_token_lifetime": False, "infinite_idle_token_lifetime": False, "leeway": 10},
    }
    rows = cli.collection("clients", {"fields": "client_id,name,client_metadata", "include_fields": "true"})
    matches = [r for r in rows if (r.get("client_metadata") or {}).get("ai_coach_dashboard") == MARKER]
    if len(matches) > 1:
        raise SetupError("Multiple dashboard clients found; refusing to choose one.")
    if matches:
        client_id = identifier(matches[0].get("client_id"))
        if state.get("client_id", client_id) != client_id:
            raise SetupError("Recorded dashboard client differs from the managed client.")
    else:
        if state.get("client_id") or any(r.get("name") == NAME for r in rows):
            raise SetupError("An existing dashboard cannot be safely adopted or replaced.")
        client_id = identifier(object_result(cli.api("post", "clients", expected)).get("client_id"))
    state.update(client_id=client_id, connection_id=connection_id)
    checkpoint()
    reconcile(cli, f"clients/{client_id}", expected)
    # The dedicated API changes only this client's membership. It does not
    # replace a snapshot and cannot disable another client added concurrently.
    enabled = connection_clients(cli, connection_id)
    if client_id not in enabled:
        cli.api("patch", f"connections/{connection_id}/clients",
                [{"client_id": client_id, "status": True}])
    if not (enabled | {client_id}).issubset(connection_clients(cli, connection_id)):
        raise SetupError("Login connection membership was not retained.")
    grants = cli.collection("client-grants", {"client_id": client_id, "audience": AUDIENCE})
    matches = [g for g in grants if g.get("client_id") == client_id and g.get("audience") == AUDIENCE
               and g.get("subject_type") == "user"]
    if len(matches) > 1:
        raise SetupError("Multiple dashboard user grants found.")
    expected_grant = {"client_id": client_id, "audience": AUDIENCE, "subject_type": "user",
                      "scope": [SCOPE], "allow_all_scopes": False}
    if matches:
        grant_id = identifier(matches[0].get("id"))
        if state.get("grant_id", grant_id) != grant_id:
            raise SetupError("Recorded dashboard grant differs from the managed grant.")
        if not subset_matches(matches[0], expected_grant):
            cli.api("patch", f"client-grants/{grant_id}", {"scope": [SCOPE], "allow_all_scopes": False})
    else:
        if state.get("grant_id"):
            raise SetupError("Recorded dashboard grant is missing.")
        grant_id = identifier(object_result(cli.api("post", "client-grants", expected_grant)).get("id"))
    verified = [g for g in cli.collection("client-grants", {"client_id": client_id, "audience": AUDIENCE})
                if g.get("id") == grant_id]
    if len(verified) != 1 or not subset_matches(verified[0], expected_grant):
        raise SetupError("Restricted dashboard grant was not retained.")
    state["grant_id"] = grant_id
    checkpoint()
    return {"dashboard_url": CALLBACK, "client_id": client_id, "configured": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--state", type=Path, default=ROOT / ".local" / "dashboard-auth0.json")
    args = parser.parse_args()
    try:
        state = load_state(args.state)
        cli = Auth0CLI(str(ROOT / ".tools" / "auth0"), TENANT)
        cli.preflight()
        result = configure(cli, state, args.connection_id, lambda: save_state(args.state, state))
    except (SetupError, OSError, ValueError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, SetupError) else "Dashboard setup failed; no credentials were printed.") from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
