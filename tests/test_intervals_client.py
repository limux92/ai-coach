"""Offline tests: HTTPX MockTransport, no credentials or external requests."""

import gzip
import hashlib
import time
import unittest
from datetime import date

import httpx

from ai_coach.intervals_client import IntervalsClient, IntervalsError, date_windows


class ClientTests(unittest.TestCase):
    def test_basic_auth_uses_fixed_origin_and_read_only_requests(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"id": "123"})

        with IntervalsClient("test-secret", transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(client.athlete()["id"], "123")
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(str(requests[0].url), "https://intervals.icu/api/v1/athlete/0")
        self.assertTrue(requests[0].headers["Authorization"].startswith("Basic "))

    def test_auth_failure_is_not_retried_and_does_not_leak_response(self):
        count = 0

        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(401, text="test-secret private-user-data")

        with IntervalsClient("test-secret", transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(IntervalsError) as ctx:
                client.athlete()
        self.assertFalse(ctx.exception.retryable)
        self.assertNotIn("test-secret", str(ctx.exception))
        self.assertNotIn("private-user-data", str(ctx.exception))
        self.assertEqual(count, 1)

    def test_retry_after_is_honoured_and_server_failure_recovers(self):
        count = 0
        sleeps = []

        def handler(request):
            nonlocal count
            count += 1
            if count == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
            if count == 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"id": "123"})

        with IntervalsClient("test-key", transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
            self.assertEqual(client.athlete()["id"], "123")
        self.assertEqual(count, 3)
        self.assertEqual(sleeps[0], 2)
        self.assertGreaterEqual(sleeps[1], 2)

    def test_long_retry_after_defers_to_durable_job(self):
        sleeps = []
        with IntervalsClient("test-key", transport=httpx.MockTransport(
                lambda _: httpx.Response(429, headers={"Retry-After": "370"})), sleep=sleeps.append) as client:
            with self.assertRaises(IntervalsError) as ctx:
                client.athlete()
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.retry_after_seconds, 370)
        self.assertEqual(sleeps, [])

    def test_original_preserves_compressed_wire_bytes(self):
        content = gzip.compress(b"original-fit-data", mtime=0)

        def handler(request):
            self.assertEqual(request.url.path, "/api/v1/activity/i123/file")
            return httpx.Response(200, stream=httpx.ByteStream(content), headers={
                "Content-Type": "application/octet-stream", "Content-Encoding": "gzip",
                "Content-Disposition": 'attachment; filename="../../activity.fit.gz"',
            })

        with IntervalsClient("test-key", transport=httpx.MockTransport(handler)) as client:
            original = client.download_original("i123")
        self.assertEqual(original.data, content)
        self.assertTrue(original.is_gzip)
        self.assertEqual(original.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(original.filename, "activity.fit.gz")

    def test_range_overlap_is_deduplicated_without_offset(self):
        ranges = []

        def handler(request):
            ranges.append(dict(request.url.params))
            rows = [{"id": "shared", "version": len(ranges)}]
            return httpx.Response(200, json=rows)

        with IntervalsClient("test-key", transport=httpx.MockTransport(handler)) as client:
            activities = client.list_activities("2026-01-01", "2026-02-15")
        self.assertEqual(activities, [{"id": "shared", "version": 2}])
        self.assertEqual(ranges, [
            {"oldest": "2026-01-01", "newest": "2026-01-31"},
            {"oldest": "2026-01-31", "newest": "2026-02-15"},
        ])

    def test_calendar_events_do_not_assume_workout_category(self):
        def handler(request):
            self.assertNotIn("category", request.url.params)
            self.assertNotIn("ext", request.url.params)
            return httpx.Response(200, json=[{"id": 1, "category": "NOTE"}])

        with IntervalsClient("test-key", transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(client.list_events("2026-09-01", "2026-09-30")[0]["category"], "NOTE")

    def test_unknown_shape_and_oversized_response_fail_explicitly(self):
        with IntervalsClient("test-key", transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"unexpected": "object"}))) as client:
            with self.assertRaises(IntervalsError) as ctx:
                client.list_wellness("2026-09-01", "2026-09-01")
            self.assertEqual(ctx.exception.code, "unexpected_response_shape")
        with IntervalsClient("test-key", max_response_bytes=10, transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"x" * 20))) as client:
            with self.assertRaises(IntervalsError) as ctx:
                client.athlete()
            self.assertEqual(ctx.exception.code, "response_too_large")

    def test_redirect_is_not_followed(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://other-origin.invalid/"})

        with IntervalsClient("test-key", transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(IntervalsError):
                client.athlete()
        self.assertEqual(len(calls), 1)

    def test_windows_validate_and_include_single_day(self):
        self.assertEqual(list(date_windows("2026-09-01", "2026-09-01")),
                         [(date(2026, 9, 1), date(2026, 9, 1))])
        with self.assertRaises(ValueError):
            list(date_windows("2026-09-02", "2026-09-01"))

    def test_deadline_exhaustion_does_not_start_request_or_sleep(self):
        requests = []
        sleeps = []

        def handler(request):
            requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "2"})

        with IntervalsClient("test-key", deadline_monotonic=time.monotonic() - 1,
                             transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(IntervalsError) as ctx:
                client.athlete()
        self.assertEqual(ctx.exception.code, "deadline_exceeded")
        self.assertEqual(requests, [])
        with IntervalsClient("test-key", deadline_monotonic=time.monotonic() + 1,
                             transport=httpx.MockTransport(handler), sleep=sleeps.append) as client:
            with self.assertRaises(IntervalsError) as ctx:
                client.athlete()
        self.assertEqual(ctx.exception.code, "deadline_exceeded")
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
