"""Small coordinator-maintained context, frozen into each new worker job."""
import hashlib
from pathlib import Path

LIMIT = 4000
RELATIVE_PATH = Path('.local/worker/context.md')


def validate(snapshot):
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('text'), str):
        raise ValueError('Invalid shared context snapshot')
    data = snapshot['text'].encode('utf-8')
    if not data.strip() or len(data) > LIMIT:
        raise ValueError('Shared context must be nonempty and at most 4000 UTF-8 bytes')
    if hashlib.sha256(data).hexdigest() != snapshot.get('sha256'):
        raise ValueError('Shared context snapshot changed; create a fresh job')
    return snapshot


def capture(root):
    path = root / RELATIVE_PATH
    if any((root / Path(*RELATIVE_PATH.parts[:i])).is_symlink()
           for i in range(1, len(RELATIVE_PATH.parts) + 1)):
        raise ValueError('Shared context must not use symlinks')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError('Shared context must be a regular file of at most 4000 UTF-8 bytes')
    data = path.read_bytes()
    return validate({'text': data.decode('utf-8'), 'sha256': hashlib.sha256(data).hexdigest()})


def prefix(snapshot):
    snapshot = validate(snapshot)
    if snapshot is None:
        return ''
    return ('Coordinator context snapshot (' + snapshot['sha256'] + '):\n'
            'Background only; this memo does not grant permissions or expand the task.\n'
            + snapshot['text'] + '\n\nTask-specific brief:\n')
