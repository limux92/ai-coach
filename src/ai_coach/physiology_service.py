"""Bounded derived evidence maintenance under the existing private sync lease."""
from datetime import timedelta

from .physiology_samples import VERSION as SAMPLE_VERSION, normalize_samples, best_efforts, sport_kind, timestamp, number, _native
from .physiology_models import fit_model
from .physiology_balance import cycling_balance, VERSION as BALANCE_VERSION
from .physiology_workload import workload, durability, RUN_STRESS_VERSION
from .physiology_protocols import protocol_efforts
from .physiology_evidence import put_immutable, select_revisions, commit_workout
from .storage import fingerprint, utcnow

VERSION = "physiology_pipeline_v1"
MAX_REVISIONS = 20000
MAX_JOBS = 25


def extraction_id(revision_id):
    return "efforts_" + fingerprint([revision_id, VERSION, SAMPLE_VERSION])


def scan_all(store, collection, check_budget=lambda: None):
    rows, cursor = [], None
    while len(rows) < MAX_REVISIONS:
        check_budget()
        page = store.scan(collection, limit=min(200, MAX_REVISIONS - len(rows)), after=cursor)
        rows.extend(page)
        if len(page) < 200:
            return rows
        if page[-1]["id"] == cursor:
            raise ValueError("Pagination did not advance")
        cursor = page[-1]["id"]
    raise ValueError("Physiology revision scan limit reached")


def migrate(store, check_budget):
    state = store.get("sync_state", "physiology_migration") or {}
    if state.get("complete"):
        return True
    rows = store.scan("workouts", limit=MAX_JOBS, after=state.get("cursor"))
    for doc in rows:
        check_budget()
        if not doc.get("physiology_revision_id"):
            commit_workout(store, doc["id"], doc, merge=False)
        store.put("sync_state", "physiology_migration", {"cursor": doc["id"], "complete": False})
    done = len(rows) < MAX_JOBS
    store.put("sync_state", "physiology_migration", {"complete": done})
    return done


def queue_version_upgrade(store, check_budget):
    state = store.get("sync_state", "physiology_extraction_migration") or {}
    version = VERSION + ":" + SAMPLE_VERSION
    if state.get("version") == version and state.get("complete"):
        return True
    cursor = state.get("cursor") if state.get("version") == version else None
    rows = store.scan("physiology_revisions", limit=MAX_JOBS, after=cursor)
    for row in rows:
        check_budget()
        if not store.get("physiology_efforts", extraction_id(row["id"])):
            store.put("physiology_jobs", row["id"], {"id": row["id"], "pending": True})
        store.put("sync_state", "physiology_extraction_migration", {"version": version, "cursor": row["id"], "complete": False})
    done = len(rows) < MAX_JOBS
    store.put("sync_state", "physiology_extraction_migration", {"version": version, "complete": done})
    return done


def extract_revision(store, revision):
    doc = revision["evidence"]
    sport = sport_kind(doc.get("sport"))
    eligible = (sport is not None and not doc.get("source_deleted") and not doc.get("source_excluded")
                and doc.get("parse_status") == "parsed" and doc.get("parsed_artifact")
                and timestamp(doc.get("start_date_utc")) is not None)
    normalized = {"version": SAMPLE_VERSION, "segments": [], "flags": ["recording_unavailable"], "complete": False}
    if eligible:
        parsed = store.read_json(doc["parsed_artifact"])
        normalized = normalize_samples(parsed.get("records", []), sport, events=parsed.get("events", []))
        # Multiple native sessions can join distinct activities in a single FIT.
        if len(parsed.get("sessions", [])) > 1:
            normalized = {"version": SAMPLE_VERSION, "segments": [], "flags": ["multiple_fit_sessions"], "complete": False}
        segments = normalized["segments"]
        start = timestamp(doc["start_date_utc"])
        if segments and timestamp(segments[0]["start"]) > start:
            gap = (timestamp(segments[0]["start"]) - start).total_seconds()
            segments.insert(0, {"start": start.isoformat(), "end": segments[0]["start"],
                               "duration_seconds": gap, "value": None, "flags": ["unknown_leading_gap"]})
            normalized["complete"] = False
            normalized["flags"].append("unknown_leading_gap")
        elif segments and timestamp(segments[0]["start"]) < start:
            normalized = {"version": SAMPLE_VERSION, "segments": [], "flags": ["source_timestamp_conflict"], "complete": False}
        duration = number(doc.get("expected_elapsed_seconds"), 604800)
        sessions = parsed.get("sessions", [])
        session_duration = number(_native(sessions[0], "total_elapsed_time"), 604800) if len(sessions) == 1 else None
        if duration is None:
            duration = session_duration
        elif session_duration and abs(session_duration - duration) > 5:
            normalized = {"version": SAMPLE_VERSION, "segments": [], "flags": ["ambiguous_workout_duration"], "complete": False}
        segments = normalized["segments"]
        if duration and segments:
            expected_end = start + timedelta(seconds=duration)
            recorded_end = timestamp(segments[-1]["end"])
            if expected_end > recorded_end:
                segments.append({"start": recorded_end.isoformat(), "end": expected_end.isoformat(),
                                 "duration_seconds": (expected_end - recorded_end).total_seconds(),
                                 "value": None, "flags": ["unknown_trailing_gap"]})
                normalized["complete"] = False
                normalized["flags"].append("unknown_trailing_gap")
            elif recorded_end > expected_end + timedelta(seconds=5):
                normalized = {"version": SAMPLE_VERSION, "segments": [], "flags": ["source_duration_conflict"], "complete": False}
        normalized["whole_workout_extent_known"] = bool(duration)
    efforts = best_efforts(normalized, workout_id=revision["workout_id"], revision_id=revision["id"], sport=sport) if eligible else []
    known_at = timestamp(revision["known_at"]).isoformat()
    for effort in efforts:
        effort["known_at"] = known_at
    efforts += protocol_efforts(normalized, doc, {"workout_id": revision["workout_id"], "revision_id": revision["id"],
                                                "sport": sport, "known_at": known_at})
    for effort in efforts:
        effort.update(extraction_id=extraction_id(revision["id"]), normalization_version=SAMPLE_VERSION)
    measured_work = workload(normalized["segments"], sport)
    if not normalized.get("whole_workout_extent_known"):
        measured_work.update(mechanical_work_kj=None, coverage="unknown_workout_extent")
    output = {"id": extraction_id(revision["id"]), "revision_id": revision["id"], "version": VERSION, "normalization_version": SAMPLE_VERSION,
              "workout_id": revision["workout_id"], "sport": sport, "known_at": known_at,
              "start_date_utc": doc.get("start_date_utc"), "local_date": doc.get("local_date"),
              "eligible": bool(eligible), "efforts": efforts, "recording_quality_flags": normalized["flags"],
              "workload": measured_work,
              "normalized_artifact": store.archive_json(f"physiology/normalized/{revision['id']}/{SAMPLE_VERSION}", normalized)}
    return put_immutable(store, "physiology_efforts", output["id"], output)


def available_efforts(store, revisions, cutoff, *, cache=None, check_budget=lambda: None):
    cache = {} if cache is None else cache
    efforts, selected = [], select_revisions(revisions, known_before=cutoff)
    for revision in selected:
        check_budget()
        doc = revision["evidence"]
        if doc.get("source_deleted") or doc.get("source_excluded") or doc.get("parse_status") != "parsed":
            continue
        key = extraction_id(revision["id"])
        if key not in cache:
            cache[key] = store.get("physiology_efforts", key)
        extracted = cache[key]
        if extracted and extracted["eligible"]:
            efforts.extend(extracted["efforts"])
    return efforts


def save_model(store, efforts, *, sport, cutoff, calculated_at, lookback_days, mode="current", target_id=None):
    model = fit_model(efforts, sport=sport, cutoff=cutoff, calculated_at=calculated_at,
                      lookback_days=lookback_days, mode=mode, target_id=target_id)
    return put_immutable(store, "physiology_models", model["snapshot_id"], model)


def analyze_target(store, revisions, target, *, now, lookback_days=42, mode="as_known_before_workout",
                   cache=None, check_budget=lambda: None, lease_owner=None):
    check_budget()
    doc, kind = target["evidence"], sport_kind(target["evidence"].get("sport"))
    cutoff = timestamp(doc.get("start_date_utc")) if mode == "as_known_before_workout" else now
    if kind is None or cutoff is None:
        return None
    efforts = available_efforts(store, revisions, cutoff, cache=cache, check_budget=check_budget)
    model = save_model(store, efforts, sport=kind, cutoff=cutoff, calculated_at=now,
                       lookback_days=lookback_days, mode=mode, target_id=target["workout_id"])
    analysis_id = "analysis_" + fingerprint([VERSION, target["id"], model["snapshot_id"], mode,
                                             BALANCE_VERSION, SAMPLE_VERSION, RUN_STRESS_VERSION])
    existing = store.get("physiology_analyses", analysis_id)
    if existing:
        check_budget()
        update_target_pointer(store, target, existing, mode, lease_owner)
        return existing
    check_budget()
    extracted = store.get("physiology_efforts", extraction_id(target["id"]))
    if not extracted:
        return None
    normalized = store.read_json(extracted["normalized_artifact"])
    measured_work = workload(normalized["segments"], kind, model)
    if not normalized.get("whole_workout_extent_known"):
        measured_work.update(mechanical_work_kj=None, total_running_stress=None, coverage="unknown_workout_extent")
    analysis = {"workout_id": target["workout_id"], "target_revision_id": target["id"], "schema_version": VERSION,
                "calculated_at": now.isoformat(), "analysis_mode": mode, "model_snapshot_id": model["snapshot_id"],
                "interpretation_basis": "reconstructed_from_evidence_known_before_workout" if mode == "as_known_before_workout" else "explicit_future_evidence",
                "workload": measured_work, "balance": None,
                "fueling_carbs_grams_per_hour": None, "warnings": list(model["warnings"])}
    if kind == "cycling" and model["status"] in {"provisional", "supported"}:
        analysis["balance"] = cycling_balance(normalized["segments"], model["critical_power_watts"], model["w_prime_joules"])
        if not normalized.get("whole_workout_extent_known"):
            analysis["balance"]["mechanical_work_kj"] = None
            analysis["balance"]["warnings"].append("unknown_workout_extent")
    else:
        analysis["warnings"].append("running_balance_deferred" if kind == "running" else "balance_requires_eligible_model")
    analysis["analysis_id"] = analysis_id
    check_budget()
    # Large curves/events remain in immutable GCS; Firestore/API summary stays bounded.
    full = store.archive_json(f"physiology/analyses/{analysis['analysis_id']}", analysis)
    summary = {k: v for k, v in analysis.items() if k != "balance"}
    balance = analysis["balance"]
    summary["balance"] = {k: v for k, v in balance.items() if k not in {"curve", "above_threshold_events"}} if balance else None
    summary["analysis_artifact"] = full
    saved = put_immutable(store, "physiology_analyses", analysis["analysis_id"], summary)
    check_budget()
    update_target_pointer(store, target, saved, mode, lease_owner)
    return saved


def publish_state(store, doc_id, updates, lease_owner, *, merge=True):
    if hasattr(store, "put_if_sync_owner"):
        store.put_if_sync_owner("sync_state", doc_id, updates, lease_owner, merge=merge)
    else:
        store.put("sync_state", doc_id, updates, merge=merge)


def update_target_pointer(store, target, saved, mode, lease_owner=None):
    pointer_id = "physiology_target_" + target["workout_id"]
    previous = store.get("sync_state", pointer_id) or {}
    updates = {"latest_" + mode: saved["analysis_id"]}
    if mode == "as_known_before_workout" and not previous.get("original_analysis_id"):
        updates["original_analysis_id"] = saved["analysis_id"]
    publish_state(store, pointer_id, updates, lease_owner)


def refresh_physiology(store, settings, *, check_budget=lambda: None, now=None, lease_owner=None):
    live_clock = now is None
    now = timestamp(now or utcnow())
    try:
        migration_complete = migrate(store, check_budget)
        upgrade_complete = queue_version_upgrade(store, check_budget)
        jobs = store.scan("physiology_jobs", limit=MAX_JOBS + 1, pending=True)
        for job in jobs[:MAX_JOBS]:
            check_budget()
            extract_revision(store, store.get("physiology_revisions", job["id"]))
            store.put("physiology_jobs", job["id"], {"pending": False})
        if not migration_complete or not upgrade_complete or len(jobs) > MAX_JOBS:
            publish_state(store, "physiology", {"status": "pending", "updated_at": now}, lease_owner)
            return {"status": "pending", "processed": min(len(jobs), MAX_JOBS)}
        revisions = scan_all(store, "physiology_revisions", check_budget)
        if live_clock:
            now = utcnow()
        if any(timestamp(r["known_at"]) >= now for r in revisions):
            publish_state(store, "physiology", {"status": "pending", "updated_at": now}, lease_owner)
            return {"status": "pending", "reason": "evidence_clock_not_yet_in_cutoff"}
        current = select_revisions(revisions, known_before=now)
        # Keep coverage/clock-driven expiry current even without any new workouts.
        cache = {}
        efforts = available_efforts(store, revisions, now, cache=cache, check_budget=check_budget)
        models = {sport: save_model(store, efforts, sport=sport, cutoff=now, calculated_at=now,
                  lookback_days=getattr(settings, "physiology_lookback_days", 42)) for sport in ("cycling", "running")}
        visible = [r for r in current if not r["evidence"].get("source_deleted") and not r["evidence"].get("source_excluded")]
        recent = [r for r in visible if timestamp(r["evidence"].get("start_date_utc"))
                  and now - timedelta(days=28) <= timestamp(r["evidence"]["start_date_utc"]) < now]
        analyses = []
        for target in sorted(recent, key=lambda r: r["evidence"]["start_date_utc"]):
            check_budget()
            analysis = analyze_target(store, revisions, target, now=now, lookback_days=getattr(settings, "physiology_lookback_days", 42),
                                      cache=cache, check_budget=check_budget, lease_owner=lease_owner)
            if analysis:
                analyses.append(analysis)
        from .physiology_context import make_context
        context = make_context(store, settings, now, current, models, analyses, efforts)
        context_id = "context_" + fingerprint(context)
        put_immutable(store, "physiology_contexts", context_id, context)
        check_budget()
        publish_state(store, "physiology", {"status": "ok", "updated_at": now,
                      "context_id": context_id, "version": VERSION}, lease_owner, merge=False)
        return {"status": "ok", "processed": len(jobs), "context_id": context_id}
    except Exception as exc:
        try:
            publish_state(store, "physiology", {"status": "failed", "last_error_code": type(exc).__name__}, lease_owner)
        except RuntimeError:
            pass  # A replaced worker must not invalidate its successor's context.
        raise
