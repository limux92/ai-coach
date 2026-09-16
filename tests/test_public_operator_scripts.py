"""Synthetic inputs keep operator verification useful without published health facts."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'infra' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault('cloud', load('public_operator_cloud', 'cloud.py'))
budget = load('public_operator_budget', 'budget.py')
verify = load('public_operator_verifier', 'verify_chat_live.py')


@pytest.fixture
def expected():
    return {'workout_id': 'synthetic-session', 'workout_name': 'Synthetic test session',
            'local_date': '2024-02-29', 'metrics': {'distance_m': 5000, 'moving_time_s': 1800,
            'average_heart_rate_bpm': 120}, 'record_count': 2000,
            'week_totals': {'distance_m': 12000, 'moving_time_s': 4000}}


def test_private_reference_round_trip_and_metric_mismatch(tmp_path, expected):
    path = tmp_path / 'expected.json'
    path.write_text(json.dumps(expected))
    assert verify.load_expected(path) == expected
    workout = {'id': expected['workout_id'], 'name': expected['workout_name'],
               'local_date': expected['local_date'], 'metrics': deepcopy(expected['metrics']),
               'record_count': expected['record_count']}
    verify.check_workout(workout, expected)
    workout['metrics']['distance_m'] += 1
    with pytest.raises(verify.VerificationError, match='known_workout_metric_mismatch'):
        verify.check_workout(workout, expected)


@pytest.mark.parametrize('change', [
    {'workout_id': '../secret'}, {'local_date': '20240229'}, {'local_date': '2024-02-30'},
    {'record_count': True}, {'record_count': 5}, {'metrics': {}},
    {'metrics': {'distance_m': 5000, 'moving_time_s': 1800, 'average_heart_rate_bpm': float('nan')}},
    {'week_totals': {'distance_m': 5000, 'moving_time_s': -1}}, {'access_token': 'SECRET'},
])
def test_invalid_baseline_fails_without_echoing_private_values(tmp_path, expected, change):
    path = tmp_path / 'expected.json'
    path.write_text(json.dumps({**expected, **change}))
    with pytest.raises(verify.VerificationError) as caught:
        verify.load_expected(path)
    assert 'SECRET' not in str(caught.value)


def test_oversized_reference_is_rejected(tmp_path):
    path = tmp_path / 'large.json'
    path.write_text(' ' * 65_537)
    with pytest.raises(verify.VerificationError, match='too_large'):
        verify.load_expected(path)


def test_generalized_live_checks_keep_auth_denial_tools_data_and_pagination_checks(monkeypatch, tmp_path, expected):
    login = tmp_path / 'login.json'
    login.write_text(json.dumps({'tokens': {'access_token': 'synthetic.access.token'}}))
    monkeypatch.setattr(verify, 'TOKEN_FILE', login)
    calls, denied = [], []
    workout = {'id': expected['workout_id'], 'name': expected['workout_name'],
               'local_date': expected['local_date'], 'metrics': expected['metrics'],
               'record_count': expected['record_count']}
    annotations = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
    def handler(request):
        if request.headers.get('authorization') != 'Bearer synthetic.access.token':
            denied.append(request.headers.get('authorization'))
            return httpx.Response(401)
        body = json.loads(request.content)
        if body['method'] == 'notifications/initialized':
            return httpx.Response(204)
        if body['method'] == 'initialize':
            result = {'protocolVersion': '2025-11-25'}
        elif body['method'] == 'tools/list':
            result = {'tools': [{'name': name, 'annotations': annotations,
                      '_meta': {'securitySchemes': [{'type': 'oauth2', 'scopes': ['coach:read']}]}}
                      for name in verify.TOOLS]}
        else:
            name, arguments = body['params']['name'], body['params']['arguments']
            calls.append((name, arguments))
            payloads = {
                'get_coach_context': {'response_version': 2, 'training_summaries': {},
                                      'summary_freshness': {'stale': False}},
                'get_training_summary': {'summary': {'period': 'week', 'start_date': '2024-02-26',
                      'end_date': '2024-03-03', 'totals': expected['week_totals']}},
                'list_completed_workouts': {'items': [workout]},
                'get_workout_details': workout,
                'get_workout_samples': {'items': [{'timestamp': f'2024-02-29T10:00:0{i}Z', 'heart_rate': 120}
                      for i in range(5)], 'total': expected['record_count'], 'next_offset': 5},
                'list_planned_workouts': {'items': []}, 'list_wellness': {'items': []},
            }
            result = {'content': [{'type': 'text', 'text': json.dumps(payloads[name])}]}
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})
    client_type = httpx.Client
    monkeypatch.setattr(verify.httpx, 'Client', lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
    report = {}
    verify.verify(report, expected)
    assert len(denied) == 2 and set(name for name, _ in calls) == verify.TOOLS
    assert dict(calls)['list_planned_workouts'] == {'oldest': '2024-03-01', 'newest': '2024-03-07', 'limit': 50}
    assert dict(calls)['get_workout_details'] == {'workout_id': expected['workout_id']}
    assert report['expected_workout_verified'] is True and report['week_totals_verified'] is True
    assert not {'distance_m', 'moving_time_s', 'average_heart_rate_bpm'} & set(report)
    assert 'synthetic.access.token' not in json.dumps(report)


def test_budget_uses_explicit_account_and_retains_project_warning_policy():
    class FakeCloud:
        def __init__(self):
            self.calls = []
            self.saved = None
        def rest(self, method, url, body=None):
            self.calls.append((method, url, deepcopy(body)))
            if method == 'GET' and self.saved is None:
                return {'budgets': []}
            if method == 'POST':
                self.saved = {**deepcopy(body), 'name': 'billingAccounts/ABCDEF-ABCDEF-ABCDEF/budgets/test'}
            return self.saved
    cloud = FakeCloud()
    result = budget.configure_budget(cloud, 'ABCDEF-ABCDEF-ABCDEF')
    assert all('/billingAccounts/ABCDEF-ABCDEF-ABCDEF/' in url for _, url, _ in cloud.calls)
    assert result['amount']['specifiedAmount'] == {'currencyCode': 'NOK', 'units': '350'}
    assert result['budgetFilter']['projects'] == [f'projects/{budget.PROJECT_NUMBER}']
    assert result['notificationsRule']['enableProjectLevelRecipients'] is True
    assert result['thresholdRules'] == [{'thresholdPercent': 1.0, 'spendBasis': 'CURRENT_SPEND'}]
    cloud.calls.clear()
    with pytest.raises(ValueError):
        budget.configure_budget(cloud, '../invalid')
    assert cloud.calls == []
