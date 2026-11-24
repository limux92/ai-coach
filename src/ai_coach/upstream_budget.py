"""Shared durable request allowance; all consumers of a credential pool must use it."""
from datetime import timedelta

from .intervals_client import IntervalsError
from .physiology_samples import timestamp
from .storage import utcnow


def reserve_state(state, now, *, daily_limit=4500, rolling_limit=2300):
    day = now.date().isoformat()
    cooldown = timestamp(state.get("cooldown_until"))
    if cooldown and cooldown > now:
        raise IntervalsError("rate_limited", retryable=True, retry_after_seconds=(cooldown - now).total_seconds())
    count = state.get("daily_count", 0) if state.get("utc_day") == day else 0
    recent = [t for t in state.get("request_times", []) if t > now.timestamp() - 900]
    if count >= daily_limit:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        raise IntervalsError("rate_limited", retryable=True, retry_after_seconds=(tomorrow - now).total_seconds())
    if len(recent) >= rolling_limit:
        raise IntervalsError("rate_limited", retryable=True, retry_after_seconds=max(1, recent[0] + 900 - now.timestamp()))
    return {**state, "utc_day": day, "daily_count": count + 1, "request_times": recent + [now.timestamp()]}


class RequestBudget:
    def __init__(self, store, pool, *, limit=80):
        from .storage import safe_id
        self.store, self.pool, self.limit, self.used = store, safe_id(pool), limit, 0
        if not 5 <= limit <= 500:
            raise ValueError("Invalid request budget")

    def reserve(self):
        if self.used >= self.limit:
            raise IntervalsError("request_budget_exhausted", retryable=True)
        if hasattr(self.store, "reserve_upstream_request"):
            self.store.reserve_upstream_request(self.pool)
        else:
            state = self.store.get("upstream_budgets", self.pool) or {}
            self.store.put("upstream_budgets", self.pool, reserve_state(state, utcnow()), merge=False)
        self.used += 1

    def observe(self, headers, status, retry_after):
        now, seconds = utcnow(), retry_after if status == 429 else None
        value = headers.get("X-RateLimit-Remaining", "")
        try:
            remaining = [int(part.strip()) for part in value.split(",")]
        except ValueError:
            remaining = []
        if len(remaining) == 2:
            if remaining[0] <= 0:
                seconds = max(seconds or 0, 900)
            if remaining[1] <= 0:
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seconds = max(seconds or 0, (tomorrow - now).total_seconds())
        if status == 429 and seconds is None:
            seconds = 300
        if seconds is not None:
            until = now + timedelta(seconds=seconds)
            if hasattr(self.store, "defer_upstream_requests"):
                self.store.defer_upstream_requests(self.pool, until)
            else:
                state = self.store.get("upstream_budgets", self.pool) or {}
                previous = timestamp(state.get("cooldown_until"))
                self.store.put("upstream_budgets", self.pool, {"cooldown_until": max(until, previous) if previous else until})
