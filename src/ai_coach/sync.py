"""Bounded, replay-safe Intervals imports. Originals are durable before decoding."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .fit_parser import (ActivityParseError, ActivityFileTooLarge, PARSER_VERSION,
                         parse_activity_file, inspect_activity_file)
from .summary_service import save_workout, refresh_summaries, list_all
from .intervals_client import IntervalsClient, IntervalsError
from .normalize import (
    SCHEMA_VERSION, is_direct_garmin, fit_upload_manufacturer, is_verified_activity_fit,
    normalize_activity, normalize_planned_workout,
    normalize_wellness, source_document_id, source_hash,
)

logger = logging.getLogger("ai_coach")
RUN_BUDGET_SECONDS = 720
MAX_PARSE_ATTEMPTS = 3
RECENT_DAYS = 14
MAX_INLINE_LAPS = 200
UPLOAD_VERIFICATION_VERSION = 1
OVERSIZE_INSPECTION_VERSION = 1


class SyncBudgetExceeded(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def _aware_timestamp(value):
    """Accept Firestore datetimes and persisted ISO timestamps without guessing a zone."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Sync watermark must be an offset-aware timestamp")
    return value


def _error_code(exc):
    # Exception messages can contain upstream data, credentials or storage paths.
    return getattr(exc, "code", None) if isinstance(exc, (IntervalsError, ActivityParseError)) \
        else type(exc).__name__


def _lap_summary(laps):
    fields = ("timestamp", "start_time", "total_elapsed_time", "total_timer_time",
              "total_distance", "avg_speed", "max_speed", "enhanced_avg_speed",
              "enhanced_max_speed", "avg_heart_rate", "max_heart_rate", "avg_cadence",
              "max_cadence", "avg_power", "max_power", "total_ascent", "total_descent",
              "lap_trigger", "sport", "intensity")
    result = []
    for lap in laps[:MAX_INLINE_LAPS]:
        item = {key: lap[key] for key in fields if key in lap and
                (lap[key] is None or isinstance(lap[key], (str, int, float, bool)))}
        result.append(item)
    # Prevent long upstream strings from turning the summary into a large document.
    while len(json.dumps(result).encode()) > 64_000:
        result.pop()
    return result


class _Importer:
    def __init__(self, store, settings, client, deadline, state):
        self.store, self.settings, self.client = store, settings, client
        self.deadline, self.state = deadline, state
        self.athlete_id = str(settings.athlete_id)
        self.errors = []
        self.warnings = []
        self.counts = {key: 0 for key in (
            "workouts_imported", "workouts_unchanged", "activities_excluded",
            "original_files_archived", "files_parsed", "parse_errors",
            "parse_terminal", "wellness_imported", "wellness_unchanged",
            "planned_workouts_imported", "planned_workouts_unchanged",
            "calendar_events_excluded", "plans_linked", "errors",
            "uploads_verified", "uploads_rejected", "workouts_retired", "summaries_updated",
            "summary_only_imported",
        )}
        self.activity_docs = {}
        self.missing_checks = 0

    def check_budget(self):
        if time.monotonic() >= self.deadline:
            raise SyncBudgetExceeded()

    def error(self, stage, exc):
        self.counts["errors"] += 1
        if len(self.errors) < 30:
            self.errors.append({"stage": stage, "code": _error_code(exc),
                                "retryable": getattr(exc, "retryable", False)})
        if isinstance(exc, IntervalsError) and exc.status_code in (401, 403):
            raise exc
        if isinstance(exc, IntervalsError) and exc.code in ("rate_limited", "deadline_exceeded"):
            raise exc
        if isinstance(exc, SyncBudgetExceeded):
            raise exc

    def identify(self):
        self.check_budget()
        profile = self.client.athlete()
        athlete_id = profile.get("id")
        if athlete_id is None:
            raise IntervalsError("missing_athlete_id")
        document_id = source_document_id(athlete_id)
        self.athlete_id = str(athlete_id)
        previous_id = self.state.get("source_athlete_id")
        if previous_id is not None and previous_id != self.athlete_id:
            raise IntervalsError("athlete_changed")
        self.store.put("athletes", document_id, {
            "id": document_id, "source": "intervals.icu", "source_id": self.athlete_id,
            "timezone": self.settings.timezone, "last_verified_at": now(),
        })

    def activity(self, summary):
        self.check_budget()
        if summary.get("deleted") is True:
            self.retire_activity(summary["id"], deleted=True)
            return True
        if fit_upload_manufacturer(summary):
            return self.uploaded_activity(summary)
        # Do this before fetching detail, archiving JSON, or downloading a file.
        if not is_direct_garmin(summary):
            self.retire_activity(summary["id"], deleted=False)
            self.counts["activities_excluded"] += 1
            return True
        doc_id = source_document_id(summary["id"])
        existing = self.store.get("workouts", doc_id) or {}
        summary_hash = source_hash(summary)
        unchanged = existing.get("source_summary_sha256") == summary_hash \
            and existing.get("schema_version") == SCHEMA_VERSION \
            and not existing.get("source_deleted") and not existing.get("source_excluded")
        same_parser = existing.get("parser_version") == PARSER_VERSION
        terminal = existing.get("parse_status") == "error" and \
            existing.get("parse_attempts", 0) >= MAX_PARSE_ATTEMPTS and same_parser
        if unchanged and existing.get("original_artifact") and (
            (existing.get("parse_status") == "parsed" and same_parser and
             existing.get("parsed_artifact")) or terminal
        ):
            self.counts["workouts_unchanged"] += 1
            self.counts["parse_terminal"] += int(terminal)
            self.activity_docs[doc_id] = existing
            return True

        detail = self.client.get_activity(str(summary["id"]))
        payload = {**summary, **detail}
        if not is_direct_garmin(payload) or payload.get("deleted") is True:
            self.retire_activity(summary["id"], deleted=payload.get("deleted") is True)
            self.counts["activities_excluded"] += 1
            return True
        self.check_budget()
        doc = normalize_activity(payload, athlete_id=self.athlete_id)
        doc.update({"source_summary_sha256": summary_hash,
                    "raw_payload_artifact": self.store.archive_json(
                        f"raw/activities/{doc_id}", {"summary": summary, "detail": detail}),
                    "created_at": existing.get("created_at") or now(),
                    "updated_at": now(), "parse_status": "pending",
                    "file_status": "pending", "parser_version": PARSER_VERSION})
        for field in ("original_artifact", "parsed_artifact", "laps_summary",
                      "lap_count", "record_count", "parse_attempts", "parse_error",
                      "original_filename", "original_content_encoding"):
            if field in existing:
                doc[field] = existing[field]
        # A failed download must not erase the previously archived evidence.
        save_workout(self.store, doc_id, doc, merge=False)
        self.check_budget()
        try:
            original = self.client.download_original(str(summary["id"]))
            self.check_budget()
            original_artifact = self.store.archive(
                f"originals/{doc_id}", original.data, content_type=original.content_type)
        except Exception as exc:
            save_workout(self.store, doc_id, {
                "file_status": "error", "file_error": _error_code(exc), "updated_at": now(),
            })
            raise
        self.counts["original_files_archived"] += 1
        original_unchanged = (existing.get("original_artifact") or {}).get("sha256") == \
            original_artifact["sha256"]
        doc.update({"original_artifact": original_artifact, "file_status": "archived",
                    "file_error": None, "original_filename": original.filename,
                    "original_content_encoding": original.content_encoding})
        if original_unchanged and same_parser and existing.get("parse_status") == "parsed":
            doc.update({"parse_status": "parsed", "parse_error": None})
            save_workout(self.store, doc_id, doc, merge=False)
            self.counts["workouts_imported"] += 1
            self.activity_docs[doc_id] = doc
            return True
        if not original_unchanged or not same_parser:
            doc.update({"parse_attempts": 0, "parsed_artifact": None,
                        "laps_summary": [], "lap_count": None, "record_count": None})
        # Persist this reference before invoking the parser. Parsing cannot lose originals.
        save_workout(self.store, doc_id, doc, merge=False)
        self.check_budget()
        if original_unchanged and terminal:
            doc["parse_status"] = "error"
            save_workout(self.store, doc_id, doc, merge=False)
            self.counts["parse_terminal"] += 1
            self.activity_docs[doc_id] = doc
            return True
        try:
            parsed = parse_activity_file(original.data, filename=original.filename)
        except ActivityParseError as exc:
            doc.update({"parse_status": "error", "parse_error": exc.code,
                        "parse_attempts": doc.get("parse_attempts", 0) + 1})
            save_workout(self.store, doc_id, doc, merge=False)
            self.counts["parse_errors"] += 1
            self.counts["workouts_imported"] += 1
            self.activity_docs[doc_id] = doc
            self.error("parse", exc)
            return doc["parse_attempts"] >= MAX_PARSE_ATTEMPTS
        self.check_budget()
        artifact = self.store.archive_json(f"parsed/{doc_id}/v{PARSER_VERSION}", asdict(parsed))
        laps_summary = _lap_summary(parsed.laps)
        doc.update({"parsed_artifact": artifact, "parse_status": "parsed", "parse_error": None,
                    "parse_attempts": doc.get("parse_attempts", 0) + 1,
                    "laps_summary": laps_summary, "lap_count": len(parsed.laps),
                    "laps_summary_truncated": len(parsed.laps) > len(laps_summary),
                    "record_count": len(parsed.records), "updated_at": now()})
        save_workout(self.store, doc_id, doc, merge=False)
        self.counts["files_parsed"] += 1
        self.counts["workouts_imported"] += 1
        self.activity_docs[doc_id] = doc
        return True

    def uploaded_activity(self, summary):
        """Quarantine uploads until native FIT provenance has been inspected.

        The private sync_state record retains archival and retry evidence; only
        a fully CRC-checked, source-verified upload is published. Oversized Zwift
        files may publish Intervals summaries with explicit original-only samples.
        """
        doc_id = source_document_id(summary["id"])
        manufacturer = fit_upload_manufacturer(summary)
        if manufacturer is None:
            raise ValueError("Upload is not a permitted FIT candidate")
        state_id = f"{manufacturer}_upload_{doc_id}"
        existing = self.store.get("workouts", doc_id) or {}
        previous = self.store.get("sync_state", state_id) or {}
        summary_hash = source_hash(summary)
        if (existing.get("provider_source") == "UPLOAD"
                and not existing.get("source_deleted") and not existing.get("source_excluded")
                and existing.get("source_summary_sha256") == summary_hash
                and existing.get("schema_version") == SCHEMA_VERSION
                and existing.get("parser_version") == PARSER_VERSION
                and existing.get("upload_verification_version") == UPLOAD_VERIFICATION_VERSION
                and existing.get("source_verification", {}).get("status") == "verified"
                and existing.get("source_verification", {}).get("manufacturer") == manufacturer
                and existing.get("source_verification_sha256") == (existing.get("original_artifact") or {}).get("sha256")
                and existing.get("original_artifact")
                and ((existing.get("parsed_artifact") and existing.get("parse_status") == "parsed")
                     or (manufacturer == "zwift" and existing.get("parse_status") == "summary_only"
                         and existing.get("inspected_artifact")
                         and existing.get("inspection_version") == OVERSIZE_INSPECTION_VERSION))):
            self.counts["workouts_unchanged"] += 1
            self.activity_docs[doc_id] = existing
            return True
        unchanged = (previous.get("source_summary_sha256") == summary_hash
                     and previous.get("parser_version") == PARSER_VERSION
                     and previous.get("upload_verification_version") == UPLOAD_VERIFICATION_VERSION)
        if unchanged and previous.get("verification_status") == "rejected":
            self.reject_upload(manufacturer)
            return True
        inspection_pending = (manufacturer == "zwift"
                              and previous.get("parse_error") == "activity_file_too_large"
                              and previous.get("inspection_version") != OVERSIZE_INSPECTION_VERSION)
        if (unchanged and previous.get("original_artifact") and previous.get("parse_status") == "error"
                and previous.get("parse_attempts", 0) >= MAX_PARSE_ATTEMPTS and not inspection_pending):
            self.counts["parse_terminal"] += 1
            return True

        detail = self.client.get_activity(str(summary["id"]))
        payload = {**summary, **detail}
        if fit_upload_manufacturer(payload) != manufacturer or payload.get("deleted") is True:
            # A changed/ambiguous source cannot be admitted using the summary alone.
            self.retire_activity(summary["id"], deleted=payload.get("deleted") is True)
            self.counts["activities_excluded"] += 1
            return True
        self.check_budget()
        pending = {"id": state_id, "source_id": str(summary["id"]), "provider_source": "UPLOAD",
                   "source_summary_sha256": summary_hash, "parser_version": PARSER_VERSION,
                   "upload_verification_version": UPLOAD_VERIFICATION_VERSION,
                   "verification_status": "pending", "verification_error": None,
                   "file_status": "pending", "file_error": None, "parse_status": "pending",
                   "parse_attempts": previous.get("parse_attempts", 0) if unchanged else 0,
                   "original_artifact": previous.get("original_artifact"),
                   "raw_payload_artifact": self.store.archive_json(
                       f"raw/activities/{doc_id}", {"summary": summary, "detail": detail}),
                   "updated_at": now()}
        self.store.put("sync_state", state_id, pending, merge=False)
        try:
            original = self.client.download_original(str(summary["id"]))
            self.check_budget()
            original_artifact = self.store.archive(
                f"originals/{doc_id}", original.data, content_type=original.content_type)
        except Exception as exc:
            self.store.put("sync_state", state_id, {
                "file_status": "error", "file_error": _error_code(exc), "updated_at": now()})
            raise
        self.counts["original_files_archived"] += 1
        if (previous.get("original_artifact") or {}).get("sha256") != original_artifact["sha256"]:
            pending["parse_attempts"] = 0
        pending.update({"original_artifact": original_artifact, "file_status": "archived",
                        "original_filename": original.filename,
                        "original_content_encoding": original.content_encoding})
        # The immutable original and its reference are durable before decoding.
        self.store.put("sync_state", state_id, pending, merge=False)
        self.check_budget()
        inspection = None
        try:
            try:
                parsed = parse_activity_file(original.data, filename=original.filename)
            except ActivityFileTooLarge:
                if manufacturer != "zwift":
                    raise
                parsed = None
            # Inspect after leaving the exception handler so the failed eager
            # parser's retained message tree can be released before streaming.
            if parsed is None:
                self.check_budget()
                pending["inspection_version"] = OVERSIZE_INSPECTION_VERSION
                inspection = inspect_activity_file(original.data, filename=original.filename)
        except ActivityParseError as exc:
            pending.update({"parse_status": "error", "parse_error": exc.code,
                            "parse_attempts": pending["parse_attempts"] + 1})
            self.store.put("sync_state", state_id, pending, merge=False)
            self.counts["parse_errors"] += 1
            self.error("parse", exc)
            return pending["parse_attempts"] >= MAX_PARSE_ATTEMPTS
        self.check_budget()
        pending.update({"parse_status": "parsed", "parse_error": None,
                        "parse_attempts": pending["parse_attempts"] + 1})
        metadata = parsed.metadata if parsed is not None else inspection["metadata"]
        if not is_verified_activity_fit(metadata, manufacturer):
            self.retire_activity(summary["id"], deleted=False)
            pending.update({"verification_status": "rejected",
                            "verification_error": f"{manufacturer}_activity_file_id_not_verified"})
            self.store.put("sync_state", state_id, pending, merge=False)
            self.reject_upload(manufacturer)
            return True

        doc = normalize_activity(payload, athlete_id=self.athlete_id,
                                 upload_fit_metadata=metadata)
        parsed_artifact = (self.store.archive_json(f"parsed/{doc_id}/v{PARSER_VERSION}", asdict(parsed))
                           if parsed is not None else None)
        laps_summary = _lap_summary(parsed.laps) if parsed is not None else []
        if inspection is not None:
            doc.update({"inspection_version": OVERSIZE_INSPECTION_VERSION,
                        "inspected_artifact": self.store.archive_json(
                            f"inspected/{doc_id}/v{OVERSIZE_INSPECTION_VERSION}", inspection),
                        "sample_availability": "original_only",
                        "sample_limit_reason": "activity_file_too_large"})
        doc.update({"source_summary_sha256": summary_hash,
                    "raw_payload_artifact": pending["raw_payload_artifact"],
                    "created_at": existing.get("created_at") or now(), "updated_at": now(),
                    "file_status": "archived", "file_error": None,
                    "original_artifact": original_artifact, "original_filename": original.filename,
                    "original_content_encoding": original.content_encoding,
                    "parsed_artifact": parsed_artifact,
                    "parse_status": "parsed" if parsed is not None else "summary_only", "parse_error": None,
                    "parser_version": PARSER_VERSION, "parse_attempts": pending["parse_attempts"],
                    "upload_verification_version": UPLOAD_VERIFICATION_VERSION,
                    "source_verification_sha256": original_artifact["sha256"],
                    "laps_summary": laps_summary,
                    "lap_count": len(parsed.laps) if parsed is not None else inspection["lap_count"],
                    "laps_summary_truncated": (len(parsed.laps) if parsed is not None else inspection["lap_count"]) > len(laps_summary),
                    "record_count": len(parsed.records) if parsed is not None else inspection["record_count"]})
        save_workout(self.store, doc_id, doc, merge=False)
        pending.update({"verification_status": "verified", "parse_status": doc["parse_status"],
                        "inspected_artifact": doc.get("inspected_artifact"), "parsed_artifact": parsed_artifact,
                        "verified_workout_id": doc_id, "updated_at": now()})
        self.store.put("sync_state", state_id, pending, merge=False)
        self.counts["workouts_imported"] += 1
        self.counts["files_parsed"] += int(parsed is not None)
        self.counts["summary_only_imported"] += int(inspection is not None)
        if inspection is not None:
            self.warnings.append({"stage": "parse", "code": "summary_only_large_fit"})
        self.counts["uploads_verified"] += 1
        self.activity_docs[doc_id] = doc
        return True

    def retire_activity(self, source_id, *, deleted):
        """Retain archives; exclude only after explicit source evidence."""
        doc_id = source_document_id(source_id)
        existing = self.store.get("workouts", doc_id)
        if not existing:
            return
        key = "source_deleted" if deleted else "source_excluded"
        if existing.get(key):
            return
        save_workout(self.store, doc_id, {key: True, "source_status_checked_at": now()})
        self.activity_docs.pop(doc_id, None)
        self.counts["workouts_retired"] += 1
        planned_id = existing.get("paired_planned_workout_id")
        plan = self.store.get("planned_workouts", planned_id) if planned_id else None
        if (plan and plan.get("source") == "intervals.icu"
                and plan.get("completed_workout_id") == doc_id):
            updates = {"completed_workout_id": None, "updated_at": now()}
            if plan.get("status") == "completed":
                updates["status"] = "planned"
            self.store.put("planned_workouts", planned_id, updates)

    def reconcile_missing(self, payloads, oldest, newest):
        seen = {str(p["id"]) for p in payloads}
        complete = True
        for doc in list(list_all(self.store, "workouts", oldest.isoformat(), newest.isoformat(), self.check_budget)):
            if (doc.get("source") != "intervals.icu" or not doc.get("source_id")
                    or doc.get("source_deleted") or doc.get("source_excluded")
                    or str(doc["source_id"]) in seen):
                continue
            self.check_budget()
            if self.missing_checks >= 30:
                self.error("activity_reconcile", IntervalsError("reconciliation_budget", retryable=True))
                return False
            self.missing_checks += 1
            try:
                detail = self.client.get_activity(str(doc["source_id"]))
                # A moved activity is updated using its new local date. Absence
                # from the range alone is never treated as a deletion.
                if self.activity(detail) is False:
                    complete = False
            except IntervalsError as exc:
                if exc.status_code == 404:
                    self.retire_activity(doc["source_id"], deleted=True)
                else:
                    self.error("activity_reconcile", exc)
                    complete = False
            except Exception as exc:
                self.error("activity_reconcile", exc)
                complete = False
        return complete

    def reject_upload(self, manufacturer):
        self.counts["uploads_rejected"] += 1
        self.counts["activities_excluded"] += 1
        if len(self.warnings) < 30:
            self.warnings.append({"stage": "upload_verification",
                                  "code": f"{manufacturer}_activity_file_id_not_verified"})

    def wellness(self, payload):
        self.check_budget()
        doc = normalize_wellness(payload, athlete_id=self.athlete_id)
        existing = self.store.get("wellness", doc["id"]) or {}
        if existing.get("source_payload_sha256") == doc["source_payload_sha256"] and \
                existing.get("schema_version") == SCHEMA_VERSION:
            self.counts["wellness_unchanged"] += 1
            return
        doc["raw_payload_artifact"] = self.store.archive_json(f"raw/wellness/{doc['id']}", payload)
        self.store.put("wellness", doc["id"], doc, merge=False)
        self.counts["wellness_imported"] += 1

    def range(self, oldest, newest):
        complete = True
        for kind, fetch, process in (
            ("activities", self.client.list_activities, self.activity),
            ("wellness", self.client.list_wellness, self.wellness),
        ):
            self.check_budget()
            try:
                payloads = fetch(oldest, newest)
            except Exception as exc:
                self.error(kind + "_fetch", exc)
                complete = False
                continue
            for payload in payloads:
                try:
                    if process(payload) is False:
                        complete = False
                except Exception as exc:
                    self.error(kind + "_import", exc)
                    complete = False
            if kind == "activities":
                if not self.reconcile_missing(payloads, oldest, newest):
                    complete = False
        return complete

    def plans(self, today):
        self.check_budget()
        complete = True
        payloads = self.client.list_events(today - timedelta(days=30), today + timedelta(days=180))
        for payload in payloads:
            self.check_budget()
            try:
                doc = normalize_planned_workout(payload, athlete_id=self.athlete_id)
                if doc is None:
                    self.counts["calendar_events_excluded"] += 1
                    continue
                existing = self.store.get("planned_workouts", doc["id"]) or {}
                if existing.get("source_payload_sha256") == doc["source_payload_sha256"] and \
                        existing.get("schema_version") == SCHEMA_VERSION:
                    self.counts["planned_workouts_unchanged"] += 1
                    continue
                doc["raw_payload_artifact"] = self.store.archive_json(
                    f"raw/planned_workouts/{doc['id']}", payload)
                for key in ("status", "completed_workout_id", "completed_at", "local_notes"):
                    if key in existing:
                        doc[key] = existing[key]
                doc["created_at"] = existing.get("created_at") or now()
                doc["updated_at"] = now()
                self.store.put("planned_workouts", doc["id"], doc, merge=False)
                self.counts["planned_workouts_imported"] += 1
            except Exception as exc:
                self.error("plans_import", exc)
                complete = False
        return complete

    def link_plans(self):
        for doc_id, activity in self.activity_docs.items():
            self.check_budget()
            planned_id = activity.get("paired_planned_workout_id")
            if not planned_id:
                continue
            plan = self.store.get("planned_workouts", planned_id)
            if not plan or plan.get("completed_workout_id") == doc_id:
                continue
            update = {"completed_workout_id": doc_id, "updated_at": now()}
            if plan.get("status") in (None, "planned", "completed"):
                update["status"] = "completed"
            self.store.put("planned_workouts", planned_id, update)
            self.counts["plans_linked"] += 1


def run_sync(store, settings, *, backfill=True, client=None):
    """Import one bounded run; every failure leaves replayable durable state.

    Authentication errors propagate to HTTP 503. Other incomplete runs return
    ``status=partial`` for Scheduler retries. A terminal, archived parsing issue
    is reported separately and cannot block the historical cursor forever.
    """
    run_id = str(uuid.uuid4())
    started = now()
    deadline = time.monotonic() + RUN_BUDGET_SECONDS
    if not store.acquire_lease(run_id, seconds=840):
        return {"status": "already_running", "run_id": run_id}
    owned_client = client is None
    importer = None
    updates = {"last_attempt_at": started}
    result = None
    try:
        state = store.get("sync_state", "intervals") or {}
        if client is None:
            client = IntervalsClient(os.environ["INTERVALS_API_KEY"], settings.athlete_id,
                                     deadline_monotonic=deadline)
        importer = _Importer(store, settings, client, deadline, state)
        store.put("sync_state", "intervals", {"last_attempt_at": started})
        importer.identify()
        updates["source_athlete_id"] = importer.athlete_id
        athlete_timezone = ZoneInfo(settings.timezone)
        today = datetime.now(athlete_timezone).date()
        recent_oldest = today - timedelta(days=RECENT_DAYS - 1)
        # Recover every day since the last fully imported refresh. A fixed
        # lookback would permanently miss workouts after an outage >14 days
        # once the initial historical backfill has already finished.
        last_success = state.get("last_success_at")
        if last_success is not None:
            recovery_oldest = _aware_timestamp(last_success).astimezone(athlete_timezone).date() \
                - timedelta(days=1)
            recent_oldest = min(recent_oldest, recovery_oldest)
        recent_complete = importer.range(recent_oldest, today)
        if recent_complete:
            updates["last_success_at"] = now()
        last_plans = state.get("last_plans_success_at")
        if last_plans is not None:
            last_plans = _aware_timestamp(last_plans)
        if last_plans is None or (started - last_plans).total_seconds() >= 3600:
            try:
                if importer.plans(today):
                    updates["last_plans_success_at"] = now()
            except Exception as exc:
                importer.error("plans_fetch", exc)
        importer.link_plans()

        if backfill and not state.get("backfill_complete"):
            importer.check_budget()
            # Do not begin another upstream window without enough time for a request.
            if deadline - time.monotonic() < 90:
                raise SyncBudgetExceeded()
            floor = date.fromisoformat(settings.history_start_date)
            cursor = date.fromisoformat(state.get("backfill_cursor") or
                                       (recent_oldest - timedelta(days=1)).isoformat())
            if cursor < floor:
                updates["backfill_complete"] = True
            else:
                oldest = max(floor, cursor - timedelta(days=30))
                complete = importer.range(oldest, cursor)
                importer.link_plans()
                importer.check_budget()
                if complete:
                    updates["backfill_cursor"] = oldest.isoformat()
                    updates["backfill_complete"] = oldest == floor
                    updates["last_backfill_success_at"] = now()
        if backfill and state.get("backfill_complete"):
            importer.check_budget()
            floor = date.fromisoformat(settings.history_start_date)
            cursor = date.fromisoformat(state.get("historical_reconcile_cursor") or
                                       (recent_oldest - timedelta(days=1)).isoformat())
            if cursor < floor:
                cursor = recent_oldest - timedelta(days=1)
            oldest = max(floor, cursor - timedelta(days=30))
            if oldest <= cursor and importer.range(oldest, cursor):
                updates["historical_reconcile_cursor"] = (
                    recent_oldest - timedelta(days=1) if oldest == floor else oldest - timedelta(days=1)
                ).isoformat()
                updates["last_historical_reconciliation_at"] = now()
                importer.link_plans()
        try:
            summary_result = refresh_summaries(store, settings, today, importer.check_budget)
            importer.counts["summaries_updated"] = summary_result["updated"]
        except Exception as exc:
            importer.error("summary_refresh", exc)
        status = "partial" if importer.errors else \
            ("ok_with_warnings" if importer.counts["parse_terminal"] or importer.warnings else "ok")
        result = {"status": status, "run_id": run_id, "counts": importer.counts,
                  "errors": importer.errors, "warnings": importer.warnings, "started_at": started.isoformat(),
                  "finished_at": now().isoformat()}
        return result
    except Exception as exc:
        # Persist only stable codes/type names. Never store exception text or payloads here.
        if importer is not None and not any(e["code"] == _error_code(exc) for e in importer.errors):
            importer.errors.append({"stage": "run", "code": _error_code(exc),
                                    "retryable": getattr(exc, "retryable", False)})
            importer.counts["errors"] += 1
        result = {"status": "partial", "run_id": run_id,
                  "counts": importer.counts if importer else {},
                  "errors": importer.errors if importer else [{"stage": "run", "code": _error_code(exc)}],
                  "started_at": started.isoformat(), "finished_at": now().isoformat()}
        if isinstance(exc, SyncBudgetExceeded) or \
                (isinstance(exc, IntervalsError) and exc.code == "deadline_exceeded"):
            return result
        raise
    finally:
        if owned_client and client is not None:
            client.close()
        if result is not None:
            updates.update({"last_status": result["status"], "last_run_id": run_id,
                            "last_counts": result["counts"], "last_errors": result["errors"],
                            "last_warnings": result.get("warnings", [])})
            if result["status"] == "partial":
                logger.error(json.dumps({"severity": "ERROR", "event": "sync_failed",
                                         "run_id": run_id, "errors": result["errors"]}))
            try:
                store.put("sync_runs", run_id, result, merge=False)
            finally:
                store.release_lease(run_id, updates)
        else:
            store.release_lease(run_id, updates)
