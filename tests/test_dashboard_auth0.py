"""Offline dashboard Auth0 setup checks; no live tenant or credentials needed."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_configure_auth0 import FakeCLI, setup


sys.modules.setdefault("configure_auth0", setup)
SOURCE = Path(__file__).resolve().parents[1] / "infra" / "configure_dashboard.py"
spec = importlib.util.spec_from_file_location("configure_dashboard_tested", SOURCE)
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class MembershipCLI(FakeCLI):
    """Modern membership endpoint: metadata omits the retired enabled_clients."""
    def __init__(self):
        super().__init__()
        self.objects["connections/con_owner"].pop("enabled_clients")
        self.memberships = {"con_owner": ["existing", "chatgpt"]}
        self.membership_page_size = 100

    def api(self, method, path, payload=None, query=None):
        if path.startswith("connections/") and path.endswith("/clients"):
            self.calls.append((method, path, deepcopy(payload if payload is not None else query)))
            members = self.memberships[path.split("/")[1]]
            if method == "get":
                assert set(query) <= {"take", "from"} and query["take"] == 100
                start = int(query.get("from", "cursor:0").removeprefix("cursor:"))
                end = start + self.membership_page_size
                result = {"clients": [{"client_id": member} for member in members[start:end]]}
                if end < len(members):
                    result["next"] = f"cursor:{end}"
                return result
            if method == "patch":
                assert isinstance(payload, list) and len(payload) == 1
                assert set(payload[0]) == {"client_id", "status"} and payload[0]["status"] is True
                if payload[0]["client_id"] not in members:
                    members.append(payload[0]["client_id"])
                return None  # Auth0's success is 204 No Content.
            raise AssertionError("Unsupported membership operation")
        if method == "patch" and path.startswith("connections/"):
            raise AssertionError("Retired enabled_clients replacement API must never be used")
        return super().api(method, path, payload, query)


@pytest.fixture
def environment(tmp_path):
    cli = MembershipCLI()
    cli.tenant = dashboard.TENANT
    cli.objects["resource-servers/coach"] = {
        "id": "coach", "identifier": dashboard.AUDIENCE, "signing_alg": "RS256",
        "allow_offline_access": True, "scopes": [{"value": "coach:read"}],
        "subject_type_authorization": {"user": {"policy": "require_client_grant"},
            "client": {"policy": "deny_all"}, "anonymous_user": {"policy": "deny_all"}},
    }
    cli.objects["clients/chatgpt"] = {
        "client_id": "chatgpt", "name": "ChatGPT AI Coach", "client_secret": "CHAT_SECRET",
        "callbacks": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "client_metadata": {"ai_coach_setup": "chat-marker"},
    }
    cli.objects["client-grants/chat-grant"] = {
        "id": "chat-grant", "client_id": "chatgpt", "audience": dashboard.AUDIENCE,
        "subject_type": "user", "scope": ["coach:read"], "allow_all_scopes": False,
    }
    path = tmp_path / "dashboard-state.json"
    state = dashboard.load_state(path)
    return cli, state, path


def configure(environment):
    cli, state, path = environment
    return dashboard.configure(cli, state, "con_owner", lambda: dashboard.save_state(path, state))


def mutations(cli):
    return [call for call in cli.calls if call[0] in {"post", "patch", "delete"}]


def test_setup_preserves_chatgpt_other_clients_resource_and_tenant(environment, capsys):
    cli, state, path = environment
    before = deepcopy(cli.objects)
    result = configure(environment)
    assert result == {"dashboard_url": dashboard.CALLBACK, "client_id": state["client_id"], "configured": True}
    for key, doc in before.items():
        if key != "connections/con_owner":
            assert cli.objects[key] == doc
    assert cli.memberships["con_owner"] == ["existing", "chatgpt", state["client_id"]]
    assert not [c for c in mutations(cli) if c[1].startswith(("resource-servers", "tenants", "users"))]
    client = cli.objects[f"clients/{state['client_id']}"]
    assert client["app_type"] == "spa" and client["token_endpoint_auth_method"] == "none"
    assert client["callbacks"] == [dashboard.CALLBACK]
    assert client["allowed_logout_urls"] == [dashboard.CALLBACK]
    assert client["web_origins"] == [dashboard.ORIGIN]
    assert client["grant_types"] == ["authorization_code", "refresh_token"]
    assert client["refresh_token"]["rotation_type"] == "rotating"
    grant = cli.objects[f"client-grants/{state['grant_id']}"]
    assert grant["scope"] == ["coach:read"] and grant["allow_all_scopes"] is False
    assert grant["subject_type"] == "user"
    assert set(json.loads(path.read_text())) <= dashboard.FIELDS
    assert path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert "SECRET" not in path.read_text() + json.dumps(result)


def test_rerun_is_read_only_when_already_configured(environment):
    cli, _, _ = environment
    first = configure(environment)
    cli.calls.clear()
    second = configure(environment)
    assert first == second and mutations(cli) == []


@pytest.mark.parametrize("collection", ["clients", "client-grants"])
def test_ambiguous_create_recovers_existing_managed_resource_without_duplicates(environment, collection):
    cli, _, _ = environment
    cli.fail_after_create = collection
    with pytest.raises(setup.SetupError):
        configure(environment)
    result = configure(environment)
    assert result["configured"] is True
    assert sum(call[:2] == ("post", collection) for call in cli.calls) == 1


def test_membership_added_during_client_creation_is_preserved(environment):
    cli, state, _ = environment
    original = cli.api
    def with_concurrent_enable(method, path, payload=None, query=None):
        result = original(method, path, payload, query)
        if method == "post" and path == "clients":
            cli.memberships["con_owner"].append("newly-enabled")
        return result
    cli.api = with_concurrent_enable
    configure(environment)
    assert cli.memberships["con_owner"] == [
        "existing", "chatgpt", "newly-enabled", state["client_id"],
    ]


@pytest.mark.parametrize("change", [
    {"id": "con_other"}, {"strategy": "google-oauth2"},
])
def test_invalid_connection_fails_before_any_mutations(environment, change):
    cli, _, _ = environment
    cli.objects["connections/con_owner"].update(change)
    with pytest.raises(setup.SetupError):
        configure(environment)
    assert mutations(cli) == []


def test_wrong_tenant_or_changed_resource_policy_fails_closed(environment):
    cli, _, _ = environment
    cli.tenant = "other.auth0.com"
    with pytest.raises(setup.SetupError):
        configure(environment)
    assert cli.calls == []
    cli.tenant = dashboard.TENANT
    cli.objects["resource-servers/coach"]["subject_type_authorization"]["client"]["policy"] = "allow_all"
    with pytest.raises(setup.SetupError):
        configure(environment)
    assert mutations(cli) == []


@pytest.mark.parametrize("conflict", ["unmarked-name", "duplicate-markers", "mismatched-state", "missing-managed-client"])
def test_conflicting_clients_are_not_adopted_or_modified(environment, conflict):
    cli, state, _ = environment
    if conflict in {"duplicate-markers", "mismatched-state"}:
        cli.objects["clients/managed"] = {"client_id": "managed", "name": dashboard.NAME,
            "client_metadata": {"ai_coach_dashboard": dashboard.MARKER}}
    if conflict == "unmarked-name":
        cli.objects["clients/unrelated"]["name"] = dashboard.NAME
    elif conflict == "duplicate-markers":
        cli.objects["clients/second"] = {**deepcopy(cli.objects["clients/managed"]), "client_id": "second"}
    else:
        state["client_id"] = "recorded-other-client"
    before = deepcopy(cli.objects)
    with pytest.raises(setup.SetupError):
        configure(environment)
    assert cli.objects == before and mutations(cli) == []


def test_only_managed_dashboard_grant_is_narrowed(environment):
    cli, state, _ = environment
    configure(environment)
    cli.objects[f"client-grants/{state['grant_id']}"]["scope"] = ["coach:read", "coach:write"]
    cli.objects[f"client-grants/{state['grant_id']}"]["allow_all_scopes"] = True
    before = deepcopy(cli.objects["client-grants/chat-grant"])
    cli.calls.clear()
    configure(environment)
    assert cli.objects["client-grants/chat-grant"] == before
    assert mutations(cli) == [("patch", f"client-grants/{state['grant_id']}",
                               {"scope": ["coach:read"], "allow_all_scopes": False})]


def test_failed_connection_membership_verification_stops_before_grant(environment):
    cli, _, _ = environment
    original = cli.api
    def discard_patch(method, path, payload=None, query=None):
        if method == "patch" and path == "connections/con_owner/clients":
            cli.calls.append((method, path, deepcopy(payload)))
            return None
        return original(method, path, payload, query)
    cli.api = discard_patch
    with pytest.raises(setup.SetupError, match="membership"):
        configure(environment)
    assert not [c for c in mutations(cli) if c[1] == "client-grants"]


@pytest.mark.parametrize("change", [
    {"tenant": "other.auth0.com"}, {"audience": "https://elsewhere.example/mcp"},
    {"callback": "https://elsewhere.example/callback"}, {"client_secret": "SECRET"},
    {"client_id": "../../private"},
])
def test_state_rejects_mismatched_binding_and_secret_fields(environment, change):
    _, state, path = environment
    path.write_text(json.dumps({**state, **change}))
    with pytest.raises(setup.SetupError) as caught:
        dashboard.load_state(path)
    assert "SECRET" not in str(caught.value)


def test_save_refuses_credential_fields_without_overwriting_state(environment):
    _, state, path = environment
    dashboard.save_state(path, state)
    before = path.read_bytes()
    with pytest.raises(setup.SetupError):
        dashboard.save_state(path, {**state, "access_token": "SECRET"})
    assert path.read_bytes() == before and b"SECRET" not in before


def test_membership_cursor_pagination_and_additive_patch(environment):
    cli, state, _ = environment
    cli.memberships['con_owner'] = [f'existing-{i}' for i in range(7)]
    cli.membership_page_size = 2
    configure(environment)
    assert cli.memberships['con_owner'] == [f'existing-{i}' for i in range(7)] + [state['client_id']]
    member_reads = [c for c in cli.calls if c[:2] == ('get', 'connections/con_owner/clients')]
    assert member_reads[0][2] == {'take': 100}
    assert member_reads[1][2] == {'take': 100, 'from': 'cursor:2'}
    assert [c for c in mutations(cli) if c[1].startswith('connections/')] == [
        ('patch', 'connections/con_owner/clients', [{'client_id': state['client_id'], 'status': True}])]
    assert 'enabled_clients' not in cli.objects['connections/con_owner']


@pytest.mark.parametrize('response', [
    {}, {'clients': None}, {'clients': 'invalid'}, {'clients': [None]},
    {'clients': [{'client_id': '../bad'}]}, {'clients': [], 'next': ''},
    {'clients': [], 'next': 123}, {'clients': [], 'next': 'a' * 1001},
    {'clients': [], 'next': 'unsafe\nvalue'},
])
def test_invalid_membership_response_stops_before_mutation(environment, response):
    cli, _, _ = environment
    original = cli.api
    def invalid_page(method, path, payload=None, query=None):
        if method == 'get' and path == 'connections/con_owner/clients':
            return deepcopy(response)
        return original(method, path, payload, query)
    cli.api = invalid_page
    with pytest.raises(setup.SetupError):
        configure(environment)
    assert mutations(cli) == []


def test_membership_repeated_cursor_fails_before_mutation(environment):
    cli, _, _ = environment
    original = cli.api
    reads = []
    def stuck_page(method, path, payload=None, query=None):
        if method == 'get' and path == 'connections/con_owner/clients':
            reads.append(deepcopy(query))
            return {'clients': [{'client_id': 'existing'}], 'next': 'opaque+/=token'}
        return original(method, path, payload, query)
    cli.api = stuck_page
    with pytest.raises(setup.SetupError, match='pagination'):
        configure(environment)
    assert reads == [{'take': 100}, {'take': 100, 'from': 'opaque+/=token'}]
    assert mutations(cli) == []


def test_atomic_enable_preserves_member_added_immediately_before_patch(environment):
    cli, state, _ = environment
    original = cli.api
    def race_on_enable(method, path, payload=None, query=None):
        if method == 'patch' and path == 'connections/con_owner/clients':
            cli.memberships['con_owner'].append('concurrent-client')
        return original(method, path, payload, query)
    cli.api = race_on_enable
    configure(environment)
    assert cli.memberships['con_owner'] == ['existing', 'chatgpt', 'concurrent-client', state['client_id']]


def test_verification_detects_removed_prior_member(environment):
    cli, _, _ = environment
    original = cli.api
    def broken_membership_server(method, path, payload=None, query=None):
        result = original(method, path, payload, query)
        if method == 'patch' and path == 'connections/con_owner/clients':
            cli.memberships['con_owner'].remove('chatgpt')
        return result
    cli.api = broken_membership_server
    with pytest.raises(setup.SetupError, match='membership'):
        configure(environment)
    assert not [c for c in mutations(cli) if c[1] == 'client-grants']
