#!/usr/bin/env python3
"""Import measured Codex turn counters locally, without retaining conversation text."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time

import workload

COUNTERS = {
    'input_tokens': 'prompt_tokens',
    'cached_input_tokens': 'cached_input_tokens',
    'output_tokens': 'output_tokens',
    'reasoning_output_tokens': 'reasoning_output_tokens',
    'total_tokens': 'total_tokens',
}


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value) else None


def _timestamp(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def read_session(path, project):
    """Use explicit per-turn snapshots; never sum cumulative counters or messages.

    This intentionally does not infer turn usage from older thread-only events.
    Unknown counters stay unknown. The on-disk Codex format can change.
    """
    turns = {}
    with path.open(encoding='utf-8') as handle:
        try:
            first = json.loads(handle.readline())
            meta = first['payload']
            cwd = Path(meta['cwd']).resolve()
            session_id = _identifier(meta['id'])
            if first['type'] != 'session_meta' or not session_id or not cwd.is_relative_to(project.resolve()):
                return []
        except (ValueError, KeyError, TypeError, AttributeError):
            return []

        def turn(turn_id, timestamp):
            if turn_id not in turns:
                turns[turn_id] = {
                    'id': f'codex:{session_id}:{turn_id}', 'session_id': session_id,
                    'turn_id': turn_id, 'agent': 'codex', 'kind': 'codex_turn',
                    'title': f'Prompt {len(turns) + 1} · {session_id[-8:]}',
                    'status': 'unknown', 'started_at': timestamp, 'updated_at': timestamp,
                }
            return turns[turn_id]

        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Includes a partially written final line; reread next poll.
            if not isinstance(event, dict):
                continue
            kind, payload = event.get('type'), event.get('payload')
            if not isinstance(payload, dict):
                continue
            turn_id = _identifier(payload.get('turn_id'))
            timestamp = _timestamp(event.get('timestamp'))
            if not turn_id or not timestamp:
                continue
            if kind == 'turn_context':
                row = turn(turn_id, timestamp)
                model = payload.get('model')
                if isinstance(model, str) and re.fullmatch(r'[a-zA-Z0-9_.:/-]{1,160}', model):
                    models = set((row.get('model') or '').split(', ')) - {''}
                    row['model'] = ', '.join(sorted(models | {model}))
            elif kind == 'event_msg' and payload.get('type') in {'task_started', 'task_complete', 'turn_aborted'}:
                row = turn(turn_id, timestamp)
                row['updated_at'] = timestamp
                if payload['type'] == 'task_started':
                    row.update(status='running', started_at=_timestamp(payload.get('started_at')) or timestamp)
                else:
                    row['status'] = 'done' if payload['type'] == 'task_complete' else 'incomplete'
            elif kind == 'token_usage_record' and payload.get('thread_id') == session_id:
                snapshot = payload.get('turn_token_usage')
                if not isinstance(snapshot, dict):
                    continue
                metrics = {target: snapshot.get(source) for source, target in COUNTERS.items()}
                if any(v is not None and (type(v) is not int or v < 0) for v in metrics.values()):
                    continue
                inp, out, total = (metrics[k] for k in ('prompt_tokens', 'output_tokens', 'total_tokens'))
                if inp is None or out is None or total != inp + out:
                    continue
                if (metrics['cached_input_tokens'] or 0) > inp or (metrics['reasoning_output_tokens'] or 0) > out:
                    continue
                row = turn(turn_id, timestamp)
                row.update(metrics, updated_at=timestamp, source='codex_token_usage_record')
    return list(turns.values())


class SessionReader:
    """Reparse only changed files; cache counters, never transcript contents."""

    def __init__(self, sessions, project):
        self.sessions, self.project = sessions, project
        self.cache = {}

    def read(self):
        present = set()
        for path in self.sessions.glob('**/rollout-*.jsonl'):
            present.add(path)
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                if path not in self.cache or self.cache[path][0] != stamp:
                    self.cache[path] = (stamp, read_session(path, self.project))
            except (OSError, UnicodeError):
                self.cache.pop(path, None)
        self.cache = {path: value for path, value in self.cache.items() if path in present}
        latest = {}
        for _, rows in self.cache.values():
            for row in rows:
                if row['id'] not in latest or row['updated_at'] >= latest[row['id']]['updated_at']:
                    latest[row['id']] = row
        return sorted(latest.values(), key=lambda row: (row['started_at'], row['id']))


def sync(reader, directory):
    rows = reader.read()
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / 'codex_usage.json'
    with (directory / 'workload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = json.loads(snapshot.read_text()) if snapshot.exists() else None
        if existing != rows:
            with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as handle:
                json.dump(rows, handle)
                temporary = Path(handle.name)
            temporary.replace(snapshot)
            workload._write_report(directory, workload._read_tasks(directory))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sessions', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions')
    parser.add_argument('--watch', action='store_true', help='Refresh every five seconds until stopped')
    args = parser.parse_args()
    if not args.sessions.is_dir():
        parser.error('Codex sessions directory does not exist')
    reader = SessionReader(args.sessions, workload.ROOT)
    workload.DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (workload.DIRECTORY / 'codex_usage.watch.lock').open('a') as lock:
        if args.watch:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.exit(message='Codex usage watcher is already running.\n')
        try:
            while True:
                rows = sync(reader, workload.DIRECTORY)
                if not args.watch:
                    print(f'Imported {len(rows)} Codex turns; counters only. {workload.DIRECTORY / "workload.html"}')
                    break
                time.sleep(5)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
