"""Evidence-rich, bounded projections; no model fitting or FIT reads on context GET."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from .physiology_samples import timestamp
from .physiology_workload import durability

LIBRARY_VERSION = "curated_physiology_cards_v1"
CARDS = [
    {"id": "galbraith2014", "title": "A single-visit field test of critical speed",
     "url": "https://pubmed.ncbi.nlm.nih.gov/24622815/", "year": 2014, "topics": ["critical_speed", "running"],
     "claim": "Distance-time field tests can estimate critical speed and D-prime from maximal running efforts.",
     "limitations": "Ten male runners were studied. D-prime differed across test protocols; agreement in critical speed does not establish equivalent D-prime or validate this custom running stress index."},
    {"id": "skiba2015", "title": "Intramuscular determinants of the ability to recover work capacity above critical power",
     "url": "https://pubmed.ncbi.nlm.nih.gov/25425258/", "year": 2015, "topics": ["cycling_balance", "recovery"],
     "claim": "A differential W-prime model estimates depletion and reconstitution during intermittent exercise.",
     "limitations": "Experimental model; recovery is not automatically calibrated for this athlete. Running balance is not established by this cycling implementation."},
    {"id": "skiba2012", "title": "Modeling the expenditure and reconstitution of work capacity above critical power",
     "url": "https://pubmed.ncbi.nlm.nih.gov/22382171/", "year": 2012, "topics": ["critical_power", "model_limits"],
     "claim": "Power-duration behavior is modeled with CP and W-prime. Recovery depends on the difference between recovery power and CP.",
     "limitations": "Original model development involved seven participants. This implementation uses the separately named 2015 differential variant, not the 2012 integral equation."},
]


def coverage(store, settings, now, oldest, newest):
    state = store.get("sync_state", "intervals") or {}
    last = timestamp(state.get("recent_coverage_at"))
    covered = (last is not None and 0 <= (now - last).total_seconds() <= 600
               and state.get("recent_coverage_oldest", "9999") <= oldest
               and state.get("recent_coverage_newest", "") >= newest)
    return {"history_complete": state.get("backfill_complete") is True and state.get("backfill_scope_start") == settings.history_start_date,
            "history_scope_start": settings.history_start_date, "history_scope": "eligible_sources_from_configured_start",
            "recent_window_complete": covered, "recent_sync_at": last.isoformat() if last else None,
            "totals_scope": "known_imported_workouts", "sample_coverage_separate": True}


def compact_model(model):
    return {k: v for k, v in model.items() if k not in {"critical_output", "capacity"}}


def make_context(store, settings, now, revisions, models, analyses, efforts):
    from .physiology_service import extraction_id
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    by_workout = {a["workout_id"]: a for a in analyses}
    periods = {}
    for days in (7, 28):
        start = (today - timedelta(days=days - 1)).isoformat()
        rows = []
        for revision in revisions:
            doc = revision["evidence"]
            if (doc.get("source_deleted") or doc.get("source_excluded")
                    or not start <= (doc.get("local_date") or "") <= today.isoformat()):
                continue
            source = store.get("physiology_efforts", extraction_id(revision["id"])) or {}
            analysis = by_workout.get(revision["workout_id"])
            rows.append({"workout_id": revision["workout_id"], "evidence_revision_id": revision["id"],
                         "local_date": doc.get("local_date"), "sport": source.get("sport"),
                         "workload": analysis["workload"] if analysis else source.get("workload"),
                         "analysis_id": analysis["analysis_id"] if analysis else None,
                         "recording_quality_flags": source.get("recording_quality_flags", ["unprocessed"])})
        rows.sort(key=lambda r: (r["local_date"], r["workout_id"]), reverse=True)
        cycling = [r for r in rows if r["sport"] == "cycling"]
        running = [r for r in rows if r["sport"] == "running"]
        kj = [r["workload"]["mechanical_work_kj"] for r in cycling if r.get("workload") and r["workload"].get("mechanical_work_kj") is not None]
        stress = [r["workload"]["total_running_stress"] for r in running if r.get("workload") and r["workload"].get("total_running_stress") is not None]
        periods[f"rolling{days}"] = {"start_date": start, "end_date": today.isoformat(),
             "data_coverage": coverage(store, settings, now, start, today.isoformat()), "workout_count": len(rows),
             "cycling_known_mechanical_work_kj": sum(kj) if kj else None,
             "cycling_work_missing_count": len(cycling) - len(kj),
             "running_known_stress": sum(stress) if stress else None,
             "running_stress_missing_count": len(running) - len(stress),
             "sessions": rows[:500], "sessions_omitted": max(0, len(rows) - 500)}
    target = analyses[-1] if analyses else None
    target_model = store.get("physiology_models", target["model_snapshot_id"]) if target else None
    target_summary = {k: v for k, v in target.items() if k != "analysis_artifact"} if target else None
    if target_summary and target.get("balance"):
        full = store.read_json(target["analysis_artifact"])
        events = full["balance"]["above_threshold_events"]
        target_summary["above_threshold_events"] = events[:10]
        target_summary["above_threshold_events_omitted"] = max(0, len(events) - 10)
        target_summary["event_lookup"] = f"/v1/physiology/analyses/{target['analysis_id']}/events"
    return {"schema_version": "physiology_context.v1", "as_of": now.isoformat(), "timezone": settings.timezone,
            "data_coverage": periods["rolling28"]["data_coverage"],
            "target_workout_id": target["workout_id"] if target else None,
            "model_used_for_target": compact_model(target_model) if target_model else None,
            "current_models": {sport: compact_model(model) for sport, model in models.items()},
            "latest_session_target_analysis": target_summary, "periods": periods,
            "durability": durability([e for e in efforts if now - timedelta(days=42) <= timestamp(e["start_utc"])
                                      and timestamp(e["end_utc"]) < now and timestamp(e["known_at"]) < now]),
            "evidence_library": {"version": LIBRARY_VERSION, "retrieval": "deterministic_topic_cards", "sources": CARDS},
            "interpretation_limits": ["Low balance predicts low estimated capacity, never confirmed exhaustion.",
              "Event counts alone do not establish poor pacing; inspect session intention, duration, output, sequence and terrain.",
              "High recent cycling kJ may contribute to a failed workout; compare usual load, do not assert causation or rule out fitness change.",
              "Durability needs supported comparable maximal efforts; insufficient evidence is not zero decline.",
              "Missing nutrition cannot establish inadequate fueling. Missing workouts are not rest days."]}


def read_context(store, settings, now):
    state = store.get("sync_state", "physiology") or {}
    context = store.get("physiology_contexts", state["context_id"]) if state.get("context_id") else None
    updated = timestamp(state.get("updated_at"))
    stale = state.get("status") != "ok" or updated is None or not 0 <= (now - updated).total_seconds() <= 600
    if not context:
        return {"schema_version": "physiology_context.v1", "status": "unavailable", "stale": True,
                "limitation": "No completed physiology projection; missing metrics are unknown."}
    # Do not mutate the immutable saved context when freshness/coverage changes.
    periods = {}
    for key, period in context["periods"].items():
        periods[key] = {**period, "sessions": period["sessions"][:7],
                        "sessions_omitted": period["workout_count"] - min(7, len(period["sessions"])),
                        "session_lookup": "/v1/physiology/sessions?days=" + key.removeprefix("rolling"),
                        "data_coverage": coverage(store, settings, now, period["start_date"], period["end_date"])}
    return {**context, "periods": periods, "status": state.get("status"), "stale": stale,
            "data_coverage": coverage(store, settings, now, context["periods"]["rolling28"]["start_date"],
                                      context["periods"]["rolling28"]["end_date"])}
