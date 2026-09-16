"""Bounded, read-only dashboard projections of the existing private archive.

The archive remains authoritative. These projections do not infer missing
measurements, change local dates, or combine different historical HR zones.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Callable, Mapping

from fastapi import HTTPException


MAX_RESPONSE_BYTES = 58_000
MAX_DAYS = 366
MAX_LAPS = 50

METRICS = (
    "distance_m", "recorded_distance_m", "moving_time_s", "elapsed_time_s",
    "elevation_gain_m", "average_speed_mps", "max_speed_mps",
    "average_heart_rate_bpm", "max_heart_rate_bpm", "average_cadence_per_minute",
    "average_power_w", "max_power_w", "energy_kcal",
)
ANALYSIS = (
    "training_load", "intensity_percent", "weighted_average_power_w", "ctl", "atl",
    "efficiency_factor", "decoupling_percent", "compliance_percent", "session_rpe_load",
)
OBSERVATIONS = ("rpe", "feel", "perceived_exertion")
LAP_NUMBERS = (
    "total_elapsed_time", "total_timer_time", "total_distance", "avg_speed",
    "max_speed", "enhanced_avg_speed", "enhanced_max_speed", "avg_heart_rate",
    "max_heart_rate", "avg_cadence", "max_cadence", "avg_power", "max_power",
    "total_ascent", "total_descent",
)
LAP_TEXT = ("timestamp", "start_time", "lap_trigger", "sport", "intensity")


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, limit: int = 160) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _number(value: Any) -> int | float | None:
    if type(value) not in (int, float):
        return None
    if isinstance(value, int) and value.bit_length() > 64:
        return None
    return value if math.isfinite(value) else None


def _numbers(value: Any, fields: tuple[str, ...]) -> dict[str, int | float | None]:
    obj = _object(value)
    return {key: _number(obj.get(key)) for key in fields}


def _encoded_size(value: Any) -> int:
    # Match the compact UTF-8 JSON emitted by FastAPI/Starlette.
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8"))


def zone_projection(value: Any) -> dict[str, Any] | None:
    """Keep the normalized definition and duration denominator together."""
    obj = _object(value)
    if not obj:
        return None
    hr = _object(obj.get("heart_rate"))
    if not hr:
        return None
    result = {key: _text(hr.get(key), 100) for key in (
        "status", "source", "basis", "zone_definition_id", "boundary_semantics", "reason",
    )}
    for key in ("boundaries_bpm", "seconds", "percentages"):
        values = hr.get(key)
        if not isinstance(values, list) or len(values) > 32:
            # A partial array could look valid while changing the definition.
            return {"calculation_version": _number(obj.get("calculation_version")),
                    "heart_rate": {"status": "invalid", "reason": "dashboard_projection_limits"}}
        result[key] = [_number(item) for item in values]
    result["classified_seconds"] = _number(hr.get("classified_seconds"))
    coverage = _object(hr.get("coverage"))
    result["coverage"] = {
        "status": _text(coverage.get("status"), 100),
        **_numbers(coverage, ("denominator_seconds", "classified_fraction", "unclassified_seconds")),
    }
    source_fields = _object(hr.get("source_fields"))
    result["source_fields"] = {key: _text(source_fields.get(key), 100)
                               for key in ("boundaries", "seconds")}
    return {"calculation_version": _number(obj.get("calculation_version")), "heart_rate": result}


def _sample_availability(doc: Mapping[str, Any]) -> str:
    if doc.get("parse_status") == "parsed" and doc.get("parsed_artifact"):
        return "available"
    if doc.get("original_artifact"):
        return "original_only"
    return "unavailable"


def workout_projection(doc: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _text(doc.get(key), limit) for key, limit in (
        ("id", 180), ("source_id", 100), ("local_date", 10),
        ("start_date_local", 40), ("start_date_utc", 40), ("timezone", 80),
        ("sport", 40), ("name", 160), ("provider_source", 40),
        ("recording_platform", 40), ("distance_type", 40),
        ("source_attribution", 160), ("garmin_attribution", 160), ("parse_status", 40),
    )}
    result.update({
        "sample_availability": _sample_availability(doc),
        "metrics": _numbers(doc.get("metrics"), METRICS),
        "analysis": _numbers(doc.get("analysis"), ANALYSIS),
        "observations": _numbers(doc.get("observations"), OBSERVATIONS),
        "zone_summary": zone_projection(doc.get("zone_summary")),
    })
    return result


def plan_projection(doc: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: _text(doc.get(key), limit) for key, limit in (
        ("id", 180), ("source_id", 100), ("local_date", 10),
        ("start_date_local", 40), ("timezone", 80), ("sport", 40), ("name", 160),
        ("status", 40), ("completed_workout_id", 180),
    )}
    result["metrics"] = _numbers(doc.get("metrics"), (
        "duration_s", "distance_m", "training_load", "intensity_percent",
    ))
    return result


def bounded_page(page: Mapping[str, Any], projection: Callable) -> dict[str, Any]:
    """Honor an item limit and byte limit without losing the next visible row.

    The caller obtains rows through the existing tombstone-aware list function.
    When the byte budget shortens that page, its final returned row becomes the
    cursor, so all omitted rows remain reachable on the next request.
    """
    result: dict[str, Any] = {"items": [], "next_cursor": page.get("next_cursor")}
    for row in page["items"]:
        item = projection(row)
        candidate = {"items": [*result["items"], item], "next_cursor": row["id"]}
        if _encoded_size(candidate) > MAX_RESPONSE_BYTES:
            if not result["items"]:
                # Projection bounds make this unreachable for stored records.
                raise HTTPException(500, "Dashboard record exceeds response budget")
            result["next_cursor"] = result["items"][-1]["id"]
            break
        result["items"].append(item)
    return result


def validate_window(oldest: date, newest: date) -> None:
    if newest < oldest:
        raise HTTPException(422, "newest must be on or after oldest")
    if (newest - oldest).days >= MAX_DAYS:
        raise HTTPException(422, "Dashboard range must contain at most 366 calendar days")


def workout_detail(doc: Mapping[str, Any]) -> dict[str, Any]:
    result = workout_projection(doc)
    description = _text(doc.get("description"), 4_000)
    stored_laps = doc.get("laps_summary")
    stored_laps = stored_laps if isinstance(stored_laps, list) else []
    lap_count = _number(doc.get("lap_count"))
    result.update({
        "description": description,
        "description_truncated": isinstance(doc.get("description"), str)
                                 and len(doc["description"]) > 4_000,
        "device_name": _text(doc.get("device_name"), 160),
        "source_availability": {
            "summary": True,
            "samples": result["sample_availability"] == "available",
            "original_archived": bool(doc.get("original_artifact")),
        },
        "record_count": _number(doc.get("record_count")),
        "lap_count": lap_count,
        "laps_summary": [],
        "laps_summary_truncated": False,
        "laps_omitted": None,
    })
    for lap in stored_laps[:MAX_LAPS]:
        lap = _object(lap)
        item = {key: _number(lap[key]) for key in LAP_NUMBERS if key in lap}
        item.update({key: _text(lap[key], 60) for key in LAP_TEXT if key in lap})
        result["laps_summary"].append(item)
        if _encoded_size(result) > MAX_RESPONSE_BYTES - 128:
            result["laps_summary"].pop()
            break
    shown = len(result["laps_summary"])
    known_count = max(len(stored_laps), lap_count if type(lap_count) is int and lap_count >= 0 else 0)
    result["laps_summary_truncated"] = bool(doc.get("laps_summary_truncated")) or shown < known_count
    if lap_count is not None and lap_count >= shown:
        result["laps_omitted"] = lap_count - shown
    return result
