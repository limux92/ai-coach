"""Freshness, bounded recommendations and export; no training-record writes.

Rate limits and the ten-minute cache are per process, not a billing spend cap.
"""
import asyncio
import base64
from copy import deepcopy
from datetime import UTC, date, datetime
import hashlib
import json
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from .quick_workout_provider import RecommendationUnavailable, recommend
from .physiology_projection import recommendation_context
from .quick_workout_schema import QuickWorkout, to_zwo
from .running_workout_schema import RunningWorkout
from .garmin_workout import to_fit


class QuickWorkoutError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def context_day(context, now):
    """Fail closed on unknown freshness, partial scans or a different local day."""
    try:
        today = now.astimezone(ZoneInfo(context['timezone'])).date()
        if date.fromisoformat(context['as_of_day']) != today:
            raise ValueError()
        sync = context['sync']
        last = datetime.fromisoformat(sync['last_success_at'].replace('Z', '+00:00'))
        if (last.tzinfo is None or not 0 <= (now - last).total_seconds() <= 86400
                or sync.get('stale') is not False or sync.get('last_status') not in {'ok', 'ok_with_warnings'}
                or context['summary_freshness'].get('stale') is not False):
            raise ValueError()
        for collection in ('workouts', 'wellness', 'observations', 'planned_workouts'):
            if context['selection'][collection].get('scan_complete') is not True:
                raise ValueError()
        return today
    except (ValueError, TypeError, KeyError, AttributeError, ZoneInfoNotFoundError):
        raise QuickWorkoutError('Training context needs a fresh, complete sync before a recommendation.', 409) from None


def workout_payload(plan):
    """A pure export used by both the dashboard and the structured MCP tool."""
    running = isinstance(plan, RunningWorkout)
    plan = (RunningWorkout if running else QuickWorkout).model_validate(plan.model_dump())
    workout = plan.decision == 'workout'
    return {'plan': plan.model_dump(mode='json'), 'sport': 'running' if running else 'cycling',
            'duration_s': plan.duration_s, 'open_duration': running and workout,
            'filename': f'quick-workout-{plan.day.isoformat()}.zwo' if workout and not running else None,
            'zwo': to_zwo(plan) if workout and not running else None,
            'garmin_filename': f'quick-run-{plan.day.isoformat()}.fit' if workout and running else None,
            'fit_base64': base64.b64encode(to_fit(plan)).decode('ascii') if workout and running else None}


class QuickWorkoutService:
    def __init__(self, backend, recommender=recommend, *, clock=time.monotonic, now=lambda: datetime.now(UTC)):
        self.backend, self.recommender, self.clock, self.now = backend, recommender, clock, now
        self._lock = asyncio.Lock()
        self._cache = {}
        self._day, self._attempts, self._last = None, 0, float('-inf')

    async def generate(self, sport='cycling'):
        if sport not in {'cycling', 'running'}:
            raise QuickWorkoutError('Unsupported workout sport.', 422)
        prescription = RunningWorkout if sport == 'running' else QuickWorkout
        if self._lock.locked():
            raise QuickWorkoutError('A recommendation is already being prepared.', 429)
        async with self._lock:
            context = await self.backend.get('/v1/context', {'days': 42, 'upcoming': 7})
            today = context_day(context, self.now())
            try:
                context = recommendation_context(context)
                canonical = json.dumps({k: v for k, v in context.items() if k != 'as_of'},
                                       sort_keys=True, allow_nan=False, default=str).encode()
                if len(canonical) > 24000:
                    raise ValueError()
            except (ValueError, TypeError):
                raise QuickWorkoutError('Training context is too large or incomplete.', 409) from None
            signature = hashlib.sha256(canonical).hexdigest()
            tick = self.clock()
            cached = self._cache.get(sport)
            if cached and cached[0] == signature and tick - cached[1] < 600:
                return deepcopy(cached[2])
            quota_day = self.now().astimezone(UTC).date()
            if self._day != quota_day:
                self._day, self._attempts = quota_day, 0
            if tick - self._last < 60 or self._attempts >= 10:
                raise QuickWorkoutError('Recommendation limit reached. Please try again later.', 429)
            self._last, self._attempts = tick, self._attempts + 1
            try:
                candidate = await asyncio.wait_for(self.recommender(context, sport=sport), timeout=50)
            except (RecommendationUnavailable, TimeoutError):
                raise QuickWorkoutError('Quick Workout is unavailable. Check the recommendation provider configuration.', 503) from None
            try:
                raw = candidate.model_dump(mode='json') if isinstance(candidate, (QuickWorkout, RunningWorkout)) else candidate
                plan = prescription.model_validate(raw)
                if plan.day != today or plan.duration_s > 3600:
                    raise ValueError()
                caveats = plan.caveats[:5]
                if context['sync']['last_status'] == 'ok_with_warnings':
                    caveats.append('The latest import completed with warnings; some details may be unavailable.')
                if context.get('history_complete') is not True:
                    caveats.append('History is incomplete; missing days are not proof of rest.')
                if plan.decision == 'workout':
                    caveats.append('Warm-up and cool-down end on LAP press; timed duration covers only the main set.'
                                   if sport == 'running' else 'Power uses the FTP configured in Zwift.')
                plan = prescription.model_validate({**plan.model_dump(), 'caveats': caveats})
            except (ValueError, TypeError, ValidationError):
                raise QuickWorkoutError('The recommendation did not pass workout validation.', 502) from None
            # A call crossing midnight cannot return yesterday's recommendation.
            context_day(context, self.now())
            payload = workout_payload(plan)
            self._cache[sport] = (signature, self.clock(), payload)
            return deepcopy(payload)
