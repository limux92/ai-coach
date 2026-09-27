#!/usr/bin/env python3
"""Delegate a bounded draft to local GPT-oss 20B; never execute its output."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4

from workload import DIRECTORY, record_task

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'gpt-oss:20b'
MAX_PROMPT_BYTES = 12_000

PRESETS = {
    'draft': 'Provide a concise draft of the requested task.',
    'implement': 'Implement the requested change for one file or function. Return complete replacement code only, without Markdown or a diff.',
    'debug': 'Identify the root cause from the supplied evidence. State uncertainties and propose a minimal fix.',
    'refactor': 'Refactor only the requested file or function, preserving behavior. Return complete replacement code only, without Markdown or a diff.',
    'docs': 'Write concise, accurate documentation grounded in the supplied source.',
    'explain': 'Explain the supplied code or question briefly using concrete examples.',
}


def read_context(spec: str, tracked: set[str]) -> str:
    """Read one tracked source file, optionally restricted to inclusive line numbers."""
    parts = spec.rsplit(':', 2)
    name, start, end = spec, None, None
    if len(parts) != 1:
        if len(parts) != 3:
            raise ValueError('Use --file path or --file path:START:END')
        name, first, last = parts
        try:
            start, end = int(first), int(last)
        except ValueError:
            raise ValueError('Line numbers must be integers') from None
        if start < 1 or end < start:
            raise ValueError('Use a positive, ascending line range')
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT) or path.relative_to(ROOT).as_posix() not in tracked:
        raise ValueError('Context must be a tracked project source file')
    if path.suffix not in {'.py', '.js', '.json', '.md', '.toml', '.css', '.html'}:
        raise ValueError('Unsupported context file type')
    if path.stat().st_size > (1_000_000 if start is not None else MAX_PROMPT_BYTES):
        raise ValueError('Choose a smaller source file or use path:START:END')
    lines = path.read_text().splitlines(keepends=True)
    label = path.relative_to(ROOT).as_posix()
    if start is not None:
        if end > len(lines):
            raise ValueError('Requested line range exceeds the source file')
        lines = lines[start - 1:end]
        label += f':{start}:{end}'
    text = ''.join(lines)
    if len(text.encode('utf-8')) > MAX_PROMPT_BYTES:
        raise ValueError('Source excerpt is too large; select fewer lines')
    return f'FILE {label}\n{text}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prompt', help='A bounded task with acceptance criteria; never include credentials or training records')
    parser.add_argument('--file', action='append', default=[], help='Tracked source path or path:START:END (repeatable)')
    parser.add_argument('--output', type=Path, help='Draft path within .local/worker (default: unique task ID)')
    parser.add_argument('--reasoning', choices=('low', 'medium', 'high'), default='low',
                        help='GPT-oss reasoning effort (default: low)')
    parser.add_argument('--task', choices=PRESETS, default='draft')
    parser.add_argument('--max-output-tokens', type=int, default=2048, metavar='256-4096')
    parser.add_argument('--timeout-seconds', type=int, default=120, metavar='30-600')
    parser.add_argument('--label', help='Non-sensitive task label for the workload view (max 160 characters)')
    args = parser.parse_args()
    label = args.label or f'{args.task.capitalize()} draft'
    if not label.strip() or len(label) > 160:
        parser.error('--label must contain 1–160 characters')
    if not 256 <= args.max_output_tokens <= 4096:
        parser.error('--max-output-tokens must be between 256 and 4096')
    if not 30 <= args.timeout_seconds <= 600:
        parser.error('--timeout-seconds must be between 30 and 600')
    tracked = set(subprocess.check_output(['git', 'ls-files'], cwd=ROOT, text=True).splitlines())
    context = []
    for spec in args.file:
        try:
            context.append(read_context(spec, tracked))
        except (ValueError, OSError) as error:
            parser.error(str(error))
    prompt = args.prompt + '\n\n' + '\n\n'.join(context)
    if len((prompt + PRESETS[args.task]).encode('utf-8')) > MAX_PROMPT_BYTES:
        parser.error('Keep prompt and source below 12 KB; use a smaller file or line range')
    task_id = uuid4().hex
    output = (args.output or DIRECTORY / f'{task_id}.txt').resolve()
    if not output.is_relative_to(DIRECTORY):
        parser.error('Drafts must go in .local/worker; review before applying')
    payload = {'model': MODEL, 'prompt': prompt, 'system': PRESETS[args.task], 'stream': False,
                         'keep_alive': '5m', 'options': {'temperature': 0, 'num_ctx': 16384,
                                                       'num_predict': args.max_output_tokens},
               'think': args.reasoning}
    request = urllib.request.Request('http://127.0.0.1:11434/api/generate',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    # Do not send local prompts through a configured system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    record_task('gpt-oss', label, 'running', task_id, model=MODEL)
    started = time.monotonic()
    try:
        with opener.open(request, timeout=args.timeout_seconds) as response:
            result = json.load(response)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result['response'] + '\n')
    except (OSError, urllib.error.URLError):
        record_task('gpt-oss', label, 'failed', task_id, model=MODEL, elapsed_seconds=round(time.monotonic() - started, 2))
        raise SystemExit('Local model request or draft write failed. Check `ollama list` and the output path.') from None
    except (ValueError, KeyError, TypeError, KeyboardInterrupt):
        record_task('gpt-oss', label, 'failed', task_id, model=MODEL, elapsed_seconds=round(time.monotonic() - started, 2))
        raise
    incomplete = result.get('done_reason') == 'length' or not result['response'].strip() or result.get('done') is False
    status = 'incomplete' if incomplete else 'done'
    record_task('gpt-oss', label, status, task_id, model=MODEL,
                elapsed_seconds=round(time.monotonic() - started, 2),
                prompt_tokens=result.get('prompt_eval_count'), output_tokens=result.get('eval_count'))
    print(f'Draft saved to {output.relative_to(ROOT)}; review it before applying.')
    print('Workload view: .local/worker/workload.html')
    if incomplete:
        print('Warning: Incomplete or empty output. Do not apply this draft.')
        raise SystemExit(2)


if __name__ == '__main__':
    main()
