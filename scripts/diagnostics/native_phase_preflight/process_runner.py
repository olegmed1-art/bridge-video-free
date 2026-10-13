"""Linux subprocess adapter: bounded capture and explicit process-group cleanup evidence."""
import os
import selectors
import signal
import subprocess
import time
from diagnostics import MAX_STDERR, safe_errno, stderr_summary, validate_stderr, ERRNOS


def empty_result():
    return {'returncode': None, 'timed_out': False, 'launch_errno': None,
            'adapter_error': None, 'spawned': False, 'direct_child_reaped': False,
            'group_state': 'not_started', 'kill_requested': False,
            'stderr': stderr_summary(b'')}


def validate_result(value):
    if type(value) is not dict or set(value) != set(empty_result()):
        raise ValueError('invalid_process_result')
    rc = value['returncode']
    if rc is not None and (type(rc) is not int or not -64 <= rc <= 255):
        raise ValueError('invalid_process_result')
    if value['launch_errno'] is not None and (type(value['launch_errno']) is not int or value['launch_errno'] not in ERRNOS):
        raise ValueError('invalid_process_result')
    for key in ('timed_out', 'spawned', 'direct_child_reaped', 'kill_requested'):
        if type(value[key]) is not bool: raise ValueError('invalid_process_result')
    if value['adapter_error'] not in (None, 'launch_failed', 'capture_failed', 'cleanup_failed') or value['group_state'] not in ('not_started', 'absent', 'present', 'unknown'):
        raise ValueError('invalid_process_result')
    if not value['spawned'] and (rc is not None or value['direct_child_reaped'] or value['kill_requested'] or value['group_state'] != 'not_started'):
        raise ValueError('invalid_process_result')
    if value['direct_child_reaped'] != (rc is not None): raise ValueError('invalid_process_result')
    validate_stderr(value['stderr'])


def execute(argv, timeout, root):
    result, prefix, overflow = empty_result(), bytearray(), False
    capture_ready = False
    proc = None
    selector = None
    # Preserve the exact stripped environment for every child.
    env = {'LC_ALL': 'C.UTF-8'}
    compiler = argv[0] == '/usr/bin/cc'
    def collect():
        nonlocal overflow
        if not compiler or not capture_ready: return
        while True:
            try: chunk = os.read(proc.stderr.fileno(), 4096)
            except BlockingIOError: return
            if not chunk: return
            remaining = MAX_STDERR - len(prefix)
            prefix.extend(chunk[:remaining])
            overflow = overflow or len(chunk) > remaining
            # Return frequently even when a writer continuously fills the pipe.
            return
    deadline = time.monotonic() + timeout
    try:
        proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE if compiler else subprocess.DEVNULL, close_fds=True,
            start_new_session=True)
        result['spawned'] = True
        result['group_state'] = 'unknown'
        if compiler:
            os.set_blocking(proc.stderr.fileno(), False)
            capture_ready = True
            selector = selectors.DefaultSelector()
            selector.register(proc.stderr, selectors.EVENT_READ)
        while True:
            collect()
            if proc.poll() is not None: break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result['timed_out'] = True
                break
            if selector is not None: selector.select(min(remaining, .02))
            else: time.sleep(min(remaining, .02))
    except BaseException as exc:
        result['adapter_error'] = 'launch_failed' if proc is None else 'capture_failed'
        result['launch_errno'] = safe_errno(exc)
    finally:
        if proc is not None:
            # Signal only the fresh session/process group we created; never caller group.
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                result['group_state'] = 'absent'
            except OSError:
                result['group_state'] = 'unknown'
                result['adapter_error'] = result['adapter_error'] or 'cleanup_failed'
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    result['kill_requested'] = True
                except ProcessLookupError: pass
                except OSError:
                    result['adapter_error'] = result['adapter_error'] or 'cleanup_failed'
            try:
                result['returncode'] = proc.wait(timeout=1)
                result['direct_child_reaped'] = True
            except BaseException:
                result['adapter_error'] = result['adapter_error'] or 'cleanup_failed'
            try:
                # Prefix capture is still bounded; stop after at most three reads.
                for _ in range(3): collect()
            except BaseException:
                result['adapter_error'] = result['adapter_error'] or 'capture_failed'
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError: result['group_state'] = 'absent'
            except OSError: result['group_state'] = 'unknown'
            else: result['group_state'] = 'present'
            try:
                if selector is not None: selector.close()
                if proc.stderr is not None: proc.stderr.close()
            except BaseException:
                result['adapter_error'] = result['adapter_error'] or 'capture_failed'
        result['stderr'] = stderr_summary(bytes(prefix), overflow)
    validate_result(result)
    return result
