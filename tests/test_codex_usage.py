"""Synthetic logs only: never read the developer's actual Codex conversations."""
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    usage = importlib.import_module('codex_usage')
    workload = importlib.import_module('workload')
    report = importlib.import_module('workload_report')
    monkeypatch.setattr(workload, 'DIRECTORY', tmp_path / 'report')
    return usage, workload, report


def event(kind, payload, second=0):
    return {'timestamp': f'2026-09-23T12:00:{second:02d}Z', 'type': kind, 'payload': payload}


def log_rows(project):
    return [
        event('session_meta', {'id': 'session-one', 'cwd': str(project), 'instructions': 'PRIVATE'}),
        event('event_msg', {'type': 'task_started', 'turn_id': 'turn-one'}),
        event('turn_context', {'turn_id': 'turn-one', 'model': 'test-model', 'instructions': 'PRIVATE'}),
        event('response_item', {'role': 'user', 'content': 'SECRET PROMPT'}),
    ]


def usage_row(turn='turn-one', inp=100, cached=60, out=20, second=1):
    return event('token_usage_record', {
        'thread_id': 'session-one', 'turn_id': turn, 'response_id': 'response',
        'usage': {'input_tokens': 999999},  # Per-call usage must not be added to snapshots.
        'thread_token_usage': {'total_tokens': 999999},
        'turn_token_usage': {'input_tokens': inp, 'cached_input_tokens': cached,
                             'output_tokens': out, 'reasoning_output_tokens': 5,
                             'total_tokens': inp + out},
    }, second)


def write_log(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def test_latest_turn_snapshot_deduplicates_and_filters_private_content(modules, tmp_path):
    usage, _, _ = modules
    path = tmp_path / 'rollout-one.jsonl'
    rows = log_rows(tmp_path) + [usage_row(), usage_row(), usage_row(inp=300, cached=250, out=40, second=2)]
    rows += [event('event_msg', {'type': 'task_complete', 'turn_id': 'turn-one', 'last_agent_message': 'SECRET RESPONSE'}, 3)]
    write_log(path, rows)
    result = usage.read_session(path, tmp_path)
    assert len(result) == 1
    assert result[0]['prompt_tokens'] == 300
    assert result[0]['total_tokens'] == 340
    assert result[0]['cached_input_tokens'] == 250
    assert result[0]['output_tokens'] == 40
    assert result[0]['status'] == 'done'
    assert 'SECRET' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)


def test_turns_are_separate_despite_compaction_or_thread_counters(modules, tmp_path):
    usage, _, _ = modules
    path = tmp_path / 'rollout-one.jsonl'
    rows = log_rows(tmp_path) + [usage_row(), event('compacted', {'message': 'PRIVATE'})]
    rows += [event('event_msg', {'type': 'task_started', 'turn_id': 'turn-two'}, 2), usage_row('turn-two', inp=50, cached=0, second=3)]
    write_log(path, rows)
    result = usage.read_session(path, tmp_path)
    assert [t['prompt_tokens'] for t in result] == [100, 50]
    assert result[-1]['status'] == 'running'


def test_old_missing_and_partial_records_stay_unknown(modules, tmp_path):
    usage, _, _ = modules
    path = tmp_path / 'rollout-one.jsonl'
    write_log(path, log_rows(tmp_path) + [event('event_msg', {'type': 'token_count', 'info': {'total_tokens': 999}})])
    with path.open('a') as handle:
        handle.write('{"type":"token_usage_record"')
    result = usage.read_session(path, tmp_path)
    assert len(result) == 1
    assert 'prompt_tokens' not in result[0]


@pytest.mark.parametrize('bad', [True, -1, '100', 1.2])
def test_invalid_counters_not_published(modules, tmp_path, bad):
    usage, _, _ = modules
    record = usage_row()
    record['payload']['turn_token_usage']['input_tokens'] = bad
    path = tmp_path / 'rollout-one.jsonl'
    write_log(path, log_rows(tmp_path) + [record])
    assert 'prompt_tokens' not in usage.read_session(path, tmp_path)[0]


def test_inconsistent_cache_and_foreign_thread_not_counted(modules, tmp_path):
    usage, _, _ = modules
    path = tmp_path / 'rollout-one.jsonl'
    foreign = usage_row()
    foreign['payload']['thread_id'] = 'other-session'
    write_log(path, log_rows(tmp_path) + [usage_row(cached=101), foreign])
    assert 'prompt_tokens' not in usage.read_session(path, tmp_path)[0]


def test_project_filter_and_incremental_discovery(modules, tmp_path):
    usage, _, _ = modules
    sessions, project = tmp_path / 'sessions', tmp_path / 'project'
    write_log(sessions / 'rollout-foreign.jsonl', log_rows(tmp_path / 'project-other') + [usage_row()])
    reader = usage.SessionReader(sessions, project)
    assert reader.read() == []
    path = sessions / 'new' / 'rollout-project.jsonl'
    write_log(path, log_rows(project / 'scripts') + [usage_row()])
    assert len(reader.read()) == 1
    write_log(path, log_rows(project) + [usage_row(inp=500, out=60)])
    assert reader.read()[0]['total_tokens'] == 560
    path.unlink()
    assert reader.read() == []


def test_sync_is_idempotent_and_manual_and_local_tasks_survive(modules, tmp_path):
    usage, workload, _ = modules
    path = tmp_path / 'sessions' / 'rollout-one.jsonl'
    write_log(path, log_rows(tmp_path) + [usage_row()])
    reader = usage.SessionReader(path.parent, tmp_path)
    workload.record_task('codex', 'Review draft', 'done', task_id='review')
    usage.sync(reader, workload.DIRECTORY)
    snapshot = workload.DIRECTORY / 'codex_usage.json'
    stamp = snapshot.stat().st_mtime_ns
    usage.sync(reader, workload.DIRECTORY)
    assert snapshot.stat().st_mtime_ns == stamp
    workload.record_task('gpt-oss', 'Local draft', 'done', task_id='local', prompt_tokens=50, output_tokens=10)
    workload.rebuild()
    result = (workload.DIRECTORY / 'workload.html').read_text()
    assert 'Review draft' in result and 'Local draft' in result and 'Prompt 1' in result
    assert len(workload._read_tasks(workload.DIRECTORY)) == 2


def test_renderer_handles_unknown_escapes_and_does_not_double_count(modules):
    _, _, report = modules
    unknown = report.render_codex_usage([{'title': '<script>alert(1)</script>', 'model': None, 'started_at': 'bad'}])
    assert '<script>' not in unknown and '&lt;script&gt;' in unknown
    assert '<p>0</p>' not in unknown
    result = report.render_codex_usage([{'prompt_tokens': 1000, 'cached_input_tokens': 600, 'output_tokens': 20, 'total_tokens': 1020}])
    assert '<td>1,000</td><td>600</td><td>400</td><td>20</td><td>1,020</td>' in result
    assert 'No measured Codex turns yet' in report.render_codex_usage([])
