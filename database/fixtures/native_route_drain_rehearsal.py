"""Disposable process/flock/PostgreSQL rehearsal; never a production drain command.

Run as root in ephemeral CI for the unchanged lease program's ownership checks.
Only transport, route directory, CA and consumer entrypoint are test substitutes.
The actual route lease and consumer supervisor code execute unchanged.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time

from ops import github_autopilot_db_route as supervisor
from ops import oracle_light_route_lease as lease

DSN = 'postgresql://postgres:postgres@localhost:5432/bridge_school_ci'
TABLE = 'public.native_route_drain_probe'
CA = 'disposable-certificate-placeholder'
SCRIPT = str(Path(__file__).resolve())


def check(value, code):
    if not value:
        raise RuntimeError(code)


def until(predicate, code, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise RuntimeError(code)


def connection():
    from database.fixtures.native_cli_commit_rehearsal import connection as disposable_connection
    return disposable_connection()


def local_connection(conn):
    check(conn.info.host == 'localhost' and conn.info.port == 5432
          and conn.info.hostaddr in ('127.0.0.1', '::1'), 'LOOPBACK_DATABASE_REQUIRED')


def route_file(root, backend):
    (root / 'route.json').write_text(json.dumps(
        dict(version=1, backend=backend, database='autopilot', epoch=1)))
    (root / 'route.json').chmod(0o644)


def fixture_root(root):
    check(root == root.resolve() and root.parent.parent == Path('/tmp')
          and root.parent.name.startswith('native-route-drain-')
          and root.name in ('protocol', 'normal', 'uncommitted-loss', 'commit-before-loss', 'wrapper-crash')
          and root.parent.stat().st_uid == 0
          and root.parent.stat().st_mode & 0o777 == 0o700,
          'DISPOSABLE_ROUTE_DIRECTORY_REQUIRED')


def publish_pid(path, pid):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(str(pid))
    temporary.replace(path)


def setup(root):
    fixture_root(root)
    root.mkdir(mode=0o755)
    lock = root / 'route.lock'
    lock.touch(mode=0o644)
    info = lock.stat()
    (root / 'lock-identity.json').write_text(json.dumps(dict(device=info.st_dev, inode=info.st_ino)))
    (root / 'ca.crt').write_text(CA)
    for name in ('lock-identity.json', 'ca.crt'):
        (root / name).chmod(0o644)
    route_file(root, 'neon')


def transport(root):
    # This local process replaces SSH only; no socket, host or credential is used.
    fixture_root(root)
    publish_pid(root / 'lease-pid', os.getpid())
    lease.ROOT = root
    os.environ['SSH_ORIGINAL_COMMAND'] = 'route-v1'
    return lease.main()


def consumer(root, mode):
    fixture_root(root)
    pid = os.getpid()
    identity = dict(pid=pid, start=process_start(pid), group=os.getpgrp())
    temporary = root / 'consumer-identity.tmp'
    temporary.write_text(json.dumps(identity))
    temporary.replace(root / 'consumer-identity.json')
    with connection() as conn:
        local_connection(conn)
        with conn.transaction():
            conn.execute(f'INSERT INTO {TABLE}(scenario) VALUES (%s)', (mode,))
            publish_pid(root / 'backend-pid', conn.info.backend_pid)
            until(lambda: (root / 'release-consumer').exists(), 'CONSUMER_RELEASE_TIMEOUT', 20)
        (root / 'committed').touch()
        if mode == 'commit-before-loss':
            until(lambda: (root / 'finish-consumer').exists(), 'CONSUMER_FINISH_TIMEOUT', 20)
    return 0


def wrapper(root, mode):
    fixture_root(root)
    def cancelled(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    supervisor.CA_SHA256 = hashlib.sha256(CA.encode()).hexdigest()
    supervisor.TARGETS = {'fixture': ('ADMIN_DATABASE_URL', 'postgres',
        'database.fixtures.native_route_drain_rehearsal', ('--consumer', str(root), mode))}
    return supervisor.execute_under_lease(
        [sys.executable, SCRIPT, '--transport', str(root)], 'fixture', os.environ.copy(), root)


def start_lease(root):
    process = subprocess.Popen([sys.executable, SCRIPT, '--transport', str(root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        check(select.select([process.stdout], [], [], 5)[0], 'LEASE_HEADER_TIMEOUT')
        record = json.loads(process.stdout.readline())
        return process, record
    except BaseException:
        process.kill()
        process.communicate(timeout=5)
        raise


def finish_lease(process):
    process.communicate(timeout=5)
    check(process.returncode == 0, 'LEASE_EXIT_FAILED')


def prove_busy(root):
    process, record = start_lease(root)
    check(record == {'busy': True}, 'EXCLUSIVE_LOCK_ADMITTED_LEASE')
    finish_lease(process)


def protocol_only(root):
    process, record = start_lease(root)
    try:
        check(record['route']['backend'] == 'neon', 'LEASE_NOT_OPEN')
        with (root / 'route.lock').open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise RuntimeError('LIVE_SHARED_LEASE_NOT_EXCLUDED')
        finish_lease(process)
        with (root / 'route.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            prove_busy(root)
            route_file(root, 'paused')
        paused, record = start_lease(root)
        check(record == {'route': dict(version=1, backend='paused', database='autopilot', epoch=1)},
              'PAUSED_ROUTE_NOT_CLOSED')
        finish_lease(paused)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    print('NATIVE_ROUTE_REAL_FLOCK_PROTOCOL_PASS')


def backend_state(conn, pid):
    return conn.execute('SELECT state FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()


def process_start(pid):
    # Linux /proc stat field 22, after stripping the parenthesized comm field.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def cleanup_consumer(root):
    """Fixture-only cleanup after abrupt wrapper death; never kill DB backends."""
    identity_file = root / 'consumer-identity.json'
    if identity_file.exists():
        identity = json.loads(identity_file.read_text())
        pid = identity['pid']
        try:
            status = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            check(identity['group'] == pid and os.getpgid(pid) == pid
                  and status[19] == identity['start'],
                  'FIXTURE_CONSUMER_IDENTITY_CHANGED')
            if status[0] != 'Z':
                arguments = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                check(b'--consumer' in arguments and str(root).encode() in arguments,
                      'FIXTURE_CONSUMER_COMMAND_CHANGED')
                os.killpg(pid, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass
    if (root / 'backend-pid').exists():
        pid = int((root / 'backend-pid').read_text())
        with connection() as observer:
            local_connection(observer)
            until(lambda: backend_state(observer, pid) is None, 'CLEANUP_BACKEND_NOT_DRAINED')


def scenario(root, mode):
    route_file(root, 'neon')
    process = subprocess.Popen([sys.executable, SCRIPT, '--wrapper', str(root), mode],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               start_new_session=True)
    stopped = False
    try:
        until(lambda: (root / 'backend-pid').exists(), 'CONSUMER_NOT_STARTED')
        pid = int((root / 'backend-pid').read_text())
        with connection() as observer:
            local_connection(observer)
            until(lambda: backend_state(observer, pid) == ('idle in transaction',), 'TX_NOT_OPEN')
            with (root / 'route.lock').open('rb') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise RuntimeError('DRAIN_ENTERED_DURING_NORMAL_CONSUMER')
                if mode == 'normal':
                    (root / 'release-consumer').touch()
                    process.wait(timeout=10)
                    check(process.returncode == 0, 'NORMAL_SUPERVISOR_FAILED')
                elif mode == 'wrapper-crash':
                    process.kill()
                    process.wait(timeout=5)
                else:
                    # Deterministically widen the real supervisor's lease-loss
                    # detection interval; no scheduling-speed assumption.
                    os.kill(process.pid, signal.SIGSTOP)
                    stopped = True
                    until(lambda: '\nState:\tT' in Path(f'/proc/{process.pid}/status').read_text(),
                          'SUPERVISOR_NOT_STOPPED')
                    if mode == 'commit-before-loss':
                        (root / 'release-consumer').touch()
                        until(lambda: (root / 'committed').exists(), 'COMMIT_NOT_OBSERVED')
                    os.kill(int((root / 'lease-pid').read_text()), signal.SIGKILL)
                def acquire():
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return True
                    except BlockingIOError:
                        return False
                until(acquire, 'EXCLUSIVE_LOCK_NOT_AVAILABLE')
                prove_busy(root)
                if mode != 'normal':
                    check(backend_state(observer, pid) is not None, 'RACE_NOT_REPRODUCED')
                    if mode == 'uncommitted-loss':
                        check(backend_state(observer, pid) == ('idle in transaction',), 'OPEN_TX_LOST')
                    if mode == 'wrapper-crash':
                        cleanup_consumer(root)
                    else:
                        os.kill(process.pid, signal.SIGCONT)
                        stopped = False
                        process.wait(timeout=10)
                    check(process.returncode != 0, 'LEASE_LOSS_ACCEPTED')
                until(lambda: backend_state(observer, pid) is None, 'DATABASE_BACKEND_NOT_DRAINED')
                count = observer.execute(f'SELECT count(*) FROM {TABLE} WHERE scenario=%s', (mode,)).fetchone()[0]
                check(count == (0 if mode in ('uncommitted-loss', 'wrapper-crash') else 1), 'COMMIT_OUTCOME_MISMATCH')
    finally:
        if stopped:
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stderr.close()
        cleanup_consumer(root)
    print('NATIVE_ROUTE_DRAIN_SCENARIO_PASS', mode)


def main(protocol=False):
    check(os.getuid() == 0, 'ROOT_REQUIRED_FOR_REAL_LEASE_OWNERSHIP_TEST')
    if not protocol:
        check(os.environ.get('ADMIN_DATABASE_URL') == DSN, 'DISPOSABLE_DSN_REQUIRED')
        with connection() as conn:
            local_connection(conn)
            check(conn.execute('SELECT to_regclass(%s)', (TABLE,)).fetchone()[0] is None, 'FIXTURE_TABLE_EXISTS')
            conn.execute(f'CREATE TABLE {TABLE}(scenario text PRIMARY KEY)')
    try:
        with tempfile.TemporaryDirectory(prefix='native-route-drain-', dir='/tmp') as directory:
            base = Path(directory)
            root = base / 'protocol'
            setup(root)
            protocol_only(root)
            if not protocol:
                for mode in ('normal', 'uncommitted-loss', 'commit-before-loss', 'wrapper-crash'):
                    root = base / mode
                    setup(root)
                    scenario(root, mode)
    finally:
        if not protocol:
            with connection() as conn:
                local_connection(conn)
                conn.execute(f'DROP TABLE {TABLE}')
    if not protocol:
        print('NATIVE_ROUTE_FLOCK_ALONE_NOT_TRANSACTION_DRAIN_CONFIRMED')
        print('NATIVE_ROUTE_LOST_LEASE_DOES_NOT_PROVE_ROLLBACK_CONFIRMED')


if __name__ == '__main__':
    args = sys.argv[1:]
    if args[:1] == ['--transport']:
        sys.exit(transport(Path(args[1])))
    elif args[:1] == ['--consumer']:
        sys.exit(consumer(Path(args[1]), args[2]))
    elif args[:1] == ['--wrapper']:
        sys.exit(wrapper(Path(args[1]), args[2]))
    else:
        check(args in ([], ['--protocol-only']), 'UNSUPPORTED_ARGUMENTS')
        main(protocol=bool(args))
