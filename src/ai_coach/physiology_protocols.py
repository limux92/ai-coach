"""Explicit owner-supplied maximal-effort protocols, bound to one recording hash."""
from datetime import timedelta

from .physiology_samples import timestamp, number


def protocol_efforts(normalized, evidence, revision):
    protocols = evidence.get("physiology_protocols") or []
    if evidence.get("physiology_protocol_artifact_sha256") != (evidence.get("parsed_artifact") or {}).get("sha256"):
        return []
    result = []
    for p in protocols:
        start = timestamp(p.get("start_utc"))
        duration = number(p.get("duration_seconds"))
        if start is None or duration is None or not 120 < duration < 900:
            continue
        end, area, coverage, before, before_complete = start + timedelta(seconds=duration), 0.0, 0.0, 0.0, True
        for s in normalized["segments"]:
            left, right = timestamp(s["start"]), timestamp(s["end"])
            dt = max(0.0, (min(right, end) - max(left, start)).total_seconds())
            if s["value"] is not None:
                area += s["value"] * dt
                coverage += dt
            before_dt = max(0.0, (min(right, start) - left).total_seconds())
            if before_dt and s["value"] is None:
                before_complete = False
            elif before_dt:
                before += s["value"] * before_dt / 1000
        if abs(coverage - duration) > 1e-6:
            continue
        mass = number(p.get("body_mass_kg"))
        kj = before if before_complete and revision["sport"] == "cycling" else None
        result.append({**revision, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                       "duration_seconds": duration, "mean_output": area / duration,
                       "maximal_intent": "verified_maximal", "intent_source": "owner_protocol_annotation",
                       "comparison_protocol_id": p["comparison_protocol_id"], "comparison_context": p["comparison_context"],
                       "recording_quality_flags": normalized["flags"],
                       "elapsed_before_seconds": (start - timestamp(normalized["segments"][0]["start"])).total_seconds(),
                       "mechanical_work_before_kj": kj,
                       "mechanical_work_before_kj_per_kg": kj / mass if kj is not None and mass else None})
    return result
