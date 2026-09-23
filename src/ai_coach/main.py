"""Cloud Run IAM protects every route; never deploy with unauthenticated access."""
import json
import logging
import os
import traceback
import uuid
from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .storage import Store, safe_id, utcnow
from .sync import run_sync
from .dashboard import bounded_page, plan_projection, validate_window, workout_detail, workout_projection
from .coach_context import build_context, read_summary, summary_freshness

app = FastAPI(title="Private AI Coach Data", version="0.1.0")
logger = logging.getLogger("ai_coach")


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Do not echo submitted health data (or non-finite numbers) in error responses.
    return JSONResponse(status_code=422, content={"detail": [
        {"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
        for error in exc.errors()
    ]})


@lru_cache
def settings():
    return Settings.from_env()


@lru_cache
def store():
    return Store(settings())


class SyncRequest(BaseModel):
    backfill: bool = True


class PlannedWorkout(BaseModel):
    local_date: date
    name: str = Field(min_length=1, max_length=300)
    sport: str = Field(min_length=1, max_length=80)
    start_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    description: str | None = Field(default=None, max_length=20000)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    targets: dict[str, Any] = Field(default_factory=dict)
    status: Literal["planned", "completed", "skipped", "cancelled"] = "planned"
    completed_workout_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_size(self):
        # Count encoded bytes exactly and reject NaN/Infinity at every nesting level.
        if len(json.dumps([self.steps, self.targets], allow_nan=False).encode()) > 200000:
            raise ValueError("Structured workout is too large")
        if self.completed_workout_id:
            safe_id(self.completed_workout_id)
        return self


@app.get("/healthz")
def healthz():
    return {"service": "ai-coach-data", "status": "ok", "version": "0.1.0"}


@app.get("/v1/status")
def status():
    raw = store().get("sync_state", "intervals") or {}
    fields = {"last_success_at", "last_attempt_at", "last_error_type", "last_error_at", "last_run_id",
              "last_stats", "stats", "backfill_cursor", "backfill_complete", "plans_last_success_at",
              "last_plan_sync_at", "last_status", "last_warnings", "athlete_id", "last_counts",
              "last_errors", "last_plans_success_at", "last_backfill_success_at", "source_athlete_id",
              "historical_reconcile_cursor", "last_historical_reconciliation_at"}
    state = {key: value for key, value in raw.items() if key in fields}
    last = state.get("last_success_at")
    state["stale"] = last is None or (utcnow() - last).total_seconds() > 3600
    state["source_connection"] = "configured" if os.getenv("INTERVALS_API_KEY", "").strip() else "awaiting_api_key"
    return state


@app.post("/internal/sync")
def sync(request: SyncRequest | None = None):
    try:
        result = run_sync(store(), settings(), backfill=(request.backfill if request else True))
        if result.get("status") == "partial":
            return JSONResponse(result, status_code=503)
        return result
    except Exception as exc:
        # Exception messages/source lines can contain credentials or training data.
        # Keep useful frame locations, without the message, source text or locals.
        frames = [{"file": frame.filename.rsplit("/", 1)[-1], "line": frame.lineno,
                   "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)]
        logger.error(json.dumps({"severity": "ERROR", "event": "sync_failed",
                                 "error_type": type(exc).__name__, "frames": frames}))
        raise HTTPException(503, "Sync failed; retained data and cursors will be retried") from None


def date_window(oldest, newest):
    if newest < oldest:
        raise HTTPException(422, "newest must be on or after oldest")


def list_items(collection, oldest, newest, limit, after):
    date_window(oldest, newest)
    try:
        items = []
        cursor = after
        while len(items) <= limit:
            rows = store().list(collection, oldest.isoformat(), newest.isoformat(), limit=limit + 1, after=cursor)
            items.extend(row for row in rows if collection != "workouts" or
                         not (row.get("source_deleted") or row.get("source_excluded")))
            if len(rows) < limit + 1:
                break
            next_cursor = rows[-1]["id"]
            if next_cursor == cursor:
                raise ValueError("Pagination did not advance")
            cursor = next_cursor
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"items": items[:limit], "next_cursor": items[limit - 1]["id"] if len(items) > limit else None}


@app.get("/v1/dashboard/workouts")
def dashboard_workouts(oldest: date, newest: date,
                       limit: Annotated[int, Query(ge=1, le=50)] = 50,
                       after: Annotated[str | None, Query(min_length=1, max_length=180)] = None):
    validate_window(oldest, newest)
    return bounded_page(list_items("workouts", oldest, newest, limit, after), workout_projection)


@app.get("/v1/dashboard/planned-workouts")
def dashboard_plans(oldest: date, newest: date,
                    limit: Annotated[int, Query(ge=1, le=50)] = 50,
                    after: Annotated[str | None, Query(min_length=1, max_length=180)] = None):
    validate_window(oldest, newest)
    return bounded_page(list_items("planned_workouts", oldest, newest, limit, after), plan_projection)


@app.get("/v1/dashboard/workouts/{workout_id}")
def dashboard_workout(workout_id: str):
    return workout_detail(workout(workout_id))


@app.get("/v1/workouts")
def workouts(oldest: date, newest: date, limit: Annotated[int, Query(ge=1, le=500)] = 100, after: str | None = None):
    return list_items("workouts", oldest, newest, limit, after)


@app.get("/v1/planned-workouts")
def plans(oldest: date, newest: date, limit: Annotated[int, Query(ge=1, le=500)] = 100, after: str | None = None):
    return list_items("planned_workouts", oldest, newest, limit, after)


@app.get("/v1/wellness")
def wellness(oldest: date, newest: date, limit: Annotated[int, Query(ge=1, le=500)] = 100, after: str | None = None):
    return list_items("wellness", oldest, newest, limit, after)


@app.get("/v1/workouts/{workout_id}")
def workout(workout_id: str):
    try:
        result = store().get("workouts", safe_id(workout_id))
    except ValueError:
        raise HTTPException(422, "Invalid workout id") from None
    if not result:
        raise HTTPException(404, "Workout not found")
    if result.get("source_deleted") or result.get("source_excluded"):
        raise HTTPException(410, "Workout no longer available in active training data")
    return result


@app.get("/v1/workouts/{workout_id}/samples")
def samples(workout_id: str, offset: Annotated[int, Query(ge=0)] = 0,
            limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
            fields: Annotated[str | None, Query(max_length=200)] = None):
    selected_fields = None
    if fields is not None:
        selected_fields = list(dict.fromkeys(field.strip() for field in fields.split(",")))
        allowed = {"timestamp", "heart_rate", "distance", "enhanced_speed", "speed", "cadence",
                   "power", "enhanced_altitude", "altitude", "temperature", "position_lat", "position_long"}
        if not selected_fields or any(field not in allowed for field in selected_fields):
            raise HTTPException(422, "Unsupported sample field selection")
    doc = workout(workout_id)
    if doc.get("parse_status") == "summary_only":
        raise HTTPException(409, "Workout summary is available. This large FIT is archived in full; detailed samples are not materialized.")
    if not doc.get("parsed_artifact"):
        raise HTTPException(409, "This workout has no decoded samples; check its parse status")
    records = store().read_json(doc["parsed_artifact"]).get("records", [])
    page = records[offset:offset + limit]
    if selected_fields is not None:
        page = [{key: record[key] for key in selected_fields if key in record} for record in page]
    return {"items": page, "total": len(records),
            "next_offset": offset + limit if offset + limit < len(records) else None,
            "source": doc.get("source_attribution") or doc.get("garmin_attribution") or "Intervals.icu",
            **{key: doc[key] for key in ("recording_platform", "distance_type") if doc.get(key)}}


def write_plan(body, plan_id, existing=None):
    if body.completed_workout_id and not store().get("workouts", body.completed_workout_id):
        raise HTTPException(422, "Completed workout link does not exist")
    doc = body.model_dump(mode="json", exclude={"steps", "targets", "start_time"})
    doc.update({"id": plan_id, "source": "ai_coach", "provider_source": "LOCAL",
                "start_date_local": f"{body.local_date.isoformat()}T{body.start_time or '00:00'}:00",
                "timezone": settings().timezone,
                "structured_workout_json": json.dumps({"steps": body.steps, "targets": body.targets}),
                "created_at": existing.get("created_at") if existing else utcnow(), "updated_at": utcnow()})
    store().put("planned_workouts", plan_id, doc, merge=False)
    return doc


@app.post("/v1/planned-workouts", status_code=201)
def create_plan(body: PlannedWorkout):
    return write_plan(body, f"local-{uuid.uuid4()}")


@app.put("/v1/planned-workouts/{plan_id}")
def update_plan(plan_id: str, body: PlannedWorkout):
    try:
        existing = store().get("planned_workouts", safe_id(plan_id))
    except ValueError:
        raise HTTPException(422, "Invalid planned workout id") from None
    if not existing:
        raise HTTPException(404, "Planned workout not found")
    if existing.get("source") != "ai_coach":
        raise HTTPException(409, "Edit imported plans in Intervals.icu; the next sync will import changes")
    return write_plan(body, plan_id, existing)


class Observation(BaseModel):
    local_date: date
    workout_id: str | None = Field(default=None, min_length=1)
    lap_index: int | None = Field(default=None, ge=0)
    elapsed_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    lactate_mmol_l: float | None = Field(default=None, ge=0, le=50, allow_inf_nan=False)
    rpe: float | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    notes: str | None = Field(default=None, max_length=20000)


@app.post("/v1/observations", status_code=201)
def create_observation(body: Observation):
    if body.workout_id:
        try:
            linked = store().get("workouts", safe_id(body.workout_id))
        except ValueError:
            raise HTTPException(422, "Invalid workout id") from None
        if not linked:
            raise HTTPException(422, "Workout link does not exist")
    doc = body.model_dump(mode="json")
    doc.update({"id": f"observation-{uuid.uuid4()}", "source": "user", "created_at": utcnow()})
    store().put("observations", doc["id"], doc, merge=False)
    return doc


@app.get("/v1/observations")
def observations(oldest: date, newest: date, limit: Annotated[int, Query(ge=1, le=500)] = 100, after: str | None = None):
    return list_items("observations", oldest, newest, limit, after)


@app.get("/v1/context")
def context(days: Annotated[int, Query(ge=1, le=90)] = 42, upcoming: Annotated[int, Query(ge=1, le=90)] = 14):
    return build_context(store(), settings(), days=days, upcoming=upcoming, sync_status=status())


@app.get("/v1/summaries")
def training_summary(period: Literal["day", "week", "month", "rolling7", "rolling28"],
                     summary_date: Annotated[date, Query(alias="date")]):
    """One saved period: week/month resolve the supplied date to its containing period."""
    today = datetime.now(ZoneInfo(settings().timezone)).date()
    summary = read_summary(store(), period, summary_date, today=today)
    if summary is None:
        raise HTTPException(404, "Summary not available for this period; missing history is not zero training")
    freshness = summary_freshness(store())
    if summary.get("calculation_version") != freshness.get("calculation_version"):
        freshness["stale"] = True
    return {"summary": summary, "freshness": freshness}
