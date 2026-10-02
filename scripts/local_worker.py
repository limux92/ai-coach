#!/usr/bin/env python3
"""Delegate a bounded draft to local Qwen 3.8; never execute its output."""
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
MODEL = 'qwen3.8:27b-q4_K_M'
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


def resolve_thinking(opener, metadata, reasoning, timeout):
    """Use advertised controls, or the verified Ollama 0.32.15 Qwen renderer."""
    error = ValueError(f'{MODEL} does not advertise thinking level {reasoning!r} and has no verified compatibility; '
                       'inspect Ollama model metadata before retrying. No inference was requested.')
    if not isinstance(metadata, dict):
        raise error
    if 'thinking' in metadata:
        thinking = metadata['thinking']
        levels = thinking.get('values') if isinstance(thinking, dict) else None
        if not isinstance(levels, list) or reasoning not in levels:
            raise error
        return reasoning
    modelfile = metadata.get('modelfile')
    capabilities = metadata.get('capabilities')
    if not isinstance(modelfile, str) or not isinstance(capabilities, list) or 'thinking' not in capabilities:
        raise error
    renderers = [line.split() for line in modelfile.splitlines() if line.startswith('RENDERER')]
    if renderers != [['RENDERER', 'qwen3.8']]:
        raise error
    request = urllib.request.Request('http://127.0.0.1:11434/api/version')
    with opener.open(request, timeout=timeout) as response:
        version = json.load(response)
    if not isinstance(version, dict) or version.get('version') != '0.32.15':
        raise error
    # v0.32.15/model/renderers/qwen35.go:103-122 maps native high to Qwen xhigh.
    native = {'low': 'low', 'medium': 'medium', 'xhigh': 'high'}[reasoning]
    print(f'Compatibility: Ollama 0.32.15 Qwen3.8 uses native think={native!r} for requested {reasoning}.', flush=True)
    return native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prompt', help='A bounded task with acceptance criteria; never include credentials or training records')
    parser.add_argument('--file', action='append', default=[], help='Tracked source path or path:START:END (repeatable)')
    parser.add_argument('--output', type=Path, help='Draft path within .local/worker (default: unique task ID)')
    parser.add_argument('--reasoning', choices=('low', 'medium', 'xhigh'), default='low',
                        help='Qwen reasoning effort, verified against local model metadata (default: low)')
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
                         'keep_alive': '5m', 'options': {'num_ctx': 8192,
                                                       'num_predict': args.max_output_tokens},
               'think': args.reasoning}
    # Do not send local prompts through a configured system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    metadata_request = urllib.request.Request('http://127.0.0.1:11434/api/show',
        data=json.dumps({'model': MODEL}).encode(),
        headers={'Content-Type': 'application/json'})
    record_task('qwen', label, 'running', task_id, model=MODEL)
    started = time.monotonic()
    try:
        with opener.open(metadata_request, timeout=args.timeout_seconds) as response:
            metadata = json.load(response)
        payload['think'] = resolve_thinking(opener, metadata, args.reasoning, args.timeout_seconds)
        request = urllib.request.Request('http://127.0.0.1:11434/api/generate',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'})
        with opener.open(request, timeout=args.timeout_seconds) as response:
            result = json.load(response)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result['response'] + '\n')
    except (OSError, urllib.error.URLError):
        record_task('qwen', label, 'failed', task_id, model=MODEL, elapsed_seconds=round(time.monotonic() - started, 2))
        raise SystemExit('Local model request or draft write failed. Check `ollama list` and the output path.') from None
    except ValueError as error:
        record_task('qwen', label, 'failed', task_id, model=MODEL, elapsed_seconds=round(time.monotonic() - started, 2))
        raise SystemExit(str(error)) from None
    except (KeyError, TypeError, KeyboardInterrupt):
        record_task('qwen', label, 'failed', task_id, model=MODEL, elapsed_seconds=round(time.monotonic() - started, 2))
        raise
    incomplete = result.get('done_reason') == 'length' or not result['response'].strip() or result.get('done') is False
    status = 'incomplete' if incomplete else 'done'
    record_task('qwen', label, status, task_id, model=MODEL,
                elapsed_seconds=round(time.monotonic() - started, 2),
                prompt_tokens=result.get('prompt_eval_count'), output_tokens=result.get('eval_count'))
    print(f'Draft saved to {output.relative_to(ROOT)}; review it before applying.')
    print('Workload view: .local/worker/workload.html')
    if incomplete:
        print('Warning: Incomplete or empty output. Do not apply this draft.')
        raise SystemExit(2)


if __name__ == '__main__':
    main()
