"""Read-only Intervals.icu personal API client.

No outbound workout writes are implemented. Credentials are only used as HTTP
Basic authentication to the fixed Intervals.icu HTTPS origin. Exceptions contain
neither upstream response bodies nor URLs/headers/credentials.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator

import httpx


class IntervalsError(Exception):
    """Safe error for logging. Retryable failures remain retryable when exhausted."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Intervals.icu request failed: {code}" +
                         (f" (HTTP {status_code})" if status_code is not None else ""))


@dataclass(frozen=True)
class OriginalFile:
    """Exact response entity bytes, before HTTP content decoding.

    This is the original file received by Intervals, not a promise of equivalence
    with a direct Garmin export. Inspect gzip magic as well as content_encoding
    when parsing. Never use the upstream filename as a local/object-store path.
    """

    data: bytes
    content_type: str
    filename: str | None
    content_encoding: str | None
    sha256: str

    @property
    def is_gzip(self) -> bool:
        return self.data.startswith(b"\x1f\x8b")


def _date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ValueError("Use a local ISO date, not a datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected an ISO date in YYYY-MM-DD format")
    return date.fromisoformat(value)


def date_windows(
    oldest: date | str, newest: date | str, *, window_days: int = 31
) -> Iterator[tuple[date, date]]:
    """Inclusive local-day windows with a boundary-day overlap.

    Intervals documents date-range paging rather than offset pagination. The
    overlap plus ID de-duplication makes adjacent query boundaries harmless.
    """
    start, end = _date(oldest), _date(newest)
    if start > end:
        raise ValueError("oldest must be on or before newest")
    if not 2 <= window_days <= 93:
        raise ValueError("window_days must be between 2 and 93")
    while True:
        stop = min(end, start + timedelta(days=window_days - 1))
        yield start, stop
        if stop == end:
            return
        start = stop


def _identifier(value: str | int) -> str:
    text = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", text):
        raise ValueError("Invalid Intervals identifier")
    return text


def _reject_json_constant(_: str) -> None:
    raise ValueError("Non-finite JSON number")


class IntervalsClient:
    BASE_URL = "https://intervals.icu/api/v1/"

    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        *,
        timeout_seconds: float = 30,
        max_attempts: int = 3,
        max_retry_delay_seconds: float = 30,
        max_response_bytes: int = 100 * 1024 * 1024,
        deadline_monotonic: float | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        reserve_request: Callable[[], None] | None = None,
        observe_response: Callable[..., None] | None = None,
        min_request_interval_seconds: float = 0,
    ) -> None:
        if not api_key or api_key != api_key.strip() or "\n" in api_key:
            raise ValueError("A valid Intervals API key is required")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if timeout_seconds <= 0 or max_retry_delay_seconds < 0 or max_response_bytes <= 0:
            raise ValueError("Invalid request limits")
        self.athlete_id = _identifier(athlete_id)
        self.max_attempts = max_attempts
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self.max_response_bytes = max_response_bytes
        self.deadline_monotonic = deadline_monotonic
        self.timeout_seconds = timeout_seconds
        self._sleep = sleep
        self._reserve_request, self._observe_response = reserve_request, observe_response
        self._min_interval, self._last_request = min_request_interval_seconds, None
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            auth=httpx.BasicAuth("API_KEY", api_key),
            timeout=httpx.Timeout(timeout_seconds, connect=min(10, timeout_seconds)),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "PrivateTrainingArchive/1.0", "Accept": "application/json"},
        )

    def __enter__(self) -> IntervalsClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _remaining(self) -> float | None:
        if self.deadline_monotonic is None:
            return None
        remaining = self.deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise IntervalsError("deadline_exceeded", retryable=True)
        return remaining

    @staticmethod
    def _retry_after(headers: httpx.Headers) -> float | None:
        value = headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                timestamp = parsedate_to_datetime(value)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                return max(0.0, (timestamp - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return None

    def _request(
        self, path: str, *, params: dict[str, Any] | None = None, raw: bool = False
    ) -> tuple[bytes, httpx.Headers]:
        # All callers below construct relative paths from validated identifiers.
        if path.startswith("/") or ":" in path or ".." in path:
            raise ValueError("Invalid relative API path")
        for attempt in range(self.max_attempts):
            if self._last_request is not None and self._min_interval:
                delay = max(0, self._min_interval - (time.monotonic() - self._last_request))
                if delay:
                    self._sleep(delay)
            if self._reserve_request:
                self._reserve_request()
            remaining = self._remaining()
            self._last_request = time.monotonic()
            request_timeout = self.timeout_seconds if remaining is None else min(self.timeout_seconds, remaining)
            try:
                request_headers = {"Accept": "*/*", "Accept-Encoding": "identity"} if raw else None
                with self._client.stream(
                    "GET", path, params=params, headers=request_headers,
                    timeout=httpx.Timeout(request_timeout, connect=min(10, request_timeout)),
                ) as response:
                    status = response.status_code
                    if self._observe_response:
                        self._observe_response(response.headers, status, self._retry_after(response.headers))
                    if status >= 300:
                        retryable = status == 429 or status == 408 or status >= 500
                        code = ("authentication" if status in (401, 403) else
                                "not_found" if status == 404 else
                                "rate_limited" if status == 429 else
                                "upstream_unavailable" if retryable else "upstream_rejected")
                        raise IntervalsError(code, retryable=retryable, status_code=status,
                                             retry_after_seconds=self._retry_after(response.headers))
                    chunks = []
                    size = 0
                    for chunk in (response.iter_raw() if raw else response.iter_bytes()):
                        self._remaining()
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise IntervalsError("response_too_large")
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers
            except httpx.HTTPError:
                error = IntervalsError("transport_failure", retryable=True)
            except IntervalsError as exc:
                error = exc

            if not error.retryable or attempt == self.max_attempts - 1:
                raise error from None
            delay = error.retry_after_seconds
            if delay is not None and delay > self.max_retry_delay_seconds:
                # Let the durable scheduler retry later; never retry earlier than
                # the upstream Retry-After just to fit the local sleep budget.
                raise error from None
            if delay is None:
                delay = min(self.max_retry_delay_seconds, 2**attempt + random.uniform(0, 0.25))
            remaining = self._remaining()
            if remaining is not None and delay >= remaining:
                raise IntervalsError("deadline_exceeded", retryable=True) from None
            self._sleep(delay)
        raise AssertionError("Unreachable retry state")

    def _json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        data, _ = self._request(path, params=params)
        try:
            return json.loads(data, parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            raise IntervalsError("invalid_json", retryable=True) from None

    def _object(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._json(path, params=params)
        if not isinstance(result, dict):
            raise IntervalsError("unexpected_response_shape")
        return result

    def athlete(self) -> dict[str, Any]:
        """Return the authorised athlete profile; use its actual id for provenance."""
        return self._object(f"athlete/{self.athlete_id}")

    def _list_range(
        self, resource: str, oldest: date | str, newest: date | str,
        *, extra: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for start, end in date_windows(oldest, newest):
            params = {"oldest": start.isoformat(), "newest": end.isoformat(), **(extra or {})}
            result = self._json(f"athlete/{self.athlete_id}/{resource}", params=params)
            if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
                raise IntervalsError("unexpected_response_shape")
            for row in result:
                identifier = row.get("id")
                if identifier is None or isinstance(identifier, (dict, list, bool)):
                    raise IntervalsError("missing_record_id")
                # Later overlapping results win in case the source changed while reading.
                records[str(identifier)] = row
        return list(records.values())

    def list_activities(self, oldest: date | str, newest: date | str) -> list[dict[str, Any]]:
        """All summaries in the range. Call is_direct_garmin BEFORE archiving."""
        return self._list_range("activities", oldest, newest)

    def activity_index(self, oldest: date | str, newest: date | str):
        """One bounded lightweight full-history listing, including late uploads.

        Official OpenAPI supports fields and limit. Request cap+1 and refuse to
        declare discovery complete at the cap; never use a truncated index for deletion.
        """
        result = self._json(f"athlete/{self.athlete_id}/activities", params={
            "oldest": _date(oldest).isoformat(), "newest": _date(newest).isoformat(),
            "fields": "id,start_date_local", "limit": 20001})
        if not isinstance(result, list) or any(not isinstance(r, dict) or not r.get("id") for r in result):
            raise IntervalsError("unexpected_response_shape")
        if len(result) >= 20001:
            raise IntervalsError("discovery_limit_exceeded", retryable=True)
        return result

    def get_activity(self, activity_id: str) -> dict[str, Any]:
        return self._object(f"activity/{_identifier(activity_id)}", params={"intervals": "true"})

    def download_original(self, activity_id: str) -> OriginalFile:
        data, headers = self._request(f"activity/{_identifier(activity_id)}/file", raw=True)
        if not data:
            raise IntervalsError("empty_original_file", retryable=True)
        filename = None
        disposition = headers.get("Content-Disposition", "")
        match = re.search(r'filename="?([^";]+)', disposition, re.IGNORECASE)
        if match:
            # Metadata only. Never let an upstream filename choose a storage path.
            candidate = re.split(r"[/\\]", match.group(1))[-1]
            filename = re.sub(r"[^A-Za-z0-9._ -]", "_", candidate)[:200] or None
        return OriginalFile(
            data=data,
            content_type=headers.get("Content-Type", "application/octet-stream"),
            filename=filename,
            content_encoding=headers.get("Content-Encoding"),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def list_wellness(self, oldest: date | str, newest: date | str) -> list[dict[str, Any]]:
        return self._list_range("wellness", oldest, newest)

    def list_events(self, oldest: date | str, newest: date | str) -> list[dict[str, Any]]:
        """Calendar events of ALL categories, with native workout_doc.

        No ext parameter: do not inflate responses with generated base64 files.
        No resolve parameter: retain original relative targets in workout_doc.
        """
        return self._list_range("events", oldest, newest)

    def get_event(self, event_id: str | int) -> dict[str, Any]:
        return self._object(f"athlete/{self.athlete_id}/events/{_identifier(event_id)}")
