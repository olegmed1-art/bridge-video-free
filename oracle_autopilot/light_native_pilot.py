"""One accepted shared dispatch, with durable claim and session serialization.

No queue selection or task creation is provided. The deployment controller must
accept an actual published shared-admission dispatch and independently verify
its Cloud environment before installing the root-owned permit. This module is
not wired into the worker until its deployment and recovery contract is reviewed.
"""
from copy import deepcopy
from functools import wraps
import fcntl
import os
from pathlib import Path
import re
import stat
import time

from . import codex_cli_bridge as bridge
from .codex_cli_queue import NativeQueue
from .light_native_adapter import FALSE_FLAGS, LightNativeAdapter, ProviderTarget
from .light_native_preflight import validate as validate_preflight

REPOSITORY = 'olegmed1-art/bridge-video-free'
LOCK_KEYS = (1946, 2019)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def fail_closed(method):
    """A failed boundary quarantines this process even if authority returns."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        require(not self.failed, 'PILOT_SESSION_ALREADY_FAILED')
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.failed = True
            raise
    return guarded


class Permit:
    """Accepted canonical bytes, reread on each boundary; never auto-renewed."""
    def __init__(self, raw, accepted_digest, reader, source, *, clock=time.time, monotonic=time.monotonic):
        require(type(raw) is bytes and 0 < len(raw) <= 65536
                and bridge.digest(raw.decode('utf-8')) == accepted_digest, 'PILOT_PERMIT_DIGEST')
        value = bridge.parse(raw.decode('utf-8'))
        require(type(value) is dict and set(value) == {'version', 'source', 'dispatch',
            'environment_id', 'environment_evidence_sha256', 'issued_at', 'expires_at',
            'owner_preflight'}, 'PILOT_PERMIT_SCHEMA')
        require(bridge.canonical(value).encode() == raw and type(value['version']) is int
                and value['version'] == 1 and value['source'] == source
                and type(source) is str and bridge.SHA.fullmatch(source), 'PILOT_PERMIT_SOURCE')
        require(type(value['environment_evidence_sha256']) is str
                and re.fullmatch('[0-9a-f]{64}', value['environment_evidence_sha256']), 'PILOT_ENVIRONMENT_EVIDENCE')
        dispatch = value['dispatch']
        require(type(dispatch) is dict and set(dispatch) == {'dispatch_id', 'expected_head_sha',
            'branch', 'mode', 'assignment', 'target_pr', 'task_fingerprint'}, 'PILOT_DISPATCH_SCHEMA')
        require(type(dispatch['dispatch_id']) is str and bridge.UUID.fullmatch(dispatch['dispatch_id'])
                and type(dispatch['expected_head_sha']) is str and bridge.SHA.fullmatch(dispatch['expected_head_sha'])
                and type(dispatch['task_fingerprint']) is str
                and re.fullmatch('[0-9a-f]{64}', dispatch['task_fingerprint'])
                and type(dispatch['target_pr']) is int and 1 <= dispatch['target_pr'] <= 1000000
                and type(dispatch['branch']) is str
                and re.fullmatch(r'(?:codex|autopilot|fix)/[A-Za-z0-9_./-]{1,180}', dispatch['branch'])
                and '..' not in dispatch['branch'] and not dispatch['branch'].startswith('autopilot/dispatch/')
                and dispatch['mode'] == 'READ_ONLY', 'PILOT_DISPATCH_IDENTITY')
        assignment = dispatch['assignment']
        require(type(assignment) is dict and assignment.get('dispatch_id') == dispatch['dispatch_id']
                and assignment.get('execution_scope') == 'REPOSITORY'
                and assignment.get('can_repair') is False
                and assignment.get('task_kind') == 'REPOSITORY_AUDIT', 'PILOT_ASSIGNMENT')
        spec = assignment.get('task_spec_json')
        require(type(spec) is dict and spec.get('repository') == REPOSITORY
                and type(spec.get('target_pr')) is int and spec['target_pr'] == dispatch['target_pr']
                and spec.get('expected_head_sha') == dispatch['expected_head_sha']
                and spec.get('execution_mode') == 'READ_ONLY'
                and spec.get('assignment_schema') == 'SLAVIK_DISPATCH_ASSIGNMENT_V1'
                and spec.get('exact_head_binding') is True
                and all(spec.get(k) is False for k in FALSE_FLAGS)
                and all(type(spec.get(k)) is int and spec[k] == 0
                        for k in ('cost_cap_microusd', 'max_repair_attempts')), 'PILOT_SCOPE')
        self.target = ProviderTarget(value['environment_id'])
        now = clock()
        require(type(value['issued_at']) is int and type(value['expires_at']) is int
                and value['issued_at'] <= now < value['expires_at']
                and 0 < value['expires_at'] - value['issued_at'] <= 7200, 'PILOT_WINDOW')
        validate_preflight(value['owner_preflight'], dispatch, value['issued_at'], value['expires_at'])
        self.raw, self.value, self.reader = raw, value, reader
        self.clock, self.monotonic = clock, monotonic
        self.last_wall, self.deadline = now, monotonic() + value['expires_at'] - now
        self.failed = False

    def check(self):
        require(not self.failed, 'PILOT_PERMIT_ALREADY_FAILED')
        try:
            now = self.clock()
            require(self.reader() == self.raw and bridge.canonical(self.value).encode() == self.raw
                    and self.target == ProviderTarget(self.value['environment_id'])
                    and self.last_wall <= now < self.value['expires_at']
                    and self.monotonic() < self.deadline, 'PILOT_PERMIT_CHANGED_OR_EXPIRED')
            self.last_wall = now
        except BaseException:
            self.failed = True
            raise

    def bind(self, request):
        bridge.validate_request(request)
        require(bridge.canonical({k: v for k, v in request.items() if k != 'reservation_id'})
                == bridge.canonical(self.value['dispatch']), 'PILOT_RESERVATION_CHANGED')


class Claim:
    """One immutable claim across process restarts; no overwrite/reset API."""
    def __init__(self, directory):
        self.path = Path(directory)
        self.fd = self.lock = None
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.identity = os.fstat(self.fd)
            self.lock = os.open('pilot.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                0o600, dir_fd=self.fd)
            self.regular(self.lock)
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.pid = os.getpid()
            self.proc_pid = next(line.split()[1] for line in
                Path('/proc/self/status').read_text().splitlines() if line.startswith('Pid:'))
            os.fsync(self.fd)
            self.check()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def regular(fd):
        row = os.fstat(fd)
        require(stat.S_ISREG(row.st_mode) and row.st_uid == os.getuid()
                and stat.S_IMODE(row.st_mode) == 0o600 and row.st_nlink == 1, 'PILOT_PRIVATE_FILE')

    def check(self):
        require(self.fd is not None and self.lock is not None and os.getpid() == self.pid,
                'PILOT_CLAIM_CLOSED')
        row = self.path.lstat()
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == os.getuid()
                and stat.S_IMODE(row.st_mode) == 0o700
                and (row.st_dev, row.st_ino) == (self.identity.st_dev, self.identity.st_ino), 'PILOT_STORE_CHANGED')
        row, opened = os.stat('pilot.lock', dir_fd=self.fd, follow_symlinks=False), os.fstat(self.lock)
        require((row.st_dev, row.st_ino) == (opened.st_dev, opened.st_ino), 'PILOT_LOCK_CHANGED')
        self.regular(self.lock)
        locks = [line.split() for line in Path(f'/proc/self/fdinfo/{self.lock}').read_text().splitlines()
                 if line.startswith('lock:')]
        require(any(len(row) == 9 and row[2:5] == ['FLOCK', 'ADVISORY', 'WRITE']
                    and row[5] == self.proc_pid and row[6].split(':')[-1] == str(opened.st_ino)
                    and row[7:] == ['0', 'EOF'] for row in locks), 'PILOT_FILE_LOCK_LOST')

    def retain(self, name, raw):
        self.check()
        require(name in ('permit.json', 'request.json') and type(raw) is bytes
                and 0 < len(raw) <= 65536, 'PILOT_RECORD')
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as out:
                self.regular(out.fileno())
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.fsync(self.fd)
        self.exact(name, raw)

    def exact(self, name, raw):
        """Read only: loss of a retained record must never recreate it."""
        self.check()
        require(name in ('permit.json', 'request.json') and type(raw) is bytes
                and 0 < len(raw) <= 65536, 'PILOT_RECORD')
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as handle:
            self.regular(handle.fileno())
            require(handle.read(65537) == raw, 'PILOT_RECORD_CONFLICT')
        self.check()

    def prior_request(self):
        self.check()
        try:
            fd = os.open('request.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            self.regular(stream.fileno())
            raw = stream.read(65537)
        require(0 < len(raw) <= 65536, 'PILOT_RECORD_CONFLICT')
        try:
            value = bridge.parse(raw.decode())
            bridge.validate_request(value)
            require(bridge.canonical(value).encode() == raw, 'PILOT_RECORD_CONFLICT')
        except Exception:
            raise RuntimeError('PILOT_RECORD_CONFLICT') from None
        self.check()
        return value

    def close(self):
        for name in ('lock', 'fd'):
            fd = getattr(self, name)
            setattr(self, name, None)
            if fd is not None:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Session:
    """Trusted loader core; caller owns one verified direct runtime connection.

    The permanent Claim and native begin RPC prevent duplicate dispatch after
    connection loss. The advisory lock serializes cooperating live processes;
    it is not cancellation of an already running provider request.
    """
    def __init__(self, permit, claim, conn, read_pr, provider, *, admission):
        self.permit, self.claim, self.conn = permit, claim, conn
        self.read_pr, self.provider = read_pr, provider
        self.admission = admission
        self.failed = False
        permit.check()
        require(admission() is True, 'PILOT_NOT_ADMITTED')
        claim.retain('permit.json', permit.raw)  # Before reserve or any provider call.
        require(conn.autocommit and not conn.closed, 'PILOT_DIRECT_SESSION_REQUIRED')
        existing = conn.execute("SELECT EXISTS(SELECT FROM pg_catalog.pg_locks "
            "WHERE locktype='advisory' AND pid=pg_catalog.pg_backend_pid() AND classid=%s "
            "AND objid=%s AND objsubid=2 AND granted)", LOCK_KEYS).fetchone()
        require(existing == (False,), 'PILOT_SESSION_ALREADY_BOUND')
        row = conn.execute('SELECT pg_catalog.pg_backend_pid(),pg_catalog.pg_try_advisory_lock(%s,%s)',
                           LOCK_KEYS).fetchone()
        require(type(row) is tuple and len(row) == 2 and type(row[0]) is int and row[1] is True,
                'PILOT_SESSION_BUSY')
        self.pid = row[0]
        self.queue = NativeQueue(self.rpc)
        self.request = None

    def check(self):
        require(not self.failed, 'PILOT_SESSION_ALREADY_FAILED')
        try:
            self.permit.check()
            require(self.admission() is True, 'PILOT_NOT_ADMITTED')
            self.claim.check()
            self.claim.exact('permit.json', self.permit.raw)
            if self.request is not None:
                self.claim.exact('request.json', bridge.canonical(self.request).encode())
            require(not self.conn.closed, 'PILOT_SESSION_LOST')
            row = self.conn.execute("SELECT pg_catalog.pg_backend_pid(),EXISTS(SELECT FROM pg_catalog.pg_locks "
                "WHERE locktype='advisory' AND pid=pg_catalog.pg_backend_pid() AND classid=%s "
                "AND objid=%s AND objsubid=2 AND mode='ExclusiveLock' AND granted)", LOCK_KEYS).fetchone()
            require(row == (self.pid, True), 'PILOT_SESSION_LOCK_LOST')
        except BaseException:
            self.failed = True
            raise

    @fail_closed
    def rpc(self, sql, parameters):
        self.check()
        row = self.conn.execute(sql, parameters).fetchone()
        require(type(row) is tuple and len(row) == 1, 'PILOT_RPC_RESULT')
        self.check()
        return {'payload': row[0]}

    @fail_closed
    def reserve(self):
        self.check()
        dispatch = self.permit.value['dispatch']
        prior = self.claim.prior_request()
        if prior is not None:
            self.permit.bind(prior)
            self.request = deepcopy(prior)
            row = self.queue.snapshot(dispatch['dispatch_id'])
            require(bridge.canonical(row['request']) == bridge.canonical(prior), 'PILOT_RESERVATION_CHANGED')
            self.check()
            return deepcopy(prior)
        pr = self.read_pr(dispatch['target_pr'])
        require(pr['number'] == dispatch['target_pr'] and pr['state'] == 'open'
                and pr['head']['sha'] == dispatch['expected_head_sha']
                and pr['head']['ref'] == dispatch['branch']
                and pr['head']['repo']['full_name'] == pr['base']['repo']['full_name'] == REPOSITORY,
                'PILOT_PR_CHANGED')
        row = self.queue.reserve(dispatch['dispatch_id'], deepcopy(dispatch['assignment']), dispatch['branch'])
        request = row['request']
        self.permit.bind(request)
        self.claim.retain('request.json', bridge.canonical(request).encode())
        self.request = deepcopy(request)
        self.check()
        return request

    @fail_closed
    def gate(self, request, target):
        self.check()
        self.permit.bind(request)
        require(target == self.permit.target and bridge.canonical(request) == bridge.canonical(self.request),
                'PILOT_BINDING_CHANGED')
        # An acknowledged task may drain after config disablement, per native
        # RPC authority. A changed/expired root permit still denies every call.
        current = self.queue.current(request)
        if not current:
            row = self.queue.snapshot(request['dispatch_id'])
            require(row['state'] == 'TERMINAL' and bridge.canonical(row['request']) == bridge.canonical(request),
                    'PILOT_DATABASE_AUTHORITY_LOST')
        return True

    @fail_closed
    def step(self):
        self.check()
        require(self.request is not None, 'PILOT_RESERVATION_REQUIRED')
        adapter = LightNativeAdapter(self.request, profile='light', target=self.permit.target,
            rpc=self.rpc, read_pr=self.read_pr, provider=self.provider, gate=self.gate)
        result = adapter.step()
        require(result.get('state') not in ('UNKNOWN', 'SUBMISSION_UNKNOWN', 'RESERVATION_HELD'),
                'PILOT_QUARANTINED')
        self.check()
        return result
