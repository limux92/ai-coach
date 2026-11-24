"""Per-packet Antigravity hook: permit only the supplied source read."""
import json
from pathlib import Path
import sys


def decision(payload, workspace):
    call = payload.get('toolCall') or {}
    args = call.get('args') or {}
    allowed = (call.get('name') == 'view_file'
               and args.get('AbsolutePath') == str(workspace / 'source.md')
               and not (workspace / 'source.md').is_symlink())
    return {'decision': 'allow' if allowed else 'deny',
            'reason': 'This review permits only the supplied source.md read.'}


def main():
    workspace = Path(__file__).resolve().parent
    try:
        payload = json.loads(sys.stdin.read(65536))
        result = decision(payload, workspace)
        name = (payload.get('toolCall') or {}).get('name')
    except (ValueError, TypeError, AttributeError):
        name, result = None, {'decision': 'deny', 'reason': 'Invalid hook input'}
    with (workspace / 'gate.jsonl').open('a') as handle:
        handle.write(json.dumps({'tool': name, 'decision': result['decision']}) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
