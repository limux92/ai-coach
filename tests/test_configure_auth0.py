"""No network, local credential reads, or real Auth0 executable calls."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit


SOURCE = Path(__file__).resolve().parents[1] / "infra" / "configure_auth0.py"
spec = importlib.util.spec_from_file_location("configure_auth0", SOURCE)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
TENANT = "dev-1o1ivpmjhvam14ve.us.auth0.com"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


def merge(target, changes):
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class FakeCLI:
    """Stateful mocked management API, including secret-bearing client responses."""
    def __init__(self):
        self.objects = {
            "tenants/settings": {"client_id_metadata_document_supported": True, "friendly_name": "Preserve"},
            "connections/con_owner": {"id": "con_owner", "strategy": "auth0", "enabled_clients": ["existing"]},
            "resource-servers/unrelated": {"id": "unrelated", "name": "Other API", "identifier": "https://other.example"},
            "clients/unrelated": {"client_id": "unrelated", "name": "Other app", "client_secret": "DO_NOT_PRINT"},
        }
        self.calls = []
        self.fail_after_create = None

    def collection(self, path, query=None):
        self.calls.append(("get-list", path, copy.deepcopy(query)))
        return [copy.deepcopy(value) for key, value in self.objects.items()
                if key.startswith(path + "/") and key.count("/") == 1]

    def api(self, method, path, payload=None, query=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if method == "get":
            return copy.deepcopy(self.objects[path])
        if method == "patch":
            merge(self.objects[path], payload)
            return copy.deepcopy(self.objects[path])
        if method == "post":
            resource_id = {"resource-servers": "api_coach", "clients": "client_coach", "client-grants": "cgr_coach"}[path]
            if path == "client-grants" and payload.get("default_for"):
                resource_id = "cgr_default"
            id_key = "client_id" if path == "clients" else "id"
            result = {**copy.deepcopy(payload), id_key: resource_id}
            if path == "clients":
                result["client_secret"] = "CREATED_SECRET_MUST_NOT_LEAK"
            self.objects[f"{path}/{resource_id}"] = result
            if self.fail_after_create == path:
                self.fail_after_create = None
                raise setup.SetupError("Simulated ambiguous create response.")
            return copy.deepcopy(result)
        raise AssertionError("Unexpected mutation")


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.state = setup.load_state(self.path, TENANT)
        self.cli = FakeCLI()
        self.checkpoint = lambda: setup.save_state(self.path, self.state)

    def configure_api(self):
        setup.configure_api(self.cli, self.state, self.checkpoint)

    def configure_client(self):
        setup.configure_client(self.cli, self.state, CALLBACK, "con_owner", self.checkpoint)

    def test_api_preserves_cimd_other_settings_and_other_api(self):
        before = copy.deepcopy(self.cli.objects["resource-servers/unrelated"])
        self.configure_api()
        settings = self.cli.objects["tenants/settings"]
        self.assertTrue(settings["client_id_metadata_document_supported"])
        self.assertEqual(settings["friendly_name"], "Preserve")
        self.assertEqual(settings["resource_parameter_profile"], "compatibility")
        self.assertEqual(before, self.cli.objects["resource-servers/unrelated"])
        api = self.cli.objects["resource-servers/api_coach"]
        self.assertEqual(api["token_dialect"], "rfc9068_profile")
        self.assertEqual(api["signing_alg"], "RS256")
        self.assertTrue(api["allow_offline_access"])
        self.assertEqual(api["subject_type_authorization"]["client"]["policy"], "deny_all")
        self.assertFalse(api["enforce_policies"])

    def test_second_run_makes_no_mutations(self):
        self.configure_api()
        self.configure_client()
        self.cli.calls.clear()
        self.configure_api()
        self.configure_client()
        self.assertFalse([call for call in self.cli.calls if call[0] in {"post", "patch", "delete"}])

    def test_public_client_exact_callback_and_explicit_read_only_user_grant(self):
        self.configure_api()
        self.configure_client()
        client = self.cli.objects["clients/client_coach"]
        self.assertEqual(client["token_endpoint_auth_method"], "none")
        self.assertFalse(client["is_first_party"])
        self.assertEqual(client["callbacks"], [CALLBACK])
        self.assertEqual(client["grant_types"], ["authorization_code", "refresh_token"])
        self.assertEqual(client["refresh_token"]["rotation_type"], "rotating")
        self.assertEqual(client["refresh_token"]["token_lifetime"], 31536000)
        self.assertEqual(client["refresh_token"]["idle_token_lifetime"], 7776000)
        self.assertFalse(client["refresh_token"]["infinite_token_lifetime"])
        grant = self.cli.objects["client-grants/cgr_coach"]
        self.assertEqual(grant["scope"], ["coach:read"])
        self.assertEqual(grant["subject_type"], "user")
        self.assertNotIn("default_for", grant)
        self.assertFalse(grant["allow_all_scopes"])
        self.assertEqual(self.cli.objects["connections/con_owner"]["enabled_clients"], ["existing"])
        persisted = self.path.read_text()
        self.assertNotIn("SECRET", persisted)
        self.assertNotIn("owner_subject", persisted)
        self.assertTrue(json.loads(persisted)["owner_binding_required"])
        self.assertFalse([call for call in self.cli.calls
                          if call[0] == "get" and call[1].startswith("client-grants/")])

    def test_ambiguous_create_can_recover_api_and_client_without_duplicates(self):
        for collection in ("resource-servers", "clients"):
            with self.subTest(collection=collection):
                operation = self.configure_api if collection == "resource-servers" else self.configure_client
                self.cli.fail_after_create = collection
                with self.assertRaises(setup.SetupError):
                    operation()
                operation()
                self.assertEqual(sum(call[0:2] == ("post", collection) for call in self.cli.calls), 1)

    def test_existing_conflicting_audience_is_not_modified(self):
        self.cli.objects["resource-servers/foreign"] = {
            "id": "foreign", "identifier": setup.AUDIENCE, "name": "Do not adopt", "scopes": []}
        with self.assertRaises(setup.SetupError):
            self.configure_api()
        self.assertFalse([call for call in self.cli.calls if call[0] != "get-list"])

    def test_matching_audience_extra_scope_is_not_removed(self):
        self.cli.objects["resource-servers/foreign"] = {
            "id": "foreign", "identifier": setup.AUDIENCE, "name": setup.RESOURCE_NAME,
            "scopes": [{"value": "coach:write"}]}
        with self.assertRaises(setup.SetupError):
            self.configure_api()
        self.assertFalse([call for call in self.cli.calls if call[0] != "get-list"])

    def test_unmarked_same_name_client_is_not_adopted(self):
        self.configure_api()
        self.cli.objects["clients/external"] = {"client_id": "external", "name": setup.CLIENT_NAME}
        self.cli.calls.clear()
        with self.assertRaises(setup.SetupError):
            self.configure_client()
        self.assertFalse([call for call in self.cli.calls if call[0] in {"post", "patch"}])

    def test_scopes_are_narrowed_only_on_our_grant(self):
        self.configure_api()
        self.configure_client()
        self.cli.objects["client-grants/cgr_coach"].update(scope=["coach:read", "coach:write"], allow_all_scopes=True)
        other_grant = {"id": "cgr_other", "client_id": "unrelated", "audience": setup.AUDIENCE,
                       "scope": ["coach:write"], "subject_type": "user"}
        self.cli.objects["client-grants/cgr_other"] = copy.deepcopy(other_grant)
        self.configure_client()
        self.assertEqual(self.cli.objects["client-grants/cgr_coach"]["scope"], ["coach:read"])
        self.assertEqual(self.cli.objects["client-grants/cgr_other"], other_grant)

    def test_invalid_callback_fails_before_any_api_call(self):
        self.configure_api()
        for callback in ("https://evil.example/callback", CALLBACK + "?secret=x", "http://chatgpt.com/connector/oauth/abc"):
            self.cli.calls.clear()
            with self.assertRaises(setup.SetupError):
                setup.configure_client(self.cli, self.state, callback, "con_owner", self.checkpoint)
            self.assertEqual(self.cli.calls, [])

    def test_state_rejects_cross_tenant_or_secret_fields(self):
        self.checkpoint()
        with self.assertRaises(setup.SetupError):
            setup.load_state(self.path, "another.eu.auth0.com")
        self.state["client_secret"] = "PRIVATE"
        with self.assertRaises(setup.SetupError):
            self.checkpoint()
        self.assertNotIn("PRIVATE", self.path.read_text())


class DCRTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI()
        self.state = {"version": 1, "tenant": TENANT, "audience": setup.AUDIENCE,
                      "owner_binding_required": True}
        setup.configure_api(self.cli, self.state, lambda: None)
        setup.configure_client(self.cli, self.state, CALLBACK, "con_owner", lambda: None)
        self.cli.objects["tenants/settings"]["flags"] = {"unrelated_flag": True}
        self.cli.calls.clear()

    def mutations(self):
        return [c for c in self.cli.calls if c[0] in {"patch", "post"}]

    def test_dcr_preserves_existing_clients_and_grants_and_requires_user_consent(self):
        prior = copy.deepcopy(self.cli.objects)
        result = setup.configure_dcr(self.cli, self.state)
        self.assertTrue(result["dcr_client_verification_required"])
        grant = self.cli.objects["client-grants/cgr_default"]
        self.assertEqual(grant["scope"], ["coach:read"])
        self.assertEqual(grant["subject_type"], "user")
        self.assertFalse(grant["allow_all_scopes"])
        self.assertNotIn("client_id", grant)
        for key in ("clients/client_coach", "client-grants/cgr_coach", "resource-servers/api_coach"):
            self.assertEqual(prior[key], self.cli.objects[key])
        settings = self.cli.objects["tenants/settings"]
        self.assertTrue(settings["flags"]["unrelated_flag"])
        self.assertTrue(settings["flags"]["enable_dynamic_client_registration"])
        self.assertNotIn("dynamic_client_registration_security_mode", settings)
        self.assertEqual([c[:2] for c in self.mutations()],
                         [("post", "client-grants"), ("patch", "tenants/settings")])
        self.cli.calls.clear()
        setup.configure_dcr(self.cli, self.state)
        self.assertEqual(self.mutations(), [])

    def test_dcr_fails_closed_if_api_scope_policy_or_consent_changes(self):
        for alteration in (
            {"scopes": [{"value": "coach:write", "description": "Write"}]},
            {"subject_type_authorization": {"client": {"policy": "allow_all"}}},
            {"subject_type_authorization": {"anonymous_user": {"policy": "allow_all"}}},
            {"skip_consent_for_verifiable_first_party_clients": True},
        ):
            with self.subTest(alteration=alteration):
                original = copy.deepcopy(self.cli.objects["resource-servers/api_coach"])
                merge(self.cli.objects["resource-servers/api_coach"], alteration)
                self.cli.calls.clear()
                with self.assertRaises(setup.SetupError):
                    setup.configure_dcr(self.cli, self.state)
                self.assertEqual(self.mutations(), [])
                self.cli.objects["resource-servers/api_coach"] = original

    def test_dcr_requires_completed_database_connection(self):
        self.cli.objects["connections/con_owner"]["is_domain_connection"] = False
        with self.assertRaises(setup.SetupError):
            setup.configure_dcr(self.cli, self.state)
        self.assertEqual(self.mutations(), [])

    def test_dcr_does_not_enable_registration_in_permissive_mode(self):
        self.cli.objects["tenants/settings"]["dynamic_client_registration_security_mode"] = "permissive"
        with self.assertRaises(setup.SetupError):
            setup.configure_dcr(self.cli, self.state)
        self.assertEqual(self.mutations(), [])
        self.assertNotIn("enable_dynamic_client_registration", self.cli.objects["tenants/settings"]["flags"])

    def test_dcr_does_not_overwrite_unexpected_defaults(self):
        setup.configure_dcr(self.cli, self.state)
        for change in ({"scope": ["coach:read", "coach:write"]},
                       {"allow_all_scopes": True}, {"subject_type": "client"}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.cli.objects["client-grants/cgr_default"])
                self.cli.objects["client-grants/cgr_default"].update(change)
                self.cli.calls.clear()
                with self.assertRaises(setup.SetupError):
                    setup.configure_dcr(self.cli, self.state)
                self.assertEqual(self.mutations(), [])
                self.cli.objects["client-grants/cgr_default"] = original

    def test_dcr_grant_creation_failure_never_enables_registration(self):
        self.cli.fail_after_create = "client-grants"
        with self.assertRaises(setup.SetupError):
            setup.configure_dcr(self.cli, self.state)
        self.assertNotIn("enable_dynamic_client_registration", self.cli.objects["tenants/settings"]["flags"])
        self.cli.calls.clear()
        setup.configure_dcr(self.cli, self.state)
        self.assertFalse([c for c in self.cli.calls if c[:2] == ("post", "client-grants")])

    def test_dcr_detects_missing_grant_before_enabling_registration(self):
        original = self.cli.api
        def drop_grant(method, path, payload=None, query=None):
            result = original(method, path, payload, query)
            if method == "post" and path == "client-grants":
                del self.cli.objects["client-grants/cgr_default"]
            return result
        self.cli.api = drop_grant
        with self.assertRaises(setup.SetupError):
            setup.configure_dcr(self.cli, self.state)
        self.assertNotIn("enable_dynamic_client_registration", self.cli.objects["tenants/settings"]["flags"])


class CLITransportTests(unittest.TestCase):
    def response(self, payload, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(payload), "NEVER_PRINT_STDERR")

    def test_preflight_rejects_mismatch_before_account_api(self):
        runner = Mock(return_value=self.response([{"name": "other.eu.auth0.com", "active": True},
                                                 {"name": TENANT, "active": False}]))
        cli = setup.Auth0CLI("/mock/auth0", TENANT, runner)
        with self.assertRaises(setup.SetupError):
            cli.preflight()
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn("--tenant", runner.call_args.args[0])
        with self.assertRaises(setup.SetupError):
            cli.api("get", "tenants/settings")

    def test_json_body_only_on_stdin_and_output_is_captured(self):
        runner = Mock(side_effect=[self.response([{"name": TENANT, "active": True}]),
                                   self.response({"client_id": "abc", "client_secret": "PRIVATE"})])
        cli = setup.Auth0CLI("/mock/auth0", TENANT, runner)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            cli.preflight()
            cli.api("post", "clients", {"name": "example", "sensitive": "STDIN_ONLY"})
        call = runner.call_args
        self.assertNotIn("STDIN_ONLY", " ".join(call.args[0]))
        self.assertIn("STDIN_ONLY", call.kwargs["input"])
        self.assertTrue(call.kwargs["capture_output"])
        self.assertEqual(call.args[0][call.args[0].index("--tenant") + 1], TENANT)
        self.assertEqual(captured.getvalue(), "")

    def test_failures_never_relay_cli_tokens_or_invalid_json(self):
        for result in (self.response({"client_secret": "PRIVATE"}, 1),
                       subprocess.CompletedProcess([], 0, "PRIVATE invalid JSON", "PRIVATE")):
            cli = setup.Auth0CLI("/mock/auth0", TENANT, Mock(return_value=result))
            with self.assertRaises(setup.SetupError) as caught:
                cli.preflight()
            self.assertNotIn("PRIVATE", str(caught.exception))
        cli = setup.Auth0CLI("/mock/auth0", TENANT, Mock(side_effect=subprocess.TimeoutExpired("cmd", 60, "PRIVATE")))
        with self.assertRaises(setup.SetupError) as caught:
            cli.preflight()
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_collection_paginates_and_never_supports_delete(self):
        runner = Mock(side_effect=[self.response([{"name": TENANT, "active": True}]),
                                   self.response([{"id": str(i)} for i in range(100)]), self.response([{"id": "last"}])])
        cli = setup.Auth0CLI("/mock/auth0", TENANT, runner)
        cli.preflight()
        self.assertEqual(len(cli.collection("clients")), 101)
        path = runner.call_args.args[0][3]
        self.assertEqual(parse_qs(urlsplit(path).query)["page"], ["1"])
        with self.assertRaises(setup.SetupError):
            cli.api("delete", "clients/anything")

    def test_query_transport_preserves_quotes_commas_and_equals(self):
        runner = Mock(side_effect=[self.response([{"name": TENANT, "active": True}]),
                                   self.response([])])
        cli = setup.Auth0CLI("/mock/auth0", TENANT, runner)
        cli.preflight()
        query = {
            "fields": "client_id,name,client_metadata",
            "q": 'client_id:"tpc_example"',
            "test_value": 'a=b,c=d&x=1+2"',
            "page": 0,
        }
        cli.api("get", "clients", query=query)
        arguments = runner.call_args.args[0]
        self.assertNotIn("--query", arguments)
        path = arguments[3]
        self.assertEqual(urlsplit(path).path, "clients")
        self.assertEqual(parse_qs(urlsplit(path).query),
                         {key: [str(value)] for key, value in query.items()})
        self.assertEqual(runner.call_args.kwargs["input"], "")


if __name__ == "__main__":
    unittest.main()
