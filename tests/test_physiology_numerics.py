"""Independent synthetic numerical and missing-data acceptance fixtures."""
import math
from datetime import UTC, datetime, timedelta

import pytest

from ai_coach.physiology_regression import regress
from ai_coach.physiology_samples import normalize_samples, best_efforts
from ai_coach.physiology_models import fit_model
from ai_coach.physiology_balance import cycling_balance
from ai_coach.physiology_workload import durability, workload

T = datetime(2026, 9, 1, tzinfo=UTC)


def segment(start, duration, value):
    return {"start": (T + timedelta(seconds=start)).isoformat(), "end": (T + timedelta(seconds=start + duration)).isoformat(),
            "duration_seconds": duration, "value": value, "flags": []}


def effort(duration, *, cp=250, capacity=18000, day=1, workout=None, sport="cycling", intent="verified_maximal"):
    start = T + timedelta(days=day)
    return {"workout_id": workout or f"w{day}", "revision_id": f"r{day}", "sport": sport,
            "duration_seconds": duration, "mean_output": cp + capacity / duration, "start_utc": start.isoformat(),
            "end_utc": (start + timedelta(seconds=duration)).isoformat(),
            "known_at": (start + timedelta(hours=1)).isoformat(), "maximal_intent": intent}


@pytest.mark.parametrize("cp,capacity", [(250, 18000), (4, 200)])
def test_known_parameters_and_dimensional_rmse(cp, capacity):
    result = regress([effort(t, cp=cp, capacity=capacity) for t in (150, 300, 600)])
    assert result["critical_output"] == pytest.approx(cp)
    assert result["capacity"] == pytest.approx(capacity)
    assert result["rmse_output"] == pytest.approx(0, abs=1e-10)
    assert result["critical_output_standard_error"] == pytest.approx(0, abs=1e-10)
    noisy = [{"duration_seconds": t, "mean_output": p} for t, p in zip((150, 300, 600), (372, 308, 281))]
    result = regress(noisy)
    # OLS in work-time space: independent precomputed centered sums.
    assert result["critical_output"] == pytest.approx(26370000 / 105000)
    assert result["capacity"] == pytest.approx(17700)
    expected = math.sqrt(sum((e["mean_output"] - result["critical_output"] - result["capacity"] / e["duration_seconds"]) ** 2 for e in noisy) / 3)
    assert result["rmse_output"] == pytest.approx(expected)
    two = regress([effort(t) for t in (150, 600)])
    assert two["capacity_standard_error"] is two["critical_output_standard_error"] is None


@pytest.mark.parametrize("field,value", [("mean_output", x) for x in (True, None, float("nan"), float("inf"), -1)]
                         + [("duration_seconds", x) for x in (True, 120, 900, float("nan"))])
def test_regression_invalid_measurements(field, value):
    rows = [effort(150), effort(600)]
    rows[0][field] = value
    with pytest.raises(ValueError):
        regress(rows)


def test_exact_irregular_time_weighted_efforts_and_speed():
    records = []
    for i in range(31):
        records.append({"timestamp": (T + timedelta(seconds=i * 5)).isoformat(), "power": 100, "speed": 2})
        if i < 30:
            records.append({"timestamp": (T + timedelta(seconds=i * 5 + 2)).isoformat(), "power": 300, "speed": 6})
    for sport, expected in (("cycling", 220), ("running", 4.4)):
        normalized = normalize_samples(records, sport)
        best = best_efforts(normalized, workout_id="w", revision_id="r", sport=sport, durations=(150,))[0]
        assert best["mean_output"] == pytest.approx(expected)
        assert best["duration_seconds"] == 150
        assert best["start_utc"] == T.isoformat()
        assert "pace" not in best


@pytest.mark.parametrize("defect", ["missing", "gap", "spike", "conflict", "file", "pause"])
def test_no_effort_bridges_invalid_data(defect):
    rows = [{"timestamp": (T + timedelta(seconds=i)).isoformat(), "power": 300} for i in range(151)]
    events = []
    if defect == "missing":
        rows[75].pop("power")
    elif defect == "gap":
        del rows[75:90]
    elif defect == "spike":
        rows[75]["power"] = 10000
    elif defect == "conflict":
        rows.insert(76, {**rows[75], "power": 100})
    elif defect == "file":
        rows[75]["_file_index"] = 1
    else:
        events = [{"timestamp": T.isoformat(), "event": "timer", "event_type": t} for t in ("start", "stop")]
    normalized = normalize_samples(rows, "cycling", events=events)
    assert not normalized["complete"]
    assert best_efforts(normalized, workout_id="w", revision_id="r", sport="cycling", durations=(150,)) == []


def test_balance_exact_curve_negative_and_segmentation_invariance():
    rows = [segment(0, 30, 450), segment(30, 60, 150), segment(90, 100, 450)]
    result = cycling_balance(rows, 250, 20000)
    expected = [14000, 20000 - 6000 * math.exp(-0.3), -6000 * math.exp(-0.3)]
    assert [p["balance_joules"] for p in result["curve"]] == pytest.approx(expected)
    assert result["minimum_balance_percent"] < 0
    assert "negative_balance_model_consistency_breach" in result["warnings"]
    assert result["mechanical_work_kj"] == pytest.approx(67.5)
    split = [segment(i, 1, 450 if i < 30 or i >= 90 else 150) for i in range(190)]
    assert cycling_balance(split, 250, 20000)["curve"][-1]["balance_joules"] == pytest.approx(expected[-1])
    assert result["above_threshold_events"][1]["recovery_since_previous_joules"] == pytest.approx(expected[1] - 14000)


def test_unknown_balance_stays_unknown_and_never_becomes_exhaustion_or_fueling_blame():
    result = cycling_balance([segment(0, 10, 400), segment(10, 3, None), segment(13, 10, 400)], 250, 2000)
    assert result["curve"][0]["balance_joules"] == 500
    assert all(p["balance_joules"] is None for p in result["curve"][1:])
    assert result["mechanical_work_kj"] is None
    assert result["fueling_carbs_grams_per_hour"] is None
    assert not any("fuel" in w or "exhaustion" in w for w in result["warnings"])
    assert cycling_balance([segment(0, 1, None), segment(1, 1, 300)], 250, 20000)["minimum_balance_percent"] is None
    with pytest.raises(ValueError):
        cycling_balance([{**segment(0, 1, 300), "duration_seconds": 100}], 250, 20000)


def test_independent_selection_status_and_expiry():
    rows = [effort(t, day=d, cp=250 - d) for d in (1, 2, 3) for t in (150, 300, 600)]
    model = fit_model(rows, sport="cycling", cutoff=T + timedelta(days=10), calculated_at=T + timedelta(days=10))
    assert model["status"] == "supported" and model["independent_effort_count"] == 3
    nested = fit_model([e for e in rows if e["workout_id"] == "w1"], sport="cycling", cutoff=T + timedelta(days=10), calculated_at=T)
    assert nested["status"] == "insufficient_data"
    stale = fit_model(rows, sport="cycling", cutoff=T + timedelta(days=30), calculated_at=T + timedelta(days=30))
    assert stale["status"] == "stale"
    expired = fit_model(rows, sport="cycling", cutoff=T + timedelta(days=50), calculated_at=T + timedelta(days=50))
    assert expired["status"] == "insufficient_data" and expired["critical_power_watts"] is None


def test_exact_cutoff_and_late_knowledge_are_excluded():
    cutoff = T + timedelta(days=10)
    rows = [effort(150, day=1), effort(600, day=2)]
    rows[0]["known_at"] = cutoff.isoformat()
    assert fit_model(rows, sport="cycling", cutoff=cutoff, calculated_at=cutoff)["status"] == "insufficient_data"


def test_durability_requires_comparable_verified_workload_and_never_zero_fallback():
    assert durability([])["observed_decline_percent"] is None
    a = {**effort(300), "mechanical_work_before_kj": 50, "comparison_protocol_id": "test", "comparison_context": "same_track"}
    b = {**effort(300, day=2), "mechanical_work_before_kj": 2050, "comparison_protocol_id": "test", "comparison_context": "same_track", "mean_output": a["mean_output"] * .85}
    result = durability([a, b])
    assert result["status"] == "supported" and result["observed_decline_percent"] == pytest.approx(15)
    b["comparison_context"] = "different_terrain"
    assert durability([a, b])["status"] == "insufficient_evidence"
    assert workload([segment(0, 60, None)], "running")["total_running_stress"] is None
    assert workload([segment(0, 60, 4)], "running", {"critical_speed_mps": 4, "status": "supported",
                    "analysis_mode": "as_known_before_workout", "snapshot_id": "m"})["total_running_stress"] == pytest.approx(100/60)
