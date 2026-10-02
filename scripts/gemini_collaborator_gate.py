#!/usr/bin/env python3
"""Fail-closed Antigravity hook for one repository collaborator job."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def collect_strings(value, output):
    if isinstance(value, str):
        output.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            collect_strings(item, output)
    elif isinstance(value, list):
        for item in value:
            collect_strings(item, output)


def relative_paths(args, root):
    strings = []
    collect_strings(args, strings)
    result = []
    for value in strings:
        if not value.startswith('/'):
            continue
        try:
            result.append(Path(value).resolve().relative_to(root).as_posix())
        except ValueError:
            result.append(None)
    return result


def decide(payload, policy):
    root = Path(policy['root']).resolve()
    call = payload.get('toolCall') or {}
    name = call.get('name')
    args = call.get('args') or {}
    paths = relative_paths(args, root)
    readable = set(policy['readable']) | {'.agents/gemini-safe-command.py'}
    editable = set(policy['editable'])
    command = args.get('CommandLine')
    read_ok = name == 'view_file' and len(paths) == 1 and paths[0] in readable
    write_ok = (name in {'write_to_file', 'replace_file_content', 'multi_replace_file_content'}
                and bool(paths) and all(path in editable for path in paths))
    command_ok = name == 'run_command' and command in set(policy['commands'].values())
    allowed = read_ok or write_ok or command_ok
    result = {'decision': 'allow' if allowed else 'deny',
              'reason': 'Exact repository collaborator scope.'}
    if command_ok:
        wrapper = str((root / '.agents/gemini-safe-command.py').resolve())
        result['permissionOverrides'] = ['command(' + wrapper + ')', 'unsandboxed(' + wrapper + ')']
    return name, args, result


def main(argv=None):
    if len(sys.argv) != 2:
        raise SystemExit(2)
    policy_path = Path(sys.argv[1])
    try:
        policy = json.loads(policy_path.read_text())
        payload = json.loads(sys.stdin.read(65536))
        name, args, result = decide(payload, policy)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        name, args = None, {}
        result = {'decision': 'deny', 'reason': 'Invalid collaborator hook input.'}
        policy = {'audit': str(policy_path.with_suffix('.jsonl'))}
    with Path(policy['audit']).open('a') as handle:
        handle.write(json.dumps({'tool': name, 'args': args, 'decision': result['decision']}) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
