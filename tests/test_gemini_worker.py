"""Failure-focused worker protocol and release evidence checks; no paid model calls."""
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gemini_worker as worker
import harness_release
import harness_jobs as jobs
import gemini_gate


def stream(**changes):
    result = {'conversation_id': 'example-session', 'status': 'SUCCESS', 'response': 'Reviewed the supplied file.'}
    result.update(changes)
    return [{'event': 'init', 'init': {'tools': ['view_file']}}, {'event': 'result', 'result': result}]


@pytest.mark.parametrize('changes', [
    {'response': ''}, {'response': ' '}, {'response': 'x' * 16001},
    {'status': 'RUNNING'}, {'status': 'WAITING'}, {'status': 'ERROR'},
    {'denied_actions': [{'action': 'command'}]}, {'error': 'failure'},
])
def test_exit_zero_cannot_hide_failed_or_incomplete_run(changes):
    assert worker.classify(stream(**changes), 0)['status'] == 'failed'


def test_read_result_is_only_ready_for_review_never_completed():
    result = worker.classify(stream(), 0)
    assert result['status'] == 'needs-review'
    assert result['conversation_id'] == 'example-session'
    assert worker.classify(stream(), 1)['status'] == 'failed'
    assert worker.classify(stream(), 0, 'timeout')['status'] == 'failed'


def test_missing_duplicate_and_tool_error_results_fail():
    assert worker.classify([], 0)['status'] == 'failed'
    events = stream()
    assert worker.classify(events + [events[-1]], 0)['status'] == 'failed'
    events.insert(1, {'event': 'step_update', 'step_update': {'tool_info': {'error': {'message': 'Denied'}}}})
    assert worker.classify(events, 0)['status'] == 'failed'


def test_failed_tool_state_without_error_payload_still_fails():
    events = stream()
    events.insert(1, {'event': 'step_update', 'step_update': {'step_type': 'tool', 'state': 'FAILED'}})
    assert worker.classify(events, 0)['status'] == 'failed'


def run_fake(tmp_path, code, timeout=3, expected_tools=None):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    logs = tmp_path / 'logs'
    logs.mkdir()
    return worker.run([sys.executable, '-c', code], workspace, logs, timeout, expected_tools), logs


def test_actual_process_stream_and_evidence_capture(tmp_path):
    code = "import json,os; print(json.dumps({'event':'init','init':{'cwd':os.getcwd(),'tools':['view_file']}})); "
    code += "print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'File reviewed'}}))"
    result, logs = run_fake(tmp_path, code, expected_tools={'view_file'})
    assert result['status'] == 'needs-review'
    assert json.loads((logs / 'result.json').read_text())['exit_code'] == 0
    assert (logs / 'process.json').is_file()


def test_scope_mismatch_is_stopped(tmp_path):
    code = "import json,os,time; print(json.dumps({'event':'init','init':{'cwd':os.getcwd(),'tools':['run_command']}}),flush=True); time.sleep(10)"
    started = time.monotonic()
    result, _ = run_fake(tmp_path, code, expected_tools={'view_file'})
    assert result['status'] == 'failed' and 'scope' in result['error']
    assert time.monotonic() - started < 5


def test_real_outer_deadline_catches_hung_process(tmp_path):
    result, logs = run_fake(tmp_path, 'import time; time.sleep(10)', timeout=0.15)
    assert result['status'] == 'failed' and 'deadline' in result['error']
    assert result['exit_code'] is not None
    assert json.loads((logs / 'result.json').read_text())['status'] == 'failed'


def test_zero_exit_partial_stream_fails(tmp_path):
    result, _ = run_fake(tmp_path, "print('{broken')")
    assert result['status'] == 'failed'


def test_output_budget_stops_process(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'MAX_OUTPUT', 1000)
    result, _ = run_fake(tmp_path, "print('x'*10000)")
    assert result['status'] == 'failed' and '2 MB' in result['error']


def receipt(tmp_path, monkeypatch, **changes):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    path = tmp_path / '.local/deployments/260925-test/summary.json'
    path.parent.mkdir(parents=True)
    data = {'mode': 'deploy', 'status': 'released', 'phase': 'verified', 'github_ci_passed': True,
            'both_candidates_verified': True, 'production_verified': True, 'runtime_configuration_preserved': True,
            'iam_preserved': True, 'private_backend_health_verified': True,
            'check_receipt_sha256': '1' * 64, 'check_integrity_digest': '2' * 64,
            'commit': 'a' * 40, 'pull_request': 'https://github.com/example/repo/pull/1',
            'revisions': {'ai-coach-sync': 'sync-1', 'ai-coach-chat': 'chat-1'}}
    data.update(changes)
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize('changes', [{'status': 'failed'}, {'mode': 'check'}, {'phase': 'ci-passed'},
                                     {'production_verified': False},
                                     {'github_ci_passed': False}, {'revisions': {}}, {'commit': ''}])
def test_release_failure_cannot_be_completed(tmp_path, monkeypatch, changes):
    path = receipt(tmp_path, monkeypatch, **changes)
    with pytest.raises(ValueError, match='not verified complete'):
        harness_release.verified_release(path)


def test_release_evidence_is_recorded_with_hash_and_limited_claim(tmp_path, monkeypatch):
    path = receipt(tmp_path, monkeypatch)
    result = harness_release.verified_release(path)
    assert len(result['receipt_sha256']) == 64
    assert 'not a fresh cloud read' in result['scope']
    assert harness_release.latest_release()['status'] == 'released'


def test_symlink_receipt_rejected(tmp_path, monkeypatch):
    path = receipt(tmp_path, monkeypatch)
    original = path.with_name('original.json')
    path.rename(original)
    path.symlink_to(original)
    with pytest.raises(ValueError):
        harness_release.read_receipt(path)


@pytest.mark.parametrize('payload', [
    {'toolCall': {'name': 'run_command', 'args': {'CommandLine': 'echo test'}}},
    {'toolCall': {'name': 'write_to_file', 'args': {'TargetFile': 'source.md'}}},
    {'toolCall': {'name': 'view_file', 'args': {'AbsolutePath': '/etc/passwd'}}},
    {'toolCall': {'name': 'invoke_subagent', 'args': {}}},
])
def test_gate_denies_commands_writes_outside_reads_and_delegation(tmp_path, payload):
    assert gemini_gate.decision(payload, tmp_path)['decision'] == 'deny'


def test_gate_allows_exact_source_but_not_symlink(tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('review data')
    payload = {'toolCall': {'name': 'view_file', 'args': {'AbsolutePath': str(source)}}}
    assert gemini_gate.decision(payload, tmp_path)['decision'] == 'allow'
    source.unlink()
    source.symlink_to(tmp_path / 'outside')
    assert gemini_gate.decision(payload, tmp_path)['decision'] == 'deny'


def test_old_or_wrong_commit_receipt_cannot_complete_new_task(tmp_path, monkeypatch):
    path = receipt(tmp_path, monkeypatch, started_at='260925-120000-abcd')
    with pytest.raises(ValueError, match='predates'):
        harness_release.verified_release(path, not_before='2026-09-25T12:00:01+00:00')
    with pytest.raises(ValueError, match='current committed'):
        harness_release.verified_release(path, expected_commit='b' * 40)
    assert harness_release.verified_release(path, expected_commit='a' * 40,
                                           not_before='2026-09-25T11:59:59+00:00')['commit'] == 'a' * 40


@pytest.mark.parametrize('payload', [None, 'invalid', []])
def test_invalid_event_payload_cannot_crash_supervisor(tmp_path, payload):
    code = 'import json; print(json.dumps(' + repr({'event': 'init', 'init': payload}) + '))'
    result, _ = run_fake(tmp_path, code)
    assert result['status'] == 'failed'
    assert result['process_reaped'] is True
