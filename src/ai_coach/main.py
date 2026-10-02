"""Cloud Run IAM protects every route; never deploy with unauthenticated access."""
import json
import logging
import os
import traceback
import uuid
from contextvars import ContextVar
from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, model_validator

from .config import Settings
from .storage import Store, safe_id, utcnow
from .sync import run_sync, run_sync_for_user, run_multi_tenant_sync
from .dashboard import bounded_page, plan_projection, validate_window, workout_detail, workout_projection
from .coach_context import build_context, read_summary, summary_freshness
from .billing import (
    create_checkout,
    create_checkout_session,
    create_portal_session,
    process_stripe_event,
    process_vipps_event,
    verify_stripe_signature,
    verify_vipps_agreement,
)

app = FastAPI(title="Private AI Coach Data", version="0.1.0")
logger = logging.getLogger("ai_coach")

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


@app.middleware("http")
async def user_scope_middleware(request: Request, call_next):
    user_id = request.headers.get("x-user-id")
    if user_id is not None:
        try:
            user_id = safe_id(user_id)
        except ValueError:
            return JSONResponse({"error": "Invalid user identity header"}, status_code=400)
    token = _current_user_id.set(user_id)
    try:
        return await call_next(request)
    finally:
        _current_user_id.reset(token)


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
def _base_store():
    return Store(settings())


def store():
    base = _base_store()
    user_id = _current_user_id.get()
    if user_id is not None:
        return base.for_user(user_id)
    return base


from .physiology_api import router as physiology_router
app.include_router(physiology_router(lambda: store(), lambda: settings()))


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


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default="Europe/Oslo", max_length=50)
    terms_accepted: bool = Field(default=False)


@app.post("/v1/user/register", status_code=201)
def user_register(body: RegisterRequest):
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    existing = store().get("users", user_id)
    now = utcnow().isoformat()
    if existing:
        if body.terms_accepted and not existing.get("terms_accepted"):
            store().put("users", user_id, {
                "terms_accepted": True,
                "terms_accepted_at": now,
                "updated_at": now,
            }, merge=True)
            existing["terms_accepted"] = True
            existing["terms_accepted_at"] = now
        return JSONResponse(existing, status_code=200)
    owner_uid = getattr(settings(), "owner_subject", None) or os.environ.get("FIREBASE_OWNER_UID", "")
    status = "active" if user_id == owner_uid else "pending_payment"
    user_doc = {
        "id": user_id,
        "email": body.email.strip().lower(),
        "display_name": (body.display_name or "").strip(),
        "status": status,
        "role": "athlete",
        "timezone": body.timezone or "Europe/Oslo",
        "terms_accepted": body.terms_accepted,
        "terms_accepted_at": now if body.terms_accepted else None,
        "created_at": now,
        "updated_at": now,
    }
    store().put("users", user_id, user_doc, merge=False)
    return JSONResponse(user_doc, status_code=201)


@app.get("/v1/user/profile")
def user_profile():
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    user_doc = store().get("users", user_id)
    if not user_doc:
        raise HTTPException(404, "User profile not found")
    return user_doc


class IntervalsCredentialsRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=128)
    athlete_id: str = Field(min_length=1, max_length=64)


@app.post("/v1/user/intervals-credentials")
def save_intervals_credentials(body: IntervalsCredentialsRequest):
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    user_doc = store().get("users", user_id)
    if not user_doc or user_doc.get("status") != "active":
        raise HTTPException(403, "Active subscription required")
    now = utcnow().isoformat()
    doc = {
        "api_key": body.api_key.strip(),
        "athlete_id": body.athlete_id.strip(),
        "updated_at": now,
    }
    store().for_user(user_id).put("credentials", "intervals", doc, merge=False)
    return {"status": "configured", "athlete_id": body.athlete_id.strip()}


@app.get("/v1/user/intervals-credentials")
def get_intervals_credentials():
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    doc = store().for_user(user_id).get("credentials", "intervals")
    if not doc or not doc.get("api_key"):
        return {"configured": False, "athlete_id": None}
    return {"configured": True, "athlete_id": doc.get("athlete_id")}


@app.post("/v1/user/sync")
def user_sync(request: SyncRequest | None = None):
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    user_doc = store().get("users", user_id)
    if not user_doc or user_doc.get("status") != "active":
        raise HTTPException(403, "Active subscription required")
    try:
        result = run_sync_for_user(user_id, store(), settings(), backfill=(request.backfill if request else True))
        if result.get("status") == "partial":
            return JSONResponse(result, status_code=503)
        return result
    except Exception as exc:
        logger.exception("user_sync_failed")
        raise HTTPException(500, "Sync execution failed")


@app.post("/internal/sync/multi-tenant")
def internal_sync_multi_tenant():
    try:
        results = run_multi_tenant_sync(store(), settings())
        return {"status": "ok", "synced_users": len(results), "results": results}
    except Exception as exc:
        logger.exception("multi_tenant_sync_failed")
        raise HTTPException(500, "Multi-tenant sync failed")


@app.post("/v1/billing/checkout")
def billing_checkout():
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    user_doc = store().get("users", user_id)
    email = user_doc.get("email") if user_doc else None
    try:
        return create_checkout(user_id, email, settings())
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/v1/billing/portal")
def billing_portal():
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    user_doc = store().get("users", user_id)
    if not user_doc or not user_doc.get("stripe_customer_id"):
        raise HTTPException(400, "No active Stripe customer found")
    try:
        return create_portal_session(user_doc["stripe_customer_id"], settings())
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/v1/billing/vipps/activate")
def vipps_activate(body: dict[str, Any]):
    user_id = _current_user_id.get()
    if not user_id:
        raise HTTPException(401, "User ID required")
    agreement_id = body.get("agreement_id")
    if not agreement_id:
        raise HTTPException(400, "Missing agreement_id")
    return process_vipps_event({
        "agreement_id": agreement_id,
        "status": "ACTIVE",
        "user_id": user_id,
    }, store())


@app.post("/v1/webhook/vipps")
async def vipps_webhook(request: Request):
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
    return process_vipps_event(event, store())


@app.post("/v1/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = settings().stripe_webhook_secret
    if webhook_secret and not verify_stripe_signature(body, sig_header, webhook_secret):
        raise HTTPException(400, "Invalid Stripe signature")
    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
    return process_stripe_event(event, store())



@app.get("/health")
@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"service": "ai-coach-data", "status": "ok", "version": "0.1.0"}


@app.get("/v1/status")
def status():
    user_id = _current_user_id.get()
    current_store = store().for_user(user_id) if user_id else store()
    raw = current_store.get("sync_state", "intervals") or {}
    fields = {"last_success_at", "last_attempt_at", "last_error_type", "last_error_at", "last_run_id",
              "last_stats", "stats", "backfill_cursor", "backfill_complete", "plans_last_success_at",
              "last_plan_sync_at", "last_status", "last_warnings", "athlete_id", "last_counts",
              "last_errors", "last_plans_success_at", "last_backfill_success_at", "source_athlete_id",
              "historical_reconcile_cursor", "last_historical_reconciliation_at"}
    state = {key: value for key, value in raw.items() if key in fields}
    last = state.get("last_success_at")
    state["stale"] = last is None or (utcnow() - last).total_seconds() > 3600
    if user_id:
        cred = current_store.get("credentials", "intervals") or {}
        state["source_connection"] = "configured" if cred.get("api_key") else "awaiting_api_key"
        if cred.get("athlete_id"):
            state["athlete_id"] = cred.get("athlete_id")
    else:
        state["source_connection"] = "configured" if os.getenv("INTERVALS_API_KEY", "").strip() else "awaiting_api_key"
    return state


@app.post("/internal/sync")
def sync(request: SyncRequest | None = None):
    try:
        backfill = request.backfill if request else True
        result = run_sync(store(), settings(), backfill=backfill)
        try:
            run_multi_tenant_sync(store(), settings(), backfill=backfill)
        except Exception as exc:
            logger.warning("multi_tenant_sync_background_failed: %s", exc)
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
