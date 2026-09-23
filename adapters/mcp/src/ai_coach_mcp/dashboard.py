"""Public dashboard shell and strictly bounded owner-only read gateway.

The shell contains no training records. Browser access tokens terminate here;
only the existing service-account identity is used against the private backend.
"""
from datetime import date
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from .backend import BackendError

DEFAULT_FIELDS = "timestamp,heart_rate,distance,enhanced_speed,speed,cadence,power"
SAMPLE_FIELDS = frozenset(DEFAULT_FIELDS.split(",")) | {
    "enhanced_altitude", "altitude", "temperature", "position_lat", "position_long"}
PERIODS = {"day", "week", "month", "rolling7", "rolling28"}
STATIC_EXTENSIONS = {".html", ".js", ".css", ".svg", ".png", ".webp", ".ico", ".woff", ".woff2"}


def identifier(value):
    return (isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,180}", value)
            and value not in {".", ".."} and not (value.startswith("__") and value.endswith("__")))


def query(request, allowed):
    pairs = list(request.query_params.multi_items())
    if len(pairs) != len(dict(pairs)) or any(key not in allowed for key, _ in pairs):
        raise ValueError("Unsupported or duplicate query parameters")
    return dict(pairs)


def integer(params, name, default, minimum, maximum):
    raw = params.get(name, str(default))
    if not re.fullmatch(r"[0-9]{1,8}", raw):
        raise ValueError("Invalid numeric query parameter")
    result = int(raw)
    if not minimum <= result <= maximum:
        raise ValueError("Numeric query parameter is outside the allowed range")
    return result


def calendar_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use YYYY-MM-DD calendar dates")
    return date.fromisoformat(value)


def window(request):
    params = query(request, {"oldest", "newest", "limit", "after"})
    oldest, newest = calendar_date(params.get("oldest")), calendar_date(params.get("newest"))
    if newest < oldest or (newest - oldest).days > 365:
        raise ValueError("Use an ordered date range of at most 366 calendar days")
    after = params.get("after")
    if after is not None and not identifier(after):
        raise ValueError("Use the next_cursor returned by the previous page")
    return {"oldest": oldest.isoformat(), "newest": newest.isoformat(),
            "limit": integer(params, "limit", 50, 1, 50), "after": after}


class OwnerAPI:
    """Authenticate before routing, including unsupported API routes/methods."""
    def __init__(self, app, settings, verifier):
        self.app, self.settings, self.verifier = app, settings, verifier

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        headers = request.headers.getlist("authorization")
        match = re.fullmatch(r"Bearer ([^\s,]{1,16384})", headers[0], re.IGNORECASE) if len(headers) == 1 else None
        access = await self.verifier.verify_token(match.group(1)) if match else None
        if access is None or access.subject != self.settings.owner_subject:
            response = JSONResponse({"error": "Sign in with the authorized owner account"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Bearer realm="training-dashboard"'})
            return await response(scope, receive, send)
        if self.settings.read_scope not in access.scopes:
            response = JSONResponse({"error": "The coach:read permission is required"}, status_code=403)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


class DashboardHeaders:
    """Security headers also cover authorization, routing and validation failures."""
    def __init__(self, app, settings):
        self.app = app
        auth_origin = f"https://{settings.firebase_project_id}.firebaseapp.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com"
        self.headers = {
            "cache-control": "no-store",
            "pragma": "no-cache",
            "referrer-policy": "no-referrer",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "permissions-policy": "camera=(), microphone=(), geolocation=()",
            "content-security-policy": (
                "default-src 'none'; script-src 'self' https://www.gstatic.com https://apis.google.com; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https://www.gstatic.com; font-src 'self'; "
                f"connect-src 'self' {auth_origin}; frame-src 'self' {auth_origin}; "
                f"form-action 'self' {auth_origin}; "
                "worker-src 'self' blob:; base-uri 'self'; object-src 'none'; frame-ancestors 'none'"
            ),
        }

    async def __call__(self, scope, receive, send):
        async def secured_send(message):
            if message["type"] == "http.response.start":
                blocked = {key.encode() for key in self.headers}
                existing = [(key, value) for key, value in message.get("headers", []) if key.lower() not in blocked]
                message["headers"] = existing + [(key.encode(), value.encode()) for key, value in self.headers.items()]
            await send(message)
        await self.app(scope, receive, secured_send)


def dashboard_routes(settings, backend, verifier, static_dir=None):
    root = Path(static_dir or os.environ.get("DASHBOARD_STATIC_DIR") or
                Path(__file__).resolve().parents[2] / "static" / "dashboard").resolve()

    async def config(request):
        return JSONResponse({
            "apiKey": settings.firebase_api_key,
            "authDomain": f"{settings.firebase_project_id}.firebaseapp.com",
            "projectId": settings.firebase_project_id,
            "timezone": "Europe/Oslo",
            "configured": bool(settings.firebase_project_id),
        })

    async def read(path, params=None):
        try:
            return JSONResponse(await backend.get(path, params))
        except BackendError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    def bounded(handler):
        async def endpoint(request):
            try:
                return await handler(request)
            except ValueError:
                # Do not echo tokens, submitted values or private data into errors.
                return JSONResponse({"error": "Invalid dashboard query parameters"}, status_code=422)
        return endpoint

    @bounded
    async def workouts(request):
        return await read("/v1/dashboard/workouts", window(request))

    @bounded
    async def plans(request):
        return await read("/v1/dashboard/planned-workouts", window(request))

    def workout_id(request):
        value = request.path_params["workout_id"]
        if not identifier(value):
            raise ValueError("Invalid workout identifier")
        return value

    @bounded
    async def detail(request):
        value = workout_id(request)
        query(request, set())
        return await read("/v1/dashboard/workouts/" + value)

    @bounded
    async def samples(request):
        value = workout_id(request)
        params = query(request, {"offset", "limit", "fields"})
        fields = params.get("fields", DEFAULT_FIELDS)
        selected = fields.split(",")
        if len(fields) > 200 or not selected or len(set(selected)) != len(selected) or any(field not in SAMPLE_FIELDS for field in selected):
            raise ValueError("Invalid sample field selection")
        return await read("/v1/workouts/" + value + "/samples", {
            "offset": integer(params, "offset", 0, 0, 1_000_000),
            "limit": integer(params, "limit", 100, 1, 500), "fields": fields})

    @bounded
    async def status(request):
        query(request, set())
        return await read("/v1/status")

    @bounded
    async def context(request):
        params = query(request, {"days", "upcoming"})
        return await read("/v1/context", {"days": integer(params, "days", 42, 1, 90),
                                         "upcoming": integer(params, "upcoming", 14, 1, 90)})

    @bounded
    async def summaries(request):
        params = query(request, {"period", "date"})
        if params.get("period") not in PERIODS:
            raise ValueError("Invalid summary period")
        return await read("/v1/summaries", {"period": params["period"],
                                          "date": calendar_date(params.get("date")).isoformat()})

    async def shell(request):
        relative = request.path_params.get("path", "")
        parts = relative.split("/")
        if "\\" in relative or any(part in {".", ".."} or part.startswith(".") for part in parts):
            return JSONResponse({"error": "Not found"}, status_code=404)
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            return JSONResponse({"error": "Not found"}, status_code=404)
        if candidate.is_file() and candidate.suffix in STATIC_EXTENSIONS:
            return FileResponse(candidate)
        if Path(relative).suffix or relative.startswith("assets/"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        index = root / "index.html"
        if not index.is_file():
            return JSONResponse({"error": "The dashboard build is not installed"}, status_code=503)
        return FileResponse(index)

    async def redirect(request):
        return RedirectResponse("/dashboard/", status_code=307, headers={"Cache-Control": "no-store"})

    api = Starlette(routes=[Route("/workouts", workouts), Route("/planned-workouts", plans),
        Route("/workouts/{workout_id}", detail), Route("/workouts/{workout_id}/samples", samples),
        Route("/status", status), Route("/context", context), Route("/summaries", summaries)])
    dashboard = Starlette(routes=[Route("/config", config), Mount("/api", OwnerAPI(api, settings, verifier)),
                                  Route("/", shell), Route("/{path:path}", shell)])
    return [Route("/dashboard", redirect), Mount("/dashboard", DashboardHeaders(dashboard, settings))]
