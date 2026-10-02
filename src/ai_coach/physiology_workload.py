"""Mechanical work, explicitly custom running stress, and matched durability."""
from .physiology_samples import number

RUN_STRESS_VERSION = "speed_ratio_squared_v1"


def workload(segments, sport, model=None):
    known = [s for s in segments if s["value"] is not None]
    complete = bool(segments) and len(known) == len(segments)
    result = {"coverage": "complete_recorded_span" if complete else "partial" if known else "unavailable",
              "known_seconds": sum(s["duration_seconds"] for s in known),
              "unknown_seconds": sum(s["duration_seconds"] for s in segments if s["value"] is None),
              "mechanical_work_kj": None, "known_mechanical_work_kj": None, "total_running_stress": None}
    if sport == "cycling":
        kj = sum(s["value"] * s["duration_seconds"] / 1000 for s in known) if known else None
        result.update(known_mechanical_work_kj=kj, mechanical_work_kj=kj if complete else None)
    elif sport == "running":
        result.update(running_stress_version=RUN_STRESS_VERSION,
                      running_stress_method="100/3600 * integral((speed_mps/critical_speed_mps)^2 dt)",
                      running_stress_required_inputs=["complete_speed_samples", "supported_pre_run_critical_speed"],
                      running_stress_limitation="custom_external_workload_index_not_validated_physiological_fatigue")
        cs = number((model or {}).get("critical_speed_mps"))
        if complete and cs and model.get("status") == "supported" and model.get("analysis_mode") == "as_known_before_workout":
            result["total_running_stress"] = sum((s["value"] / cs) ** 2 * s["duration_seconds"] for s in known) * 100 / 3600
            result["running_stress_snapshot_id"] = model["snapshot_id"]
    return result


def durability(efforts):
    """Product protocol v1: 300s maximal, fresh <=100kJ, fatigued 2000..2200kJ.

    Comparable means same explicit protocol AND context (terrain/equipment/intention),
    distinct workouts within 42 days. Missing data never becomes zero decline.
    """
    result = {"protocol_version": "matched_300s_2000kj_v1", "metric": "five_minute_power_after_2000kj",
              "status": "insufficient_evidence", "observed_decline_percent": None,
              "reason": "no_comparable_prefatigued_maximal_effort", "evidence": []}
    valid = [e for e in efforts if e.get("sport") == "cycling" and e.get("duration_seconds") == 300
             and e.get("maximal_intent") == "verified_maximal" and e.get("comparison_protocol_id")
             and e.get("comparison_context") and number(e.get("mechanical_work_before_kj")) is not None
             and number(e.get("mean_output"))]
    from .physiology_samples import timestamp
    pairs = []
    for fresh in valid:
        if fresh["mechanical_work_before_kj"] > 100:
            continue
        for tired in valid:
            if (2000 <= tired["mechanical_work_before_kj"] <= 2200 and fresh["workout_id"] != tired["workout_id"]
                    and fresh["comparison_protocol_id"] == tired["comparison_protocol_id"]
                    and fresh["comparison_context"] == tired["comparison_context"]
                    and abs((timestamp(fresh["start_utc"]) - timestamp(tired["start_utc"])).total_seconds()) <= 42 * 86400):
                pairs.append((fresh, tired))
    if pairs:
        fresh, tired = max(pairs, key=lambda pair: pair[1]["start_utc"])
        result.update(status="supported", reason=None,
                      observed_decline_percent=100 * (fresh["mean_output"] - tired["mean_output"]) / fresh["mean_output"],
                      evidence=[fresh, tired], warning="matched_observation_not_causal_proof_or_training_prescription")
    return result
