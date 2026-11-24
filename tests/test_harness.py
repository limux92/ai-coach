"""Exercise source boundaries, explicit worker routing and review lifecycle offline."""
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import harness
import harness_jobs as jobs
import local_worker
import gemini_jobs
import gemini_collaborator
import gemini_collaborator_gate
import harness_context
from harness_report import render_jobs


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, 'DIRECTORY', tmp_path / '.local/worker/jobs')
    monkeypatch.setattr(local_worker, 'ROOT', tmp_path)
    (tmp_path / 'sample.py').write_text('def add(a, b):\n    return a + b\n')
    monkeypatch.setattr(jobs.subprocess, 'check_output', lambda *a, **k: b'sample.py\0')
    monkeypatch.setenv('TERM_PROGRAM', 'vscode')
    monkeypatch.setattr(harness.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    return tmp_path


def create(**kwargs):
    return jobs.create('Small draft', 'Explain addition', 'One accurate sentence', 'sample.py', **kwargs)


def fake_worker(monkeypatch, code=0, change_source=False):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert isinstance(command, list) and kwargs.get('shell') is not True
        output = Path(command[command.index('--output') + 1])
        output.write_text("raise RuntimeError('This draft must never execute')\n")
        if change_source:
            (jobs.ROOT / 'sample.py').write_text('# concurrent edit\n')
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(harness.subprocess, 'run', run)
    return calls


def test_local_lifecycle_never_applies_or_executes_draft(workspace, monkeypatch):
    original = (workspace / 'sample.py').read_bytes()
    job = create()
    calls = fake_worker(monkeypatch)
    assert harness.run_local(job) == 0
    restored = jobs.load(job['id'])
    assert restored['status'] == 'needs-review'
    assert restored['attempts'][0]['draft_sha256']
    harness.review(restored, 'accepted', 'Inspected synthetic output; no execution needed')
    assert jobs.load(job['id'])['status'] == 'accepted'
    assert (workspace / 'sample.py').read_bytes() == original
    assert len(calls) == 1
    with pytest.raises(ValueError, match='already accepted'):
        harness.run_local(restored, 'Another change')
    (workspace / 'sample.py').write_text('# Primary assistant integrated a reviewed change\n')
    harness.complete(restored, 'Focused integration checks passed')
    assert jobs.load(job['id'])['status'] == 'completed'
    assert len(calls) == 1


def test_completion_allows_shorter_integrated_source(workspace, monkeypatch):
    job = jobs.create('Refactor', 'Simplify', 'Same behavior', 'sample.py:2:2')
    fake_worker(monkeypatch)
    harness.run_local(job)
    harness.review(job, 'accepted', 'Draft inspected')
    (workspace / 'sample.py').write_text('def add(a, b): return a + b\n')
    harness.complete(job, 'Function behavior checked after integration')
    assert jobs.load(job['id'])['status'] == 'completed'


def test_gemini_planning_and_handoff_do_not_launch_any_process(workspace, monkeypatch):
    job = create(task='review')
    calls = fake_worker(monkeypatch)
    assert job['provider'] == 'gemini' and job['status'] == 'ready'
    with pytest.raises(ValueError, match='run dispatch'):
        harness.run_local(job)
    harness.handoff(job)
    assert calls == []
    assert 'Explain addition' in (jobs.DIRECTORY / job['id'] / 'handoff.md').read_text()
    assert jobs.load(job['id'])['status'] == 'ready'


def test_codex_job_can_record_primary_work_without_worker_launch(workspace, monkeypatch):
    job = create(task='architecture')
    calls = fake_worker(monkeypatch)
    with pytest.raises(ValueError, match='primary conversation'):
        harness.run_local(job)
    harness.complete(job, 'Primary assistant completed architecture review')
    assert jobs.load(job['id'])['status'] == 'completed'
    assert not calls


@pytest.mark.parametrize('task', jobs.CODEX_TASKS)
def test_sensitive_categories_cannot_be_routed_to_workers(task):
    assert jobs.route(task) == 'codex'
    for provider in ('local', 'gemini'):
        with pytest.raises(ValueError, match='stay with Codex'):
            jobs.route(task, provider)


def test_source_change_prevents_inference(workspace, monkeypatch):
    job = create()
    (workspace / 'sample.py').write_text('# new source\n')
    calls = fake_worker(monkeypatch)
    with pytest.raises(ValueError, match='Source changed'):
        harness.run_local(job)
    assert calls == []
    assert jobs.load(job['id'])['attempts'] == []


def test_source_change_during_inference_marks_draft_stale(workspace, monkeypatch):
    job = create()
    fake_worker(monkeypatch, change_source=True)
    assert harness.run_local(job) == 2
    assert jobs.load(job['id'])['status'] == 'stale'
    with pytest.raises(ValueError, match='Only complete'):
        harness.review(job, 'accepted', 'Review')


def test_modified_draft_cannot_be_accepted(workspace, monkeypatch):
    job = create()
    fake_worker(monkeypatch)
    harness.run_local(job)
    (jobs.DIRECTORY / job['id'] / 'draft-1.txt').write_text('changed output')
    with pytest.raises(ValueError, match='Draft changed'):
        harness.review(job, 'accepted', 'Reviewed')


def test_incomplete_output_and_one_explicit_correction_limit(workspace, monkeypatch):
    job = create()
    calls = fake_worker(monkeypatch, code=2)
    assert harness.run_local(job) == 2
    with pytest.raises(ValueError, match='Only complete'):
        harness.review(job, 'accepted', 'Inspected')
    with pytest.raises(ValueError, match='requires concise'):
        harness.run_local(job)
    assert len(calls) == 1
    assert harness.run_local(job, 'Return a shorter answer') == 2
    with pytest.raises(ValueError, match='Two attempts'):
        harness.run_local(job, 'Try again')
    assert len(calls) == 2


def test_terminal_visibility_required(workspace, monkeypatch):
    job = create()
    calls = fake_worker(monkeypatch)
    monkeypatch.delenv('TERM_PROGRAM')
    with pytest.raises(ValueError, match='visible VS Code'):
        harness.run_local(job)
    assert not calls


@pytest.mark.parametrize('source', ['../sample.py', '/tmp/sample.py', '.local/private.json',
                                  'data/activity.json', '.env', 'credentials.json', 'untracked.py'])
def test_private_untracked_and_outside_sources_rejected(workspace, source):
    with pytest.raises(ValueError):
        jobs.source_snapshot(source)


def test_symlink_source_and_symlink_job_directory_rejected(workspace):
    (workspace / 'alias.py').symlink_to(workspace / 'sample.py')
    with pytest.raises(ValueError, match='Symlink'):
        jobs.source_snapshot('alias.py')
    jobs.DIRECTORY.parent.mkdir(parents=True)
    jobs.DIRECTORY.symlink_to(workspace)
    with pytest.raises(ValueError, match='private jobs directory'):
        create()


def test_oversized_combined_context_rejected(workspace):
    (workspace / 'sample.py').write_text('#' * 11500)
    with pytest.raises(ValueError, match='exceed 12 KB'):
        jobs.create('Task', 'g' * 1000, 'Check it', 'sample.py')
    assert not list(jobs.DIRECTORY.glob('*.json'))


def test_prompt_shell_characters_pass_as_literal_argument(workspace, monkeypatch):
    job = jobs.create('Literal prompt', 'Explain $(touch /tmp/nope) and `false`', 'Do not execute', 'sample.py')
    calls = fake_worker(monkeypatch)
    harness.run_local(job)
    assert '$(touch /tmp/nope)' in calls[0][2]


def test_lock_prevents_overlapping_harness_requests(workspace):
    with harness.locked():
        with pytest.raises(ValueError, match='Another harness operation'):
            with harness.locked():
                pytest.fail('Second process must not enter')


def test_report_escapes_metadata_and_omits_private_brief():
    rendered = render_jobs([{'id': 'a', 'title': '<script>alert(1)</script>', 'review': None,
                             'prompt': 'private prompt', 'source': 'private source', 'attempts': []}])
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert 'private prompt' not in rendered and 'private source' not in rendered
    assert 'Accepted means reviewed, not applied' in rendered
    assert 'No jobs yet' in render_jobs([])


def test_invalid_record_id_rejected(workspace):
    with pytest.raises(ValueError, match='32-character'):
        jobs.load('../../other')
    job = create()
    data = json.loads(jobs.job_path(job['id']).read_text())
    data['id'] = 'b' * 32
    jobs.job_path(job['id']).write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Invalid job'):
        jobs.load(job['id'])


@pytest.mark.parametrize('audit', ['allowed', 'missing', 'denied'])
def test_gemini_packet_requires_gate_and_never_executes_result(workspace, monkeypatch, audit):
    memo = workspace / harness_context.RELATIVE_PATH
    memo.parent.mkdir(parents=True)
    memo.write_text('Shared objective: verified infrastructure outcomes.')
    job = create(task='review')
    source = (workspace / 'sample.py').read_bytes()
    monkeypatch.setattr(gemini_jobs, 'cli_path', lambda: '/example/agy')
    packets = []

    def run(command, packet, directory, timeout):
        packets.append(packet)
        assert '--sandbox' in command and '--conversation' not in command
        assert 'Shared objective: verified infrastructure outcomes.' in command[command.index('--print') + 1]
        assert command[command.index('--model') + 1] == gemini_jobs.MODEL
        assert (packet / 'source.md').is_file() and (packet / '.agents/hooks.json').is_file()
        assert packet != workspace and timeout == 125
        if audit != 'missing':
            (packet / 'gate.jsonl').write_text(json.dumps({'tool': 'view_file',
                'decision': 'allow' if audit == 'allowed' else 'deny'}) + '\n')
        return {'status': 'needs-review', 'error': None, 'response': 'raise RuntimeError("never execute")',
                'completed_tools': ['view_file'], 'process_reaped': True}

    monkeypatch.setattr(gemini_jobs.gemini_worker, 'run', run)
    assert gemini_jobs.run_job(job, harness.artifact, harness.report) == (0 if audit == 'allowed' else 2)
    assert job['status'] == ('needs-review' if audit == 'allowed' else 'failed')
    assert (workspace / 'sample.py').read_bytes() == source
    assert not packets[0].exists()
    result = json.loads((jobs.DIRECTORY / job['id'] / 'gemini-1/result.json').read_text())
    assert result['status'] == job['status']
    with pytest.raises(ValueError, match='requires concise'):
        gemini_jobs.run_job(job, harness.artifact, harness.report)


def test_gemini_multi_source_packet_is_exact_and_digest_bound(workspace, monkeypatch):
    (workspace / 'other.py').write_text('VALUE = 2\n')
    monkeypatch.setattr(jobs.subprocess, 'check_output', lambda *a, **k: b'sample.py\0other.py\0')
    job = jobs.create('Two-file review', 'Compare behavior', 'Mention both files',
                      ['sample.py', 'other.py'], task='review')
    context, digest = jobs.sources_snapshot(job)
    assert 'FILE sample.py' in context and 'FILE other.py' in context
    assert digest == job['source_sha256']
    assert job['sources'] == ['sample.py', 'other.py']
    with pytest.raises(ValueError, match='one source selection'):
        jobs.create('Too broad locally', 'Implement', 'Done',
                    ['sample.py', 'other.py'], task='implement')


def test_gemini_packet_preflight_failure_does_not_use_attempt(workspace, monkeypatch):
    job = create(task='review')
    monkeypatch.setattr(gemini_jobs, 'cli_path', lambda: '/example/agy')
    monkeypatch.setattr(gemini_jobs, 'prepare_workspace',
                        lambda *args: (_ for _ in ()).throw(OSError('setup failed')))
    with pytest.raises(OSError, match='setup failed'):
        gemini_jobs.run_job(job, harness.artifact, harness.report)
    assert job['attempts'] == []
    assert jobs.load(job['id'])['attempts'] == []


def test_collaborator_gate_allows_only_exact_paths_and_commands(workspace):
    wrapper = workspace / '.agents/gemini-safe-command.py'
    policy = {'root': str(workspace), 'readable': ['sample.py'], 'editable': ['sample.py'],
              'commands': {'status': str(wrapper) + ' status'}}
    allowed_read = {'toolCall': {'name': 'view_file', 'args': {'AbsolutePath': str(workspace / 'sample.py')}}}
    denied_read = {'toolCall': {'name': 'view_file', 'args': {'AbsolutePath': str(workspace / '.env')}}}
    allowed_write = {'toolCall': {'name': 'write_to_file', 'args': {'TargetFile': str(workspace / 'sample.py')}}}
    allowed_command = {'toolCall': {'name': 'run_command', 'args': {'CommandLine': str(wrapper) + ' status'}}}
    assert gemini_collaborator_gate.decide(allowed_read, policy)[2]['decision'] == 'allow'
    assert gemini_collaborator_gate.decide(allowed_write, policy)[2]['decision'] == 'allow'
    assert gemini_collaborator_gate.decide(allowed_command, policy)[2]['decision'] == 'allow'
    assert gemini_collaborator_gate.decide(denied_read, policy)[2]['decision'] == 'deny'


def test_collaborator_setup_failure_restores_settings_and_does_not_create_attempt(workspace, monkeypatch):
    (workspace / 'scripts').mkdir()
    (workspace / 'scripts/gemini_collaborator_gate.py').write_text('# gate\n')
    settings = workspace / 'settings.json'
    original = b'{"permissions":{"allow":"invalid"}}\n'
    settings.write_bytes(original)
    monkeypatch.setattr(gemini_collaborator, 'SETTINGS', settings)
    job = jobs.create('Scoped edit', 'Edit the sample', 'Keep scope exact', 'sample.py',
                      task='implement', provider='gemini', editable=['sample.py'], commands=['status'])
    with pytest.raises(ValueError, match='permissions.allow'):
        gemini_collaborator.prepare_repository(job, ['status'])
    assert settings.read_bytes() == original
    assert not (workspace / '.agents').exists()
    assert job['attempts'] == []


def test_gemini_requires_visible_terminal_and_terminated_prior_worker(workspace, monkeypatch):
    job = create(task='review')
    monkeypatch.delenv('TERM_PROGRAM')
    with pytest.raises(ValueError, match='visible VS Code'):
        gemini_jobs.run_job(job, harness.artifact, harness.report)
    monkeypatch.setenv('TERM_PROGRAM', 'vscode')
    job['attempts'] = [{'process_reaped': False}]
    with pytest.raises(ValueError, match='termination is unconfirmed'):
        gemini_jobs.run_job(job, harness.artifact, harness.report, 'Reviewed error')


def test_recovery_refuses_a_present_process(workspace, monkeypatch):
    job = create(task='review')
    job.update(status='running', attempts=[{'status': 'running'}])
    directory = harness.artifact(job, 'gemini-1')
    directory.mkdir()
    (directory / 'process.json').write_text('{"pid": 12345}')
    monkeypatch.setattr(harness.os, 'kill', lambda *_: None)
    with pytest.raises(ValueError, match='still present'):
        harness.recover(job)
    assert job['status'] == 'running'


def test_release_completion_requires_receipt_before_marking_complete(workspace):
    job = create(task='release')
    with pytest.raises(ValueError, match='requires --release-receipt'):
        harness.complete(job, 'Agent said it deployed')
    assert job['status'] == 'needs-codex'


@pytest.mark.parametrize('uncommitted', [False, True])
def test_release_completion_binds_receipt_to_current_committed_source(workspace, monkeypatch, uncommitted):
    job = create(task='release')
    committed = (workspace / 'sample.py').read_bytes()
    if uncommitted:
        (workspace / 'sample.py').write_text('# feature not shipped\n')
    receipt = workspace / '.local/deployments/current/summary.json'
    receipt.parent.mkdir(parents=True)
    data = dict.fromkeys(['github_ci_passed', 'both_candidates_verified',
                          'production_verified', 'runtime_configuration_preserved', 'iam_preserved',
                          'private_backend_health_verified'], True)
    data.update(mode='deploy', status='released', phase='verified', commit='a' * 40,
                check_receipt_sha256='1' * 64, check_integrity_digest='2' * 64,
                pull_request='https://example.test/pr/1',
                revisions={'ai-coach-sync': 'sync-1', 'ai-coach-chat': 'chat-1'},
                started_at=datetime.fromisoformat(job['created_at']).strftime('%y%m%d-%H%M%S') + '-test')
    receipt.write_text(json.dumps(data))

    def git(command, **kwargs):
        return ('a' * 40 + '\n' if command[1] == 'rev-parse' else
                committed if command[1] == 'show' else b'sample.py\0')

    monkeypatch.setattr(jobs.subprocess, 'check_output', git)
    if uncommitted:
        with pytest.raises(ValueError, match='differs from the deployed'):
            harness.complete(job, 'Saved production checks', receipt)
        assert job['status'] == 'needs-codex'
    else:
        harness.complete(job, 'Saved production checks', receipt)
        assert job['status'] == 'completed' and job['integration']['release']['commit'] == 'a' * 40


def test_shared_context_is_frozen_and_sent_to_local_worker(workspace, monkeypatch):
    memo = workspace / harness_context.RELATIVE_PATH
    memo.parent.mkdir(parents=True)
    memo.write_text('Objective: first reviewed scope.')
    job = create()
    memo.write_text('Objective: next scope.')
    restored = jobs.load(job['id'])
    assert restored['shared_context']['text'] == 'Objective: first reviewed scope.'
    calls = fake_worker(monkeypatch)
    harness.run_local(restored)
    assert 'first reviewed scope' in calls[0][2] and 'next scope' not in calls[0][2]
    harness.handoff(restored)
    assert 'first reviewed scope' in (jobs.DIRECTORY / job['id'] / 'handoff.md').read_text()
    assert create()['shared_context']['text'] == 'Objective: next scope.'
    assert create(without_context=True)['shared_context'] is None


def test_changed_snapshot_is_rejected(workspace):
    memo = workspace / harness_context.RELATIVE_PATH
    memo.parent.mkdir(parents=True)
    memo.write_text('Verified facts only.')
    job = create()
    job['shared_context']['text'] = 'Unreviewed altered facts.'
    jobs.save(job)
    with pytest.raises(ValueError, match='snapshot changed'):
        jobs.load(job['id'])


@pytest.mark.parametrize('mode', ['oversize', 'symlink', 'parent_symlink'])
def test_shared_context_boundaries(workspace, mode):
    memo = workspace / harness_context.RELATIVE_PATH
    if mode == 'parent_symlink':
        (workspace / '.local').symlink_to(workspace, target_is_directory=True)
    else:
        memo.parent.mkdir(parents=True)
        if mode == 'symlink':
            memo.symlink_to(workspace / 'sample.py')
        else:
            memo.write_text('x' * 4001)
    with pytest.raises(ValueError, match='Shared context'):
        create()


def test_shared_context_counts_toward_total_input_budget(workspace):
    memo = workspace / harness_context.RELATIVE_PATH
    memo.parent.mkdir(parents=True)
    memo.write_text('x' * 4000)
    (workspace / 'sample.py').write_text('#' * 8500)
    with pytest.raises(ValueError, match='exceed 12 KB'):
        create()
    assert not list(jobs.DIRECTORY.glob('*.json'))


def test_legacy_job_does_not_pick_up_new_context(workspace):
    job = create()
    job.pop('shared_context')
    jobs.save(job)
    memo = workspace / harness_context.RELATIVE_PATH
    memo.write_text('New scope.')
    assert 'New scope' not in jobs.prepare(jobs.load(job['id']))[0]
