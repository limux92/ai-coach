"""Private IAM API for immutable model/analysis reads and explicit operator reanalysis."""
from typing import Annotated, Literal
import uuid
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from .physiology_samples import timestamp
from .physiology_context import read_context
from .physiology_service import analyze_target, scan_all
from .summary_service import save_workout
from .storage import safe_id, utcnow


class Protocol(BaseModel):
    start_utc: str
    duration_seconds: float = Field(gt=120, lt=900, allow_inf_nan=False)
    comparison_protocol_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    comparison_context: str = Field(min_length=1, max_length=160)
    maximal_intent: Literal["verified_maximal"]
    body_mass_kg: float | None = Field(default=None, ge=30, le=250, allow_inf_nan=False)

    @model_validator(mode="after")
    def aware_start(self):
        value = timestamp(self.start_utc)
        if value is None:
            raise ValueError("Offset-aware timestamp required")
        self.start_utc = value.isoformat()
        return self


class Protocols(BaseModel):
    parsed_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    efforts: list[Protocol] = Field(max_length=8)


def router(get_store, get_settings):
    routes = APIRouter()

    def read(collection, identifier):
        try:
            value = get_store().get(collection, safe_id(identifier))
        except ValueError:
            raise HTTPException(422, "Invalid identifier") from None
        if value is None:
            raise HTTPException(404, "Physiology evidence not available")
        return value

    @routes.get("/v1/physiology/context")
    def context():
        return read_context(get_store(), get_settings(), utcnow())

    @routes.get("/v1/physiology/models/{snapshot_id}")
    def model(snapshot_id: str):
        return read("physiology_models", snapshot_id)

    @routes.get("/v1/physiology/sessions")
    def sessions(days: Annotated[int, Query(ge=7, le=28)] = 7, offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=50)] = 25):
        if days not in (7, 28):
            raise HTTPException(422, "Use a 7 or 28 day window")
        state = get_store().get("sync_state", "physiology") or {}
        if not state.get("context_id"):
            raise HTTPException(404, "Physiology context not available")
        saved = read("physiology_contexts", state["context_id"])
        period = saved["periods"][f"rolling{days}"]
        values = period["sessions"]
        current = read_context(get_store(), get_settings(), utcnow())
        return {"context_id": state["context_id"], "as_of": saved["as_of"],
                "stale": current["stale"],
                "data_coverage": current["periods"][f"rolling{days}"]["data_coverage"], "items": values[offset:offset + limit],
                "total": period["workout_count"], "omitted_by_storage_bound": period["sessions_omitted"],
                "next_offset": offset + limit if offset + limit < len(values) else None}

    @routes.get("/v1/physiology/analyses/{analysis_id}")
    def analysis(analysis_id: str):
        result = read("physiology_analyses", analysis_id)
        return {k: v for k, v in result.items() if k != "analysis_artifact"}

    @routes.get("/v1/physiology/analyses/{analysis_id}/events")
    def events(analysis_id: str, offset: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=100)] = 50):
        result = read("physiology_analyses", analysis_id)
        full = get_store().read_json(result["analysis_artifact"])
        values = (full.get("balance") or {}).get("above_threshold_events", [])
        return {"analysis_id": analysis_id, "items": values[offset:offset + limit], "total": len(values),
                "next_offset": offset + limit if offset + limit < len(values) else None}

    @routes.get("/v1/workouts/{workout_id}/physiology")
    def workout_analysis(workout_id: str):
        doc = read("workouts", workout_id)
        if doc.get("source_deleted") or doc.get("source_excluded"):
            raise HTTPException(410, "Workout retired from current data")
        return read("sync_state", "physiology_target_" + workout_id)

    @routes.post("/internal/physiology/workouts/{workout_id}/reanalyze")
    def reanalyze(workout_id: str, mode: Literal["retrospective", "as_known_before_workout"] = "retrospective"):
        store, lease = get_store(), "physiology-" + str(uuid.uuid4())
        if not store.acquire_lease(lease, seconds=840):
            raise HTTPException(409, "Importer is running; retry later")
        try:
            doc = read("workouts", workout_id)
            if doc.get("source_deleted") or doc.get("source_excluded"):
                raise HTTPException(410, "Workout retired from current data")
            if not doc.get("physiology_revision_id"):
                raise HTTPException(409, "Wait for physiology evidence migration")
            deadline = time.monotonic() + 240
            last_lease_check = 0.0
            def check_budget():
                nonlocal last_lease_check
                if time.monotonic() >= deadline:
                    raise HTTPException(503, "Reanalysis budget exceeded; retry later")
                if hasattr(store, "assert_sync_lease") and time.monotonic() - last_lease_check > 5:
                    store.assert_sync_lease(lease)
                    last_lease_check = time.monotonic()
            state = store.get("sync_state", "physiology") or {}
            if state.get("status") != "ok":
                raise HTTPException(409, "Physiology evidence processing is incomplete")
            result = analyze_target(store, scan_all(store, "physiology_revisions", check_budget),
                                    read("physiology_revisions", doc["physiology_revision_id"]), now=utcnow(),
                                    lookback_days=get_settings().physiology_lookback_days, mode=mode,
                                    check_budget=check_budget, lease_owner=lease)
            if result is None:
                raise HTTPException(409, "Eligible source recording required")
            return {k: v for k, v in result.items() if k != "analysis_artifact"}
        finally:
            store.release_lease(lease, {})

    @routes.post("/internal/physiology/workouts/{workout_id}/protocols")
    def protocols(workout_id: str, body: Protocols):
        store, lease = get_store(), "protocol-" + str(uuid.uuid4())
        if not store.acquire_lease(lease, seconds=840):
            raise HTTPException(409, "Importer is running; retry later")
        try:
            doc = read("workouts", workout_id)
            if doc.get("source_deleted") or doc.get("source_excluded"):
                raise HTTPException(410, "Workout retired from current data")
            if body.parsed_artifact_sha256 != (doc.get("parsed_artifact") or {}).get("sha256") or doc.get("parse_status") != "parsed":
                raise HTTPException(409, "Protocol must identify the current decoded recording")
            save_workout(store, workout_id, {"physiology_protocols": [p.model_dump() for p in body.efforts],
                                            "physiology_protocol_artifact_sha256": body.parsed_artifact_sha256})
            return {"status": "pending", "workout_id": workout_id}
        finally:
            store.release_lease(lease, {})

    return routes
