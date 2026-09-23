"""No network, credentials, training data or cloud writes in deployment tests."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra"))
import deploy_chat as deploy


ISSUER = deploy.PUBLIC_URL.removesuffix("/mcp")
JWKS = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
OWNER = "verified-firebase-owner"


def response(status=200, body=None, headers=None):
    return deploy.Response(status, headers or {}, json.dumps(body or {}).encode())


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "oauth.json"
    path.write_text(json.dumps({"firebase_project_id": deploy.PROJECT, "firebase_api_key": "public-firebase-api-key", "owner_subject": OWNER, "oauth_redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"]}))
    return path


@pytest.fixture
def settings(config_file, monkeypatch):
    original = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: True if path == ROOT / "adapters/mcp/static/dashboard/index.html" else original(path))
    return deploy.load_settings(config_file)


class Cloud:
    def __init__(self):
        self.project = deploy.PROJECT
        self.number = deploy.PROJECT_NUMBER
        self.journal = []
        self.runtime_exists = False
        self.chat_exists = False
        self.chat_env = []
        self.backend_annotations = {}
        self.policies = {deploy.BACKEND_SERVICE: {"bindings": []}, deploy.SERVICE: {"bindings": []}}
        self.project_policy = {"bindings": [{"role": "roles/datastore.user", "members": [f"serviceAccount:{deploy.RUNTIME}"], "condition": {"expression": deploy.auth_condition()}}]}
        self.firebase_changes = {}
        self.environments = []
        self.builder_exists = True

    def json(self, *args, **kwargs):
        self.journal.append(("json", args, kwargs))
        if args[:3] == ("firestore", "databases", "describe"):
            return {"type": "FIRESTORE_NATIVE", "locationId": deploy.REGION}
        if args[:2] == ("projects", "describe"):
            return {"projectNumber": self.number}
        if args[:2] == ("projects", "get-iam-policy"):
            return copy.deepcopy(self.project_policy)
        if args[:3] == ("iam", "service-accounts", "describe"):
            exists = self.builder_exists if args[3] == deploy.BUILDER else self.runtime_exists
            return {"email": args[3]} if exists else None
        if args[:3] == ("run", "services", "describe"):
            if args[3] == deploy.BACKEND_SERVICE:
                return {"metadata": {"annotations": self.backend_annotations}}
            return {"metadata": {"name": deploy.SERVICE}, "spec": {"template": {"spec": {"containers": [{"env": self.chat_env}]}}}} if self.chat_exists else None
        if args[:3] == ("run", "services", "get-iam-policy"):
            return copy.deepcopy(self.policies[args[3]])
        raise AssertionError(f"Unexpected mocked read: {args[:3]}")

    def rest(self, method, url, body=None):
        self.journal.append(("rest", (url, method), {}))
        if url.endswith("/defaultSupportedIdpConfigs/google.com"):
            return {"enabled": True, **self.firebase_changes.get("provider", {})}
        if url.endswith("/config"):
            return {"authorizedDomains": [ISSUER.removeprefix("https://")], **self.firebase_changes.get("config", {})}
        if url.endswith("/accounts:lookup"):
            return {"users": [{"localId": OWNER, "emailVerified": True, "providerUserInfo": [{"providerId": "google.com"}], **self.firebase_changes.get("user", {})}]}
        raise AssertionError("Unexpected Firebase URL")

    def command(self, *args, **kwargs):
        self.journal.append(("command", args, kwargs))
        if args[:3] == ("iam", "service-accounts", "create"):
            self.runtime_exists = True
        elif args[:2] == ("run", "deploy"):
            assert args[2] == deploy.SERVICE
            self.chat_exists = True
            env_path = next(arg.removeprefix("--env-vars-file=") for arg in args if arg.startswith("--env-vars-file="))
            self.environments.append(json.loads(Path(env_path).read_text()))
        elif args[:3] in (("run", "services", "add-iam-policy-binding"),
                          ("run", "services", "remove-iam-policy-binding")):
            service = args[3]
            member = next(arg.removeprefix("--member=") for arg in args if arg.startswith("--member="))
            role = next(arg.removeprefix("--role=") for arg in args if arg.startswith("--role="))
            bindings = self.policies[service]["bindings"]
            binding = next((item for item in bindings if item["role"] == role), None)
            if args[2] == "add-iam-policy-binding":
                if binding is None:
                    binding = {"role": role, "members": []}
                    bindings.append(binding)
                if member not in binding["members"]:
                    binding["members"].append(member)
            elif binding and member in binding["members"]:
                binding["members"].remove(member)
        elif args[:2] == ("auth", "print-identity-token"):
            return "synthetic-google-id-token\n"
        else:
            raise AssertionError(f"Unexpected mocked mutation: {args[:3]}")
        return ""

    @property
    def mutations(self):
        return [args for kind, args, _ in self.journal if kind == "command" and args[0] != "auth"]


class Http:
    def __init__(self, cloud):
        self.cloud = cloud
        self.overrides = {}
        self.oauth_discovery_status = 200
        self.metadata = {"issuer": ISSUER, "jwks_uri": JWKS,
                         "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token",
                         "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                         "code_challenge_methods_supported": ["S256"]}
        self.keys = [{"kty": "RSA", "kid": "signing-1", "use": "sig", "alg": "RS256",
                      "n": base64.urlsafe_b64encode(b"\x81" * 256).decode().rstrip("="), "e": "AQAB"}]

    def __call__(self, url, *, method="GET", headers=None, body=None):
        headers = headers or {}
        transport = "private" if "X-Serverless-Authorization" in headers else "public"
        self.cloud.journal.append(("http", (url, transport, method), {"headers": headers, "body": body}))
        if (url, transport) in self.overrides:
            return self.overrides[(url, transport)]
        if url == JWKS:
            return response(body={"keys": self.keys})
        if url == deploy.BACKEND_URL + "/v1/status":
            return response(403)
        origin = deploy.PUBLIC_URL.removesuffix("/mcp")
        public = bool(deploy.public_bindings(self.cloud.policies[deploy.SERVICE]))
        if not public and transport != "private":
            return response(403)
        if url == origin + "/.well-known/oauth-authorization-server":
            return response(body=self.metadata)
        if url == origin + "/dashboard/config":
            return response(body={"projectId": deploy.PROJECT, "apiKey": "public-firebase-api-key"})
        if url == origin + "/dashboard/api/status":
            return response(401)
        if url == origin + "/v1/health":
            return response(body={"service": "ai-coach-mcp", "status": "ok"})
        metadata_url = origin + "/.well-known/oauth-protected-resource/mcp"
        if url == metadata_url:
            return response(body={"resource": deploy.PUBLIC_URL, "authorization_servers": [ISSUER],
                                  "scopes_supported": ["coach:read"]})
        if url == deploy.PUBLIC_URL:
            return response(401, headers={"www-authenticate": f'Bearer resource_metadata="{metadata_url}"'})
        raise AssertionError("Unexpected mocked HTTP destination")


def test_requires_explicit_nonplaceholder_configuration_before_cloud_calls(config_file, monkeypatch):
    settings = deploy.load_settings(config_file)
    assert settings.owner_subject == OWNER and settings.oauth_issuer == ISSUER
    config_file.write_text('{}')
    monkeypatch.setattr(deploy, "Cloud", lambda *_: pytest.fail("Cloud constructed before config validation"))
    with pytest.raises(SystemExit, match="requires exactly"):
        deploy.main(["--firebase-config", str(config_file)])


@pytest.mark.parametrize("changes", [
    {"owner_subject": ""}, {"owner_subject": " owner"}, {"owner_subject": "two users"},
    {"owner_subject": "x" * 129}, {"firebase_project_id": "other-project"},
    {"firebase_api_key": "bad\nkey"}, {"oauth_redirect_uris": []},
    {"oauth_redirect_uris": ["http://evil.example/cb"]}, {"access_token": "secret"},
    {"oauth_issuer": "https://retired-provider.example"},
])
def test_invalid_or_placeholder_settings_fail_closed(config_file, changes):
    data = json.loads(config_file.read_text())
    data.update(changes)
    config_file.write_text(json.dumps(data))
    with pytest.raises(deploy.DeploymentError):
        deploy.load_settings(config_file)


def test_config_disallows_secret_and_unknown_fields(config_file):
    data = json.loads(config_file.read_text())
    data["access_token"] = "must-not-be-loaded"
    config_file.write_text(json.dumps(data))
    with pytest.raises(deploy.DeploymentError, match="requires exactly"):
        deploy.load_settings(config_file)


def test_success_deploys_private_then_exposes_only_oauth_adapter(settings):
    cloud = Cloud()
    http = Http(cloud)
    result = deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=http)
    assert result["deployed"] is True and result["oauth_denial_verified"] is True
    assert result["ready_for_chat"] is False and result["owner_login_verified"] is False
    assert deploy.public_bindings(cloud.policies[deploy.BACKEND_SERVICE]) == []
    assert deploy.public_bindings(cloud.policies[deploy.SERVICE]) == [("roles/run.invoker", "allUsers")]
    grants = [args for args in cloud.mutations if "add-iam-policy-binding" in args]
    assert len(grants) == 2
    assert grants[0][3] == deploy.BACKEND_SERVICE
    assert f"--member=serviceAccount:{deploy.RUNTIME}" in grants[0]
    assert "--role=roles/run.invoker" in grants[0]
    assert grants[1][3] == deploy.SERVICE and "--member=allUsers" in grants[1]
    build = next(args for args in cloud.mutations if args[:2] == ("run", "deploy"))
    for required in ("--no-allow-unauthenticated", "--invoker-iam-check", "--min=0", "--max=2",
                     "--cpu=1", "--memory=512Mi", "--concurrency=8", "--timeout=120s", "--clear-secrets"):
        assert required in build
    assert f"--service-account={deploy.RUNTIME}" in build
    assert f"--build-service-account=projects/{deploy.PROJECT}/serviceAccounts/{deploy.BUILDER}" in build
    env = cloud.environments[0]
    assert set(env) == {"BACKEND_URL", "BACKEND_ALLOWED_HOST", "MCP_PUBLIC_URL", "FIREBASE_PROJECT_ID", "FIREBASE_API_KEY", "FIREBASE_OWNER_UID", "OAUTH_REDIRECT_URIS", "AUTH_FIRESTORE_DATABASE"}
    assert "synthetic-google-id-token" not in json.dumps(env)
    publication = next(index for index, (kind, args, _) in enumerate(cloud.journal)
                       if kind == "command" and "--member=allUsers" in args)
    private_checks = [(index, kwargs) for index, (kind, args, kwargs) in enumerate(cloud.journal)
                      if kind == "http" and args[:2] == (deploy.PUBLIC_URL, "private")]
    assert len(private_checks) == 2 and all(index < publication for index, _ in private_checks)
    assert private_checks[0][1]["headers"]["X-Serverless-Authorization"] == "Bearer synthetic-google-id-token"
    assert "Authorization" not in private_checks[0][1]["headers"]
    # Probes never fetch training context or files and never send real OAuth tokens.
    assert all("/v1/context" not in args[0] for kind, args, _ in cloud.journal if kind == "http")


def test_provider_or_backend_failure_makes_no_cloud_mutations(settings):
    for target in ("provider", "backend"):
        cloud = Cloud()
        http = Http(cloud)
        if target == "provider":
            http.keys = []
        else:
            http.overrides[(deploy.BACKEND_URL + "/v1/status", "public")] = response(200, {"private": "must not print"})
        with pytest.raises(deploy.DeploymentError):
            deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=http)
        assert cloud.mutations == []


@pytest.mark.parametrize("section,changes", [("provider", {"enabled": False}),
    ("config", {"authorizedDomains": []}), ("user", {"disabled": True}),
    ("user", {"emailVerified": False}), ("user", {"providerUserInfo": []}), ("user", {"localId": "other"})])
def test_firebase_owner_and_provider_preflight(settings, section, changes):
    cloud = Cloud()
    cloud.firebase_changes[section] = changes
    with pytest.raises(deploy.DeploymentError):
        deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud))
    assert cloud.mutations == []


def test_only_public_firebase_signing_keys_are_accepted(settings):
    http = Http(Cloud())
    deploy.verify_provider(settings, http)
    for field, value in (("use", "enc"), ("d", "private"), ("kty", "EC")):
        original = copy.deepcopy(http.keys)
        http.keys[0][field] = value
        with pytest.raises(deploy.DeploymentError):
            deploy.verify_provider(settings, http)
        http.keys = original


def test_wrong_project_or_projectwide_runtime_permission_is_rejected(settings):
    for scenario in ("number", "permission"):
        cloud = Cloud()
        if scenario == "number":
            cloud.number = "000000"
        else:
            cloud.project_policy = {"bindings": [{"role": "roles/editor", "members": [f"serviceAccount:{deploy.RUNTIME}"]}]}
        with pytest.raises(deploy.DeploymentError):
            deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud))
        assert cloud.mutations == []


def test_backend_public_iam_or_disabled_check_rejected_even_if_probe403(settings):
    for scenario in ("iam", "check"):
        cloud = Cloud()
        if scenario == "iam":
            cloud.policies[deploy.BACKEND_SERVICE]["bindings"] = [{"role": "roles/run.invoker", "members": ["allUsers"]}]
        else:
            cloud.backend_annotations["run.googleapis.com/invoker-iam-disabled"] = "true"
        with pytest.raises(deploy.DeploymentError):
            deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud))
        assert cloud.mutations == []


def test_private_oauth_failure_never_publishes(settings):
    cloud = Cloud()
    http = Http(cloud)
    http.overrides[(deploy.PUBLIC_URL, "private")] = response(200, {"do_not_log": "sensitive body"})
    with pytest.raises(deploy.DeploymentError, match="deny missing/invalid") as error:
        deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=http)
    assert "sensitive" not in str(error.value)
    assert not any("--member=allUsers" in args for args in cloud.mutations)
    assert deploy.public_bindings(cloud.policies[deploy.SERVICE]) == []


def test_wrong_discovery_audience_is_rejected_before_publication(settings):
    cloud = Cloud()
    http = Http(cloud)
    metadata_url = deploy.PUBLIC_URL.removesuffix("/mcp") + "/.well-known/oauth-protected-resource/mcp"
    http.overrides[(metadata_url, "private")] = response(body={"resource": deploy.BACKEND_URL,
                                                               "authorization_servers": [ISSUER]})
    with pytest.raises(deploy.DeploymentError, match="configured resource"):
        deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=http)
    assert not any("--member=allUsers" in args for args in cloud.mutations)


def test_failed_final_public_probe_revokes_adapter_public_access(settings):
    cloud = Cloud()
    http = Http(cloud)
    http.overrides[(deploy.PUBLIC_URL, "public")] = response(200)
    with pytest.raises(deploy.DeploymentError, match="restored to private"):
        deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=http)
    assert deploy.public_bindings(cloud.policies[deploy.SERVICE]) == []
    assert deploy.public_bindings(cloud.policies[deploy.BACKEND_SERVICE]) == []
    revocations = [args for args in cloud.mutations if "remove-iam-policy-binding" in args]
    assert len(revocations) == 1 and revocations[0][3] == deploy.SERVICE


def test_redeployment_removes_existing_public_binding_before_build(settings):
    cloud = Cloud()
    cloud.chat_exists = True
    cloud.runtime_exists = True
    cloud.policies[deploy.SERVICE] = {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}
    deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud))
    removal = next(index for index, args in enumerate(cloud.mutations) if "remove-iam-policy-binding" in args)
    build = next(index for index, args in enumerate(cloud.mutations) if args[:2] == ("run", "deploy"))
    assert removal < build
    assert not any(args[:3] == ("iam", "service-accounts", "create") for args in cloud.mutations)


def test_readonly_check_does_not_create_resources_or_claim_ready(settings):
    cloud = Cloud()
    result = deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud), check_only=True)
    assert result == {"project": deploy.PROJECT, "preflight_verified": True, "deployed": False,
                      "owner_login_verified": False, "ready_for_chat": False}
    assert cloud.mutations == []



def test_redeployment_replaces_retired_identity_configuration(settings):
    cloud = Cloud()
    cloud.chat_exists = True
    cloud.chat_env = [
        {"name": "OAUTH_ISSUER", "value": "https://retired-idp.example/"},
        {"name": "OAUTH_AUDIENCE", "value": "retired-audience"},
        {"name": "OAUTH_JWKS_URL", "value": "https://retired-idp.example/jwks"},
        {"name": "OAUTH_OWNER_SUBJECT", "value": "retired-owner"},
        {"name": "DASHBOARD_CLIENT_ID", "value": "retired-client"},
    ]
    deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud))
    env = cloud.environments[0]
    legacy_keys = {"OAUTH_ISSUER", "OAUTH_AUDIENCE", "OAUTH_JWKS_URL",
                   "OAUTH_OWNER_SUBJECT", "DASHBOARD_CLIENT_ID"}
    assert legacy_keys.isdisjoint(env.keys())
    assert env["FIREBASE_OWNER_UID"] == OWNER
    assert env["AUTH_FIRESTORE_DATABASE"] == "ai-coach-auth"
    assert env["FIREBASE_PROJECT_ID"] == settings.firebase_project_id
    assert env["MCP_PUBLIC_URL"] == settings.public_url
    assert env["BACKEND_URL"] == settings.backend_url


def test_dashboard_requires_a_built_shell(settings, tmp_path):
    source = tmp_path / "adapter"
    (source / "src/ai_coach_mcp").mkdir(parents=True)
    (source / "Dockerfile").write_text("FROM python")
    (source / "src/ai_coach_mcp/app.py").write_text("")
    cloud = Cloud()
    with pytest.raises(deploy.DeploymentError, match="Build the dashboard"):
        deploy.deploy(cloud, settings, source, http=Http(cloud))
    assert cloud.mutations == []


@pytest.mark.parametrize("condition", [None, {"expression": "true"},
    {"expression": 'resource.name=="projects/other/databases/(default)"'}])
def test_auth_database_grant_cannot_be_broadened(settings, condition):
    cloud = Cloud()
    cloud.project_policy["bindings"][0]["condition"] = condition or {}
    with pytest.raises(deploy.DeploymentError, match="database-restricted"):
        deploy.deploy(cloud, settings, ROOT / "adapters/mcp", http=Http(cloud))
    assert cloud.mutations == []
