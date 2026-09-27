"""Skiba 2015 differential cycling W' balance, exact per constant segment.

Source: doi:10.1007/s00421-014-3050-7. Above CP, dB/dt=-(P-CP).
Below CP, dB/dt=(W'-B)*(CP-P)/W'. At CP balance is unchanged.
The initial condition is full W', an explicit assumption. An unknown interval
makes the subsequent trajectory unknown; there is no invented recovery/restart.
"""
import math

from .physiology_samples import number, timestamp

VERSION = "skiba_2015_differential_exact_v1"


def cycling_balance(segments, cp, w_prime, *, recovery_tau_seconds=None, calibration_id=None):
    if not number(cp) or not number(w_prime):
        raise ValueError("Positive CP and W prime required")
    calibrated = recovery_tau_seconds is not None
    if calibrated and (not number(recovery_tau_seconds) or not calibration_id):
        raise ValueError("Individual recovery needs positive tau and calibration provenance")
    balance, curve, events, current, elapsed, work = float(w_prime), [], [], None, 0.0, 0.0
    minimum, minimum_time, known_seconds, unknown_seconds = balance, 0.0, 0.0, 0.0
    previous_end = None
    warnings = ["initial_full_capacity_assumed"]
    if not calibrated:
        warnings.append("recovery_not_individually_calibrated")
    def finish():
        nonlocal current
        if current:
            current["mean_power_watts"] = current.pop("work_joules") / current["duration_seconds"]
            events.append(current)
            current = None
    for segment in segments:
        dt, power = number(segment["duration_seconds"]), number(segment.get("value"), 2500)
        if not dt or timestamp(segment["start"]) is None or timestamp(segment["end"]) is None:
            raise ValueError("Invalid normalized segment")
        if abs((timestamp(segment["end"]) - timestamp(segment["start"])).total_seconds() - dt) > 1e-6:
            raise ValueError("Segment duration does not match timestamps")
        if previous_end is not None and timestamp(segment["start"]) != previous_end:
            raise ValueError("Segments must explicitly represent every gap")
        previous_end = timestamp(segment["end"])
        before = balance
        if power is None:
            finish()
            balance = None
            unknown_seconds += dt
        else:
            known_seconds += dt
            work += power * dt / 1000
            if balance is not None:
                if power > cp:
                    balance -= (power - cp) * dt
                elif power < cp:
                    tau = recovery_tau_seconds if calibrated else w_prime / (cp - power)
                    balance = w_prime - (w_prime - balance) * math.exp(-dt / tau)
            if power > cp:
                if current is None:
                    prior = events[-1] if events else None
                    prior_end = prior["balance_end_joules"] if prior else w_prime
                    current = {"sequence": len(events) + 1, "start_utc": segment["start"],
                               "start_elapsed_seconds": elapsed, "duration_seconds": 0.0, "work_joules": 0.0,
                               "above_cp_work_joules": 0.0, "balance_start_joules": before,
                               "recovery_since_previous_joules": before - prior_end if before is not None and prior_end is not None else None}
                current["duration_seconds"] += dt
                current["work_joules"] += power * dt
                current["above_cp_work_joules"] += (power - cp) * dt
                current.update(end_utc=segment["end"], end_elapsed_seconds=elapsed + dt, balance_end_joules=balance,
                               modeled_depletion_joules=current["balance_start_joules"] - balance
                               if current["balance_start_joules"] is not None and balance is not None else None)
            else:
                finish()
        elapsed += dt
        if balance is not None and balance < minimum:
            minimum, minimum_time = balance, elapsed
        curve.append({"elapsed_seconds": elapsed, "balance_joules": balance})
    finish()
    if unknown_seconds:
        warnings.append("unknown_gap_invalidates_subsequent_balance")
    if minimum < 0:
        warnings.append("negative_balance_model_consistency_breach")
    if not segments or segments[0].get("value") is None:
        minimum = minimum_time = None
        warnings.append("no_power_samples")
    return {"balance_model_version": VERSION + ("_fixed_calibrated_tau" if calibrated else ""),
            "recovery_tau_seconds": recovery_tau_seconds,
            "recovery_parameter_source": "individually_calibrated" if calibrated else "population_estimate",
            "recovery_calibration_id": calibration_id, "initial_balance_joules": w_prime,
            "status": "complete" if segments and not unknown_seconds else "insufficient_data" if not segments else "partial",
            "minimum_balance_joules": minimum, "minimum_balance_percent": 100 * minimum / w_prime if minimum is not None else None,
            "minimum_balance_elapsed_seconds": minimum_time, "minimum_scope": "known_prefix",
            "mechanical_work_kj": work if segments and not unknown_seconds else None,
            "known_mechanical_work_kj": work if known_seconds else None, "known_output_seconds": known_seconds,
            "unknown_output_seconds": unknown_seconds, "above_threshold_events": events,
            "above_threshold_event_count": len(events), "curve": curve,
            "fueling_carbs_grams_per_hour": None, "warnings": warnings}
