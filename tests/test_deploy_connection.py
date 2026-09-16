"""Offline checks for safe deployment and late Intervals connection activation."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
import deploy
import put_secret


class FakeCloud:
    project = "example-ai-coach"

    def __init__(self, *, versions=None, service=None, job=None):
        self.versions = versions
        self.service = service
        self.job = job
        self.calls = []

    def json(self, *args, **kwargs):
        self.calls.append(("json", args, kwargs))
        if args[:3] == ("secrets", "versions", "list"):
            return self.versions
        if args[:3] == ("run", "services", "describe"):
            return self.service
        if args[:3] == ("scheduler", "jobs", "describe"):
            return self.job
        raise AssertionError(f"Unexpected cloud read: {args}")

    def command(self, *args, **kwargs):
        self.calls.append(("command", args, kwargs))
        if args[:3] == ("scheduler", "jobs", "pause"):
            self.job["state"] = "PAUSED"
        elif args[:3] == ("scheduler", "jobs", "resume"):
            self.job["state"] = "ENABLED"
        return ""

    @property
    def mutations(self):
        return [args for kind, args, _ in self.calls if kind == "command"]


class ConnectionTests(unittest.TestCase):
    def test_private_verification_uses_status_route_and_validates_database_response(self):
        cloud = FakeCloud()
        response = io.BytesIO(b'{"source_connection":"awaiting_api_key","stale":true}')
        response.status = 200
        output = io.StringIO()
        denied = urllib.error.HTTPError("https://example.run.app/v1/status", 403, "Forbidden", {}, None)
        with patch.object(deploy.urllib.request, "urlopen", side_effect=[denied, response]) as opened, \
             patch.object(cloud, "command", return_value="test-private-token"), redirect_stdout(output):
            deploy.verify_private(cloud, "https://example.run.app/")
        self.assertEqual(opened.call_args_list[0].args[0], "https://example.run.app/v1/status")
        authenticated = opened.call_args_list[1].args[0]
        self.assertEqual(authenticated.full_url, "https://example.run.app/v1/status")
        self.assertEqual(authenticated.get_header("Authorization"), "Bearer test-private-token")
        self.assertNotIn("test-private-token", output.getvalue())

    def test_private_verification_rejects_invalid_status_shape(self):
        for payload in (b'{}', b'{"source_connection":"configured","stale":"yes"}',
                        b'{"source_connection":"unknown","stale":true}', b'<html>404</html>'):
            with self.subTest(payload=payload):
                cloud = FakeCloud()
                response = io.BytesIO(payload)
                response.status = 200
                denied = urllib.error.HTTPError("https://example.run.app/v1/status", 403, "Forbidden", {}, None)
                with patch.object(deploy.urllib.request, "urlopen", side_effect=[denied, response]):
                    with self.assertRaisesRegex(RuntimeError, "status check returned"):
                        deploy.verify_private(cloud, "https://example.run.app")

    def test_private_verification_rejects_anonymous_success(self):
        response = io.BytesIO(b'{}')
        response.status = 200
        with patch.object(deploy.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Unauthenticated request unexpectedly"):
                deploy.verify_private(FakeCloud(), "https://example.run.app")

    def test_configure_only_preserves_existing_service_without_source_build(self):
        cloud = FakeCloud(service={"spec": {"existing": "settings"}})
        args = SimpleNamespace(configure_only=True, env_file=None,
                               service="ai-coach-sync", region="europe-north1")
        deploy.prepare_service(cloud, args, None)
        self.assertEqual(cloud.mutations, [])
        self.assertEqual(cloud.service, {"spec": {"existing": "settings"}})
        self.assertEqual(len(cloud.calls), 1)

    def test_configure_only_missing_service_fails_before_mutations(self):
        cloud = FakeCloud(service=None)
        args = SimpleNamespace(configure_only=True, env_file=None,
                               service="ai-coach-sync", region="europe-north1")
        with self.assertRaisesRegex(SystemExit, "does not exist"):
            deploy.prepare_service(cloud, args, None)
        self.assertEqual(cloud.mutations, [])

    def test_configure_only_rejects_environment_replacement(self):
        cloud = FakeCloud(service={})
        args = SimpleNamespace(configure_only=True, env_file=Path("additional-env.json"),
                               service="ai-coach-sync", region="europe-north1")
        with self.assertRaisesRegex(SystemExit, "existing environment is preserved"):
            deploy.prepare_service(cloud, args, None)
        self.assertEqual(cloud.calls, [])

    def test_deployment_without_key_requires_explicit_flag_before_mutations(self):
        cloud = FakeCloud(versions=[])
        with patch.object(deploy, "Cloud", return_value=cloud):
            with self.assertRaisesRegex(SystemExit, "--allow-unconnected"):
                deploy.main(["--project", cloud.project, "--run-now"])
        self.assertEqual(cloud.mutations, [])

    def test_allow_unconnected_returns_absence_without_creating_placeholder(self):
        cloud = FakeCloud(versions=None)
        self.assertIsNone(put_secret.require_secret_version(cloud, True))
        self.assertEqual(cloud.mutations, [])

    def test_latest_enabled_version_is_numeric(self):
        cloud = FakeCloud(versions=[{"name": "projects/p/secrets/s/versions/9"},
                                   {"name": "projects/p/secrets/s/versions/12"}])
        self.assertEqual(put_secret.require_secret_version(cloud, False), 12)

    def test_missing_service_keeps_saved_key_pending_without_scheduler_calls(self):
        cloud = FakeCloud(service=None)
        result = put_secret.activate_connection(cloud, 12, "ai-coach-sync", "europe-north1")
        self.assertTrue(result["key_configured"])
        self.assertTrue(result["service_pending"])
        self.assertFalse(result["connected"])
        self.assertFalse(result["first_sync_requested"])
        self.assertEqual(cloud.mutations, [])
        self.assertEqual(len(cloud.calls), 1)

    def test_existing_service_attaches_version_resumes_job_and_runs_once(self):
        cloud = FakeCloud(service={"spec": {}}, job={"state": "PAUSED"})
        result = put_secret.activate_connection(cloud, 12, "ai-coach-sync", "europe-north1")
        self.assertEqual(cloud.mutations, [
            ("run", "services", "update", "ai-coach-sync", "--region=europe-north1",
             "--update-secrets=INTERVALS_API_KEY=intervals-api-key:12"),
            ("scheduler", "jobs", "resume", put_secret.JOB_NAME, "--location=europe-west1"),
            ("scheduler", "jobs", "run", put_secret.JOB_NAME, "--location=europe-west1"),
        ])
        self.assertTrue(result["connected"])
        self.assertFalse(result["scheduler_paused"])
        self.assertTrue(result["first_sync_requested"])

    def test_connection_uses_independent_cloud_run_and_scheduler_locations(self):
        cloud = FakeCloud(service={}, job={"state": "PAUSED"})
        result = put_secret.activate_connection(cloud, 12, "ai-coach-sync", "europe-north1",
                                                 scheduler_region="europe-west3")
        for _, args, _ in cloud.calls:
            if args[0] == "scheduler":
                self.assertIn("--location=europe-west3", args)
                self.assertNotIn("--region=europe-north1", args)
            elif args[0] == "run":
                self.assertIn("--region=europe-north1", args)
                self.assertNotIn("--location=europe-west3", args)
        self.assertEqual(result["region"], "europe-north1")
        self.assertEqual(result["scheduler_region"], "europe-west3")

    def test_existing_enabled_job_is_not_resumed_again(self):
        cloud = FakeCloud(service={}, job={"state": "ENABLED"})
        put_secret.activate_connection(cloud, 2, "ai-coach-sync", "europe-north1")
        self.assertFalse(any(args[:3] == ("scheduler", "jobs", "resume")
                             for args in cloud.mutations))
        self.assertEqual(sum(args[:3] == ("scheduler", "jobs", "run")
                             for args in cloud.mutations), 1)

    def test_missing_job_leaves_connected_service_waiting_for_deployment(self):
        cloud = FakeCloud(service={}, job=None)
        result = put_secret.activate_connection(cloud, 2, "ai-coach-sync", "europe-north1")
        self.assertTrue(result["connected"])
        self.assertTrue(result["scheduler_pending"])
        self.assertFalse(result["first_sync_requested"])
        self.assertEqual(len(cloud.mutations), 1)

    def test_already_attached_version_does_not_replace_revision(self):
        cloud = FakeCloud(service={"spec": {"template": {"spec": {"containers": [{
            "env": [{"name": "INTERVALS_API_KEY", "valueFrom": {"secretKeyRef": {
                "name": "intervals-api-key", "key": "7"}}}]
        }]}}}})
        self.assertTrue(put_secret.attach_secret(cloud, 7, "ai-coach-sync", "europe-north1"))
        self.assertEqual(cloud.mutations, [])

    def test_unconfigured_job_is_paused_even_when_run_now_requested(self):
        cloud = FakeCloud(job={"state": "ENABLED"})
        result = put_secret.set_scheduler_state(cloud, "europe-west1",
                                                connected=False, run_now=True)
        self.assertTrue(result["scheduler_paused"])
        self.assertFalse(result["first_sync_requested"])
        self.assertEqual(cloud.mutations, [
            ("scheduler", "jobs", "pause", put_secret.JOB_NAME, "--location=europe-west1")])

    def test_connection_cli_passes_key_via_stdin_and_prints_only_safe_status(self):
        key = "test-credential-only"
        cloud = FakeCloud(service=None)
        uploaded = []

        def save(*args, **kwargs):
            uploaded.append((args, kwargs))
            return '{"name":"projects/p/secrets/intervals-api-key/versions/3"}'

        cloud.command = save
        output = io.StringIO()
        with patch.object(put_secret, "Cloud", return_value=cloud), \
             patch.object(put_secret.getpass, "getpass", return_value=key), \
             patch.object(put_secret, "validate_key") as validate, redirect_stdout(output):
            put_secret.main(["--project", cloud.project])
        validate.assert_called_once_with(key)
        self.assertEqual(uploaded[0][1]["input_text"], key)
        self.assertNotIn(key, str(uploaded[0][0]))
        self.assertNotIn(key, output.getvalue())
        self.assertIn('"service_pending": true', output.getvalue())


if __name__ == "__main__":
    unittest.main()
