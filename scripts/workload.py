#!/usr/bin/env python3
"""Record local delegation metadata and rebuild the workload report."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from workload_report import render_report

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / '.local/worker'
STATUSES = ('planned', 'running', 'done', 'failed', 'incomplete')
AGENTS = ('codex', 'gpt-oss')


def _read_tasks(directory):
    log = directory / 'workload.jsonl'
    latest = {}
    if log.exists():
        for line in log.read_text().splitlines():
            row = json.loads(line)
            latest[row['id']] = row
    return latest


def _write_report(directory, tasks):
    usage = directory / 'codex_usage.json'
    turns = json.loads(usage.read_text()) if usage.exists() else []
    report = render_report(list(tasks.values()), turns)
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False, suffix='.html') as handle:
        handle.write(report)
        temporary = Path(handle.name)
    temporary.replace(directory / 'workload.html')


def record_task(agent, title, status, task_id=None, model=None, **metrics):
    """Store a short label and measured counters, never prompts or generated code."""
    if agent not in AGENTS or status not in STATUSES:
        raise ValueError('Invalid agent or status')
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 160):
        raise ValueError('Use a model name of 1–160 characters')
    if not title.strip() or len(title) > 160:
        raise ValueError('Use a nonempty task label of at most 160 characters')
    allowed = {'elapsed_seconds', 'prompt_tokens', 'output_tokens'}
    if set(metrics) - allowed:
        raise ValueError('Unknown workload metric')
    for value in metrics.values():
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            raise ValueError('Metrics must be nonnegative numbers or unknown')
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (DIRECTORY / 'workload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tasks = _read_tasks(DIRECTORY)
        task_id = task_id or uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        row = tasks.get(task_id, {'id': task_id, 'started_at': now})
        if 'agent' in row and row['agent'] != agent:
            raise ValueError('An existing task cannot change agents')
        row.update(agent=agent, title=title, status=status, updated_at=now, **metrics)
        if model is not None:
            row['model'] = model
        with (DIRECTORY / 'workload.jsonl').open('a') as log:
            log.write(json.dumps(row) + '\n')
        tasks[task_id] = row
        _write_report(DIRECTORY, tasks)
    return task_id


def rebuild():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (DIRECTORY / 'workload.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write_report(DIRECTORY, _read_tasks(DIRECTORY))
    return DIRECTORY / 'workload.html'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    record = commands.add_parser('record', help='Manually record a planning or review stage')
    record.add_argument('--agent', choices=AGENTS, required=True)
    record.add_argument('--model', help='Exact local model name, when known')
    record.add_argument('--title', required=True, help='Short non-sensitive task label')
    record.add_argument('--status', choices=STATUSES, required=True)
    record.add_argument('--id', dest='task_id', help='Reuse this ID when updating a task')
    commands.add_parser('report', help='Rebuild the local HTML report')
    args = parser.parse_args()
    if args.command == 'record':
        try:
            task_id = record_task(args.agent, args.title, args.status, args.task_id, model=args.model)
        except ValueError as error:
            parser.error(str(error))
        print(f'Task {task_id}')
    else:
        rebuild()
    print(DIRECTORY / 'workload.html')


if __name__ == '__main__':
    main()
