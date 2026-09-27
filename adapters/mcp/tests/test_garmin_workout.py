"""Decode real generated FIT bytes with an independent implementation."""
import base64
import io

import fitdecode
import pytest

from ai_coach_mcp.garmin_workout import to_fit
from ai_coach_mcp.running_workout_schema import RunningWorkout
from ai_coach_mcp.quick_workout_service import workout_payload
from test_running_workout import running_dict


def decode(data):
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        return [(m.name, {f.name: f.value for f in m.fields}) for m in reader
                if isinstance(m, fitdecode.FitDataMessage)]


def test_workout_messages_targets_and_duration():
    plan = RunningWorkout(**running_dict())
    data = to_fit(plan)
    messages = decode(data)
    assert [m[0] for m in messages] == ['file_id', 'workout', *['workout_step'] * 5]
    assert messages[0][1]['type'] == 'workout'
    assert messages[1][1]['sport'] == 'running'
    assert messages[1][1]['num_valid_steps'] == 5
    assert messages[1][1]['wkt_name'] == plan.title
    steps = [row for name, row in messages if name == 'workout_step']
    assert [s['message_index'] for s in steps] == [0, 1, 2, 3, 4]
    assert [s['duration_type'] for s in steps] == ['open', 'time', 'time', 'time', 'open']
    assert sum(s['duration_time'] for s in steps if s['duration_type'] == 'time') == plan.duration_s
    assert steps[0]['duration_value'] == steps[-1]['duration_value'] == 0
    assert all('duration_time' not in s for s in (steps[0], steps[-1]))
    assert [s['intensity'] for s in steps] == ['warmup', 'active', 'recovery', 'active', 'cooldown']
    assert all(s['target_type'] == 'open' for s in steps)
    assert all(not any('power' in key for key in s) for s in steps)
    assert 'Press LAP' in steps[0]['notes'] and 'Press LAP' in steps[-1]['notes']
    assert [s['duration_time'] for s in steps[1:-1]] == [180, 120, 180]
    assert data[8:12] == b'.FIT'


def test_exports_share_plan_and_rest_has_no_files():
    plan = RunningWorkout(**running_dict())
    result = workout_payload(plan)
    assert result['garmin_filename'] == 'quick-run-2026-09-25.fit'
    assert decode(base64.b64decode(result['fit_base64']))[1][1]['num_valid_steps'] == len(plan.steps)
    assert result['zwo'] is None and result['plan'] == plan.model_dump(mode='json')
    rest = RunningWorkout(**{**running_dict(), 'decision': 'rest', 'steps': []})
    result = workout_payload(rest)
    assert all(result[key] is None for key in ('filename', 'zwo', 'garmin_filename', 'fit_base64'))
    with pytest.raises(ValueError, match='Rest'):
        to_fit(rest)


def test_deterministic_files_and_distinct_workout_identity():
    plan = RunningWorkout(**running_dict())
    a = to_fit(plan)
    assert a == to_fit(plan)
    changed = RunningWorkout(**{**running_dict(), 'title': 'Other interval session'})
    assert decode(a)[0][1]['serial_number'] != decode(to_fit(changed))[0][1]['serial_number']


def test_non_ascii_title_stays_valid_utf8_at_byte_limit():
    plan = RunningWorkout(**{**running_dict(), 'title': '🚲' * 79})
    assert decode(to_fit(plan))[1][1]['wkt_name'] == '🚲' * 20


def test_corruption_is_detected_by_decoder():
    data = bytearray(to_fit(RunningWorkout(**running_dict())))
    data[-3] ^= 1
    with pytest.raises(fitdecode.FitCRCError):
        decode(data)


def test_mutated_model_is_revalidated_before_export():
    plan = RunningWorkout(**running_dict())
    plan.steps[0].duration_s = 300
    with pytest.raises(ValueError):
        to_fit(plan)
