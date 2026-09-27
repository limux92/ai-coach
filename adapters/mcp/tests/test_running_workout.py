"""Running-only prescription, provider and authenticated dashboard checks."""
import asyncio
import base64
import json

import httpx
import pytest

import ai_coach_mcp.app as app_module
from ai_coach_mcp.quick_workout_provider import recommend, RecommendationUnavailable
from ai_coach_mcp.quick_workout_schema import QuickWorkout
from ai_coach_mcp.quick_workout_service import QuickWorkoutService, QuickWorkoutError
from ai_coach_mcp.running_workout_schema import RunningWorkout
from test_adapter import key, settings, setup, token, invoke
from test_dashboard import client_for, bearer
from test_quick_workout_flow import Backend, NOW, context
from test_quick_workout_schema import plan_dict


def running_dict():
    return {'sport': 'running', 'day': '2026-09-25', 'decision': 'workout',
            'title': 'Controlled running intervals', 'rationale': 'Synthetic running context', 'caveats': [],
            'steps': [
                {'kind': 'warmup', 'duration_type': 'lap_press', 'duration_s': None, 'effort': 'easy'},
                {'kind': 'run', 'duration_type': 'time', 'duration_s': 180, 'effort': 'steady'},
                {'kind': 'recovery', 'duration_type': 'time', 'duration_s': 120, 'effort': 'easy'},
                {'kind': 'run', 'duration_type': 'time', 'duration_s': 180, 'effort': 'steady'},
                {'kind': 'cooldown', 'duration_type': 'lap_press', 'duration_s': None, 'effort': 'easy'}]}


@pytest.mark.parametrize('index,change', [
    (0, {'duration_type': 'time', 'duration_s': 300}),
    (-1, {'duration_type': 'time', 'duration_s': 300}),
    (0, {'duration_s': 300}), (-1, {'effort': 'hard'}),
    (1, {'duration_type': 'lap_press', 'duration_s': None}),
    (1, {'duration_s': None}), (1, {'duration_s': True}),
    (1, {'duration_s': '180'}), (1, {'duration_s': 29}),
    (1, {'duration_s': 1801}), (2, {'effort': 'hard'}),
    (1, {'power_start': 0.8}), (1, {'pace': 300}),
])
def test_invalid_running_steps_are_rejected(index, change):
    raw = running_dict()
    raw['steps'][index].update(change)
    with pytest.raises(ValueError):
        RunningWorkout.model_validate(raw)


def test_running_contract_duration_and_rest():
    raw = running_dict()
    assert RunningWorkout.model_validate(raw).duration_s == 480
    for change in ({'sport': 'cycling'}, {'steps': raw['steps'][1:]},
                   {'steps': raw['steps'][:-1]}, {'steps': [raw['steps'][0], raw['steps'][2], raw['steps'][-1]]},
                   {'decision': 'rest'}, {'title': 'Bad\0title'}):
        with pytest.raises(ValueError):
            RunningWorkout.model_validate(raw | change)
    rest = RunningWorkout.model_validate(raw | {'decision': 'rest', 'steps': []})
    assert rest.duration_s == 0
    with pytest.raises(ValueError):
        RunningWorkout.model_validate(plan_dict())


def test_cycling_and_running_have_separate_caches_but_shared_quota():
    calls, ticks = [], [0.0]
    async def provider(ctx, *, sport):
        calls.append(sport)
        return RunningWorkout(**running_dict()) if sport == 'running' else QuickWorkout(**plan_dict())
    service = QuickWorkoutService(Backend(), provider, clock=lambda: ticks[0], now=lambda: NOW)
    ride = asyncio.run(service.generate())
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(service.generate('running'))
    assert exc.value.status_code == 429 and calls == ['cycling']
    ticks[0] = 61
    run = asyncio.run(service.generate('running'))
    assert calls == ['cycling', 'running']
    assert run['sport'] == 'running' and run['open_duration'] is True and run['duration_s'] == 480
    assert run['filename'] is None and run['zwo'] is None
    assert run['garmin_filename'] == 'quick-run-2026-09-25.fit'
    assert base64.b64decode(run['fit_base64'])[8:12] == b'.FIT'
    assert ride['fit_base64'] is None and ride['sport'] == 'cycling'
    assert asyncio.run(service.generate()) == ride
    assert asyncio.run(service.generate('running')) == run
    assert len(calls) == 2
    run['plan']['title'] = 'Mutated by caller'
    assert asyncio.run(service.generate('running'))['plan']['title'] != run['plan']['title']
    with pytest.raises(QuickWorkoutError):
        asyncio.run(service.generate('swimming'))
    assert len(calls) == 2


def test_running_rejects_a_cycling_model_answer():
    async def provider(ctx, *, sport):
        return QuickWorkout(**plan_dict())
    service = QuickWorkoutService(Backend(), provider, now=lambda: NOW)
    with pytest.raises(QuickWorkoutError) as exc:
        asyncio.run(service.generate('running'))
    assert exc.value.status_code == 502


def test_owner_running_route_and_mcp(settings, key, monkeypatch):
    calls = []
    async def provider(ctx, *, sport):
        calls.append(sport)
        return RunningWorkout(**running_dict())
    service = QuickWorkoutService(Backend(), provider, now=lambda: NOW)
    monkeypatch.setattr(app_module, 'QuickWorkoutService', lambda backend: service)
    app, tokens, _ = setup(settings, key, lambda _: pytest.fail('Unexpected backend call'))
    with client_for(app, settings) as client:
        path = '/dashboard/api/quick-workout/run'
        assert client.post(path).status_code == 401
        assert client.post(path, headers=bearer(key, settings, sub='someone-else')).status_code == 401
        auth = bearer(key, settings)
        assert client.get(path, headers=auth).status_code == 405
        assert client.post(path+'?model=anything', headers=auth).status_code == 422
        assert client.post(path, headers=auth, json={}).status_code == 422
        assert calls == []
        response = client.post(path, headers=auth)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert calls == ['running'] and response.json()['sport'] == 'running'
        assert client.post(path, headers=auth).json() == response.json()
        assert calls == ['running']
        exported = invoke(client, token(key, settings), 'render_running_workout', {'plan': running_dict()})
        assert not exported.get('isError')
        assert json.loads(exported['content'][0]['text'])['garmin_filename'].endswith('.fit')
    assert tokens.calls == 0


def test_openai_running_schema_and_prompt(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('QUICK_WORKOUT_MODEL', 'test-model')
    def transport(request):
        data = json.loads(request.content)
        assert data['store'] is False and 'tools' not in data
        assert 'RUNNING' in data['instructions'] and 'lap_press' in data['instructions']
        assert 'Cycling fitness or FTP is not proof of running tolerance' in data['instructions']
        schema = data['text']['format']['schema']
        assert schema['properties']['sport']['const'] == 'running'
        step = schema['$defs']['RunningStep']
        assert set(step['required']) == {'kind', 'effort', 'duration_type', 'duration_s'}
        assert step['properties']['duration_s']['anyOf'][1]['type'] == 'null'
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(running_dict())}]}]})
    result = asyncio.run(recommend(context(), sport='running', transport=httpx.MockTransport(transport)))
    assert isinstance(result, RunningWorkout) and result.steps[0].duration_s is None
    wrong_response = {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps(running_dict())}]}]}
    fake = httpx.MockTransport(lambda _: httpx.Response(200, json=wrong_response))
    with pytest.raises(RecommendationUnavailable):
        asyncio.run(recommend(context(), sport='cycling', transport=fake))
