#!/usr/bin/env python3
"""Bounded, read-only live MCP verification. Reports no tokens or raw payloads."""
from datetime import date, datetime, timedelta, timezone
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import httpx

PROJECT = Path(__file__).resolve().parents[1]
TOKEN_FILE = PROJECT / ".local/firebase-chat-login.json"
REPORT_FILE = PROJECT / ".local/verification/chat-live-verification.json"
URL = "https://ai-coach-chat-600465847441.europe-north1.run.app/mcp"
TOOLS = {
    "get_coach_context", "get_training_summary", "list_completed_workouts",
    "get_workout_details", "get_workout_samples", "list_planned_workouts", "list_wellness",
}


class VerificationError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def number_matches(value, expected):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and math.isclose(value, expected, rel_tol=0, abs_tol=0.01))


def safe_save(report):
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=REPORT_FILE.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, REPORT_FILE)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class MCP:
    def __init__(self, client, token):
        self.client = client
        self.token = token
        self.protocol = "2025-11-25"
        self.session = None
        self.request_id = 0

    def post(self, method, params=None, *, token=None, notification=False):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": self.protocol}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if self.session is not None:
            headers["Mcp-Session-Id"] = self.session
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notification:
            self.request_id += 1
            body["id"] = self.request_id
        return self.client.post(URL, headers=headers, json=body)

    def rpc(self, method, params=None):
        response = self.post(method, params, token=self.token)
        require(response.status_code == 200, "authenticated_rpc_http_status")
        # The deployed adapter explicitly uses stateless JSON responses.
        require("application/json" in response.headers.get("content-type", ""), "rpc_content_type")
        payload = response.json()
        require(isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"
                and payload.get("id") == self.request_id and "error" not in payload, "rpc_envelope")
        result = payload.get("result")
        require(isinstance(result, dict), "rpc_result_shape")
        if method == "initialize":
            protocol = result.get("protocolVersion")
            require(isinstance(protocol, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", protocol), "protocol_negotiation")
            self.protocol = protocol
            self.session = response.headers.get("mcp-session-id")
        return result

    def call(self, name, arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        require(result.get("isError") is not True, "tool_reported_error")
        content = result.get("content")
        require(isinstance(content, list) and len(content) == 1
                and isinstance(content[0], dict) and content[0].get("type") == "text", "tool_content_shape")
        payload = json.loads(content[0]["text"])
        require(isinstance(payload, dict), "tool_payload_shape")
        return payload


def items(payload):
    result = payload.get("items")
    require(isinstance(result, list) and all(isinstance(row, dict) for row in result), "listing_shape")
    return result


def load_expected(path):
    """Read an explicit private baseline; public source contains no athlete data."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(65_537)
        require(len(raw) <= 65_536, "expected_reference_too_large")
        expected = json.loads(raw)
    except (OSError, ValueError, TypeError):
        raise VerificationError("expected_reference_unreadable") from None
    require(isinstance(expected, dict) and set(expected) == {
        "workout_id", "workout_name", "local_date", "metrics", "record_count", "week_totals",
    }, "expected_reference_shape")
    require(isinstance(expected["workout_id"], str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", expected["workout_id"])
            and expected["workout_id"] not in {".", ".."}
            and not (expected["workout_id"].startswith("__") and expected["workout_id"].endswith("__")),
            "expected_workout_id")
    require(isinstance(expected["workout_name"], str) and 1 <= len(expected["workout_name"]) <= 300,
            "expected_workout_name")
    try:
        day = date.fromisoformat(expected["local_date"])
    except (TypeError, ValueError):
        raise VerificationError("expected_local_date") from None
    require(day.isoformat() == expected["local_date"], "expected_local_date")
    require(type(expected["record_count"]) is int and expected["record_count"] > 5,
            "expected_record_count")
    for section, fields in (("metrics", {"distance_m", "moving_time_s", "average_heart_rate_bpm"}),
                            ("week_totals", {"distance_m", "moving_time_s"})):
        values = expected[section]
        require(isinstance(values, dict) and set(values) == fields, "expected_metrics_shape")
        for value in values.values():
            require(type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value),
                    "expected_metric_value")
    return expected


def check_workout(workout, expected):
    require(workout.get("id") == expected["workout_id"]
            and workout.get("name") == expected["workout_name"], "known_workout_identity")
    require(workout.get("local_date") == expected["local_date"], "known_workout_date")
    metrics = workout.get("metrics")
    require(isinstance(metrics, dict), "workout_metrics_shape")
    for key, value in expected["metrics"].items():
        require(number_matches(metrics.get(key), value), "known_workout_metric_mismatch")
    require(workout.get("record_count") == expected["record_count"], "known_workout_record_count")


def verify(report, expected):
    workout_id = expected["workout_id"]
    day = date.fromisoformat(expected["local_date"])
    week_start = day - timedelta(days=day.weekday())
    calendar_start, calendar_end = day + timedelta(days=1), day + timedelta(days=7)
    credentials = json.loads(TOKEN_FILE.read_text())
    token = credentials.get("tokens", {}).get("access_token") if isinstance(credentials, dict) else None
    require(isinstance(token, str) and token.count(".") == 2, "owner_access_token_missing")
    with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False, trust_env=False) as client:
        mcp = MCP(client, token)
        for label, candidate in (("missing_oauth", None), ("invalid_oauth", "invalid-token")):
            response = mcp.post("tools/list", {}, token=candidate)
            require(response.status_code == 401, label + "_not_rejected")
            report[label + "_status"] = 401
        init = mcp.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                       "clientInfo": {"name": "ai-coach-live-verifier", "version": "1"}})
        response = mcp.post("notifications/initialized", token=token, notification=True)
        require(response.status_code in (200, 202, 204), "initialized_notification_status")
        report["protocol_version"] = init["protocolVersion"]
        listing = mcp.rpc("tools/list", {})
        tools = listing.get("tools")
        require(isinstance(tools, list) and len(tools) == 7 and all(isinstance(tool, dict) for tool in tools), "tool_count")
        require({tool.get("name") for tool in tools} == TOOLS and not listing.get("nextCursor"), "tool_names")
        for tool in tools:
            annotation = tool.get("annotations", {})
            require(annotation.get("readOnlyHint") is True and annotation.get("destructiveHint") is False
                    and annotation.get("idempotentHint") is True and annotation.get("openWorldHint") is False,
                    "readonly_annotations")
            require(tool.get("_meta", {}).get("securitySchemes") == [{"type": "oauth2", "scopes": ["coach:read"]}],
                    "tool_oauth_metadata")
        report["readonly_tool_count"] = 7
        called = []
        def call(name, args):
            result = mcp.call(name, args)
            called.append(name)
            report["tools_called"] = list(called)
            return result
        context = call("get_coach_context", {"days": 42, "upcoming": 14})
        require(context.get("response_version") == 2 and isinstance(context.get("training_summaries"), dict)
                and isinstance(context.get("summary_freshness"), dict), "context_shape")
        report["context_workout_count"] = len(context.get("workouts", []))
        report["context_summary_count"] = sum(isinstance(v, dict) for v in context["training_summaries"].values())
        report["summary_stale"] = context["summary_freshness"].get("stale") is not False
        week = call("get_training_summary", {"period": "week", "date": day.isoformat()})
        summary = week.get("summary", {})
        require(summary.get("period") == "week" and summary.get("start_date") == week_start.isoformat()
                and summary.get("end_date") == (week_start + timedelta(days=6)).isoformat(), "week_period")
        totals = summary.get("totals", {})
        require(all(number_matches(totals.get(key), value) for key, value in expected["week_totals"].items()),
                "week_totals")
        completed = call("list_completed_workouts", {"oldest": day.isoformat(), "newest": day.isoformat(), "limit": 50})
        completed_rows = items(completed)
        matches = [row for row in completed_rows if row.get("id") == workout_id]
        require(len(matches) == 1, "known_workout_not_listed")
        check_workout(matches[0], expected)
        detail = call("get_workout_details", {"workout_id": workout_id})
        check_workout(detail, expected)
        samples = call("get_workout_samples", {"workout_id": workout_id, "offset": 0, "limit": 5,
                                               "fields": "timestamp,heart_rate"})
        sample_rows = items(samples)
        require(len(sample_rows) == 5 and samples.get("total") == expected["record_count"]
                and samples.get("next_offset") == 5, "sample_counts")
        require(all(set(row) == {"timestamp", "heart_rate"} for row in sample_rows), "sample_field_selection")
        calendar_window = {"oldest": calendar_start.isoformat(), "newest": calendar_end.isoformat(), "limit": 50}
        plans = call("list_planned_workouts", calendar_window)
        wellness = call("list_wellness", calendar_window)
        report.update(completed_returned=len(completed_rows), plans_returned=len(items(plans)),
                      wellness_returned=len(items(wellness)), sample_page_size=5,
                      total_samples=expected["record_count"], expected_workout_verified=True,
                      week_totals_verified=True)
        require(set(called) == TOOLS and len(called) == 7, "all_tools_not_called")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-json", type=Path, required=True,
                        help="Private JSON baseline with workout identity, metrics, record count and week totals")
    args = parser.parse_args()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "chatgpt_connected": False, "chatgpt_ui_verified": False,
              "different_user_denial_live_verified": False,
              "different_user_denial_coverage": "adapter_unit_tests_only", "tools_called": []}
    try:
        verify(report, load_expected(args.expected_json))
        report["status"] = "passed"
    except VerificationError as error:
        report["failure_code"] = str(error)
    except Exception:
        # Never include exception messages, response text, headers, or token material.
        report["failure_code"] = "request_or_payload_processing_failed"
    try:
        safe_save(report)
    except Exception:
        print(json.dumps({"status": "failed", "failure_code": "verification_report_write_failed"}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
