"""Small, versioned zone projections from activity-local provider calculations.

Intervals.icu documents ``icu_hr_zones`` as each zone's maximum BPM and
``icu_hr_zone_times`` as seconds in that zone. The provider does not document a
matching duration denominator here. Do not turn elapsed/moving time or a count
of FIT records into HR coverage, or apply today's athlete settings to old files.
The immutable raw source remains the full historical calculation snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


CALCULATION_VERSION = 1
MAX_ZONES = 32
MAX_PROVIDER_INTEGER = 2**31 - 1


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Avoid converting an arbitrarily large Python integer to a float.
    if not 0 <= value <= MAX_PROVIDER_INTEGER:
        return None
    if not math.isfinite(value):
        return None
    return int(value) if value == int(value) else value


def _boundaries(value: Any) -> list[int] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ZONES:
        return None
    result = [_number(item) for item in value]
    if any(type(item) is not int or item <= 0 for item in result):
        return None
    if any(left >= right for left, right in zip(result, result[1:])):
        return None
    return result


def _definition_id(boundaries: list[int]) -> str:
    # Identity describes physical bins, not mutable athlete-profile settings.
    definition = {"source": "intervals.icu", "metric": "heart_rate",
                  "boundary_semantics": "inclusive_upper_bpm",
                  "boundaries_bpm": boundaries}
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_zone_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize HR zones without guessing missing data or recomputing time.

    ``available`` means the provider's arrays are valid and aligned. It does
    not claim complete HR coverage. Percentages use only classified seconds;
    when that denominator is zero each percentage is null. The returned lists
    are independent copies, so subsequent source edits cannot mutate a saved
    projection in memory.
    """
    boundaries = _boundaries(payload.get("icu_hr_zones"))
    summary: dict[str, Any] = {
        "status": "missing",
        "source": "intervals.icu",
        "basis": "provider_zone_times",
        "zone_definition_id": _definition_id(boundaries) if boundaries else None,
        "boundaries_bpm": boundaries or [],
        "boundary_semantics": "inclusive_upper_bpm",
        "seconds": [],
        "percentages": [],
        "classified_seconds": None,
        "coverage": {"status": "unknown_provider_duration_basis",
                     "denominator_seconds": None, "classified_fraction": None,
                     "unclassified_seconds": None},
        "source_fields": {"boundaries": "icu_hr_zones", "seconds": "icu_hr_zone_times"},
        "reason": None,
    }
    result = {"calculation_version": CALCULATION_VERSION, "heart_rate": summary}
    if payload.get("icu_ignore_hr") is True:
        summary.update(status="ignored", reason="provider_ignored_heart_rate")
        summary["coverage"]["status"] = "not_applicable"
        return result
    if payload.get("icu_ignore_hr") is not None and type(payload["icu_ignore_hr"]) is not bool:
        summary.update(status="invalid", reason="invalid_ignore_heart_rate_flag")
        return result
    raw_boundaries = payload.get("icu_hr_zones")
    raw_seconds = payload.get("icu_hr_zone_times")
    if raw_boundaries is None and raw_seconds is None:
        summary["reason"] = "zone_data_missing"
        return result
    if raw_boundaries is None:
        summary["reason"] = "zone_boundaries_missing"
        return result
    if boundaries is None:
        summary.update(status="invalid", reason="invalid_zone_boundaries")
        return result
    if raw_seconds is None:
        summary["reason"] = "zone_times_missing"
        return result
    if not isinstance(raw_seconds, list) or len(raw_seconds) != len(boundaries):
        summary.update(status="invalid", reason="zone_times_length_mismatch")
        return result
    seconds = [_number(item) for item in raw_seconds]
    if any(item is None for item in seconds):
        summary.update(status="invalid", reason="invalid_zone_times")
        return result
    classified = sum(seconds)
    summary.update(status="available", seconds=seconds, classified_seconds=classified,
                   percentages=[round(item / classified * 100, 6) if classified else None
                                for item in seconds])
    return result
