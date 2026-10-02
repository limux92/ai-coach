"""Versioned projections for Firestore; archive full source JSON separately.

Stored names include units. Vendor analysis, measured summaries and subjective
observations are separate. No timestamps without an offset are treated as UTC.
Functions return complete source-owned projections, suitable for replacement;
merging blindly would retain upstream-cleared fields. Keep locally authored
notes and status overrides in a separate document or explicit overlay.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, date, datetime
from typing import Any, Mapping

from .zones import build_zone_summary


SCHEMA_VERSION = 2
MAX_INLINE_STRUCTURE_BYTES = 256 * 1024


class NormalizationError(ValueError):
    """Payload validation failure; message contains no source data."""


def serialize_payload(payload: Mapping[str, Any]) -> bytes:
    """Canonical source JSON for immutable raw storage and change detection."""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise NormalizationError("Source payload is not valid JSON") from None


def source_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(serialize_payload(payload)).hexdigest()


def source_document_id(source_id: str | int) -> str:
    value = str(source_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise NormalizationError("Invalid source record id")
    return f"intervals_{value}"


def is_direct_garmin(payload: Mapping[str, Any]) -> bool:
    """Fail closed for unknown origins. strava_id alone does not define origin."""
    return payload.get("source") == "GARMIN_CONNECT" and not payload.get("strava_only", False)


FIT_MANUFACTURERS = {"garmin": 1, "zwift": 260}


def fit_upload_manufacturer(payload: Mapping[str, Any]) -> str | None:
    """An upload's device label selects inspection, never proves provenance."""
    device, file_type = payload.get("device_name"), payload.get("file_type")
    if (payload.get("source") != "UPLOAD" or payload.get("strava_only", False)
            or not isinstance(file_type, str) or file_type.casefold() != "fit"
            or not isinstance(device, str)):
        return None
    match = re.match(r"^(garmin|zwift)(?:\s|$)", device.strip(), re.I)
    return match.group(1).casefold() if match else None


def is_garmin_upload_candidate(payload: Mapping[str, Any]) -> bool:
    return fit_upload_manufacturer(payload) == "garmin"


def is_verified_garmin_fit(metadata: Mapping[str, Any]) -> bool:
    return is_verified_activity_fit(metadata, "garmin")


def is_verified_activity_fit(metadata: Mapping[str, Any], manufacturer: str) -> bool:
    """Require matching native activity file_id fields in every decoded FIT file.

    These are recorded provenance fields, not a cryptographic authenticity claim.
    Accessory device_info and developer fields cannot substitute for file_id.
    """
    if manufacturer not in FIT_MANUFACTURERS:
        return False
    count = metadata.get("fit_file_count")
    if (metadata.get("format") != "FIT" or metadata.get("crc_verified") is not True
            or type(count) is not int or count < 1):
        return False
    messages = metadata.get("other_messages", {}).get("file_id", [])
    if not isinstance(messages, list) or not messages:
        return False
    verified_indices = set()
    for message in messages:
        if not isinstance(message, dict):
            return False
        index = message.get("_file_index")
        if type(index) is not int or not 0 <= index < count:
            return False
        fields = message.get("_fields", [])
        if not isinstance(fields, list):
            return False
        for definition, expected_raw in ((0, 4), (1, FIT_MANUFACTURERS[manufacturer])):
            # FIT file_id: native type=activity (4), matching manufacturer.
            matches = [f for f in fields if isinstance(f, dict)
                       and f.get("definition_number") == definition
                       and f.get("developer_data_index") is None]
            if (len(matches) != 1 or type(matches[0].get("raw_value")) is not int
                    or matches[0]["raw_value"] != expected_raw):
                return False
        verified_indices.add(index)
    return verified_indices == set(range(count))


def _source_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("id")
    if value is None or isinstance(value, bool):
        raise NormalizationError("Source record id is required")
    source_document_id(value)
    return str(value)


def _utc(value: Any, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, (str, datetime)):
        raise NormalizationError("Expected an offset-aware timestamp")
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except ValueError:
        raise NormalizationError("Invalid timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NormalizationError("Expected an offset-aware timestamp")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _local_start(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise NormalizationError("Local start date is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise NormalizationError("Invalid local start date") from None
    # Preserve upstream local wall-clock representation, including any supplied
    # offset. Do not invent a timezone or silently shift planned workouts.
    return value, parsed.date().isoformat()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _numbers(payload: Mapping[str, Any], fields: Mapping[str, str]) -> dict[str, int | float | None]:
    return {target: _number(payload.get(source)) for source, target in fields.items()}


def _base(payload: Mapping[str, Any], athlete_id: str, fetched_at: datetime | str | None) -> dict[str, Any]:
    source_id = _source_id(payload)
    # Explicit None fields clear old projections when values disappear upstream.
    return {
        "schema_version": SCHEMA_VERSION,
        "id": source_document_id(source_id),
        "source": "intervals.icu",
        "source_id": source_id,
        "source_athlete_id": str(athlete_id),
        "source_payload_sha256": source_hash(payload),
        "source_updated_at": _text(payload.get("updated")),
        "fetched_at": _utc(fetched_at or datetime.now(UTC), required=True),
    }


def normalize_activity(
    payload: Mapping[str, Any], *, athlete_id: str, fetched_at: datetime | str | None = None,
    upload_fit_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    direct = is_direct_garmin(payload)
    manufacturer = fit_upload_manufacturer(payload)
    verified_upload = (manufacturer is not None and upload_fit_metadata is not None
                       and is_verified_activity_fit(upload_fit_metadata, manufacturer))
    if not direct and not verified_upload:
        raise NormalizationError("Activity is not from a permitted, verified Garmin or Zwift source")
    doc = _base(payload, athlete_id, fetched_at)
    start, local_day = _local_start(payload.get("start_date_local"))
    paired_id = payload.get("paired_event_id")
    metrics = _numbers(payload, {
        "distance": "recorded_distance_m", "moving_time": "moving_time_s",
        "elapsed_time": "elapsed_time_s", "total_elevation_gain": "elevation_gain_m",
        "average_speed": "average_speed_mps", "max_speed": "max_speed_mps",
        "average_heartrate": "average_heart_rate_bpm", "max_heartrate": "max_heart_rate_bpm",
        "average_cadence": "average_cadence_per_minute", "icu_average_watts": "average_power_w",
        "max_watts": "max_power_w", "calories": "energy_kcal",
    })
    metrics["distance_m"] = _number(payload.get("icu_distance"))
    if metrics["distance_m"] is None:
        metrics["distance_m"] = metrics["recorded_distance_m"]
    doc.update({
        "provider_source": "GARMIN_CONNECT" if direct else "UPLOAD",
        "garmin_attribution": ("Garmin device data via Intervals.icu" if direct else
                               "Garmin device data, manually uploaded to Intervals.icu")
                              if direct or manufacturer == "garmin" else None,
        "device_name": _text(payload.get("device_name")),
        "start_date_local": start,
        "local_date": local_day,
        "start_date_utc": _utc(payload.get("start_date")),
        "timezone": _text(payload.get("timezone")),
        "sport": _text(payload.get("type")),
        "name": _text(payload.get("name")),
        "description": _text(payload.get("description")),
        "file_type": _text(payload.get("file_type")),
        "paired_planned_workout_id": source_document_id(paired_id) if paired_id is not None else None,
        "metrics": metrics,
        "zone_summary": build_zone_summary(payload),
        "analysis": _numbers(payload, {
            "icu_training_load": "training_load", "icu_intensity": "intensity_percent",
            "icu_weighted_avg_watts": "weighted_average_power_w", "icu_ctl": "ctl",
            "icu_atl": "atl", "icu_efficiency_factor": "efficiency_factor",
            "decoupling": "decoupling_percent", "compliance": "compliance_percent",
            "session_rpe": "session_rpe_load", "icu_ftp": "ftp_w",
            "icu_pm_cp": "model_cp_w", "icu_pm_w_prime": "model_w_prime_j",
        }),
        "observations": _numbers(payload, {"icu_rpe": "rpe", "feel": "feel",
                                               "perceived_exertion": "perceived_exertion"}),
        "stream_types": [x for x in payload.get("stream_types", []) if isinstance(x, str)]
                        if isinstance(payload.get("stream_types"), list) else [],
    })
    if verified_upload:
        doc.update({"import_method": "manual_upload",
                    "source_verification": {"method": "native_fit_file_id", "status": "verified",
                                            "manufacturer": manufacturer, "file_type": "activity"}})
    if manufacturer == "zwift":
        doc.update({"recording_platform": "zwift", "distance_type": "virtual",
                    "source_attribution": "Zwift virtual activity, uploaded to Intervals.icu"})
    return doc


def normalize_calendar_event(
    payload: Mapping[str, Any], *, athlete_id: str, fetched_at: datetime | str | None = None
) -> dict[str, Any]:
    """Preserve NOTE, RACE, etc. as calendar events, never pretend they are workouts."""
    doc = _base(payload, athlete_id, fetched_at)
    start, local_day = _local_start(payload.get("start_date_local"))
    doc.update({
        "start_date_local": start,
        "local_date": local_day,
        "end_date_local": _text(payload.get("end_date_local")),
        "category": _text(payload.get("category")),
        "sport": _text(payload.get("type")),
        "name": _text(payload.get("name")),
        "description": _text(payload.get("description")),
        "source_calendar_id": payload.get("calendar_id"),
        "source_external_id": _text(payload.get("external_id")),
    })
    return doc


def normalize_planned_workout(
    payload: Mapping[str, Any], *, athlete_id: str, fetched_at: datetime | str | None = None
) -> dict[str, Any] | None:
    """Return None for non-WORKOUT categories; archive those as calendar events.

    A past date is not proof of completion. Root may link completed activities via
    paired_planned_workout_id, or keep a local status overlay. Re-imported source
    data must not erase locally authored status/notes.
    """
    if payload.get("category") != "WORKOUT":
        return None
    doc = normalize_calendar_event(payload, athlete_id=athlete_id, fetched_at=fetched_at)
    structure = payload.get("workout_doc")
    structure = structure if isinstance(structure, dict) else None
    structure_bytes = serialize_payload(structure) if structure is not None else None
    inline = structure_bytes is not None and len(structure_bytes) <= MAX_INLINE_STRUCTURE_BYTES
    metrics = _numbers(payload, {"moving_time": "duration_s", "distance": "distance_m",
                                 "icu_training_load": "training_load",
                                 "icu_intensity": "intensity_percent"})
    if structure:
        if metrics["duration_s"] is None:
            metrics["duration_s"] = _number(structure.get("duration"))
        if metrics["distance_m"] is None:
            metrics["distance_m"] = _number(structure.get("distance"))
    doc.update({
        "status": "planned",
        "metrics": metrics,
        "target_type": _text(structure.get("target")) if structure else None,
        "structured_workout_json": structure_bytes.decode("utf-8") if inline else None,
        "structure_stored_in_raw_payload": structure_bytes is not None and not inline,
        "source_workout_id": payload.get("workout_id"),
    })
    return doc


def normalize_wellness(
    payload: Mapping[str, Any], *, athlete_id: str, fetched_at: datetime | str | None = None
) -> dict[str, Any]:
    source_id = _source_id(payload)
    try:
        day = date.fromisoformat(source_id)
    except ValueError:
        raise NormalizationError("Wellness id must be a local ISO date") from None
    doc = _base(payload, athlete_id, fetched_at)
    doc.update({
        "id": day.isoformat(),
        "local_date": day.isoformat(),
        # Intervals can merge providers/custom/user entries. Do not falsely
        # attribute every daily field to Garmin.
        "provider_source": "INTERVALS_MERGED_WELLNESS",
        "metrics": _numbers(payload, {
            "weight": "weight_kg", "restingHR": "resting_heart_rate_bpm",
            "hrv": "hrv_rmssd_ms", "hrvSDNN": "hrv_sdnn_ms",
            "sleepSecs": "sleep_duration_s", "sleepScore": "sleep_score",
            "avgSleepingHR": "average_sleeping_heart_rate_bpm", "steps": "steps",
            "spO2": "oxygen_saturation_percent", "respiration": "respiration_rate",
            "systolic": "systolic_blood_pressure_mmhg", "diastolic": "diastolic_blood_pressure_mmhg",
            "bodyFat": "body_fat_percent", "vo2max": "vo2max_ml_kg_min",
        }),
        "analysis": _numbers(payload, {"ctl": "ctl", "atl": "atl", "rampRate": "ramp_rate",
                                           "readiness": "readiness"}),
        "observations": _numbers(payload, {
            "soreness": "soreness", "fatigue": "fatigue", "stress": "stress", "mood": "mood",
            "motivation": "motivation", "injury": "injury", "sleepQuality": "sleep_quality",
            "lactate": "lactate_mmol_l",
        }),
        "comments": _text(payload.get("comments")),
        "weight_is_temporary": payload.get("tempWeight") is True,
        "resting_heart_rate_is_temporary": payload.get("tempRestingHR") is True,
    })
    return doc
