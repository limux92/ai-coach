"""Time-weighted native FIT measurements. Gaps never imply zero or recovery."""
from __future__ import annotations

from bisect import bisect_right
from datetime import UTC, datetime, timedelta
import math

VERSION = "native_step_v1"
DURATIONS = (150, 180, 300, 420, 600, 720, 840)


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if isinstance(result, datetime) and result.utcoffset() is not None:
            return result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def number(value, maximum=1e9):
    return float(value) if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value) else None


def sport_kind(sport):
    if sport in {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "cycling"}:
        return "cycling"
    if sport in {"Run", "VirtualRun", "TrailRun", "running"}:
        return "running"
    return None


def _native(record, key):
    # A developer field may share a native FIT field name. Never silently use it.
    if "_fields" not in record:
        return record.get(key)
    fields = [f for f in record["_fields"] if f.get("name") == key and f.get("developer_data_index") is None]
    return fields[0].get("value") if len(fields) == 1 else None


def normalize_samples(records, sport, *, events=(), max_gap_seconds=5):
    """Left-held values on [t_i,t_next), no extrapolated final sample.

    Invalid ordering is rejected, identical duplicates collapse, conflicting
    duplicates become unknown. Timer pauses remain unknown even with zero speed.
    Values >2500W/>15m/s are excluded, not clipped. These are quality guardrails,
    not physiological limits or a claim that lesser spikes are always credible.
    """
    if sport not in {"cycling", "running"} or not 0 < max_gap_seconds <= 30:
        raise ValueError("Invalid normalization policy")
    points, flags = [], set()
    if len({r.get("_file_index", 0) for r in records}) > 1:
        return {"version": VERSION, "segments": [], "flags": ["multiple_fit_files"], "complete": False}
    for record in records:
        time = timestamp(_native(record, "timestamp"))
        if time is None or (points and time < points[-1][0]):
            return {"version": VERSION, "segments": [], "flags": ["invalid_timestamp_order"], "complete": False}
        raw = _native(record, "power") if sport == "cycling" else _native(record, "enhanced_speed")
        if sport == "running" and raw is None:
            raw = _native(record, "speed")
        value = number(raw, 2500 if sport == "cycling" else 15)
        quality = [] if value is not None else ["missing_output" if raw is None else "invalid_or_implausible_output"]
        if points and time == points[-1][0]:
            flags.add("duplicate_timestamp")
            if value != points[-1][1]:
                points[-1] = (time, None, ["conflicting_duplicate"])
            continue
        points.append((time, value, quality))
    timer = []
    for event in events:
        if _native(event, "event") == "timer":
            time = timestamp(_native(event, "timestamp"))
            kind = _native(event, "event_type")
            if time is None or kind not in {"start", "stop", "stop_all", "stop_disable", "stop_disable_all"}:
                return {"version": VERSION, "segments": [], "flags": ["invalid_timer_event"], "complete": False}
            timer.append((time, kind == "start"))
    timer.sort(key=lambda item: item[0])
    segments, timer_index, active = [], 0, True
    for (start, value, quality), (end, _, _) in zip(points, points[1:]):
        while timer_index < len(timer) and timer[timer_index][0] <= start:
            active = timer[timer_index][1]
            timer_index += 1
        interval_flags = list(quality)
        if (end - start).total_seconds() > max_gap_seconds:
            interval_flags.append("unknown_gap")
        if not active or (timer_index < len(timer) and timer[timer_index][0] < end):
            interval_flags.append("timer_pause_or_transition")
        dt = (end - start).total_seconds()
        if dt != 1:
            flags.add("irregular_recording")
        flags.update(interval_flags)
        segments.append({"start": start.isoformat(), "end": end.isoformat(), "duration_seconds": dt,
                         "value": None if interval_flags else value, "flags": interval_flags})
    return {"version": VERSION, "segments": segments, "flags": sorted(flags),
            "complete": bool(segments) and all(s["value"] is not None for s in segments)}


def _best_run(run, duration):
    boundaries, area = [0.0], [0.0]
    for segment in run:
        boundaries.append(boundaries[-1] + segment["duration_seconds"])
        area.append(area[-1] + segment["duration_seconds"] * segment["value"])
    total = boundaries[-1]
    if total < duration:
        return None
    def integral(t):
        index = min(bisect_right(boundaries, t) - 1, len(run) - 1)
        return area[index] + (t - boundaries[index]) * run[index]["value"]
    # Sliding integrals are piecewise linear: a maximum touches an input boundary
    # at either end. Evaluating both sets is exact even for irregular intervals.
    candidates = {0.0, total - duration}
    candidates.update(t for t in boundaries if 0 <= t <= total - duration)
    candidates.update(t - duration for t in boundaries if duration <= t <= total)
    best = max(sorted(candidates), key=lambda t: integral(t + duration) - integral(t))
    start = timestamp(run[0]["start"]) + timedelta(seconds=best)
    return {"start_utc": start.isoformat(), "end_utc": (start + timedelta(seconds=duration)).isoformat(),
            "duration_seconds": duration, "mean_output": (integral(best + duration) - integral(best)) / duration}


def best_efforts(normalized, *, workout_id, revision_id, sport, durations=DURATIONS):
    runs, current = [], []
    for segment in normalized["segments"]:
        if segment["value"] is None:
            if current:
                runs.append(current)
            current = []
        else:
            current.append(segment)
    if current:
        runs.append(current)
    result = []
    for duration in durations:
        if not 120 < duration < 900:
            raise ValueError("Duration outside model range")
        candidates = [candidate for run in runs if (candidate := _best_run(run, duration))]
        if not candidates:
            continue
        best = max(candidates, key=lambda effort: effort["mean_output"])
        before = [s for s in normalized["segments"] if s["start"] < best["start_utc"]]
        work = 0.0
        for s in before:
            if s["value"] is None:
                work = None
                break
            dt = min(timestamp(s["end"]), timestamp(best["start_utc"])) - timestamp(s["start"])
            work += s["value"] * dt.total_seconds() / 1000
        first = timestamp(normalized["segments"][0]["start"])
        best.update(workout_id=workout_id, revision_id=revision_id, sport=sport,
                    output_unit="watts" if sport == "cycling" else "speed_mps",
                    recording_quality_flags=normalized["flags"], maximal_intent="unknown",
                    elapsed_before_seconds=(timestamp(best["start_utc"]) - first).total_seconds(),
                    mechanical_work_before_kj=work if sport == "cycling" else None,
                    mechanical_work_before_kj_per_kg=None)
        result.append(best)
    return result
