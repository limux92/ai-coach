"""Rich evidence must not disable Quick Workout or hide patient observations."""
import asyncio
from copy import deepcopy
import json

from ai_coach_mcp.physiology_projection import recommendation_context
from test_quick_workout_flow import Backend, service


def evidence():
    model = {"snapshot_id": "model-synthetic", "sport": "cycling", "status": "provisional",
             "data_cutoff_utc": "2026-09-25T08:00:00Z", "analysis_mode": "as_known_before_workout",
             "critical_power_watts": 250, "w_prime_joules": 18000,
             "selected_efforts": [{"provenance": "synthetic-field" * 100}] * 3,
             "uncertainty": {"biological_confidence_interval": None}, "warnings": ["maximal_intent_unverified"]}
    event = {"sequence": 1, "start_utc": "2026-09-25T08:00:00Z", "duration_seconds": 60,
             "mean_power_watts": 400, "modeled_depletion_joules": 9000, "recovery_since_previous_joules": 0}
    return {"schema_version": "physiology_context.v1", "status": "ok", "stale": False,
            "data_coverage": {"history_complete": False, "recent_window_complete": True},
            "current_models": {"cycling": model}, "model_used_for_target": model,
            "latest_session_target_analysis": {"analysis_id": "analysis-test", "model_snapshot_id": "model-synthetic",
                "analysis_mode": "as_known_before_workout", "fueling_carbs_grams_per_hour": None,
                "balance": {"minimum_balance_percent": -5, "warnings": ["negative_balance_model_consistency_breach"]},
                "above_threshold_events": [dict(event, sequence=i) for i in range(10)],
                "above_threshold_events_omitted": 3},
            "periods": {"rolling28": {"sessions": [{"record": "synthetic" * 300}] * 7, "workout_count": 10}},
            "durability": {"status": "insufficient_evidence", "observed_decline_percent": None},
            "evidence_library": {"version": "curated_test", "sources": [{"id": "skiba2015", "limitations": "Population model"}]}}


def test_rich_context_still_generates_with_status_provenance_and_unknowns():
    backend = Backend()
    backend.data.update(observations=[{"injury": "Knee pain; no hard efforts"}], physiology=evidence())
    before = deepcopy(backend.data)
    assert len(json.dumps(before).encode()) > 24000
    worker, calls = service(backend)
    asyncio.run(worker.generate())
    sent = calls[0]
    assert len(json.dumps(sent).encode()) <= 24000
    assert sent["observations"] == before["observations"]
    assert sent["physiology"]["model_used_for_target"]["status"] == "provisional"
    assert sent["physiology"]["model_used_for_target"]["uncertainty"]["biological_confidence_interval"] is None
    assert sent["physiology"]["durability"]["observed_decline_percent"] is None
    assert sent["physiology"]["periods"]["rolling28"]["sessions_omitted"] == 10
    target = sent["physiology"]["latest_session_target_analysis"]
    assert target["model_snapshot_id"] == "model-synthetic"
    assert target["fueling_carbs_grams_per_hour"] is None
    assert target["above_threshold_events_omitted"] == 11
    assert target["balance"]["minimum_balance_percent"] == -5
    assert backend.data == before
    assert recommendation_context(sent) == sent


def test_budget_fallback_omits_numbers_explicitly_without_dropping_injury():
    backend = Backend()
    backend.data.update(observations=[{"injury": "No hard efforts", "notes": "a" * 22500}], physiology=evidence())
    worker, calls = service(backend)
    asyncio.run(worker.generate())
    sent = calls[0]
    assert sent["observations"] == backend.data["observations"]
    assert sent["physiology"]["quantitative_evidence_omitted"] is True
    assert "Do not infer" in sent["physiology"]["limitation"]
    assert sent["physiology"]["data_coverage"]["history_complete"] is False
    assert len(json.dumps(sent).encode()) <= 24000
