"""Release both existing Cloud Run services while preserving configuration and IAM."""
from __future__ import annotations

import json
import re
import time

from release_git import ReleaseError, require
from release_health import (BACKEND_SERVICE, BACKEND_URL, BUILDER, DOMAIN, PROJECT,
                            REGION, ROOT, SERVICE, DeploymentError, bindings,
                            load_settings, probe_adapter, probe_backend,
                            probe_gateway, runtime_config, traffic, http)

SERVICES = (BACKEND_SERVICE, SERVICE)


def traffic_argument(allocation):
    if not isinstance(allocation, dict) or not allocation:
        raise ValueError("allocation must be a nonempty dict")
    total = 0
    for rev, pct in allocation.items():
        if not isinstance(rev, str):
            raise ValueError("revision names must be strings")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", rev):
            raise ValueError(f"invalid revision name: {rev!r}")
        if isinstance(pct, bool) or not isinstance(pct, int):
            raise ValueError(f"traffic percentage for {rev!r} must be an integer")
        if not (1 <= pct <= 100):
            raise ValueError(f"traffic percentage for {rev!r} must be between 1 and 100")
        total += pct
    if total != 100:
        raise ValueError(f"total traffic must sum to 100, got {total}")
    parts = [f"{rev}={pct}" for rev, pct in sorted(allocation.items())]
    return ",".join(parts)

class CloudRelease:
    def __init__(self, run, receipt):
        self.run, self.receipt = run, receipt
        try:
            self.settings = load_settings(ROOT / ".local/firebase-auth.json")
        except DeploymentError as error:
            raise ReleaseError(str(error)) from None
        self.before = None
        self.candidates = {}
        self.expected_templates = {}
        self.expected_traffic = {}
        self.expected_tags = {}

    def command(self, *args, cwd=None, timeout=180):
        return self.run([str(ROOT / "scripts/gcloud"), *args, f"--project={PROJECT}", "--quiet"],
                        label="Cloud Run " + " ".join(args[:3]), cwd=cwd, timeout=timeout)

    def document(self, *args):
        return json.loads(self.command(*args, "--format=json"))

    def snapshot(self, label):
        result = {}
        for name in SERVICES:
            result[name] = self.document("run", "services", "describe", name, f"--region={REGION}")
            result[name + "-iam"] = self.document("run", "services", "get-iam-policy", name, f"--region={REGION}")
        (self.run.directory / (label + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        return result

    @staticmethod
    def tags(service):
        return {row["tag"]: row["revisionName"] for row in service["status"].get("traffic", []) if row.get("tag")}

    def note(self, stage):
        self.receipt["stage"] = stage
        self.run.save()
        print(stage, flush=True)

    def preflight(self, *, bootstrap_backend_health=None):
        self.before = self.snapshot("cloud-before")
        self.receipt.update(project=PROJECT, region=REGION, services=list(SERVICES),
                            owner_login_or_training_read_verified=False, manual_traffic_rollback={})
        for name in SERVICES:
            service = self.before[name]
            require(any(c.get("status") == "True" for c in service["status"].get("conditions", []) if c["type"] == "Ready"),
                    "Existing Cloud Run service is not ready: " + name)
            self.expected_templates[name] = service["spec"]["template"]
            self.expected_traffic[name] = traffic(service)
            self.expected_tags[name] = self.tags(service)
            runtime_config(service)
            self.receipt["manual_traffic_rollback"][name] = [str(ROOT / "scripts/gcloud"), "run", "services", "update-traffic",
                name, f"--project={PROJECT}", f"--region={REGION}", "--to-revisions=" + traffic_argument(traffic(service)), "--quiet"]
        self.preserved(self.before)
        self.note("Verify existing private backend static health and gateway metadata")
        if bootstrap_backend_health:
            require(self.expected_traffic[BACKEND_SERVICE] == {bootstrap_backend_health: 100},
                    "Health migration must name the exact existing backend revision at 100% traffic.")
            result = probe_backend(BACKEND_URL, allow_missing_health=True)
            self.receipt["backend_health_migration"] = {
                "from_revision": bootstrap_backend_health, "existing_health": result}
        else:
            probe_backend(BACKEND_URL)
        probe_adapter(self.settings, http=http)
        self.receipt["previous_traffic"] = dict(self.expected_traffic)

    def preserved(self, state):
        for name in SERVICES:
            require(runtime_config(state[name]) == runtime_config(self.before[name]),
                    name + " runtime/access configuration changed; stop for review.")
            require(bindings(state[name + "-iam"]) == bindings(self.before[name + "-iam"]),
                    name + " IAM changed; stop for review.")
        backend = state[BACKEND_SERVICE]
        require(backend.get("metadata", {}).get("annotations", {}).get("run.googleapis.com/invoker-iam-disabled") != "true",
                "Backend IAM checks are disabled.")
        require(not any(member in {"allUsers", "allAuthenticatedUsers"}
                        for row in state[BACKEND_SERVICE + "-iam"].get("bindings", []) for member in row.get("members", [])),
                "Backend has a public IAM binding.")

    def stable(self, state, *, deploying=None):
        self.preserved(state)
        for name in SERVICES:
            require(traffic(state[name]) == self.expected_traffic[name], name + " traffic changed concurrently.")
            if name != deploying:
                require(state[name]["spec"]["template"] == self.expected_templates[name], name + " revision changed concurrently.")
                require(self.tags(state[name]) == self.expected_tags[name], name + " tags changed concurrently.")

    def stage(self, name, source, assets, sha, run_id):
        suffix = "r-" + sha[:8] + "-" + run_id.lower()
        revision, tag = name + "-" + suffix, "check-" + run_id.lower()
        self.stable(self.snapshot("before-build-" + name))
        self.command("run", "deploy", name, f"--region={REGION}", f"--source={source}",
                     f"--build-service-account=projects/{PROJECT}/serviceAccounts/{BUILDER}",
                     "--revision-suffix=" + suffix, "--tag=" + tag, "--no-traffic", timeout=2400)
        state = self.snapshot("candidate-" + name)
        self.stable(state, deploying=name)
        service = state[name]
        require(service["status"].get("latestReadyRevisionName") == revision
                and service["status"].get("latestCreatedRevisionName") == revision,
                "Candidate is not the latest ready revision: " + name)
        targets = [t for t in service["status"]["traffic"] if t.get("tag") == tag]
        require(len(targets) == 1 and targets[0].get("revisionName") == revision
                and targets[0].get("percent", 0) == 0, "Candidate is not a zero-traffic target: " + name)
        require(self.tags(service) == {**self.expected_tags[name], tag: revision}, "Unexpected candidate tags: " + name)
        self.expected_templates[name] = service["spec"]["template"]
        self.expected_tags[name] = self.tags(service)
        self.candidates[name] = {"revision": revision, "tag": tag, "url": targets[0]["url"]}
        self.receipt["candidates"] = dict(self.candidates)
        self.note("Verify zero-traffic candidate: " + name)
        self.probe(name, targets[0]["url"], assets)

    def probe(self, name, origin, assets, *, production=False):
        if name == BACKEND_SERVICE:
            probe_backend(origin)
        else:
            probe_gateway(self.settings, origin, assets, production=production)

    def promote(self, name, assets):
        self.stable(self.snapshot("before-promotion-" + name))
        candidate = self.candidates[name]
        self.receipt.setdefault("promotion_attempted", []).append(name)
        self.command("run", "services", "update-traffic", name, f"--region={REGION}",
                     "--to-revisions=" + candidate["revision"] + "=100", "--remove-tags=" + candidate["tag"])
        self.expected_traffic[name] = {candidate["revision"]: 100}
        self.expected_tags[name].pop(candidate["tag"], None)
        self.stable(self.snapshot("after-promotion-" + name))
        self.note("Verify production: " + name)
        origin = BACKEND_URL if name == BACKEND_SERVICE else DOMAIN
        for attempt in range(6):
            try:
                self.probe(name, origin, assets, production=True)
                return
            except RuntimeError:
                if attempt == 5:
                    raise
                print("Waiting for endpoint propagation...", flush=True)
                time.sleep(5)

    def rollback(self):
        results = {}
        # Restore gateway first, then backend. Keep trying the other service if one fails.
        for name in reversed(SERVICES):
            try:
                state = self.snapshot("failure-" + name)
                previous, current = traffic(self.before[name]), traffic(state[name])
                if current == previous:
                    results[name] = "not needed; previous traffic is serving"
                    continue
                candidate = self.candidates.get(name)
                require(candidate and current == {candidate["revision"]: 100},
                        "Another operator changed traffic; do not overwrite it.")
                self.command("run", "services", "update-traffic", name, f"--region={REGION}",
                             "--to-revisions=" + traffic_argument(previous), "--remove-tags=" + candidate["tag"])
                restored = self.snapshot("rollback-" + name)
                require(traffic(restored[name]) == previous, "Rollback traffic could not be verified.")
                results[name] = "previous traffic restored and verified"
            except BaseException:
                results[name] = "NOT VERIFIED; inspect receipts and use reviewed recovery"
                print("Rollback not verified for " + name + "; inspect the private receipts.", flush=True)
        self.receipt["rollback"] = results

    def deploy(self, sources, assets, sha, run_id, before_promote):
        try:
            for name in SERVICES:
                self.stage(name, sources[name], assets, sha, run_id)
            self.receipt["both_candidates_verified"] = True
            before_promote()
            for name in SERVICES:
                self.promote(name, assets)
            self.stable(self.snapshot("cloud-after"))
        except BaseException:
            if self.receipt.get("promotion_attempted"):
                self.rollback()
            raise
        self.receipt.update(production_verified=True, public_url=DOMAIN + "/dashboard/",
                            revisions={name: row["revision"] for name, row in self.candidates.items()},
                            runtime_configuration_preserved=True, iam_preserved=True,
                            private_backend_health_verified=True)
