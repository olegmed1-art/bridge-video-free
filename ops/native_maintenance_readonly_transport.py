"""Verified-source SSH HOLD audit. Deliberately no mutation/credential API."""
import argparse
import base64
import json
import os
from pathlib import Path
import resource
import select
import shlex
import signal
import subprocess
import sys
import time

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_lifetime as lifetime

MAX_OUTPUT = 8192


class Stopped(RuntimeError):
    pass


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def receive(fd, deadline, cancelled=lambda: False):
    data = bytearray()
    while b'\n' not in data:
        if cancelled():
            raise Stopped('SIGNAL_CANCELLED')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Stopped('PACKET_TIMEOUT')
        if not select.select([fd], [], [], min(remaining, 0.1))[0]:
            continue
        chunk = os.read(fd, 16384)
        if not chunk:
            raise Stopped('CONTROL_EOF')
        data.extend(chunk)
        if len(data) > bundle.MAX_WIRE + 1:
            raise Stopped('PACKET_SIZE')
    payload, remainder = bytes(data).split(b'\n', 1)
    # No pipelined instructions before READY.
    if remainder:
        raise Stopped('EARLY_CONTROL')
    return payload


def exited_unreaped(process):
    # Keep the leader's PID reserved until its process group has been stopped.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def stop_group(process):
    # Only fixed reviewed children which do not escape their new process group.
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT, signal.SIGHUP})
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            until = time.monotonic() + 0.25
            while time.monotonic() < until and not exited_unreaped(process):
                time.sleep(0.01)
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def supervise(command, cwd, control_fd, *, heartbeat=10, duration=90, ready=emit,
              cancelled=lambda: False):
    started = time.monotonic()
    last_beat = started
    pending = bytearray()
    output = bytearray()
    child = subprocess.Popen(command, cwd=cwd, env={'PATH': '/usr/bin:/bin'},
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    try:
        ready({'state': 'READY'})
        while True:
            if cancelled():
                raise Stopped('SIGNAL_CANCELLED')
            now = time.monotonic()
            if now - started >= duration:
                raise Stopped('TOTAL_TIMEOUT')
            if now - last_beat >= heartbeat:
                raise Stopped('HEARTBEAT_TIMEOUT')
            readable = select.select([control_fd, child.stdout], [], [], 0.05)[0]
            # Process cancellation before accepting an already completed child.
            if control_fd in readable:
                chunk = os.read(control_fd, 256)
                if not chunk:
                    raise Stopped('CONTROL_EOF')
                pending.extend(chunk)
                if len(pending) > 64:
                    raise Stopped('CONTROL_SIZE')
                if b'\n' in pending:
                    frame, remainder = bytes(pending).split(b'\n', 1)
                    if remainder:
                        raise Stopped('CONTROL_RATE')
                    pending.clear()
                    if frame == b'CANCEL':
                        raise Stopped('CANCELLED')
                    if frame != b'BEAT':
                        raise Stopped('CONTROL_INVALID')
                    if now - last_beat < 0.1:
                        raise Stopped('CONTROL_RATE')
                    last_beat = now
            if child.stdout in readable:
                output.extend(os.read(child.stdout.fileno(), 4096))
                if len(output) > MAX_OUTPUT:
                    raise Stopped('OUTPUT_SIZE')
            if exited_unreaped(child):
                # Drain remaining bounded pipe bytes before cleanup/reap.
                while select.select([child.stdout], [], [], 0)[0]:
                    chunk = os.read(child.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT:
                        raise Stopped('OUTPUT_SIZE')
                break
    finally:
        try:
            stop_group(child)
        finally:
            child.stdout.close()
    if child.returncode != 0:
        raise Stopped('AUDIT_FAILED')
    if cancelled():
        raise Stopped('SIGNAL_CANCELLED')
    return bytes(output)


def remote(source, digest, mode):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cancellation = [False]
    def interrupted(signum, frame):
        # Latch, never throw between fork and Popen returning its child handle.
        cancellation[0] = True
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        bundle.check(os.getuid() == 0, 'ROOT_REQUIRED')
        bundle.check(mode in ('audit', 'probe'), 'MODE_INVALID')
        payload = receive(sys.stdin.fileno(), time.monotonic() + 15, lambda: cancellation[0])
        with bundle.extracted(payload, source, digest) as root:
            if mode == 'audit':
                command = [sys.executable, '-I', '-B', '-S', str(root / 'ops/oracle_light_active_hold_attest.py')]
            else:
                command = [sys.executable, '-I', '-B', '-S', '-c', 'import time; time.sleep(120)']
            output = supervise(command, root, sys.stdin.fileno(), cancelled=lambda: cancellation[0])
            bundle.check(mode == 'audit', 'PROBE_UNEXPECTED_EXIT')
            record = json.loads(output)
            bundle.check(record == {
                'audit': 'ACTIVE_HOLD_PASS', 'light_active': True, 'admission': 'HOLD',
                'live_dsn_matches_root_file': True, 'database_login': 'READ_ONLY_PASS',
                'database_binding': 'NEON_PROJECT_BRANCH_ENDPOINT_PASS',
                'queue_nonterminal': 0, 'same_invocation': True}, 'AUDIT_RESULT_INVALID')
        if cancellation[0]:
            raise Stopped('SIGNAL_CANCELLED')
        emit({'result': 'ACTIVE_HOLD_PASS'})
        return 0
    except Stopped as exc:
        emit({'result': str(exc)})
        return 2
    except BaseException:
        emit({'result': 'TRANSPORT_REFUSED'})
        return 2


def bootstrap(repo, source, digest, mode, run=None):
    bundle.check(bundle.identifier(source, 40) and bundle.identifier(digest, 64), 'IDENTITY_INVALID')
    bundle.check(mode in ('audit', 'probe'), 'MODE_INVALID')
    modules = {}
    for name in ('native_maintenance_bundle', 'native_maintenance_lifetime', 'native_maintenance_readonly_transport'):
        path = 'ops/' + name + '.py'
        content = bundle.git(repo, 'show', source + ':' + path)
        bundle.check(0 < len(content) <= 32768, 'BOOTSTRAP_SIZE')
        modules['ops.' + name] = base64.b64encode(content).decode('ascii')
    # Code and expected digest travel in the authenticated, reviewed command;
    # untrusted package bytes travel separately on stdin. No secret in either.
    code = "\n".join([
        'import base64,sys,types',
        "sys.modules['ops']=types.ModuleType('ops')",
        'sources=' + repr(modules),
        'for name,encoded in sources.items():',
        ' module=types.ModuleType(name)',
        ' sys.modules[name]=module',
        " exec(compile(base64.b64decode(encoded),name,'exec'),module.__dict__)",
        "sys.exit(sys.modules['ops.native_maintenance_readonly_transport'].remote(" +
        repr(source) + ',' + repr(digest) + ',' + repr(mode) + '))'])
    if run is None:
        return code  # Local disposable transport tests only.
    encoded = modules['ops.native_maintenance_lifetime']
    return (lifetime.loader(encoded) + 'import sys\nsys.exit(lifetime.managed(' +
            repr(code) + ',' + repr(encoded) + ',' + repr(source) + ',' + repr(run) + '))')


def exchange(command, payload, behavior):
    bundle.check(behavior in ('audit', 'cancel', 'eof', 'heartbeat'), 'BEHAVIOR_INVALID')
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    os.set_blocking(process.stdin.fileno(), False)
    pending = memoryview(payload + b'\n')
    received = bytearray()
    ready = False
    result = None
    total = 0
    next_beat = time.monotonic() + 1
    deadline = time.monotonic() + 110
    try:
        while time.monotonic() < deadline:
            writable = [process.stdin] if pending and not process.stdin.closed else []
            readers, writers, _ = select.select([process.stdout], writable, [], 0.05)
            if writers:
                n = os.write(process.stdin.fileno(), pending[:16384])
                pending = pending[n:]
            if readers:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                total += len(chunk)
                bundle.check(total <= MAX_OUTPUT, 'REMOTE_OUTPUT_SIZE')
                received.extend(chunk)
                while b'\n' in received:
                    line, tail = bytes(received).split(b'\n', 1)
                    received[:] = tail
                    record = json.loads(line)
                    if record == {'state': 'READY'}:
                        bundle.check(not ready and not pending, 'REMOTE_READY_INVALID')
                        ready = True
                        next_beat = time.monotonic() + 1
                        if behavior == 'cancel':
                            pending = memoryview(b'CANCEL\n')
                        elif behavior == 'eof':
                            process.stdin.close()
                    else:
                        bundle.check(result is None and set(record) == {'result'}, 'REMOTE_RESULT_INVALID')
                        result = record['result']
            if ready and behavior == 'audit' and time.monotonic() >= next_beat and not pending:
                pending = memoryview(b'BEAT\n')
                next_beat = time.monotonic() + 1
        else:
            raise Stopped('CLIENT_TIMEOUT')
        process.wait(timeout=5)
        expected = {'audit': 'ACTIVE_HOLD_PASS', 'cancel': 'CANCELLED',
                    'eof': 'CONTROL_EOF', 'heartbeat': 'HEARTBEAT_TIMEOUT'}[behavior]
        bundle.check(ready and not received and result == expected
                     and process.returncode == (0 if behavior == 'audit' else 2), 'REMOTE_RUN_FAILED')
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout):
            stream.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', required=True)
    parser.add_argument('--known-hosts', required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    source = os.environ.get('GITHUB_SHA')
    bundle.check(os.environ.get('GITHUB_REPOSITORY') == 'olegmed1-art/bridge-video-free'
                 and os.environ.get('GITHUB_REF') == 'refs/heads/main', 'MAIN_JOB_REQUIRED')
    payload = bundle.build(repo, source)
    digest = bundle.digest(payload)
    run = os.environ.get('GITHUB_RUN_ID', '') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '')
    ssh = ['ssh', '-F', '/dev/null', '-i', args.key, '-o', 'BatchMode=yes',
           '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'UserKnownHostsFile=' + args.known_hosts, '-o', 'ConnectTimeout=15',
           '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=5',
           '-o', 'ServerAliveCountMax=2', 'ubuntu@92.5.47.149']
    encoded = base64.b64encode(bundle.git(repo, 'show', source + ':ops/native_maintenance_lifetime.py')).decode()
    code = lifetime.loader(encoded) + 'lifetime.probes(' + repr(source) + ',' + repr(run) + ')'
    probe_command = shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-S', '-c', code])
    proof = subprocess.run([*ssh, probe_command], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=40)
    bundle.check(proof.returncode == 0 and len(proof.stdout) < 4096, 'LIFETIME_PROBES_FAILED')
    records = [json.loads(line) for line in proof.stdout.splitlines()]
    bundle.check(records == [{'audit': 'NATIVE_LIFETIME_PASS', 'case': case, 'cgroup_empty': True,
                              'source_sha': source} for case in ('main-kill', 'runtime-max')],
                 'LIFETIME_PROBE_RESULTS_INVALID')
    for record in records:
        emit(record)
    for behavior in ('cancel', 'eof', 'heartbeat', 'audit'):
        code = bootstrap(repo, source, digest, 'audit' if behavior == 'audit' else 'probe', run)
        remote_command = shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-S', '-c', code])
        command = [*ssh, remote_command]
        result = exchange(command, payload, behavior)
        emit({'audit': 'READ_ONLY_TRANSPORT_PASS', 'case': behavior, 'result': result,
              'source_sha': source})


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        emit({'audit': 'READ_ONLY_TRANSPORT_REFUSED'})
        sys.exit(2)
