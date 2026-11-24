"""Private job records and source checks for the bounded worker workflow."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

import local_worker
import harness_context

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / '.local/worker/jobs'
LOCAL_TASKS = ('implement', 'debug', 'refactor', 'docs', 'explain', 'tests')
CODEX_TASKS = ('architecture', 'security', 'iam', 'release')
TASKS = LOCAL_TASKS + ('review',) + CODEX_TASKS
PROVIDERS = ('local', 'gemini', 'codex')
GEMINI_COMMANDS = ('status', 'diff-check', 'release-tests', 'release-validate',
                   'harness-tests', 'backend-tests')
PRIVATE_PARTS = {'.local', '.tools', '.git', '.venv', '.codex', '.agents', 'data', 'node_modules'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def bounded(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > limit:
        raise ValueError(f'{name} must be nonempty and at most {limit} UTF-8 bytes')
    return value.strip()


def route(task, provider=None):
    if task not in TASKS or provider not in (None, *PROVIDERS):
        raise ValueError('Unknown task or provider')
    if task in CODEX_TASKS:
        if provider not in (None, 'codex'):
            raise ValueError('Architecture, security, IAM and releases stay with Codex')
        return 'codex'
    return provider or ('gemini' if task == 'review' else 'local')


def job_path(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Use the complete 32-character job ID')
    path = DIRECTORY / f'{job_id}.json'
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve() / '.local/worker/jobs'):
        raise ValueError('Job records must remain in the private jobs directory')
    return path


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    temporary.replace(path)


def save(job):
    atomic_write(job_path(job['id']), json.dumps(job, indent=2, ensure_ascii=False) + '\n')


def load(job_id):
    job = json.loads(job_path(job_id).read_text(encoding='utf-8'))
    if not isinstance(job, dict) or job.get('schema') != 1 or job.get('id') != job_id:
        raise ValueError('Invalid job record')
    route(job.get('task'), job.get('provider'))
    bounded(job.get('title'), 'Title', 160)
    bounded(job.get('goal'), 'Goal', 4000)
    bounded(job.get('acceptance'), 'Acceptance criteria', 2000)
    harness_context.validate(job.get('shared_context'))
    sources = selected_sources(job)
    if job['provider'] == 'local' and len(sources) != 1:
        raise ValueError('Local worker jobs require one source selection')
    if not isinstance(job.get('source_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', job['source_sha256']):
        raise ValueError('Invalid source digest')
    collaborator_scope(job)
    attempts = job.get('attempts')
    limit = 3 if job['provider'] == 'gemini' else 2
    if not isinstance(attempts, list) or len(attempts) > limit or not all(isinstance(a, dict) for a in attempts):
        raise ValueError('Invalid attempt history')
    return job


def source_snapshot(spec, *, digest_only=False):
    bounded(spec, 'Source selection', 500)
    name = spec.rsplit(':', 2)[0] if ':' in spec else spec
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or PRIVATE_PARTS.intersection(relative.parts):
        raise ValueError('Select one tracked public source file, never private data')
    path = ROOT / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT.resolve()) or any((ROOT / Path(*relative.parts[:i])).is_symlink()
                                                          for i in range(1, len(relative.parts) + 1)):
        raise ValueError('Symlink source selections are not allowed')
    lowered = relative.name.lower()
    if (lowered.startswith('.env') and lowered != '.env.example') or any(
        part in lowered for part in ('credential', 'private-key', 'client_secret', 'service-account')
    ):
        raise ValueError('Credential and environment files cannot be worker context')
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    if relative.as_posix() not in tracked or path.suffix not in {'.py', '.js', '.json', '.md', '.toml', '.css', '.html'}:
        raise ValueError('Select one tracked source file of a supported type')
    if digest_only:
        return '', hashlib.sha256(path.read_bytes()).hexdigest()
    text = local_worker.read_context(spec, tracked)
    return text, hashlib.sha256(path.read_bytes()).hexdigest()


def selected_sources(job):
    """Return the exact ordered source list, including schema-1 job compatibility."""
    sources = job.get('sources')
    if sources is None:
        sources = [job.get('source')]
    if (not isinstance(sources, list) or not 1 <= len(sources) <= 16
            or not all(isinstance(item, str) for item in sources)
            or len(sources) != len(set(sources))):
        raise ValueError('Select 1-16 unique tracked source excerpts')
    return sources


def collaborator_scope(job):
    """Validate and return a repository-aware Gemini edit/test scope."""
    editable = job.get('editable', [])
    commands = job.get('commands', [])
    if not isinstance(editable, list) or not isinstance(commands, list):
        raise ValueError('Invalid Gemini collaborator scope')
    if not editable and not commands:
        return [], []
    if job.get('provider') != 'gemini':
        raise ValueError('Only Gemini jobs may use repository collaborator scope')
    sources = selected_sources(job)
    if (not all(isinstance(name, str) for name in editable)
            or len(editable) != len(set(editable)) or not set(editable).issubset(sources)
            or any(':' in name for name in editable)):
        raise ValueError('Editable files must be unique whole-file selections in the Gemini source set')
    if (len(commands) != len(set(commands)) or any(name not in GEMINI_COMMANDS for name in commands)):
        raise ValueError('Unknown or duplicate Gemini safe command')
    for name in editable:
        source_snapshot(name, digest_only=True)
    return editable, commands


def sources_snapshot(job, *, digest_only=False):
    """Read an exact ordered source set and bind it to one deterministic digest."""
    sources = selected_sources(job)
    snapshots = [source_snapshot(spec, digest_only=digest_only) for spec in sources]
    if len(snapshots) == 1:
        return snapshots[0]
    digest = hashlib.sha256()
    for spec, (_, source_digest) in zip(sources, snapshots):
        digest.update(spec.encode('utf-8') + b'\0' + source_digest.encode('ascii') + b'\0')
    context = '' if digest_only else '\n\n'.join(text for text, _ in snapshots)
    return context, digest.hexdigest()


def prompt(job, feedback=''):
    text = harness_context.prefix(job.get('shared_context'))
    text += f"Goal:\n{job['goal']}\n\nAcceptance criteria:\n{job['acceptance']}"
    if feedback:
        text += '\n\nCorrection feedback:\n' + bounded(feedback, 'Feedback', 2000)
    return text


def prepare(job, feedback=''):
    context, digest = sources_snapshot(job)
    if digest != job['source_sha256']:
        raise ValueError('Source changed since planning; create a fresh job before using this draft')
    preset = job['task'] if job['task'] in local_worker.PRESETS else 'draft'
    request = prompt(job, feedback)
    if len((request + '\n\n' + context + local_worker.PRESETS[preset]).encode('utf-8')) > local_worker.MAX_PROMPT_BYTES:
        raise ValueError('Task and source exceed 12 KB; select fewer lines or shorten the brief')
    return request, context, preset


def create(title, goal, acceptance, source, task='implement', provider=None, without_context=False,
           editable=None, commands=None):
    selected = route(task, provider)
    sources = [source] if isinstance(source, str) else source
    probe = {'sources': sources}
    if selected == 'local' and len(selected_sources(probe)) != 1:
        raise ValueError('Local worker jobs require one source selection; use Gemini for bounded multi-file review')
    _, digest = sources_snapshot(probe)
    job = {'schema': 1, 'id': uuid4().hex, 'created_at': now(),
           'title': bounded(title, 'Title', 160), 'goal': bounded(goal, 'Goal', 4000),
           'acceptance': bounded(acceptance, 'Acceptance criteria', 2000),
           'source': sources[0], 'sources': sources, 'source_sha256': digest, 'task': task, 'provider': selected,
           'status': {'local': 'ready', 'gemini': 'ready', 'codex': 'needs-codex'}[selected],
           'attempts': [], 'review': None,
           'shared_context': None if without_context else harness_context.capture(ROOT)}
    job['editable'] = list(editable or [])
    job['commands'] = list(commands or [])
    collaborator_scope(job)
    prepare(job)
    save(job)
    return job


def all_jobs():
    return [load(path.stem) for path in sorted(DIRECTORY.glob('*.json'))]
