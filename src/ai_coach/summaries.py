"""Deterministic summaries of known imported workouts, never inferred history.

Calendar dates use the stored activity-local date. No timestamp is reinterpreted
using the athlete's current timezone: travel and midnight starts retain their
original day. Time/distance totals cover valid known measurements; contributor
counts describe the coverage of each measurement independently.
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CALCULATION_VERSION = 1
TOTAL_FIELDS = ("distance_m", "moving_time_s", "elapsed_time_s", "elevation_gain_m", "training_load")
PERIODS = frozenset({"day", "week", "month", "rolling7", "rolling28", "custom"})
TOTALS_SCOPE = "known_imported_workouts"


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("Expected an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Expected an ISO calendar date") from None
    if parsed.isoformat() != value:
        raise ValueError("Expected an ISO calendar date")
    return parsed


def period_bounds(day: str, period: str) -> tuple[str, str]:
    """Return inclusive calendar bounds; rolling windows end on ``day``.

    Weeks start Monday. Months and weeks include future calendar dates if day
    falls partway through them; callers must expose their as-of date separately.
    Custom ranges require explicit start/end and have no derived bounds.
    """
    current = _date(day)
    if period == "day":
        start, end = current, current
    elif period == "week":
        start = current - timedelta(days=current.weekday())
        end = start + timedelta(days=6)
    elif period == "month":
        start = current.replace(day=1)
        end = current.replace(day=calendar.monthrange(current.year, current.month)[1])
    elif period in {"rolling7", "rolling28"}:
        start, end = current - timedelta(days=int(period.removeprefix("rolling")) - 1), current
    else:
        raise ValueError("Unsupported derived period")
    return start.isoformat(), end.isoformat()


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return None
    return value if finite and value >= 0 else None


def _sum(values: Iterable[int | float]) -> float | None:
    values = list(values)
    if not values:
        return None
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError):
        return None
    return total if math.isfinite(total) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _metric(workout: Mapping[str, Any], field: str) -> int | float | None:
    section = "analysis" if field == "training_load" else "metrics"
    return _number(_mapping(workout.get(section)).get(field))


def _measurements(workouts: list[Mapping[str, Any]]) -> dict[str, Any]:
    known = {field: [value for workout in workouts
                     if (value := _metric(workout, field)) is not None]
             for field in TOTAL_FIELDS}
    return {
        "workout_count": len(workouts),
        "active_days": len({workout["local_date"] for workout in workouts}),
        "totals": {field: _sum(values) for field, values in known.items()},
        "contributors": {**{field: len(values) for field, values in known.items()},
                         "longest_distance_m": len(known["distance_m"])},
        "longest_distance_m": max(known["distance_m"], default=None),
    }


def _pace(workouts: list[Mapping[str, Any]]) -> tuple[float | None, int]:
    # Use only paired duration+distance. Including duration from an activity
    # missing distance would overstate pace, even if both overall totals exist.
    paired = [(distance, duration) for workout in workouts
              if (distance := _metric(workout, "distance_m")) is not None and distance > 0
              and (duration := _metric(workout, "moving_time_s")) is not None]
    distance = _sum(item[0] for item in paired)
    duration = _sum(item[1] for item in paired)
    pace = duration / distance * 1000 if distance and duration is not None else None
    return (pace if pace is not None and math.isfinite(pace) else None), len(paired)


def _valid_hr_zone(workout: Mapping[str, Any]) -> tuple[Mapping[str, Any], tuple[Any, ...]] | None:
    zone = _mapping(_mapping(workout.get("zone_summary")).get("heart_rate"))
    if zone.get("status") != "available":
        return None
    definition = zone.get("zone_definition_id")
    boundaries = zone.get("boundaries_bpm")
    seconds = zone.get("seconds")
    if (not isinstance(definition, str) or not definition
            or not isinstance(boundaries, list) or not boundaries
            or not isinstance(seconds, list) or len(seconds) != len(boundaries)):
        return None
    if (any(_number(boundary) is None or boundary <= 0 for boundary in boundaries)
            or any(a >= b for a, b in zip(boundaries, boundaries[1:]))
            or any(_number(duration) is None for duration in seconds)):
        return None
    total = _sum(seconds)
    reported = _number(zone.get("classified_seconds"))
    if (total is None or reported is None
            or not math.isclose(total, reported, rel_tol=1e-9, abs_tol=1e-6)):
        return None
    # IDs are normally computed from boundaries. Including boundaries in the key
    # also fails safely if a malformed record reuses an ID with different bins.
    return zone, (definition, tuple(boundaries))


def _heart_rate_zones(workouts: list[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    missing = 0
    for workout in workouts:
        valid = _valid_hr_zone(workout)
        if valid is None:
            missing += 1
        else:
            zone, key = valid
            grouped.setdefault(key, []).append(zone)
    result = []
    for (definition, boundaries), zones in sorted(grouped.items()):
        seconds = [_sum(zone["seconds"][index] for zone in zones) for index in range(len(boundaries))]
        classified = _sum(seconds) if all(value is not None for value in seconds) else None
        result.append({
            "zone_definition_id": definition,
            "boundaries_bpm": list(boundaries),
            "boundary_semantics": "inclusive_upper_bpm",
            "percentage_basis": "classified_seconds",
            "seconds": seconds,
            "percentages": [duration / classified * 100 if classified else None for duration in seconds],
            "classified_seconds": classified,
            "workout_count": len(zones),
            "sources": sorted({zone["source"] for zone in zones if isinstance(zone.get("source"), str)}),
        })
    return {
        "groups": result,
        "contributing_workout_count": len(workouts) - missing,
        "missing_workout_count": missing,
        "coverage_status": "unknown_provider_duration_basis",
        "unclassified_seconds": None,
        "duration_denominator_seconds": None,
    }


def summarize_workouts(
    workouts: Iterable[Mapping[str, Any]], *, start: str, end: str, period: str, timezone: str,
) -> dict[str, Any]:
    """Summarize unique, active workouts within inclusive stored local dates.

    Last occurrence of an ID wins, including moves outside the window and
    deletion markers. Deduplicate before date filtering so old snapshots cannot
    reappear. Malformed rows are ignored and counted rather than fabricated.
    Output has no caps: the persisted aggregate must include every sport and
    zone definition; the compact response layer can limit displayed groups with
    explicit truncation information.
    """
    first, last = _date(start), _date(end)
    if last < first:
        raise ValueError("Summary end date precedes start date")
    if period not in PERIODS:
        raise ValueError("Unsupported summary period")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError("Invalid summary timezone") from None
    if isinstance(workouts, (str, bytes, Mapping)):
        raise ValueError("Expected an iterable of workout documents")

    unique = {}
    invalid = 0
    duplicates = 0
    for workout in workouts:
        if not isinstance(workout, Mapping) or not isinstance(workout.get("id"), str) or not workout["id"]:
            invalid += 1
            continue
        if workout["id"] in unique:
            duplicates += 1
        unique[workout["id"]] = workout

    selected = []
    for workout in unique.values():
        if workout.get("source_deleted") is True or workout.get("source_excluded") is True:
            continue
        try:
            local_day = _date(workout.get("local_date"))
        except ValueError:
            invalid += 1
            continue
        if first <= local_day <= last:
            selected.append(workout)
    sports: dict[str, list[Mapping[str, Any]]] = {}
    for workout in selected:
        sport = workout.get("sport")
        sport = sport.strip() if isinstance(sport, str) and sport.strip() else "Unknown"
        sports.setdefault(sport, []).append(workout)

    by_sport = []
    for sport, records in sorted(sports.items()):
        measurements = _measurements(records)
        pace, pace_contributors = _pace(records)
        measurements["contributors"]["pace_s_per_km"] = pace_contributors
        by_sport.append({"sport": sport, **measurements,
                         "pace_s_per_km": pace,
                         "heart_rate_zones": _heart_rate_zones(records)})
    return {
        "start_date": start,
        "end_date": end,
        "period": period,
        "timezone": timezone,
        "calculation_version": CALCULATION_VERSION,
        "totals_scope": TOTALS_SCOPE,
        "history_complete": False,
        "calendar_days": (last - first).days + 1,
        **_measurements(selected),
        "by_sport": by_sport,
        "ignored_malformed_records": invalid,
        "duplicate_records_removed": duplicates,
    }


def _changes(current: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for field in TOTAL_FIELDS:
        now = _number(_mapping(current.get("totals")).get(field))
        before = _number(_mapping(previous.get("totals")).get(field))
        absolute = now - before if now is not None and before is not None else None
        percent = absolute / before * 100 if absolute is not None and before else None
        result[field] = {
            "current": now, "previous": before,
            "absolute_change": absolute if absolute is not None and math.isfinite(absolute) else None,
            "percent_change": percent if percent is not None and math.isfinite(percent) else None,
        }
    return result


def compare_summaries(current: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    """Compare known totals over matching day counts, with no fitness inference.

    A missing previous metric stays unknown, and a known zero baseline has no
    percentage change. Empty imports never establish that no training occurred.
    Caller supplies equal-length windows (e.g. Monday-to-Tuesday this week and
    Monday-to-Tuesday last week) rather than comparing partial/full periods.
    """
    result: dict[str, Any] = {
        "calculation_version": CALCULATION_VERSION,
        "totals_scope": TOTALS_SCOPE,
        "history_complete": False,
        "comparable": False,
        "reason": None,
        "current_start_date": current.get("start_date") if isinstance(current.get("start_date"), str) else None,
        "current_end_date": current.get("end_date") if isinstance(current.get("end_date"), str) else None,
        "previous_start_date": previous.get("start_date") if isinstance(previous.get("start_date"), str) else None,
        "previous_end_date": previous.get("end_date") if isinstance(previous.get("end_date"), str) else None,
        "changes": None,
        "by_sport": [],
    }
    try:
        current_days = (_date(current.get("end_date")) - _date(current.get("start_date"))).days + 1
        previous_days = (_date(previous.get("end_date")) - _date(previous.get("start_date"))).days + 1
    except ValueError:
        result["reason"] = "invalid_dates"
        return result
    if current_days < 1 or previous_days < 1:
        result["reason"] = "invalid_dates"
        return result
    if current_days != previous_days:
        result["reason"] = "unequal_window_lengths"
        return result
    if (current.get("calculation_version") != previous.get("calculation_version")
            or type(current.get("calculation_version")) is not int):
        result["reason"] = "incompatible_calculation_versions"
        return result
    if current.get("timezone") != previous.get("timezone") or not current.get("timezone"):
        result["reason"] = "incompatible_timezones"
        return result
    result.update({"comparable": True, "calendar_days": current_days,
                   "changes": _changes(current, previous)})
    current_sports = {row["sport"]: row for row in (current.get("by_sport") or [])
                      if isinstance(row, Mapping) and isinstance(row.get("sport"), str)}
    previous_sports = {row["sport"]: row for row in (previous.get("by_sport") or [])
                       if isinstance(row, Mapping) and isinstance(row.get("sport"), str)}
    result["by_sport"] = [{"sport": sport, "changes": _changes(current_sports.get(sport, {}),
                                                              previous_sports.get(sport, {}))}
                          for sport in sorted(current_sports.keys() | previous_sports.keys())]
    return result
