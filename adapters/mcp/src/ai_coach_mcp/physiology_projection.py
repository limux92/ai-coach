"""Bound physiology in recommendation prompts; full evidence remains in read tools."""
import json

MODEL_FIELDS = ("snapshot_id", "sport", "algorithm_version", "status", "lookback_days", "data_cutoff_utc",
                "analysis_mode", "eligible_effort_count", "independent_effort_count", "critical_power_watts",
                "w_prime_joules", "critical_speed_mps", "d_prime_meters", "rmse_output",
                "critical_output_standard_error", "capacity_standard_error", "uncertainty", "warnings")


def pick(value, fields):
    return {key: value[key] for key in fields if key in value} if isinstance(value, dict) else None


def recommendation_context(context):
    """Keep original observations intact; never truncate user injury/wellness data.

    A compact physiology view carries evidence IDs and limitations. If even that
    exceeds the remaining request budget, omit its quantitative claims explicitly.
    The caller still enforces the existing total 24 KB safety limit.
    """
    source = context.get("physiology")
    if not isinstance(source, dict) or source.get("projection") == "quick_workout_v1":
        return context
    base = {key: value for key, value in context.items() if key != "physiology"}
    view = pick(source, ("schema_version", "as_of", "status", "stale", "data_coverage", "target_workout_id", "limitation"))
    view.update(projection="quick_workout_v1", detail_lookup="/v1/physiology/context")
    view["current_models"] = {sport: pick(model, MODEL_FIELDS) for sport, model in source.get("current_models", {}).items()}
    view["model_used_for_target"] = pick(source.get("model_used_for_target"), MODEL_FIELDS)
    target = source.get("latest_session_target_analysis") or {}
    view["latest_session_target_analysis"] = pick(target, ("analysis_id", "workout_id", "target_revision_id",
        "analysis_mode", "model_snapshot_id", "workload", "balance", "fueling_carbs_grams_per_hour", "warnings", "event_lookup"))
    events = target.get("above_threshold_events", [])
    view["latest_session_target_analysis"]["above_threshold_events"] = events[:2]
    view["latest_session_target_analysis"]["above_threshold_events_omitted"] = max(0, len(events) - 2) + target.get("above_threshold_events_omitted", 0)
    view["periods"] = {key: {**{k: v for k, v in period.items() if k != "sessions"},
                            "sessions_omitted": period.get("workout_count")}
                       for key, period in source.get("periods", {}).items()}
    view["durability"] = source.get("durability")
    view["evidence_library"] = source.get("evidence_library")
    view["interpretation_limits"] = source.get("interpretation_limits")
    # Defaults match the service/provider JSON serialization used for their cap.
    size = lambda value: len(json.dumps(value, allow_nan=False, default=str).encode("utf-8"))
    allowance = min(8000, 24000 - size(base) - 128)
    if size(view) > allowance:
        view = {**pick(source, ("schema_version", "as_of", "status", "stale", "data_coverage")),
                "projection": "quick_workout_v1", "detail_lookup": "/v1/physiology/context",
                "quantitative_evidence_omitted": True,
                "limitation": "Physiology detail exceeds this recommendation budget. Do not infer model parameters, exhaustion, durability or fueling. Use conservative guidance from the remaining training context."}
    return {**base, "physiology": view}
