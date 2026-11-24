"""Quota, lightweight late-upload discovery, and scheduler command capture."""
from datetime import UTC, datetime, timedelta
import sys
from pathlib import Path

import httpx
import pytest

from ai_coach.intervals_client import IntervalsClient, IntervalsError
from ai_coach.upstream_budget import RequestBudget, reserve_state
from ai_coach.sync import _Importer, RUN_BUDGET_SECONDS
from ai_coach.config import Settings
from test_sync import MemoryStore, FakeClient, activity, TODAY

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from deploy import configure_scheduler_job


def test_request_budget_counts_retries_and_is_shared(monkeypatch):
    store = MemoryStore()
    now = datetime(2026, 9, 1, tzinfo=UTC)
    monkeypatch.setattr("ai_coach.upstream_budget.utcnow", lambda: now)
    budget = RequestBudget(store, "shared_oauth_pool", limit=5)
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(503)
    with IntervalsClient("synthetic", reserve_request=budget.reserve, transport=httpx.MockTransport(upstream), sleep=lambda _: None) as client:
        with pytest.raises(IntervalsError):
            client.athlete()
        with pytest.raises(IntervalsError, match="request_budget_exhausted"):
            client.athlete()
    assert len(calls) == 5 and budget.used == 5
    second = RequestBudget(store, "shared_oauth_pool", limit=5)
    second.reserve()
    assert store.get("upstream_budgets", "shared_oauth_pool")["daily_count"] == 6


def test_retry_after_and_upstream_exhaustion_survive_new_client(monkeypatch):
    store = MemoryStore()
    now = datetime(2026, 9, 1, 23, 59, tzinfo=UTC)
    monkeypatch.setattr("ai_coach.upstream_budget.utcnow", lambda: now)
    first = RequestBudget(store, "pool")
    first.observe(httpx.Headers(), 429, 370)
    with pytest.raises(IntervalsError) as raised:
        RequestBudget(store, "pool").reserve()
    assert raised.value.retry_after_seconds == 370
    first.observe(httpx.Headers({"X-RateLimit-Remaining": "200,0"}), 200, None)
    assert store.get("upstream_budgets", "pool")["cooldown_until"] == now + timedelta(seconds=370)


def test_daily_and_rolling_pool_windows():
    now = datetime(2026, 9, 1, 10, tzinfo=UTC)
    state = reserve_state({}, now, daily_limit=1)
    with pytest.raises(IntervalsError):
        reserve_state(state, now, daily_limit=1)
    assert reserve_state(state, now + timedelta(days=1), daily_limit=1)["daily_count"] == 1
    with pytest.raises(IntervalsError):
        reserve_state(state, now, rolling_limit=1)
    assert len(reserve_state(state, now + timedelta(seconds=900), rolling_limit=1)["request_times"]) == 1


def test_index_requests_exact_documented_fields_and_fails_closed_at_cap():
    def upstream(request):
        assert dict(request.url.params) == {"oldest": "2000-01-01", "newest": "2026-09-27", "fields": "id,start_date_local", "limit": "20001"}
        return httpx.Response(200, json=[{"id": "old-upload", "start_date_local": "2020-01-01"}])
    with IntervalsClient("synthetic", transport=httpx.MockTransport(upstream)) as client:
        assert client.activity_index("2000-01-01", "2026-09-27")[0]["id"] == "old-upload"
    with IntervalsClient("synthetic", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{"id": "x"}] * 20001))) as client:
        with pytest.raises(IntervalsError, match="discovery_limit_exceeded"):
            client.activity_index("2000-01-01", "2026-09-27")


def test_new_old_dated_activity_is_discovered_next_poll_without_full_download(monkeypatch):
    store = MemoryStore()
    class Indexed(FakeClient):
        def activity_index(self, oldest, newest):
            return [{"id": r["id"], "start_date_local": r["start_date_local"]} for r in self.activities]
    client = Indexed([activity("late", days_ago=1000)])
    importer = _Importer(store, Settings("test", "test"), client, float("inf"), {})
    seen = []
    def import_activity(row):
        seen.append(row["id"])
        return True
    importer.activity = import_activity
    assert importer.discover(TODAY)
    assert seen == ["late"]
    assert importer.discover(TODAY)
    assert seen == ["late"] and client.details == 1 and client.downloads == 0


@pytest.mark.parametrize("existing", [None, {"state": "ENABLED", "retryConfig": {"maxRetryDuration": "3600s"}}])
def test_scheduler_create_and_update_disable_retry_skip_and_retain_oidc(existing):
    class Cloud:
        calls = []
        def json(self, *args, **kwargs):
            return existing
        def command(self, *args, **kwargs):
            self.calls.append(args)
    cloud = Cloud()
    configure_scheduler_job(cloud, job_name="test-job", location="test-region", url="https://private.example", service_account="scheduler@test.example")
    command = cloud.calls[-1]
    assert command[2] == ("update" if existing else "create")
    for flag in ("--schedule=*/5 * * * *", "--attempt-deadline=300s", "--max-retry-attempts=0", "--max-retry-duration=0s",
                 "--oidc-service-account-email=scheduler@test.example", "--oidc-token-audience=https://private.example"):
        assert flag in command
    assert RUN_BUDGET_SECONDS < 300
