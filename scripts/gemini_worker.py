"""Supervise a bounded Antigravity stream; model prose is never execution proof."""
import json
import os
import selectors
import signal
import subprocess
import time

MAX_OUTPUT = 2_000_000
MAX_ANSWER = 16_000


def classify(events, returncode, stop_reason=None):
    """Fail closed on transport, permission and incomplete-result failures."""
    results = [e.get('result') for e in events if e.get('event') == 'result']
    initial = [e.get('init') for e in events if e.get('event') == 'init']
    result = results[0] if len(results) == 1 and isinstance(results[0], dict) else {}
    info = {'conversation_id': result.get('conversation_id'), 'usage': result.get('usage', {}),
            'provider_status': result.get('status'), 'exit_code': returncode, 'status': 'failed',
            'error': stop_reason, 'response': ''}
    denied = result.get('denied_actions')
    steps = [e['step_update'] for e in events if isinstance(e.get('step_update'), dict)]
    tool_errors = [s for s in steps if s.get('state') in ('ERROR', 'FAILED', 'CANCELLED')
                   or isinstance(s.get('tool_info'), dict) and s['tool_info'].get('error')]
    info['completed_tools'] = [s.get('tool_name') for s in steps if s.get('step_type') == 'tool' and s.get('state') == 'DONE']
    if not info['error'] and (denied or tool_errors):
        info['error'] = 'Tool denied or failed; inspect the saved events. No completion recorded.'
    if not info['error'] and (returncode != 0 or len(initial) != 1 or not isinstance(initial[0], dict) or not result
                              or events[0].get('event') != 'init' or events[-1].get('event') != 'result'
                              or result.get('status') != 'SUCCESS' or result.get('error')):
        info['error'] = 'Worker did not return one successful terminal result.'
    answer = result.get('response')
    if not info['error'] and (not isinstance(answer, str) or not answer.strip()
                              or len(answer.encode('utf-8')) > MAX_ANSWER):
        info['error'] = 'Worker answer is empty or exceeds the 16 KB result limit.'
    if not info['error']:
        info.update(status='needs-review', response=answer, error=None)
    return info


def stop_process(process):
    """Reap the owned child; avoid signaling an already completed process group."""
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            process.terminate()
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=3)
        return True
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except (ProcessLookupError, PermissionError):
                pass
    try:
        process.wait(timeout=3)
        return True
    except subprocess.TimeoutExpired:
        return False


def run(command, workspace, directory, timeout=120, expected_tools=None, on_event=None):
    """Capture a fresh CLI invocation with a real outer deadline and bounded logs."""
    started = time.monotonic()
    events, pending = [], bytearray()
    total, stop_reason = 0, None
    process, selector, logs, stderr_tail = None, None, {}, b''
    try:
        for name in ('stdout', 'stderr'):
            logs[name] = (directory / (name + '.jsonl')).open('xb')
        process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        (directory / 'process.json').write_text(json.dumps({'pid': process.pid, 'started_at': time.time()}))
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
        selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
        while selector.get_map() and not stop_reason:
            if time.monotonic() - started >= timeout:
                stop_reason = 'Worker deadline exceeded; result remains unverified.'
                break
            for key, _ in selector.select(min(1, timeout)):
                data = os.read(key.fd, 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                total += len(data)
                if total > MAX_OUTPUT:
                    stop_reason = 'Worker output exceeded 2 MB.'
                    break
                logs[key.data].write(data)
                logs[key.data].flush()
                if key.data != 'stdout':
                    # CLI timeouts can produce partial output and exit zero.
                    stderr_tail = (stderr_tail + data.lower())[-65536:]
                    if any(word in stderr_tail for word in (b'timeout', b'timed out', b'denied', b'agy_error')):
                        stop_reason = 'CLI reported a timeout, denial or error; inspect stderr.'
                    continue
                pending.extend(data)
                while b'\n' in pending:
                    line, _, remainder = pending.partition(b'\n')
                    pending = bytearray(remainder)
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('Expected object')
                        for field in ('init', 'step_update', 'result'):
                            if field in event and not isinstance(event[field], dict):
                                raise ValueError('Expected event payload object')
                    except (ValueError, UnicodeError):
                        stop_reason = 'Invalid worker event stream.'
                        break
                    events.append(event)
                    if event.get('event') == 'init':
                        init = event.get('init', {})
                        if (init.get('cwd') != str(workspace)
                                or expected_tools is not None and set(init.get('tools', [])) - set(expected_tools)):
                            stop_reason = 'Worker workspace or tool scope does not match the task.'
                    step = event.get('step_update') or {}
                    if step.get('step_type') == 'tool':
                        print('Gemini tool: ' + str(step.get('tool_name')) + ' ' + str(step.get('state')), flush=True)
                    if on_event:
                        on_event(event)
        if pending.strip() and not stop_reason:
            stop_reason = 'Truncated worker event stream.'
        if not stop_reason:
            try:
                process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                stop_reason = 'Worker failed to exit before the deadline.'
    except KeyboardInterrupt:
        stop_reason = 'Worker interrupted; no completion recorded.'
    except (OSError, ValueError, TypeError, AttributeError) as error:
        stop_reason = 'Supervisor error: ' + type(error).__name__
    finally:
        reaped = stop_process(process) if process else True
        if selector:
            selector.close()
        for handle in logs.values():
            handle.close()
        if process:
            process.stdout.close()
            process.stderr.close()
    if not reaped:
        stop_reason = 'Worker termination could not be confirmed; inspect process.json before another run.'
    info = classify(events, process.returncode if process else None, stop_reason)
    info['process_reaped'] = reaped
    info['elapsed_seconds'] = round(time.monotonic() - started, 2)
    (directory / 'result.json').write_text(json.dumps(info, indent=2) + '\n')
    return info
