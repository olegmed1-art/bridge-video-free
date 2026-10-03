"""Local candidate. Structured one-shot prepare admission; no power API calls."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

SHA = '1111111111111111111111111111111111111111'
BASE = Path('/nonexistent/synthetic-preparation')
PINS = {
    'runner.py': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'protocol.py': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'durable.py': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'guest.py': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'prepare_driver.py': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
}
RUN = re.compile(r'https://github\.com/example-owner/example-repository/actions/runs/[1-9][0-9]*\Z')
JOB = re.compile(r'https://github\.com/example-owner/example-repository/actions/runs/[1-9][0-9]*/job/[1-9][0-9]*\Z')


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


class Gate:
    def __init__(self, nonce, simulation, wall, mono, arm_seconds=300):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', nonce):
            raise ValueError('NONCE')
        if not 1 <= arm_seconds <= 300:
            raise ValueError('ARM_LIMIT')
        self.nonce, self.simulation = nonce, simulation
        self.state, self.reason = 'ARMED', None
        self.arm_wall, self.arm_mono = wall + arm_seconds, mono + arm_seconds
        self.bound = None
        self.starts = 0

    def stop(self, reason):
        self.state, self.reason = 'TERMINAL', reason
        return 'STOP'

    def tick(self, wall, mono):
        if self.state == 'TERMINAL':
            return 'NONE'
        if self.state == 'ARMED':
            if wall >= self.arm_wall or mono >= self.arm_mono:
                return self.stop('ARM_EXPIRED')
        else:
            limit = 270 if self.state == 'EXECUTING' else 120
            if wall >= self.bound['t0'] + limit or mono >= self.mono_t0 + limit:
                return self.stop('PREPARE_DEADLINE' if limit == 270 else 'ADMISSION_EXPIRED')
        return 'NONE'

    def receive(self, msg, wall, mono):
        if self.state == 'TERMINAL':
            return 'NONE'
        if self.tick(wall, mono) == 'STOP':
            return 'STOP'
        if not isinstance(msg, dict) or msg.get('nonce') != self.nonce or msg.get('simulation') is not self.simulation:
            return self.stop('IDENTITY_OR_MODE')
        kind = msg.get('type')
        if kind == 'CANCEL':
            if set(msg) != {'type', 'nonce', 'simulation'}:
                return self.stop('SCHEMA')
            return self.stop('PARENT_CANCEL')
        fields = {'type', 'nonce', 'simulation', 'run_url', 'sha', 't0'}
        if kind == 'RUNNING':
            fields |= {'job_url', 'accepted_at', 'running_at', 'observed_at'}
        if kind not in ('BIND', 'RUNNING') or set(msg) != fields:
            return self.stop('SCHEMA')
        if msg['sha'] != SHA or not isinstance(msg['run_url'], str) or not RUN.fullmatch(msg['run_url']):
            return self.stop('REVISION_OR_RUN')
        if not number(msg['t0']) or not 0 <= wall - msg['t0'] < 120:
            return self.stop('ADMISSION_TIME')
        if kind == 'BIND':
            if self.state != 'ARMED':
                return self.stop('DUPLICATE_BIND')
            self.bound = {k: msg[k] for k in ('run_url', 'sha', 't0')}
            self.mono_t0 = mono - (wall - msg['t0'])
            self.state = 'BOUND'
            return 'BOUND'
        if self.state != 'BOUND' or any(msg[k] != self.bound[k] for k in self.bound):
            return self.stop('UNBOUND_OR_CHANGED_BINDING')
        if not isinstance(msg['job_url'], str) or not JOB.fullmatch(msg['job_url']) or not msg['job_url'].startswith(msg['run_url'] + '/job/'):
            return self.stop('JOB_BINDING')
        a, r, o = (msg[k] for k in ('accepted_at', 'running_at', 'observed_at'))
        if not all(number(x) for x in (a, r, o)) or not msg['t0'] <= a <= r <= o <= wall or wall - r > 20:
            return self.stop('STALE_OR_INVALID_RUNNING')
        self.state = 'EXECUTING'
        self.starts += 1
        return 'START'


def safe_file(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != 1001 or s.st_nlink != 1 or s.st_mode & 0o022:
        raise ValueError('UNSAFE_FILE')
    return path.read_bytes()


def preflight():
    if sys.platform != 'linux' or socket.gethostname() != 'synthetic-executor' or os.getuid() != 1001:
        raise ValueError('WRONG_EXECUTOR')
    s = BASE.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != 1001 or s.st_mode & 0o022:
        raise ValueError('UNSAFE_DIRECTORY')
    for name, digest in PINS.items():
        if hashlib.sha256(safe_file(BASE / name)).hexdigest() != digest:
            raise ValueError('HASH_MISMATCH')
    # Public known_hosts only. Never open the private key.
    result = subprocess.run(['/usr/bin/ssh-keygen', '-F', '192.0.2.1', '-f', '/nonexistent/synthetic-known-hosts'], capture_output=True, timeout=3, check=True)
    fp = subprocess.run(['/usr/bin/ssh-keygen', '-lf', '-'], input=result.stdout, capture_output=True, timeout=3, check=True)
    if not exact_ed25519(fp.stdout):
        raise ValueError('HOST_PIN')


def exact_ed25519(output):
    ed = [line.split()[1] for line in output.splitlines() if line.endswith(b'(ED25519)') and len(line.split()) >= 3]
    return ed == [b'SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA']


def launch(gate):
    preflight()
    if gate.tick(time.time(), time.monotonic()) == 'STOP':
        raise ValueError('EXPIRED_DURING_PREFLIGHT')
    # EXECUTING's 270s limit is not sufficient for starting: recheck 120s.
    if time.time() - gate.bound['t0'] >= 120 or time.monotonic() - gate.mono_t0 >= 120:
        raise ValueError('EXPIRED_DURING_PREFLIGHT')
    # Avoid preexec_fn in this threaded supervisor. Bootstrap sets PDEATHSIG
    # in its own fresh interpreter before exec of the pinned driver.
    bootstrap = ('import ctypes,os,signal,sys; '
                 'r=ctypes.CDLL(None).prctl(1,signal.SIGKILL); '
                 'os._exit(125) if r!=0 or os.getppid()!=int(sys.argv[1]) else None; '
                 'os.execv(sys.argv[2],sys.argv[2:])')
    return subprocess.Popen(['/usr/bin/python3', '-B', '-u', '-c', bootstrap, str(os.getpid()),
                             '/usr/bin/python3', '-B', '-u', str(BASE / 'prepare_driver.py'), '--run-url', gate.bound['run_url'], '--start-epoch', str(gate.bound['t0'])],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)


def reader(stream, events, label, max_line):
    try:
        while True:
            line = stream.readline(max_line + 1)
            if not line:
                events.put((label + '_EOF', b''))
                return
            events.put((label, line))
            if len(line) > max_line:
                return
    except Exception:
        events.put((label + '_ERROR', b''))


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('DUPLICATE_JSON_KEY')
        obj[key] = value
    return obj


def hard_timer(gate):
    """Kernel deadline covers blocked reads, launch, preflight and stdout.

    SIGALRM terminates supervisor directly; child PDEATHSIG contains driver.
    Missing supervisor output requires parent Stop independently.
    """
    wall, mono = time.time(), time.monotonic()
    if gate.state == 'ARMED':
        remaining = min(gate.arm_wall - wall, gate.arm_mono - mono)
    else:
        limit = 270 if gate.state == 'EXECUTING' else 120
        remaining = min(gate.bound['t0'] + limit - wall, gate.mono_t0 + limit - mono)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, remaining))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('simulation', 'live'), required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--arm-seconds', type=int, default=300)
    args = parser.parse_args()
    simulation = args.mode == 'simulation'
    gate = Gate(args.nonce, simulation, time.time(), time.monotonic(), args.arm_seconds)
    child = None
    lock = None
    def emit(state, **kw):
        print(json.dumps({'state': state, 'nonce': gate.nonce, 'simulation': simulation, 'at': time.time(), **kw}), flush=True)
    try:
        if not simulation:
            if sys.platform != 'linux':
                raise ValueError('WRONG_EXECUTOR')
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            hard_timer(gate)
            preflight()
            import fcntl
            lock = open(BASE / 'prepare_driver.py', 'rb')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        events = queue.Queue(maxsize=32)
        stdin_stream = getattr(sys.stdin.buffer, 'raw', sys.stdin.buffer)
        threading.Thread(target=reader, args=(stdin_stream, events, 'INPUT', 4096), daemon=True).start()
        emit('RECEIVER_READY', arm_expires_at=gate.arm_wall)
        output_bytes = 0
        while gate.state != 'TERMINAL':
            if gate.tick(time.time(), time.monotonic()) == 'STOP':
                break
            try:
                label, raw = events.get(timeout=0.05)
            except queue.Empty:
                continue
            if label == 'INPUT':
                if len(raw) > 4096:
                    gate.stop('OVERSIZE_INPUT'); continue
                try:
                    msg = json.loads(raw, object_pairs_hook=unique_object)
                except (ValueError, UnicodeError):
                    gate.stop('INVALID_JSON'); continue
                action = gate.receive(msg, time.time(), time.monotonic())
                if action == 'BOUND':
                    if not simulation:
                        hard_timer(gate)
                    emit('BOUND', run_url=gate.bound['run_url'], admission_deadline=gate.bound['t0'] + 120)
                elif action == 'START':
                    if simulation:
                        emit('SIMULATED_EXECUTION_STARTING', child_invocations=0)
                        gate.stop('SIMULATION_COMPLETE')
                    else:
                        hard_timer(gate)
                        child = launch(gate)
                        emit('EXECUTION_STARTING', pid=child.pid, deadline=gate.bound['t0'] + 270)
                        threading.Thread(target=reader, args=(child.stdout, events, 'CHILD', 1048576), daemon=True).start()
            elif label == 'CHILD':
                output_bytes += len(raw)
                if output_bytes > 1048576:
                    gate.stop('CHILD_OUTPUT_LIMIT'); continue
                try:
                    obj = json.loads(raw)
                    if not isinstance(obj, dict): raise ValueError('CHILD_JSON')
                except (ValueError, UnicodeError):
                    gate.stop('CHILD_OUTPUT_FORMAT'); continue
                emit('DRIVER_PROGRESS', result=obj)
                if obj.get('state') == 'ABORT_REQUEST_PARENT_STOP':
                    gate.stop('DRIVER_ABORT')
            elif label == 'CHILD_EOF':
                rc = child.wait(timeout=1)
                gate.stop('PREPARATION_COMPLETE' if rc == 0 else 'DRIVER_ABORT')
            else:
                gate.stop('CHANNEL_CLOSED_OR_ERROR')
    except Exception:
        gate.stop('SUPERVISOR_ERROR')
    finally:
        # Emit BEFORE cleanup so parent can submit independent Stop immediately.
        emit('SIMULATED_STOP_NOW' if simulation else 'STOP_NOW', reason=gate.reason or 'INTERRUPTED', child_started=child is not None)
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
        if lock is not None:
            lock.close()
    return 0 if gate.reason in ('SIMULATION_COMPLETE', 'PREPARATION_COMPLETE') else 2


if __name__ == '__main__':
    raise SystemExit(main())
