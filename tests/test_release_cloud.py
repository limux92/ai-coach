"""Two-service release simulation and static health boundaries; no cloud calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_cloud as cloud
import release_health as health
from release_git import ReleaseError


def service(name):
    revision = name + "-old"
    return {"metadata": {"annotations": {"run.googleapis.com/ingress": "all"}},
            "spec": {"template": {"metadata": {"name": revision, "annotations": {"autoscaling.knative.dev/maxScale": "2"}},
                                   "spec": {"serviceAccountName": "existing-runtime", "containers": [{"image": "image-old",
                                            "env": [{"name": "EXAMPLE", "value": "preserve"}]}]}}},
            "status": {"conditions": [{"type": "Ready", "status": "True"}],
                       "latestReadyRevisionName": revision, "latestCreatedRevisionName": revision,
                       "traffic": [{"revisionName": revision, "percent": 100}]}}


class FakeCloud:
    def __init__(self, monkeypatch):
        self.state = {name: service(name) for name in cloud.SERVICES}
        for name in cloud.SERVICES:
            members = ["allUsers"] if name == cloud.SERVICE else ["serviceAccount:gateway"]
            self.state[name + "-iam"] = {"bindings": [{"role": "roles/run.invoker", "members": members}]}
        self.commands, self.probes, self.events = [], [], []
        self.fail_probe = None
        self.fail_promotion = None
        self.drift = None
        self.operator_change = None
        self.subject = cloud.CloudRelease.__new__(cloud.CloudRelease)
        self.subject.run = SimpleNamespace(save=lambda: None)
        self.subject.receipt = {}
        self.subject.settings = SimpleNamespace(public_url="https://canonical.run.app/mcp")
        self.subject.before = None
        self.subject.candidates = {}
        self.subject.expected_templates, self.subject.expected_traffic, self.subject.expected_tags = {}, {}, {}
        self.subject.snapshot = self.snapshot
        self.subject.command = self.command
        self.subject.probe = self.probe
        monkeypatch.setattr(cloud, "probe_backend", lambda *a: None)
        monkeypatch.setattr(cloud, "probe_adapter", lambda *a, **k: None)
        monkeypatch.setattr(cloud.time, "sleep", lambda _: None)
        self.subject.preflight()

    def snapshot(self, label):
        if self.drift and label.startswith("candidate-"):
            self.drift(self.state)
            self.drift = None
        if self.operator_change and label.startswith("failure-"):
            self.state[cloud.SERVICE]["status"]["traffic"] = [{"revisionName": "other-operator", "percent": 100}]
            self.operator_change = False
        return deepcopy(self.state)

    def command(self, *args, **kwargs):
        self.commands.append(args)
        flags = dict(a.split("=", 1) for a in args if a.startswith("--") and "=" in a)
        if args[:2] == ("run", "deploy"):
            name = args[2]
            revision = name + "-" + flags["--revision-suffix"]
            state = self.state[name]
            state["spec"]["template"]["metadata"]["name"] = revision
            state["spec"]["template"]["spec"]["containers"][0]["image"] = "image-new"
            state["status"].update(latestReadyRevisionName=revision, latestCreatedRevisionName=revision)
            state["status"]["traffic"].append({"revisionName": revision, "tag": flags["--tag"],
                                               "url": "https://candidate-" + name + ".run.app"})
            self.events.append("stage:" + name)
        else:
            name = args[3]
            rows = [{"revisionName": part.split("=")[0], "percent": int(part.split("=")[1])}
                    for part in flags["--to-revisions"].split(",")]
            self.state[name]["status"]["traffic"] = rows
            self.events.append("traffic:" + name)
            if self.fail_promotion == name:
                self.fail_promotion = None
                raise ReleaseError("response lost after traffic changed")
        return ""

    def probe(self, name, origin, assets, *, production=False):
        self.probes.append((name, production))
        self.events.append(("production:" if production else "candidate:") + name)
        if self.fail_probe == (name, production):
            raise ReleaseError("probe failed")

    def deploy(self):
        self.subject.deploy({name: Path("/safe/source") for name in cloud.SERVICES}, {}, "abcdef123", "test",
                            lambda: self.events.append("git-rechecked"))

    def restored(self, name):
        return cloud.traffic(self.state[name]) == {name + "-old": 100}


def test_both_candidates_pass_before_first_promotion(monkeypatch):
    fake = FakeCloud(monkeypatch)
    fake.deploy()
    assert fake.events == ["stage:" + cloud.BACKEND_SERVICE, "candidate:" + cloud.BACKEND_SERVICE,
                           "stage:" + cloud.SERVICE, "candidate:" + cloud.SERVICE, "git-rechecked",
                           "traffic:" + cloud.BACKEND_SERVICE, "production:" + cloud.BACKEND_SERVICE,
                           "traffic:" + cloud.SERVICE, "production:" + cloud.SERVICE]
    assert fake.subject.receipt["both_candidates_verified"]
    assert set(fake.subject.receipt["revisions"]) == set(cloud.SERVICES)
    assert not fake.subject.receipt["owner_login_or_training_read_verified"]
    deploys = [args for args in fake.commands if args[1] == "deploy"]
    assert len(deploys) == 2 and all("--no-traffic" in args for args in deploys)
    assert not any(arg.startswith(("--set-env", "--clear-", "--allow-", "--service-account"))
                   for args in fake.commands for arg in args)


@pytest.mark.parametrize("name", cloud.SERVICES)
def test_either_candidate_failure_leaves_both_services_untouched(monkeypatch, name):
    fake = FakeCloud(monkeypatch)
    fake.fail_probe = (name, False)
    with pytest.raises(ReleaseError, match="probe failed"):
        fake.deploy()
    assert all(fake.restored(service) for service in cloud.SERVICES)
    assert not any(args[1:3] == ("services", "update-traffic") for args in fake.commands)


@pytest.mark.parametrize("name", cloud.SERVICES)
def test_production_failure_restores_both_previous_revisions(monkeypatch, name):
    fake = FakeCloud(monkeypatch)
    fake.fail_probe = (name, True)
    with pytest.raises(ReleaseError, match="probe failed"):
        fake.deploy()
    assert all(fake.restored(service) for service in cloud.SERVICES)
    assert all("NOT VERIFIED" not in result for result in fake.subject.receipt["rollback"].values())


@pytest.mark.parametrize("name", cloud.SERVICES)
def test_ambiguous_promotion_response_recovers_both_services(monkeypatch, name):
    fake = FakeCloud(monkeypatch)
    fake.fail_promotion = name
    with pytest.raises(ReleaseError, match="response lost"):
        fake.deploy()
    assert all(fake.restored(service) for service in cloud.SERVICES)


def test_rollback_does_not_overwrite_other_operator_but_restores_backend(monkeypatch):
    fake = FakeCloud(monkeypatch)
    fake.fail_probe = (cloud.SERVICE, True)
    fake.operator_change = True
    with pytest.raises(ReleaseError):
        fake.deploy()
    assert cloud.traffic(fake.state[cloud.SERVICE]) == {"other-operator": 100}
    assert fake.restored(cloud.BACKEND_SERVICE)
    assert "NOT VERIFIED" in fake.subject.receipt["rollback"][cloud.SERVICE]


@pytest.mark.parametrize("name", cloud.SERVICES)
@pytest.mark.parametrize("change", ["env", "iam"])
def test_configuration_or_iam_drift_stops_both_promotions(monkeypatch, name, change):
    fake = FakeCloud(monkeypatch)
    def drift(state):
        if change == "env":
            state[name]["spec"]["template"]["spec"]["containers"][0]["env"] = []
        else:
            state[name + "-iam"]["bindings"] = []
    fake.drift = drift
    with pytest.raises(ReleaseError, match="changed"):
        fake.deploy()
    assert all(fake.restored(service) for service in cloud.SERVICES)


def test_authenticated_backend_probe_reads_only_static_health(monkeypatch):
    calls = []
    monkeypatch.setattr(health, "identity_token", lambda: "test-only-token")
    def http(url, **kwargs):
        calls.append((url, kwargs))
        if not kwargs.get("headers"):
            return health.Response(403, {}, b"")
        return health.Response(200, {}, json.dumps({"service": "ai-coach-data", "status": "ok", "version": "0.1.0"}).encode())
    monkeypatch.setattr(health, "http", http)
    health.probe_backend("https://candidate.run.app")
    assert len(calls) == 2
    assert all(url == "https://candidate.run.app/health" for url, _ in calls)
    assert calls[1][1]["headers"] == {"Authorization": "Bearer test-only-token"}


@pytest.mark.parametrize("migration", [False, True])
@pytest.mark.parametrize("status,body", [
    (404, b'{"detail":"Not Found"}'), (404, b"Google frontend 404"),
    (403, b"denied"), (500, b"failed"), (200, b'{"status":"wrong"}')])
def test_health_migration_only_permits_application_missing_route(monkeypatch, migration, status, body):
    monkeypatch.setattr(health, "identity_token", lambda: "test-token")
    monkeypatch.setattr(health, "http", lambda _, **kw: health.Response(
        status if kw.get("headers") else 403, {}, body))
    if migration and status == 404 and body == b'{"detail":"Not Found"}':
        assert health.probe_backend("https://old.run.app", allow_missing_health=True) == "missing-route"
    else:
        with pytest.raises(ReleaseError):
            health.probe_backend("https://old.run.app", allow_missing_health=migration)


@pytest.mark.parametrize("status", [200, 401, 404, 500])
def test_health_migration_still_requires_anonymous_403(monkeypatch, status):
    monkeypatch.setattr(health, "http", lambda *a, **k: health.Response(status, {}, b""))
    monkeypatch.setattr(health, "identity_token", lambda: pytest.fail("Must reject anonymous response first"))
    with pytest.raises(ReleaseError, match="deny anonymous"):
        health.probe_backend("https://old.run.app", allow_missing_health=True)


def test_migration_requires_exact_revision_and_candidates_remain_strict(monkeypatch):
    fake = FakeCloud(monkeypatch)
    calls = []
    monkeypatch.setattr(cloud, "probe_backend", lambda origin, **kw: calls.append((origin, kw)) or "missing-route")
    with pytest.raises(ReleaseError, match="exact existing"):
        fake.subject.preflight(bootstrap_backend_health="wrong-revision")
    assert calls == []
    old = cloud.BACKEND_SERVICE + "-old"
    fake.subject.preflight(bootstrap_backend_health=old)
    assert calls == [(cloud.BACKEND_URL, {"allow_missing_health": True})]
    assert fake.subject.receipt["backend_health_migration"] == {"from_revision": old, "existing_health": "missing-route"}
    cloud.CloudRelease.probe(fake.subject, cloud.BACKEND_SERVICE, "https://candidate.run.app", {})
    cloud.CloudRelease.probe(fake.subject, cloud.BACKEND_SERVICE, cloud.BACKEND_URL, {}, production=True)
    assert calls[1:] == [("https://candidate.run.app", {}), (cloud.BACKEND_URL, {})]


def failed_backend(fake):
    revision = cloud.BACKEND_SERVICE + "-r-abcdef12-260927-061340-af98"
    status = fake.state[cloud.BACKEND_SERVICE]["status"]
    status["latestCreatedRevisionName"] = revision
    status["conditions"] = [{"type": "Ready", "status": "False", "reason": "HealthCheckContainerError"}]
    documents = {}
    for name in (revision, status["latestReadyRevisionName"]):
        documents[name] = {"metadata": {"name": name, "labels": {"serving.knative.dev/service": cloud.BACKEND_SERVICE}},
                           "status": {"conditions": [{"type": "Ready", "status": "False" if name == revision else "True",
                                                      "reason": "HealthCheckContainerError" if name == revision else ""}]}}
    fake.subject.document = lambda *args: documents[args[3]]
    return revision, documents


def test_explicit_failed_candidate_recovery_retains_health_iam_and_gateway_gates(monkeypatch):
    fake = FakeCloud(monkeypatch)
    revision, _ = failed_backend(fake)
    calls = []
    monkeypatch.setattr(cloud, "probe_backend", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ReleaseError, match="not ready"):
        fake.subject.preflight()
    fake.subject.preflight(recover_failed_backend_candidate=revision)
    assert calls == [((cloud.BACKEND_URL,), {})]
    assert fake.subject.receipt["failed_backend_candidate_recovery"]["serving_ready_verified"]
    fake.state[cloud.SERVICE]["status"]["conditions"][0]["status"] = "False"
    with pytest.raises(ReleaseError, match="not ready"):
        fake.subject.preflight(recover_failed_backend_candidate=revision)
    fake.state[cloud.SERVICE]["status"]["conditions"][0]["status"] = "True"
    fake.state[cloud.BACKEND_SERVICE + "-iam"]["bindings"][0]["members"].append("allUsers")
    with pytest.raises(ReleaseError, match="public IAM"):
        fake.subject.preflight(recover_failed_backend_candidate=revision)


@pytest.mark.parametrize("change", ["wrong-name", "wrong-latest", "traffic", "old-unready", "wrong-service",
                                    "unknown-failure", "failed-ready", "service-unknown"])
def test_failed_candidate_recovery_rejects_unverified_state(monkeypatch, change):
    fake = FakeCloud(monkeypatch)
    revision, documents = failed_backend(fake)
    status = fake.state[cloud.BACKEND_SERVICE]["status"]
    if change == "wrong-name":
        revision = "some-other-revision"
    elif change == "wrong-latest":
        status["latestCreatedRevisionName"] = "other"
    elif change == "traffic":
        status["traffic"] = [{"revisionName": revision, "percent": 100}]
    elif change == "old-unready":
        documents[status["latestReadyRevisionName"]]["status"]["conditions"][0]["status"] = "False"
    elif change == "wrong-service":
        documents[revision]["metadata"]["labels"]["serving.knative.dev/service"] = "other"
    elif change == "unknown-failure":
        documents[revision]["status"]["conditions"][0]["reason"] = "other"
    elif change == "failed-ready":
        documents[revision]["status"]["conditions"][0]["status"] = "True"
    else:
        status["conditions"][0]["status"] = "Unknown"
    with pytest.raises(ReleaseError):
        fake.subject.preflight(recover_failed_backend_candidate=revision)


def test_token_failure_does_not_expose_provider_output(monkeypatch, capsys):
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="PRIVATE"))
    with pytest.raises(ReleaseError) as error:
        health.identity_token()
    assert "PRIVATE" not in str(error.value) + str(capsys.readouterr())


def test_asset_hash_mismatch_blocks_verification(monkeypatch):
    monkeypatch.setattr(health, "probe_adapter", lambda *a, **k: None)
    monkeypatch.setattr(health, "http", lambda *a, **k: health.Response(200, {}, b"wrong build"))
    with pytest.raises(ReleaseError, match="asset does not match"):
        health.probe_gateway(SimpleNamespace(public_url="https://canonical.run.app/mcp"),
                             "https://candidate.run.app", {"index.html": "expected"})


@pytest.mark.parametrize("allocation", [{}, {"a": True}, {"a": 99}, {"A": 100}, {"a": "100"}, {"a": 101}, {"a": 0}])
def test_worker_traffic_formatter_rejects_invalid_allocations(allocation):
    with pytest.raises(ValueError):
        cloud.traffic_argument(allocation)
    assert cloud.traffic_argument({"b": 30, "a": 70}) == "a=70,b=30"


@pytest.mark.parametrize("origin", ["https://attacker.example/#.run.app", "http://candidate.run.app",
                                    "https://user:pass@candidate.run.app", "https://candidate.run.app/workouts"])
def test_private_health_rejects_invalid_destination_before_getting_token(monkeypatch, origin):
    monkeypatch.setattr(health, "identity_token", lambda: pytest.fail("Must not request a token"))
    with pytest.raises(ReleaseError, match="Unexpected private health origin"):
        health.probe_backend(origin)
