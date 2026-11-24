"""Conservative recent-evidence selection and immutable model payloads."""
from datetime import timedelta
from itertools import combinations, product

from .physiology_samples import timestamp, number
from .physiology_regression import regress
from .storage import fingerprint

VERSION = "critical_work_time_ols_v1"


def fit_model(efforts, *, sport, cutoff, calculated_at, lookback_days=42, mode="current", target_id=None):
    cutoff, calculated_at = timestamp(cutoff), timestamp(calculated_at)
    if cutoff is None or calculated_at is None or not 14 <= lookback_days <= 90:
        raise ValueError("Invalid model window")
    if sport not in {"cycling", "running"} or mode not in {"current", "as_known_before_workout", "retrospective"}:
        raise ValueError("Invalid model mode")
    start = cutoff - timedelta(days=lookback_days)
    eligible = []
    for e in efforts:
        end, begun, known = timestamp(e.get("end_utc")), timestamp(e.get("start_utc")), timestamp(e.get("known_at"))
        duration, output = number(e.get("duration_seconds")), number(e.get("mean_output"))
        if (e.get("sport") == sport and duration is not None and 120 < duration < 900 and output
                and end and begun and known and start <= begun < end < cutoff and known < cutoff
                and abs((end - begun).total_seconds() - duration) < 1e-6
                and e.get("workout_id") != target_id and e.get("eligible", True)):
            eligible.append(e)
    # Retain alternatives before enforcing independence: a strong workout often
    # owns the entire upper envelope. Limit to best 12 distinct workouts/duration.
    grouped = {}
    for e in sorted(eligible, key=lambda e: (-e["mean_output"], e.get("maximal_intent") != "verified_maximal", e["workout_id"])):
        group = grouped.setdefault(e["duration_seconds"], [])
        if len(group) < 12 and e["workout_id"] not in {x["workout_id"] for x in group}:
            group.append(e)
    if len(grouped) > 16:
        raise ValueError("Too many candidate durations")
    chosen = []
    for count in (3, 2):
        best_score = -1
        for durations in combinations(sorted(grouped), count):
            if durations[-1] / durations[0] < 3 or any(b / a < 1.4 for a, b in zip(durations, durations[1:])):
                continue
            for subset in product(*(grouped[t] for t in durations)):
                if len({e["workout_id"] for e in subset}) != count:
                    continue
                score = sum(e["mean_output"] / grouped[e["duration_seconds"]][0]["mean_output"] for e in subset)
                if score > best_score:
                    best_score, chosen = score, list(subset)
        if chosen:
            break
    result = {"algorithm_version": VERSION, "sport": sport, "calculated_at": calculated_at.isoformat(),
              "data_cutoff_utc": cutoff.isoformat(), "lookback_days": lookback_days, "analysis_mode": mode,
              "target_workout_id": target_id, "eligible_effort_count": len(eligible),
              "independent_effort_count": len(chosen), "selected_efforts": chosen,
              "status": "insufficient_data", "critical_output": None, "capacity": None,
              "uncertainty": {"kind": "conditional_ols_standard_errors", "biological_confidence_interval": None},
              "warnings": ["field_efforts_do_not_establish_physiological_certainty"]}
    if len(chosen) >= 2:
        try:
            fit = regress(chosen)
        except ValueError:
            result["warnings"].append("nonphysical_fit")
        else:
            result.update(fit)
            result["status"] = "provisional"
            verified = all(e.get("maximal_intent") == "verified_maximal" for e in chosen)
            if (len(chosen) >= 3 and verified and fit["rmse_output"] / fit["critical_output"] <= 0.05
                    and len({timestamp(e["start_utc"]).date() for e in chosen}) >= 3):
                result["status"] = "supported"
            if not verified:
                result["warnings"].append("maximal_intent_unverified")
            if max(timestamp(e["end_utc"]) for e in chosen) < cutoff - timedelta(days=21):
                result["status"] = "stale"
                result["warnings"].append("no_recent_independent_efforts")
    else:
        result["warnings"].append("need_independent_duration_separated_efforts")
    names = ("critical_power_watts", "w_prime_joules") if sport == "cycling" else ("critical_speed_mps", "d_prime_meters")
    result[names[0]], result[names[1]] = result["critical_output"], result["capacity"]
    # Calculation clock is metadata. Identical evidence/window/config is idempotent.
    identity = {k: v for k, v in result.items() if k != "calculated_at"}
    result["snapshot_id"] = "model_" + fingerprint(identity)
    return result
