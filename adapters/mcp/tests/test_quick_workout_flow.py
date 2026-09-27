"""Final offline integration checks: real owner verification, fake provider/data."""
import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import xml.etree.ElementTree as ET

import httpx
import pytest

import ai_coach_mcp.app as app_module
from ai_coach_mcp.quick_workout_provider import recommend, RecommendationUnavailable
from ai_coach_mcp.quick_workout_schema import QuickWorkout
from ai_coach_mcp.quick_workout_service import QuickWorkoutService, QuickWorkoutError
from test_adapter import key, settings, setup, token, invoke
from test_dashboard import client_for, bearer
from test_quick_workout_schema import plan_dict

NOW = datetime(2026, 9, 25, 10, tzinfo=UTC)


def context():
    return {'as_of': NOW.isoformat(), 'as_of_day': '2026-09-25', 'timezone': 'Europe/Oslo',
            'sync': {'stale': False, 'last_status': 'ok', 'last_success_at': NOW.isoformat()},
            'summary_freshness': {'stale': False}, 'history_complete': False,
            'selection': {name: {'scan_complete': True} for name in
                          ('workouts', 'wellness', 'observations', 'planned_workouts')},
            'workouts': [], 'wellness': []}


class Backend:
    def __init__(self):
        self.data = context()
    async def get(self, path, params):
        assert path == '/v1/context' and params == {'days': 42, 'upcoming': 7}
        return deepcopy(self.data)


def service(backend=None, plan=None):
    calls = []
    async def provider(ctx, *, sport="cycling"):
        calls.append(ctx)
        return QuickWorkout.model_validate(plan or plan_dict())
    return QuickWorkoutService(backend or Backend(), provider, now=lambda: NOW), calls


def test_workout_cache_and_rest():
    s, calls = service()
    first = asyncio.run(s.generate())
    second = asyncio.run(s.generate())
    assert len(calls) == 1 and first == second
    assert ET.fromstring(first['zwo']).find('sportType').text == 'bike'
    assert 'History is incomplete' in first['zwo']
    assert first['filename'] == 'quick-workout-2026-09-25.zwo'
    rest = plan_dict() | {'decision': 'rest', 'steps': []}
    s, _ = service(plan=rest)
    result = asyncio.run(s.generate())
    assert result['zwo'] is None and result['filename'] is None


@pytest.mark.parametrize('mutation', [
    lambda d: d['sync'].update(stale=True),
    lambda d: d['sync'].pop('stale'),
    lambda d: d['sync'].update(last_status='partial'),
    lambda d: d['sync'].update(last_success_at='2026-09-25T11:00:00+00:00'),
    lambda d: d['summary_freshness'].pop('stale'),
    lambda d: d['selection']['workouts'].update(scan_complete=False),
    lambda d: d.update(as_of_day='2026-09-24'),
    lambda d: d.update(timezone='Invalid/Timezone'),
])
def test_bad_context_never_calls_model(mutation):
    backend = Backend()
    mutation(backend.data)
    s, calls = service(backend)
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(s.generate())
    assert exc.value.status_code == 409 and not calls


def test_plan_date_and_duration_are_rejected_not_clamped():
    for p in (plan_dict() | {'day': '2026-09-24'}, plan_dict() | {'steps': [
            {'kind': 'warmup', 'duration_s': 1800, 'power_start': .4, 'power_end': .6},
            {'kind': 'steady', 'duration_s': 1800, 'power_start': .6, 'power_end': .6},
            {'kind': 'cooldown', 'duration_s': 1800, 'power_start': .6, 'power_end': .4}]}):
        s, _ = service(plan=p)
        with pytest.raises(QuickWorkoutError) as exc:
            asyncio.run(s.generate())
        assert exc.value.status_code == 502


def test_concurrent_calls_are_not_billed_twice():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(ctx, *, sport="cycling"):
            entered.set()
            await release.wait()
            return QuickWorkout.model_validate(plan_dict())
        s = QuickWorkoutService(Backend(), provider, now=lambda: NOW)
        first = asyncio.create_task(s.generate())
        await entered.wait()
        with pytest.raises(QuickWorkoutError) as exc:
            await s.generate()
        assert exc.value.status_code == 429
        release.set()
        await first
    asyncio.run(run())


def test_owner_route_and_mcp_export(settings, key, monkeypatch):
    s, calls = service()
    monkeypatch.setattr(app_module, 'QuickWorkoutService', lambda backend: s)
    app, tokens, _ = setup(settings, key, lambda r: pytest.fail('Unexpected private call'))
    with client_for(app, settings) as client:
        path = '/dashboard/api/quick-workout'
        assert client.post(path).status_code == 401
        assert client.post(path, headers=bearer(key, settings, sub='someone-else')).status_code == 401
        assert not calls
        auth = bearer(key, settings)
        assert client.get(path, headers=auth).status_code == 405
        assert client.post(path+'?prompt=anything', headers=auth).status_code == 422
        assert client.post(path, headers=auth, json={}).status_code == 422
        assert not calls
        response = client.post(path, headers=auth)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert len(calls) == 1 and response.json()['zwo'].startswith('<?xml')
        assert response.json()['sport'] == 'cycling'
        assert response.json()['garmin_filename'] is None
        assert response.json()['fit_base64'] is None
        cached = client.post(path, headers=auth)
        assert len(calls) == 1 and cached.json()['zwo'] == response.json()['zwo']
        exported = invoke(client, token(key, settings), 'render_quick_workout', {'plan': plan_dict()})
        assert not exported.get('isError')
        assert json.loads(exported['content'][0]['text'])['filename'].endswith('.zwo')
        bad = plan_dict()
        bad['steps'][0]['power_start'] = 99
        invalid = invoke(client, token(key, settings), 'render_quick_workout', {'plan': bad})
        assert invalid.get('isError')
    assert tokens.calls == 0


def response_body():
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps(plan_dict())}]}]}


def test_openai_contract_and_no_storage(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('QUICK_WORKOUT_MODEL', 'test-model')
    def transport(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.openai.com/v1/responses'
        assert body['store'] is False and 'tools' not in body
        schema = body['text']['format']['schema']
        assert set(schema['required']) == set(schema['properties'])
        assert '$defs' in schema and schema['additionalProperties'] is False
        assert 'never invent' in body['instructions']
        return httpx.Response(200, json=response_body())
    result = asyncio.run(recommend(context(), transport=httpx.MockTransport(transport)))
    assert result.duration_s == 1800


@pytest.mark.parametrize('body', [
    {'status': 'incomplete', 'output': []},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]},
    {'status': 'completed', 'output': [None]},
    {'status': 'completed', 'output': {}},
    ['private provider response'],
])
def test_invalid_provider_response_is_safe(monkeypatch, body):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('QUICK_WORKOUT_MODEL', 'test-model')
    with pytest.raises(RecommendationUnavailable) as exc:
        asyncio.run(recommend(context(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    assert 'private' not in str(exc.value) and 'synthetic' not in str(exc.value)


def test_missing_provider_config_does_not_make_request(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('QUICK_WORKOUT_MODEL', raising=False)
    with pytest.raises(RecommendationUnavailable, match='not configured'):
        asyncio.run(recommend(context(), transport=httpx.MockTransport(lambda r: pytest.fail('No request allowed'))))


def test_rate_and_daily_attempt_limits_reset_next_day():
    backend = Backend()
    tick, current = [0.0], [NOW]
    count = []
    async def provider(ctx, *, sport="cycling"):
        count.append(1)
        return QuickWorkout.model_validate(plan_dict() | {'day': ctx['as_of_day']})
    s = QuickWorkoutService(backend, provider, clock=lambda: tick[0], now=lambda: current[0])
    asyncio.run(s.generate())
    backend.data['workouts'] = [{'synthetic': 1}]
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(s.generate())
    assert exc.value.status_code == 429
    for i in range(1, 10):
        tick[0] += 61
        backend.data['workouts'] = [{'synthetic': i}]
        asyncio.run(s.generate())
    tick[0] += 61
    backend.data['workouts'] = [{'synthetic': 11}]
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(s.generate())
    assert exc.value.status_code == 429 and len(count) == 10
    current[0] += timedelta(days=1)
    tick[0] += 86400
    backend.data['as_of_day'] = '2026-09-26'
    backend.data['sync']['last_success_at'] = current[0].isoformat()
    asyncio.run(s.generate())
    assert len(count) == 11


def test_crossing_local_midnight_rejects_old_day():
    now = [datetime(2026, 9, 25, 21, 59, 59, tzinfo=UTC)]
    backend = Backend()
    backend.data['sync']['last_success_at'] = now[0].isoformat()
    async def provider(ctx, *, sport="cycling"):
        now[0] += timedelta(seconds=2)
        return QuickWorkout.model_validate(plan_dict())
    s = QuickWorkoutService(backend, provider, now=lambda: now[0])
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(s.generate())
    assert exc.value.status_code == 409


@pytest.mark.parametrize('status,content', [(429, b'private upstream detail'), (200, b'x'*64001),
                                           (200, b'not json'), (302, b'')])
def test_provider_errors_and_response_bound(monkeypatch, status, content):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('QUICK_WORKOUT_MODEL', 'test-model')
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(status, content=content, headers={'Location': 'https://other.example/'})
    with pytest.raises(RecommendationUnavailable) as exc:
        asyncio.run(recommend(context(), transport=httpx.MockTransport(handler)))
    assert len(calls) == 1 and 'private' not in str(exc.value)


def test_provider_input_bound_and_timeout(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('QUICK_WORKOUT_MODEL', 'test-model')
    with pytest.raises(RecommendationUnavailable):
        asyncio.run(recommend({'oversized': 'x'*25000}, transport=httpx.MockTransport(lambda r: pytest.fail('No call'))))
    async def unavailable(ctx, *, sport):
        raise TimeoutError()
    s = QuickWorkoutService(Backend(), unavailable, now=lambda: NOW)
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(s.generate())
    assert exc.value.status_code == 503


def test_completed_sync_with_parse_warnings_keeps_explicit_caveat():
    backend = Backend()
    backend.data['sync']['last_status'] = 'ok_with_warnings'
    s, calls = service(backend)
    payload = asyncio.run(s.generate())
    assert len(calls) == 1
    assert any('warnings' in caveat for caveat in payload['plan']['caveats'])
