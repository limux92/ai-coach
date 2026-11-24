"""Read release evidence independently of worker prose; never deploy or query cloud."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import harness_jobs as jobs


def read_receipt(path):
    path = Path(path)
    if not path.is_absolute():
        path = jobs.ROOT / path
    base = jobs.ROOT / '.local/deployments'
    if (not path.resolve().is_relative_to(base.resolve()) or path.name != 'summary.json'
            or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(jobs.ROOT))
            or not path.is_file() or path.stat().st_size > 256_000):
        raise ValueError('Use a bounded, regular deployment summary.json under .local/deployments')
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('Invalid release receipt')
    return path, data


def verified_release(path, *, expected_commit=None, not_before=None):
    path, data = read_receipt(path)
    required = ('github_ci_passed', 'both_candidates_verified', 'production_verified',
                'runtime_configuration_preserved', 'iam_preserved', 'private_backend_health_verified')
    revisions = data.get('revisions') or {}
    digests = (data.get('check_receipt_sha256'), data.get('check_integrity_digest'))
    if (not isinstance(revisions, dict) or data.get('mode') != 'deploy' or data.get('status') != 'released'
            or data.get('phase') != 'verified'
            or not all(data.get(key) is True for key in required)
            or not all(isinstance(value, str) and len(value) == 64
                       and all(character in '0123456789abcdef' for character in value) for value in digests)
            or not data.get('commit') or not data.get('pull_request')
            or not all(revisions.get(key) for key in ('ai-coach-sync', 'ai-coach-chat'))):
        raise ValueError('Release is not verified complete. Status: ' + str(data.get('status'))
                         + '; stage: ' + str(data.get('stage')))
    if expected_commit is not None and data['commit'] != expected_commit:
        raise ValueError('Release receipt does not match the current committed source')
    if not_before is not None:
        try:
            started = datetime.strptime(data['started_at'][:13], '%y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
            planned = datetime.fromisoformat(not_before)
        except (KeyError, TypeError, ValueError):
            raise ValueError('Release receipt must have a valid start time') from None
        if started < planned:
            raise ValueError('Release receipt predates this task; old success cannot complete new work')
    return {'receipt': path.relative_to(jobs.ROOT).as_posix(),
            'receipt_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'commit': data['commit'], 'revisions': revisions,
            'scope': 'Saved release evidence; not a fresh cloud read or owner workflow test'}


def latest_release():
    paths = sorted((jobs.ROOT / '.local/deployments').glob('*/summary.json'), reverse=True)
    for path in paths:
        try:
            _, data = read_receipt(path)
        except (ValueError, OSError):
            continue
        if data.get('mode') == 'deploy':
            return {'status': data.get('status', 'unknown'), 'stage': data.get('stage'),
                    'error': data.get('error'), 'receipt': path.relative_to(jobs.ROOT).as_posix(),
                    'production_verified': data.get('production_verified') is True,
                    'scope': 'Latest saved release attempt; live state not queried'}
    return {'status': 'unknown', 'production_verified': False, 'scope': 'No saved release attempt'}
