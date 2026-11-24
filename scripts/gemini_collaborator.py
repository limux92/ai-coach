"""Run a preflighted, exact-scope Gemini repository collaborator attempt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import gemini_worker
import harness_jobs as jobs

SETTINGS = Path.home() / '.gemini/antigravity-cli/settings.json'
PROFILE = '''---
name: ai-coach-collaborator
description: Repository-aware AI-Coach co-developer for one exact task slice.
tools:
  - view_file
  - write_to_file
  - replace_file_content
  - multi_replace_file_content
  - run_command
mainAgent: true
subagent: false
commandExecutionPolicy: "off"
---
Codex coordinates this task. Read only the allowed source, edit only the exact
editable files, and run only named safe-wrapper commands. Do not access private
paths, credentials, training records, networks, GitHub, Google Cloud, or delegate.
Report actual tool outcomes and any remaining uncertainty.
'''


def command_catalog(root):
    python = str(root / '.venv/bin/python')
    receipts = sorted((root / '.local/release-checks').glob('*/summary.json'), reverse=True)
    latest_receipt = str(receipts[0]) if receipts else str(root / '.local/release-checks/MISSING/summary.json')
    return {
        'status': ['git', 'status', '--short'],
        'diff-check': ['git', 'diff', '--check'],
        'release-tests': [python, '-m', 'pytest', '-q', 'tests/test_release.py',
                          'tests/test_release_split.py', 'tests/test_release_cloud.py',
                          'tests/test_release_scheduler.py'],
        'release-validate': [python, 'scripts/release_deploy.py', '--validate-only',
                             '--receipt', latest_receipt],
        'harness-tests': [python, '-m', 'pytest', '-q', 'tests/test_harness.py',
                          'tests/test_gemini_worker.py', 'tests/test_local_worker.py'],
        'backend-tests': [python, '-m', 'pytest', '-q'],
    }


def protected_snapshot(root, editable):
    """Bind all tracked non-target content and nonignored untracked paths."""
    tracked = [name for name in subprocess.check_output(
        ['git', 'ls-files', '-z'], cwd=root).decode().split('\0') if name and name not in editable]
    digests = {}
    for name in tracked:
        path = root / name
        if path.is_symlink():
            content = ('symlink:' + os.readlink(path)).encode()
        elif path.is_file():
            content = path.read_bytes()
        else:
            content = b'<missing>'
        digests[name] = hashlib.sha256(content).hexdigest()
    untracked = {name for name in subprocess.check_output(
        ['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
                 if name and not name.startswith('.agents/')}
    return digests, untracked


def write_wrapper(path, root, allowed):
    catalog = command_catalog(root)
    commands = {name: catalog[name] for name in allowed}
    text = ("#!/usr/bin/env python3\nimport subprocess,sys\nfrom pathlib import Path\n"
            f"root=Path({str(root)!r})\ncommands={commands!r}\n"
            "if len(sys.argv)!=2 or sys.argv[1] not in commands: raise SystemExit(2)\n"
            "raise SystemExit(subprocess.run(commands[sys.argv[1]],cwd=root,check=False).returncode)\n")
    path.write_text(text)
    path.chmod(0o700)


def prepare_repository(job, commands):
    root = jobs.ROOT.resolve()
    agents = root / '.agents'
    if agents.exists() or agents.is_symlink():
        raise ValueError('Remove or review the existing .agents task profile before collaborator setup')
    if not SETTINGS.is_file() or SETTINGS.is_symlink():
        raise ValueError('Antigravity settings file is missing or unsafe')
    original_settings = SETTINGS.read_bytes()
    settings = json.loads(original_settings)
    permissions = settings.get('permissions')
    if permissions is not None and not isinstance(permissions, dict):
        raise ValueError('Antigravity permissions setting must be an object or null')

    try:
        profile = agents / 'agents/ai-coach-collaborator.md'
        profile.parent.mkdir(parents=True)
        profile.write_text(PROFILE)
        wrapper = agents / 'gemini-safe-command.py'
        write_wrapper(wrapper, root, commands)
        policy_path = agents / 'collaborator-policy.json'
        audit = agents / 'collaborator-gate.jsonl'
        command_lines = {name: str(wrapper.resolve()) + ' ' + name for name in commands}
        readable = [spec.rsplit(':', 2)[0] if ':' in spec else spec for spec in jobs.selected_sources(job)]
        policy = {'root': str(root), 'readable': readable, 'editable': job['editable'],
                  'commands': command_lines, 'audit': str(audit)}
        policy_path.write_text(json.dumps(policy, indent=2) + '\n')
        gate = root / 'scripts/gemini_collaborator_gate.py'
        if not gate.is_file() or gate.is_symlink():
            raise ValueError('Tracked collaborator gate is missing or unsafe')
        hook = shlex.join([sys.executable, str(gate), str(policy_path)])
        (agents / 'hooks.json').write_text(json.dumps({
            'collaborator': {'PreToolUse': [{'matcher': '*', 'hooks': [
                {'type': 'command', 'command': hook, 'timeout': 5}]}]}}))

        permission_map = settings.setdefault('permissions', {})
        if permission_map is None:
            permission_map = settings['permissions'] = {}
        allow = permission_map.setdefault('allow', [])
        if not isinstance(allow, list):
            raise ValueError('Antigravity permissions.allow must be a list')
        grants = ['command(' + str(wrapper.resolve()) + ')', 'unsandboxed(' + str(wrapper.resolve()) + ')']
        if set(grants).intersection(allow):
            raise ValueError('Temporary collaborator permissions already exist; inspect settings first')
        allow.extend(grants)
        SETTINGS.write_text(json.dumps(settings, indent=2) + '\n')
        if not all(path.is_file() and not path.is_symlink()
                   for path in (profile, wrapper, policy_path, agents / 'hooks.json')):
            raise ValueError('Collaborator profile preflight failed')
        return agents, wrapper, audit, original_settings
    except BaseException:
        SETTINGS.write_bytes(original_settings)
        if agents.exists() and not agents.is_symlink():
            shutil.rmtree(agents)
        raise


def cleanup_repository(agents, original_settings):
    SETTINGS.write_bytes(original_settings)
    shutil.rmtree(agents)


def run_job(job, artifact, report, feedback, model, timeout, executable):
    if os.environ.get('TERM_PROGRAM') != 'vscode' or not sys.stdin.isatty():
        raise ValueError('Run Gemini collaborator jobs in the visible VS Code integrated terminal')
    editable, commands = jobs.collaborator_scope(job)
    if not editable and not commands:
        raise ValueError('Gemini collaborator job has no editable files or safe commands')
    if job['status'] in ('accepted', 'completed', 'running'):
        raise ValueError('Job is accepted, completed or already running')
    if job['attempts'] and job['attempts'][-1].get('process_reaped') is False:
        raise ValueError('Prior worker termination is unconfirmed; inspect the visible terminal before new work')
    if len(job['attempts']) >= 3:
        raise ValueError('Three Gemini attempts used; Codex must reassess')
    if job['attempts'] and not feedback.strip():
        raise ValueError('A correction requires concise --feedback; no automatic retries')
    if not re.fullmatch(r'gemini-[a-z0-9.-]+', model) or not 15 <= timeout <= 300:
        raise ValueError('Use an explicit Gemini model slug and a 15–300 second timeout')

    request, _, _ = jobs.prepare(job, feedback)
    protected_before = protected_snapshot(jobs.ROOT, set(editable))
    agents = original_settings = None
    try:
        agents, wrapper, audit, original_settings = prepare_repository(job, commands)
        number = len(job['attempts']) + 1
        draft = artifact(job, f'draft-{number}.txt')
        run_dir = artifact(job, f'gemini-{number}')
        if draft.exists() or run_dir.exists():
            raise ValueError('Attempt artifacts already exist; refusing to overwrite')
        run_dir.mkdir()
        prompt = (request + '\n\nAllowed reads:\n' + '\n'.join('- ' + name for name in jobs.selected_sources(job))
                  + '\n\nEditable files:\n' + ('\n'.join('- ' + name for name in editable) or '- none; review only')
                  + '\n\nSafe commands:\n' + '\n'.join(f'- {wrapper} {name}' for name in commands)
                  + '\n\nInspect the relevant sources, make the smallest complete edit, run the relevant safe commands, '
                    'and return exact outcomes. Do not use any other path or command.')
        (run_dir / 'prompt.txt').write_text(prompt)
        attempt = {'number': number, 'started_at': jobs.now(), 'status': 'running', 'model': model,
                   'draft': draft.relative_to(jobs.ROOT).as_posix(),
                   'evidence': run_dir.relative_to(jobs.ROOT).as_posix(),
                   'pre_source_sha256': job['source_sha256']}
        job['attempts'].append(attempt)
        job.update(status='running', review=None)
        jobs.save(job)
        report()
        print(f"Gemini collaborator attempt {number}/3: {job['title']}\nModel: {model}\n{request}", flush=True)
        command = [executable, '--print', prompt, '--agent', 'ai-coach-collaborator', '--model', model,
                   '--mode', 'accept-edits', '--disable-slash-commands',
                   '--output-format', 'stream-json', '--print-timeout', f'{timeout}s']
        try:
            info = gemini_worker.run(command, jobs.ROOT, run_dir, timeout + 5)
            decisions = [json.loads(line) for line in audit.read_text().splitlines()] if audit.is_file() else []
            (run_dir / 'gate.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in decisions))
            tools = info.get('completed_tools', [])
            missing_edit = editable and not any(
                name in {'write_to_file', 'replace_file_content', 'multi_replace_file_content'} for name in tools)
            missing_command = commands and 'run_command' not in tools
            if not decisions or any(row.get('decision') != 'allow' for row in decisions) or missing_edit or missing_command:
                info.update(status='failed', response='', error=info.get('error')
                            or 'Collaborator scope audit or required edit is missing; inspect gate.jsonl')
            if protected_snapshot(jobs.ROOT, set(editable)) != protected_before:
                info.update(status='failed', response='',
                            error='Repository content outside the editable scope changed; inspect before recovery')
            answer = info.pop('response', '')
            attempt.update(info)
            if answer:
                draft.write_text(answer + '\n')
        except (OSError, ValueError, KeyboardInterrupt) as error:
            attempt.update(status='failed', error='Gemini launch/supervision failed: ' + type(error).__name__)

        _, digest = jobs.sources_snapshot(job, digest_only=True)
        attempt['post_source_sha256'] = digest
        job['source_sha256'] = digest
        if draft.is_file():
            attempt['draft_sha256'] = hashlib.sha256(draft.read_bytes()).hexdigest()
        attempt['finished_at'] = jobs.now()
        job['status'] = attempt['status']
        jobs.save(job)
        (run_dir / 'result.json').write_text(json.dumps({k: v for k, v in attempt.items()
                                                        if k != 'draft'}, indent=2) + '\n')
        report()
        print(f"Job {job['id']}: {job['status']}. {attempt.get('error') or 'Codex review required.'}", flush=True)
        print(f'Evidence: {run_dir}', flush=True)
        return 0 if job['status'] == 'needs-review' else 2
    finally:
        if agents is not None and original_settings is not None:
            cleanup_repository(agents, original_settings)
