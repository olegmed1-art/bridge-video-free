"""Finite legacy transition kernel over trusted fixed ports; no production CLI.

Ports derive actual observations from verified capture/systemd/proc/primary
sources. Portable ports prove orchestration contracts, not production acceptance.
CurrentAnchor, original baseline/history and Native admission are never authority.
The fixed lifecycle_clock(request) port returns LifecycleClock from actual boot
ID and CLOCK_BOOTTIME; its once-retained recovery anchor is never renewed.
"""
import base64
from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


class Refusal(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Refusal('LEGACY_TRANSITION_' + code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value, length=64):
    need(type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value), 'PIN')


def integer(value, minimum=0, maximum=2**63 - 1):
    need(type(value) is int and minimum <= value <= maximum, 'INTEGER')


def exact(value, names):
    need(type(value) is dict and set(value) == set(names), 'SCHEMA')


def parse(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 65536, 'BYTES')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE')
            result[key] = value
        return result
    def number(_):
        raise Refusal('LEGACY_TRANSITION_NUMBER')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=number)
        need(encoded(value) == raw, 'CANONICAL')
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal('LEGACY_TRANSITION_JSON') from None


def _path(value):
    need(type(value) is str and re.fullmatch(r'/[A-Za-z0-9_./-]{1,1023}', value) and
         all(part not in ('', '.', '..') for part in value.split('/')[1:]), 'PATH')


def drop_bytes(release, admission):
    need(admission in ('ACTIVE', 'HOLD'), 'ADMISSION')
    return ('[Service]\nWorkingDirectory=' + release + '\nEnvironment=AUTOPILOT_ADMISSION_MODE=' +
            admission + '\nEnvironmentFile=' + release + '/ops/autopilot/broker-hold.env\n').encode()


@dataclass(frozen=True)
class Snapshot:
    observed_at: int
    hostname: str
    unit: str
    legacy_source: str
    legacy_bundle_sha256: str
    release: str
    state: str
    admission: str
    pid: int
    invocation_id: str
    start_ticks: int
    immutable_config_sha256: str
    hardening_sha256: str
    drop_bytes: bytes
    control_pid: int = 0
    cgroup_empty: bool = False
    needs_reload: bool = False

    def binding(self):
        value = asdict(self)
        value.pop('observed_at')
        value['drop_base64'] = base64.b64encode(value.pop('drop_bytes')).decode()
        return value

    @classmethod
    def from_binding(cls, value):
        names = set(cls.__dataclass_fields__) - {'observed_at', 'drop_bytes'} | {'drop_base64'}
        exact(value, names)
        try:
            raw = base64.b64decode(value['drop_base64'], validate=True)
        except (TypeError, ValueError):
            raise Refusal('LEGACY_TRANSITION_DROP') from None
        return cls(observed_at=0, drop_bytes=raw, **{k: v for k, v in value.items() if k != 'drop_base64'})

    def validate(self, now, *, fresh=True):
        integer(self.observed_at)
        integer(now)
        need(not fresh or 0 <= now - self.observed_at <= 30, 'SNAPSHOT_STALE')
        need(type(self.hostname) is str and re.fullmatch('[A-Za-z0-9.-]{1,253}', self.hostname), 'HOST')
        need(type(self.unit) is str and re.fullmatch(r'[A-Za-z0-9_.@-]{1,190}\.service', self.unit), 'UNIT')
        digest(self.legacy_source, 40)
        for value in (self.legacy_bundle_sha256, self.immutable_config_sha256, self.hardening_sha256):
            digest(value)
        _path(self.release)
        need('/releases/' in self.release and self.release.rsplit('/releases/', 1)[1] == self.legacy_source, 'RELEASE')
        integer(self.pid, maximum=2**31 - 1)
        integer(self.control_pid, maximum=2**31 - 1)
        integer(self.start_ticks)
        need(type(self.cgroup_empty) is bool and type(self.needs_reload) is bool and
             self.admission in ('ACTIVE', 'HOLD') and self.control_pid == 0, 'STATE')
        need(type(self.drop_bytes) is bytes and self.drop_bytes in
             (drop_bytes(self.release, 'ACTIVE'), drop_bytes(self.release, 'HOLD')), 'DROP')
        if self.state == 'RUNNING':
            need(self.pid > 1 and self.start_ticks > 0 and not self.cgroup_empty and not self.needs_reload and
                 self.drop_bytes == drop_bytes(self.release, self.admission), 'RUNNING')
            digest(self.invocation_id, 32)
        else:
            need(self.state == 'STOPPED' and self.pid == self.start_ticks == 0 and self.cgroup_empty, 'ABSENCE')
            need(self.invocation_id == '' or type(self.invocation_id) is str and re.fullmatch('[0-9a-f]{32}', self.invocation_id), 'INVOCATION')
        return self


@dataclass(frozen=True)
class NativeHold:
    observed_at: int
    unit: str
    state: str
    admission: str
    pid: int
    invocation_id: str
    start_ticks: int
    configuration_sha256: str

    def binding(self):
        return {k: v for k, v in asdict(self).items() if k != 'observed_at'}

    def validate(self, now):
        integer(self.observed_at)
        need(0 <= now - self.observed_at <= 30 and self.admission == 'HOLD', 'NATIVE_HOLD')
        need(type(self.unit) is str and re.fullmatch(r'[A-Za-z0-9_.@-]{1,190}\.service', self.unit), 'NATIVE_UNIT')
        digest(self.configuration_sha256)
        integer(self.pid)
        integer(self.start_ticks)
        if self.state == 'RUNNING':
            need(self.pid > 1 and self.start_ticks > 0, 'NATIVE_PROCESS')
            digest(self.invocation_id, 32)
        else:
            need(self.state == 'STOPPED' and self.pid == self.start_ticks == 0 and self.invocation_id == '', 'NATIVE_PROCESS')
        return self


@dataclass(frozen=True)
class ArmedCleanup:
    observed_at: int
    request_sha256: str
    unit: str
    pid: int
    invocation_id: str
    start_ticks: int
    script_sha256: str
    exec_stop_post_request_sha256: str
    runtime_max_seconds: int
    timeout_stop_seconds: int
    user: str = 'root'
    group: str = 'root'
    no_new_privileges: str = 'yes'
    kill_mode: str = 'control-group'
    restart: str = 'no'
    phase: str = 'MAIN'

    def wire(self):
        return asdict(self)


@dataclass(frozen=True)
class DrainProof:
    observed_at: int
    request_sha256: str
    snapshot_sha256: str
    database: dict
    provider: dict
    leases: dict

    def wire(self):
        return asdict(self)


@dataclass(frozen=True)
class LifecycleClock:
    observed_at: int
    boot_id: str
    boottime_ns: int


REQUEST_KEYS = {'version', 'kind', 'source', 'controller_sha256', 'legacy_source', 'legacy_bundle_sha256',
    'original_baseline_sha256', 'original_inventory_sha256', 'before', 'native_before', 'start_hold',
    'max_seconds', 'cleanup_script_sha256', 'forward_agreement', 'active_recovery_agreement'}


def scope_sha256(value, purpose):
    need(purpose in ('FORWARD', 'RECOVERY'), 'PURPOSE')
    base = {k: v for k, v in value.items() if k not in ('forward_agreement', 'active_recovery_agreement')}
    return sha(encoded(dict(operation='LEGACY_ACTIVE_TO_HOLD' if purpose == 'FORWARD' else
                            'RESTORE_PREVIOUS_LEGACY_ACTIVE', request_scope=base)))


def _agreement(value, accepted, request, purpose):
    exact(value, ('version', 'kind', 'operation_sha256', 'not_before', 'expires_at', 'evidence_sha256'))
    digest(accepted)
    digest(value['evidence_sha256'])
    need(type(value['version']) is int and value['version'] == 1 and
         value['kind'] == ('LEGACY_TRANSITION_FORWARD' if purpose == 'FORWARD' else 'LEGACY_TRANSITION_ACTIVE_RECOVERY') and
         sha(encoded(value)) == accepted and value['operation_sha256'] == scope_sha256(request, purpose), 'AGREEMENT')
    integer(value['not_before'])
    integer(value['expires_at'])
    need(0 < value['expires_at'] - value['not_before'] <= (1800 if purpose == 'FORWARD' else 300), 'AGREEMENT_WINDOW')


@dataclass(frozen=True, repr=False)
class Request:
    raw: bytes
    digest: str
    accepted_forward_agreement_sha256: str
    accepted_active_recovery_agreement_sha256: str | None

    @property
    def value(self):
        return parse(self.raw)

    @property
    def before(self):
        return Snapshot.from_binding(self.value['before'])

    @classmethod
    def load(cls, raw, accepted_request_sha256, *, accepted_forward_agreement_sha256,
             accepted_active_recovery_agreement_sha256=None):
        digest(accepted_request_sha256)
        need(type(raw) is bytes and sha(raw) == accepted_request_sha256, 'REQUEST_PIN')
        value = parse(raw)
        exact(value, REQUEST_KEYS)
        need(type(value['version']) is int and value['version'] == 1 and value['kind'] == 'LEGACY_HOLD_TRANSITION', 'REQUEST')
        digest(value['source'], 40)
        digest(value['legacy_source'], 40)
        for key in ('controller_sha256', 'legacy_bundle_sha256', 'original_baseline_sha256', 'original_inventory_sha256', 'cleanup_script_sha256'):
            digest(value[key])
        need(type(value['start_hold']) is bool, 'TARGET')
        integer(value['max_seconds'], 30, 300)
        before = Snapshot.from_binding(value['before']).validate(0, fresh=False)
        need(before.state == 'RUNNING' and before.admission == 'ACTIVE' and
             before.legacy_source == value['legacy_source'] and before.legacy_bundle_sha256 == value['legacy_bundle_sha256'], 'BEFORE')
        exact(value['native_before'], set(NativeHold.__dataclass_fields__) - {'observed_at'})
        NativeHold(observed_at=0, **value['native_before']).validate(0)
        _agreement(value['forward_agreement'], accepted_forward_agreement_sha256, value, 'FORWARD')
        recovery = value['active_recovery_agreement']
        if recovery is None:
            need(accepted_active_recovery_agreement_sha256 is None, 'RECOVERY_SCOPE')
        else:
            _agreement(recovery, accepted_active_recovery_agreement_sha256, value, 'RECOVERY')
            need(value['forward_agreement']['not_before'] <= recovery['not_before'] and
                 recovery['expires_at'] <= value['forward_agreement']['expires_at'] + 300, 'RECOVERY_WINDOW')
        return cls(raw, accepted_request_sha256, accepted_forward_agreement_sha256, accepted_active_recovery_agreement_sha256)

    def check(self):
        actual = Request.load(self.raw, self.digest, accepted_forward_agreement_sha256=self.accepted_forward_agreement_sha256,
                              accepted_active_recovery_agreement_sha256=self.accepted_active_recovery_agreement_sha256)
        need(type(self) is Request and self == actual, 'REQUEST_PROJECTION')


ROLES = frozenset(('request', 'intent', 'armed', 'stop-intent', 'stop-observed', 'hold-drop-intent',
    'reload-intent', 'hold-start-intent', 'hold-started', 'completed', 'unknown', 'recovery-stop-intent',
    'recovery-stop-observed', 'active-drop-intent', 'active-reload-intent', 'active-start-intent',
    'active-started', 'cleanup-stop-intent', 'cleanup-drop-intent', 'cleanup-reload-intent',
    'recovery-anchor', 'hold-dropped', 'hold-configured', 'active-dropped', 'active-configured'))


class Journal:
    """New root-private create-only journal. Partial records are replay barriers."""
    def __init__(self, root):
        self.root = Path(root)
        self._parents()
        self.root.mkdir(mode=0o700)
        self._sync(self.root.parent)
        self._directory()

    @classmethod
    def open(cls, root):
        value = object.__new__(cls)
        value.root = Path(root)
        value._parents()
        value._directory()
        return value

    def _parents(self):
        need(hasattr(os, 'geteuid') and os.geteuid() == 0 and self.root.is_absolute(), 'JOURNAL_ROOT')
        for parent in self.root.parents:
            row = parent.lstat()
            need(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022, 'JOURNAL_PARENT')

    def _directory(self):
        row = self.root.lstat()
        need(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and stat.S_IMODE(row.st_mode) == 0o700, 'JOURNAL_DIRECTORY')
        need(all(path.name.endswith('.json') and path.name[:-5] in ROLES for path in self.root.iterdir()), 'JOURNAL_INVENTORY')

    @staticmethod
    def _sync(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _path(self, role):
        need(role in ROLES, 'JOURNAL_ROLE')
        self._parents()
        self._directory()
        return self.root / (role + '.json')

    def retain(self, role, raw):
        need(type(raw) is bytes and 0 < len(raw) <= 65536, 'JOURNAL_BYTES')
        path = self._path(role)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            row = os.fstat(fd)
            need(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and row.st_nlink == 1 and stat.S_IMODE(row.st_mode) == 0o600, 'JOURNAL_METADATA')
            left = raw
            while left:
                count = os.write(fd, left)
                need(count > 0, 'JOURNAL_WRITE')
                left = left[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self._sync(self.root)
        need(self.read(role) == raw, 'JOURNAL_READBACK')

    def read(self, role):
        path = self._path(role)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and
                 stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= 65536, 'JOURNAL_METADATA')
            raw = os.read(fd, 65537)
            after, linked = os.fstat(fd), path.stat(follow_symlinks=False)
            signature = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_gid,
                                     row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            need(len(raw) == before.st_size <= 65536 and signature(before) == signature(after) == signature(linked), 'JOURNAL_DRIFT')
            return raw
        finally:
            os.close(fd)


@dataclass(frozen=True)
class Outcome:
    state: str
    request_sha256: str
    unknown: bool = False
    safe_state: str = 'UNKNOWN'
    historical_chain_verified: bool = False
    original_baseline_matched: bool = False
    incident_closed: bool = False
    replay_authorized: bool = False
    native_admission_authorized: bool = False
    new_task_authorized: bool = False

    def public(self):
        return dict(version=1, audit='LEGACY_HOLD_TRANSITION', **asdict(self))


class _Budget:
    def __init__(self, request, clock, monotonic):
        self.request, self.clock, self.monotonic = request, clock, monotonic
        self.started, self.last_wall = clock(), clock()
        self.deadline = monotonic() + request.value['max_seconds']

    def forward(self):
        now = self.clock()
        agreement = self.request.value['forward_agreement']
        need(self.last_wall <= now and self.started <= now and self.monotonic() < self.deadline and
             agreement['not_before'] <= now < agreement['expires_at'], 'FORWARD_EXPIRED')
        self.last_wall = now


def _native(request, ports, clock):
    value = ports.native_hold(request)
    need(type(value) is NativeHold, 'NATIVE_READBACK')
    value.validate(int(clock()))
    need(value.binding() == request.value['native_before'], 'NATIVE_CHANGED')
    return value


def _snapshot(request, ports, clock):
    value = ports.snapshot(request)
    need(type(value) is Snapshot, 'SNAPSHOT_READBACK')
    value.validate(int(clock()))
    before = request.before
    for key in ('hostname', 'unit', 'legacy_source', 'legacy_bundle_sha256', 'release',
                'immutable_config_sha256', 'hardening_sha256'):
        need(getattr(value, key) == getattr(before, key), 'CONFIGURATION_CHANGED')
    return value


def _armed(request, value, now, *, original=None, cleanup=False, fresh=True):
    need(type(value) is ArmedCleanup, 'CLEANUP_READBACK')
    integer(value.observed_at)
    need(not fresh or 0 <= now - value.observed_at <= 30, 'CLEANUP_STALE')
    need(value.request_sha256 == value.exec_stop_post_request_sha256 == request.digest and
         value.script_sha256 == request.value['cleanup_script_sha256'], 'CLEANUP_BINDING')
    need(type(value.unit) is str and re.fullmatch(r'[A-Za-z0-9_.@-]{1,190}\.service', value.unit) and
         value.unit not in (request.before.unit, request.value['native_before']['unit']), 'CLEANUP_UNIT')
    integer(value.pid, 2)
    integer(value.start_ticks, 1)
    digest(value.invocation_id, 32)
    integer(value.runtime_max_seconds, 1, 300)
    integer(value.timeout_stop_seconds, 1, 120)
    need(value.runtime_max_seconds == request.value['max_seconds'] and
         value.user == value.group == 'root' and value.no_new_privileges == 'yes' and
         value.kill_mode == 'control-group' and value.restart == 'no' and
         value.phase in (('MAIN', 'CLEANUP') if cleanup else ('MAIN',)), 'CLEANUP_NOT_ARMED')
    if original is not None:
        stable = set(ArmedCleanup.__dataclass_fields__) - {'observed_at', 'phase', 'pid', 'start_ticks'}
        need(all(getattr(value, key) == getattr(original, key) for key in stable), 'CLEANUP_REPLACED')
        need(value.phase == 'CLEANUP' or (value.pid, value.start_ticks) == (original.pid, original.start_ticks), 'CLEANUP_REPLACED')
    return value


def _retained_armed(request, journal):
    raw = journal.read('armed')
    need(raw is not None, 'CLEANUP_NOT_RETAINED')
    value = parse(raw)
    exact(value, ArmedCleanup.__dataclass_fields__)
    return _armed(request, ArmedCleanup(**value), 0, fresh=False)


def _lifecycle_clock(request, ports, clock):
    value = ports.lifecycle_clock(request)
    need(type(value) is LifecycleClock, 'LIFECYCLE_CLOCK')
    integer(value.observed_at)
    integer(value.boottime_ns, 1)
    need(0 <= int(clock()) - value.observed_at <= 30 and type(value.boot_id) is str and
         re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value.boot_id),
         'LIFECYCLE_CLOCK')
    return value


def _anchor_value(request, value, armed_wall):
    agreement = request.value['active_recovery_agreement']
    integer(armed_wall)
    need(0 <= armed_wall - value.observed_at <= 30, 'RECOVERY_ANCHOR_CLOCK')
    # Conservative whole-second projection: never extend the accepted window.
    return dict(version=1, request_sha256=request.digest, initial=asdict(value), armed_wall=armed_wall,
        not_before_ns=value.boottime_ns + max(0, agreement['not_before'] - value.observed_at + 1) * 10**9,
        expires_ns=value.boottime_ns + (agreement['expires_at'] - armed_wall - 1) * 10**9)


def _recovery_anchor(request, journal):
    agreement = request.value['active_recovery_agreement']
    need(agreement is not None, 'RECOVERY_NOT_ACCEPTED')
    raw = journal.read('recovery-anchor')
    need(raw is not None, 'RECOVERY_ANCHOR')
    value = parse(raw)
    exact(value, ('version', 'request_sha256', 'initial', 'armed_wall', 'not_before_ns', 'expires_ns'))
    exact(value['initial'], LifecycleClock.__dataclass_fields__)
    initial = LifecycleClock(**value['initial'])
    integer(initial.observed_at)
    integer(initial.boottime_ns, 1)
    need(type(value['version']) is int and value == _anchor_value(request, initial, value['armed_wall']), 'RECOVERY_ANCHOR')
    return value


def _boottime_ns():
    need(hasattr(time, 'CLOCK_BOOTTIME'), 'LOCAL_BOOTTIME_UNAVAILABLE')
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


class _RecoveryBudget:
    def __init__(self, request, journal, clock, boottime_ns):
        self.request, self.clock, self.boottime_ns = request, clock, boottime_ns
        self.anchor = _recovery_anchor(request, journal)
        self.last_wall = self.anchor['armed_wall']
        self.last_ns = self.anchor['initial']['boottime_ns']

    def local(self):
        wall, ns = self.clock(), self.boottime_ns()
        integer(ns, 1)
        agreement = self.request.value['active_recovery_agreement']
        need(wall >= self.last_wall and ns >= self.last_ns and
             agreement['not_before'] <= wall < agreement['expires_at'] and
             self.anchor['not_before_ns'] <= ns < self.anchor['expires_ns'], 'RECOVERY_EXPIRED')
        self.last_wall, self.last_ns = wall, ns

    def observe(self, ports):
        actual = _lifecycle_clock(self.request, ports, self.clock)
        need(actual.boot_id == self.anchor['initial']['boot_id'] and
             actual.observed_at >= self.anchor['armed_wall'] and
             actual.boottime_ns >= self.anchor['initial']['boottime_ns'], 'RECOVERY_EXPIRED')
        local_ns = self.boottime_ns()
        integer(local_ns, 1)
        need(0 <= local_ns - actual.boottime_ns <= 30 * 10**9, 'LIFECYCLE_CLOCK')
        self.local()


def _boundary(request, ports, journal, clock, *, budget=None, recovery=False, cleanup=False):
    request.check()
    if budget is not None:
        budget.forward()
        need(ports.guard_forward(request) is None, 'FORWARD_GUARD')
        budget.forward()
    elif recovery:
        recovery.observe(ports)
        need(ports.guard_recovery(request) is None, 'RECOVERY_GUARD')
        recovery.observe(ports)
    _native(request, ports, clock)
    original = _retained_armed(request, journal)
    actual = _armed(request, ports.verify_cleanup(request, original), int(clock()), original=original,
                    cleanup=bool(cleanup or recovery))
    if budget is not None:
        budget.forward()
    elif recovery:
        recovery.local()
    return actual


def _effect(request, ports, journal, clock, role, action, before, *, budget=None, recovery=False, cleanup=False,
            drain=None):
    _boundary(request, ports, journal, clock, budget=budget, recovery=recovery, cleanup=cleanup)
    need(journal.read(role) is None, 'EFFECT_ALREADY_INTENDED')
    journal.retain(role, encoded(dict(version=1, request_sha256=request.digest, role=role, before=before.binding())))
    _boundary(request, ports, journal, clock, budget=budget, recovery=recovery, cleanup=cleanup)
    need(_snapshot(request, ports, clock).binding() == before.binding(), 'EFFECT_BEFORE_CHANGED')
    armed = _boundary(request, ports, journal, clock, budget=budget, recovery=recovery, cleanup=cleanup)
    need(_snapshot(request, ports, clock).binding() == before.binding(), 'EFFECT_BEFORE_CHANGED')
    if budget is not None:
        budget.forward()
    elif recovery:
        recovery.local()
    if drain is not None:
        _drain_value(request, before, armed, drain, int(clock()))
    if budget is not None:
        budget.forward()
    elif recovery:
        recovery.local()
    need(action() is None, 'EFFECT_ACK_UNKNOWN')
    _boundary(request, ports, journal, clock, budget=budget, recovery=recovery, cleanup=cleanup)


def _drain(request, ports, snapshot, armed, clock):
    value = ports.drain(request, snapshot)
    return _drain_value(request, snapshot, armed, value, int(clock()))


def _drain_value(request, snapshot, armed, value, now):
    need(snapshot.state == 'STOPPED' and snapshot.cgroup_empty and snapshot.pid == snapshot.control_pid == 0, 'DRAIN_PROCESS')
    need(type(value) is DrainProof, 'DRAIN_READBACK')
    integer(value.observed_at)
    need(0 <= now - value.observed_at <= 30 and value.request_sha256 == request.digest and
         value.snapshot_sha256 == sha(encoded(snapshot.binding())), 'DRAIN_BINDING')
    exact(value.database, ('identity_sha256', 'snapshot_sha256', 'native_enabled', 'legacy_backends',
        'live_tasks', 'active_outbox', 'worker_leases', 'probe_leases'))
    exact(value.provider, ('evidence_sha256', 'inflight', 'unknown'))
    exact(value.leases, ('evidence_sha256', 'owner_pid', 'owner_start_ticks', 'exclusive', 'active_consumers'))
    for digest_value in (value.database['identity_sha256'], value.database['snapshot_sha256'],
                         value.provider['evidence_sha256'], value.leases['evidence_sha256']):
        digest(digest_value)
    need(value.database['snapshot_sha256'] == value.snapshot_sha256 and
         value.database['native_enabled'] is False and value.leases['exclusive'] is True and
         type(value.leases['owner_pid']) is int and value.leases['owner_pid'] == armed.pid and
         type(value.leases['owner_start_ticks']) is int and value.leases['owner_start_ticks'] == armed.start_ticks, 'DRAIN_OWNERSHIP')
    rows = [value.database[key] for key in ('legacy_backends', 'live_tasks', 'active_outbox', 'worker_leases', 'probe_leases')]
    rows += [value.provider['inflight'], value.provider['unknown'], value.leases['active_consumers']]
    need(all(type(row) is list and row == [] for row in rows), 'DRAIN_NOT_EMPTY')
    return value


def _hold(value):
    return value.admission == 'HOLD' and not value.needs_reload and value.drop_bytes == drop_bytes(value.release, 'HOLD')


def _record_unknown(request, journal, phase):
    try:
        if journal.read('unknown') is None:
            journal.retain('unknown', encoded(dict(version=1, request_sha256=request.digest, phase=phase)))
    except Exception:
        pass  # Never remove or rewrite uncertain records.


def _journal_snapshot(request, value):
    snapshot = Snapshot.from_binding(value).validate(0, fresh=False)
    original = request.before
    for key in ('hostname', 'unit', 'legacy_source', 'legacy_bundle_sha256', 'release',
                'immutable_config_sha256', 'hardening_sha256'):
        need(getattr(snapshot, key) == getattr(original, key), 'JOURNAL_CONFIGURATION')
    return snapshot


def _stopped_after(before, after):
    need(after.state == 'STOPPED' and after.cgroup_empty and
         after.invocation_id in ('', before.invocation_id), 'JOURNAL_STOP')
    left, right = before.binding(), after.binding()
    for key in ('state', 'pid', 'start_ticks', 'cgroup_empty', 'invocation_id'):
        left.pop(key)
        right.pop(key)
    need(left == right, 'JOURNAL_STOP')


def _configured_after(before, after, admission, *, reloaded):
    need(before.state == after.state == 'STOPPED' and
         after.drop_bytes == drop_bytes(after.release, admission), 'JOURNAL_CONFIG')
    left, right = before.binding(), after.binding()
    for key in ('drop_base64', 'needs_reload') + (('admission',) if reloaded else ()):
        left.pop(key)
        right.pop(key)
    need(left == right and (not reloaded or after.admission == admission and not after.needs_reload),
         'JOURNAL_CONFIG')


def _started_after(request, before, after, admission):
    need(before.state == 'STOPPED' and after.state == 'RUNNING' and after.admission == admission and
         after.invocation_id != request.before.invocation_id, 'JOURNAL_START')
    left, right = before.binding(), after.binding()
    for key in ('state', 'pid', 'start_ticks', 'cgroup_empty', 'invocation_id'):
        left.pop(key)
        right.pop(key)
    need(left == right, 'JOURNAL_START')


def _recovery_lineage(request, journal, *, active_readback=False):
    """Finite lifecycle receipts only; partial records are never ACTIVE authority."""
    need(journal.read('request') == request.raw, 'JOURNAL_REQUEST')
    rows = {role: parse(raw) for role in ROLES - {'request'}
            if (raw := journal.read(role)) is not None}
    need(type(rows.get('intent')) is dict and type(rows['intent'].get('version')) is int and
         rows['intent'] == dict(version=1, request_sha256=request.digest, kind='TRANSITION_INTENT'),
         'JOURNAL_INTENT')
    _retained_armed(request, journal)
    need('recovery-anchor' in rows, 'RECOVERY_ANCHOR')
    need(not any(role in rows for role in ('cleanup-stop-intent', 'cleanup-drop-intent', 'cleanup-reload-intent')),
         'RECOVERY_AFTER_CONTAINMENT')

    def effect(role, before, *, required=False):
        row = rows.get(role)
        need(not required or row is not None, 'JOURNAL_MISSING')
        if row is None:
            return False
        exact(row, ('version', 'request_sha256', 'role', 'before'))
        need(type(row['version']) is int and row['version'] == 1 and row['request_sha256'] == request.digest and
             row['role'] == role and _journal_snapshot(request, row['before']).binding() == before.binding(),
             'JOURNAL_EFFECT')
        return True

    def observed(role):
        need(role in rows, 'JOURNAL_ACK_MISSING')
        return _journal_snapshot(request, rows[role])

    effect('stop-intent', request.before, required=True)
    current = observed('stop-observed')
    _stopped_after(request.before, current)
    if effect('hold-drop-intent', current):
        dropped = observed('hold-dropped')
        _configured_after(current, dropped, 'HOLD', reloaded=False)
        current = dropped
    else:
        need(not any(key in rows for key in ('hold-dropped', 'reload-intent', 'hold-configured', 'hold-start-intent', 'hold-started')),
             'JOURNAL_ORDER')
    if effect('reload-intent', current):
        configured = observed('hold-configured')
        _configured_after(current, configured, 'HOLD', reloaded=True)
        current = configured
    else:
        need(not any(key in rows for key in ('hold-configured', 'hold-start-intent', 'hold-started')), 'JOURNAL_ORDER')
    if effect('hold-start-intent', current):
        need(request.value['start_hold'] and _hold(current), 'JOURNAL_ORDER')
        started = observed('hold-started')
        _started_after(request, current, started, 'HOLD')
        current = started
    else:
        need('hold-started' not in rows, 'JOURNAL_ORDER')
    if 'completed' in rows:
        expected = dict(version=1, request_sha256=request.digest,
            state='COMPLETED_RUNNING_HOLD' if current.state == 'RUNNING' else 'COMPLETED_STOPPED_HOLD',
            snapshot_sha256=sha(encoded(current.binding())))
        need(_hold(current) and type(rows['completed']) is dict and
             type(rows['completed'].get('version')) is int and rows['completed'] == expected, 'JOURNAL_COMPLETION')
    if effect('recovery-stop-intent', current):
        need(current.state == 'RUNNING' and _hold(current), 'JOURNAL_ORDER')
        stopped = observed('recovery-stop-observed')
        _stopped_after(current, stopped)
        current = stopped
    else:
        need('recovery-stop-observed' not in rows, 'JOURNAL_ORDER')
    if effect('active-drop-intent', current):
        dropped = observed('active-dropped')
        _configured_after(current, dropped, 'ACTIVE', reloaded=False)
        current = dropped
    else:
        need('active-dropped' not in rows, 'JOURNAL_ORDER')
    if effect('active-reload-intent', current):
        configured = observed('active-configured')
        _configured_after(current, configured, 'ACTIVE', reloaded=True)
        current = configured
    else:
        need('active-configured' not in rows, 'JOURNAL_ORDER')
    pending_active = False
    if effect('active-start-intent', current):
        need(current.admission == 'ACTIVE' and current.state == 'STOPPED' and not current.needs_reload, 'JOURNAL_ORDER')
        if 'active-started' in rows:
            started = observed('active-started')
            _started_after(request, current, started, 'ACTIVE')
            current = started
        else:
            need(active_readback, 'JOURNAL_ACK_MISSING')
            pending_active = True
    else:
        need('active-started' not in rows, 'JOURNAL_ORDER')
    if 'unknown' in rows:
        exact(rows['unknown'], ('version', 'request_sha256', 'phase'))
        need(type(rows['unknown']['version']) is int and rows['unknown']['version'] == 1 and
             rows['unknown']['request_sha256'] == request.digest and
             rows['unknown']['phase'] in ('PREFLIGHT', 'ARM', 'STOP', 'DRAIN', 'DROP', 'RELOAD', 'START_HOLD'), 'JOURNAL_UNKNOWN')
    return current, pending_active


def _finish(request, ports, journal, clock, expected, *, budget=None, recovery=False, cleanup=False):
    # Callbacks can cross an actual lifecycle change. Never publish the snapshot
    # obtained before those callbacks as the resulting current state.
    for _ in range(2):
        _boundary(request, ports, journal, clock, budget=budget, recovery=recovery, cleanup=cleanup)
        current = _snapshot(request, ports, clock)
        need(current.binding() == expected.binding(), 'FINAL_STATE_CHANGED')
    if budget is not None:
        budget.forward()
    elif recovery:
        recovery.local()
    return current


def run_transition(request, *, ports, journal, clock=time.time, monotonic=time.monotonic,
                   boottime_ns=_boottime_ns):
    """Forward once. Trusted ports have fixed targets and expose no command API."""
    phase = 'PREFLIGHT'
    try:
        request.check()
        need(all(journal.read(role) is None for role in ROLES), 'EXISTING_INTENT')
        budget = _Budget(request, clock, monotonic)
        budget.forward()
        need(ports.guard_forward(request) is None, 'FORWARD_GUARD')
        budget.forward()
        _native(request, ports, clock)
        before = _snapshot(request, ports, clock)
        need(before.binding() == request.before.binding(), 'BEFORE_CHANGED')
        budget.forward()
        journal.retain('request', request.raw)
        journal.retain('intent', encoded(dict(version=1, request_sha256=request.digest, kind='TRANSITION_INTENT')))
        phase = 'ARM'
        _native(request, ports, clock)
        armed = _armed(request, ports.arm_cleanup(request), int(clock()))
        journal.retain('armed', encoded(armed.wire()))
        if request.value['active_recovery_agreement'] is not None:
            clock_witness = _lifecycle_clock(request, ports, clock)
            need(0 <= boottime_ns() - clock_witness.boottime_ns <= 30 * 10**9, 'LIFECYCLE_CLOCK')
            journal.retain('recovery-anchor', encoded(_anchor_value(request, clock_witness, int(clock()))))
        _boundary(request, ports, journal, clock, budget=budget)
        phase = 'STOP'
        _effect(request, ports, journal, clock, 'stop-intent', lambda: ports.stop_expected(request, before), before, budget=budget)
        stopped = _snapshot(request, ports, clock)
        need(stopped.state == 'STOPPED' and stopped.cgroup_empty, 'STOP_NOT_OBSERVED')
        journal.retain('stop-observed', encoded(stopped.binding()))
        phase = 'DRAIN'
        armed = _boundary(request, ports, journal, clock, budget=budget)
        drain = _drain(request, ports, stopped, armed, clock)
        phase = 'DROP'
        _effect(request, ports, journal, clock, 'hold-drop-intent',
            lambda: ports.replace_drop_cas(request, stopped.drop_bytes, drop_bytes(stopped.release, 'HOLD')), stopped, budget=budget, drain=drain)
        current = _snapshot(request, ports, clock)
        need(current.state == 'STOPPED' and current.drop_bytes == drop_bytes(current.release, 'HOLD'), 'DROP_NOT_OBSERVED')
        journal.retain('hold-dropped', encoded(current.binding()))
        phase = 'RELOAD'
        _effect(request, ports, journal, clock, 'reload-intent', lambda: ports.reload_expected(request), current, budget=budget)
        current = _snapshot(request, ports, clock)
        need(current.state == 'STOPPED' and _hold(current), 'HOLD_NOT_OBSERVED')
        journal.retain('hold-configured', encoded(current.binding()))
        if request.value['start_hold']:
            phase = 'START_HOLD'
            armed = _boundary(request, ports, journal, clock, budget=budget)
            drain = _drain(request, ports, current, armed, clock)
            _effect(request, ports, journal, clock, 'hold-start-intent', lambda: ports.start_once(request, admission='HOLD'), current, budget=budget, drain=drain)
            current = _snapshot(request, ports, clock)
            need(current.state == 'RUNNING' and _hold(current) and current.invocation_id != before.invocation_id, 'START_HOLD_NOT_OBSERVED')
            journal.retain('hold-started', encoded(current.binding()))
        current = _finish(request, ports, journal, clock, current, budget=budget)
        state = 'COMPLETED_RUNNING_HOLD' if current.state == 'RUNNING' else 'COMPLETED_STOPPED_HOLD'
        journal.retain('completed', encoded(dict(version=1, request_sha256=request.digest, state=state,
                                                snapshot_sha256=sha(encoded(current.binding())))))
        return Outcome(state, request.digest, safe_state=current.state + '_HOLD')
    except Exception:
        try:
            intended = journal.read('stop-intent') is not None
            _record_unknown(request, journal, phase)
        except Exception:
            intended = True  # An unreadable/partial journal never proves absence.
        return Outcome('UNKNOWN' if intended else 'REFUSED_BEFORE_EFFECT', request.digest, unknown=True)


def _initial_cleanup(request, ports, journal, clock):
    request.check()
    current = _snapshot(request, ports, clock)
    _native(request, ports, clock)
    if journal.read('stop-intent') is None:
        # A request, failed preflight or armed supervisor is not a stop intent.
        current = _snapshot(request, ports, clock)
        need(current.binding() == request.before.binding(), 'UNOWNED_BEFORE_EFFECT')
        return current, Outcome('ORIGINAL_ACTIVE_UNTOUCHED', request.digest, safe_state='RUNNING_ACTIVE')
    need(journal.read('request') == request.raw, 'JOURNAL_REQUEST')
    _boundary(request, ports, journal, clock, cleanup=True)
    return current, None


def _known_started(journal, role, current):
    raw = journal.read(role)
    return raw is not None and parse(raw) == current.binding()


def contain_transition(request, *, ports, journal, clock=time.time):
    """Narrow safe-HOLD cleanup after expiry. No forward effect is retried."""
    try:
        current, untouched = _initial_cleanup(request, ports, journal, clock)
        if untouched is not None:
            return untouched
        if current.state == 'RUNNING' and _hold(current):
            _finish(request, ports, journal, clock, current, cleanup=True)
            return Outcome('SAFE_RUNNING_HOLD', request.digest, safe_state='RUNNING_HOLD')
        if current.state == 'RUNNING':
            # Only an acknowledged operation-owned ACTIVE start may be stopped.
            # A lost initial stop/start ACK or replacement never permits a retry.
            need(_known_started(journal, 'active-started', current), 'UNOWNED_OR_UNCERTAIN_PROCESS')
            _effect(request, ports, journal, clock, 'cleanup-stop-intent',
                    lambda: ports.stop_expected(request, current), current, cleanup=True)
            current = _snapshot(request, ports, clock)
        need(current.state == 'STOPPED' and current.cgroup_empty, 'ABSENCE')
        if current.drop_bytes != drop_bytes(current.release, 'HOLD'):
            need(journal.read('hold-drop-intent') is None or journal.read('active-drop-intent') is not None,
                 'DROP_ACK_UNKNOWN_NO_RETRY')
            _effect(request, ports, journal, clock, 'cleanup-drop-intent',
                lambda: ports.replace_drop_cas(request, current.drop_bytes, drop_bytes(current.release, 'HOLD')), current, cleanup=True)
            current = _snapshot(request, ports, clock)
        if not _hold(current):
            need((journal.read('reload-intent') is None or journal.read('active-drop-intent') is not None) and
                 journal.read('cleanup-reload-intent') is None,
                 'RELOAD_ACK_UNKNOWN_NO_RETRY')
            _effect(request, ports, journal, clock, 'cleanup-reload-intent', lambda: ports.reload_expected(request), current, cleanup=True)
            current = _snapshot(request, ports, clock)
        need(current.state == 'STOPPED' and _hold(current), 'SAFE_HOLD_NOT_OBSERVED')
        _finish(request, ports, journal, clock, current, cleanup=True)
        return Outcome('SAFE_STOPPED_HOLD', request.digest, safe_state='STOPPED_HOLD')
    except Exception:
        return Outcome('UNKNOWN', request.digest, unknown=True)


def rollback_transition(request, *, ports, journal, clock=time.time, boottime_ns=_boottime_ns):
    """Availability recovery only under its separately accepted finite scope."""
    try:
        current, untouched = _initial_cleanup(request, ports, journal, clock)
        if untouched is not None:
            return untouched
        tail, pending_active = _recovery_lineage(request, journal,
            active_readback=current.state == 'RUNNING' and current.admission == 'ACTIVE')
        if not pending_active:
            need(current.binding() == tail.binding(), 'RECOVERY_TAIL_CHANGED')
        recovery = _RecoveryBudget(request, journal, clock, boottime_ns)
        _boundary(request, ports, journal, clock, recovery=recovery)
        if current.state == 'RUNNING' and current.admission == 'ACTIVE':
            need(journal.read('active-start-intent') is not None and
                 current.drop_bytes == drop_bytes(current.release, 'ACTIVE'), 'ACTIVE_NOT_OWNED')
            _finish(request, ports, journal, clock, current, recovery=recovery)
            return Outcome('ACTIVE_OBSERVED', request.digest, unknown=not _known_started(journal, 'active-started', current),
                           safe_state='RUNNING_ACTIVE')  # Readback only; never start again.
        if current.state == 'RUNNING':
            need(_hold(current) and _known_started(journal, 'hold-started', current), 'UNOWNED_HOLD')
            _effect(request, ports, journal, clock, 'recovery-stop-intent',
                    lambda: ports.stop_expected(request, current), current, recovery=recovery)
            current = _snapshot(request, ports, clock)
            need(current.state == 'STOPPED', 'RECOVERY_STOP_NOT_OBSERVED')
            journal.retain('recovery-stop-observed', encoded(current.binding()))
        need(current.state == 'STOPPED' and current.cgroup_empty, 'RECOVERY_ABSENCE')
        armed = _boundary(request, ports, journal, clock, recovery=recovery)
        drain = _drain(request, ports, current, armed, clock)
        if current.drop_bytes != drop_bytes(current.release, 'ACTIVE'):
            _effect(request, ports, journal, clock, 'active-drop-intent',
                lambda: ports.replace_drop_cas(request, current.drop_bytes, drop_bytes(current.release, 'ACTIVE')), current, recovery=recovery, drain=drain)
            current = _snapshot(request, ports, clock)
            journal.retain('active-dropped', encoded(current.binding()))
        if current.admission != 'ACTIVE' or current.needs_reload:
            _effect(request, ports, journal, clock, 'active-reload-intent', lambda: ports.reload_expected(request), current, recovery=recovery)
            current = _snapshot(request, ports, clock)
            journal.retain('active-configured', encoded(current.binding()))
        need(current.state == 'STOPPED' and current.admission == 'ACTIVE' and not current.needs_reload, 'ACTIVE_CONFIG_NOT_OBSERVED')
        armed = _boundary(request, ports, journal, clock, recovery=recovery)
        drain = _drain(request, ports, current, armed, clock)
        _effect(request, ports, journal, clock, 'active-start-intent', lambda: ports.start_once(request, admission='ACTIVE'), current, recovery=recovery, drain=drain)
        current = _snapshot(request, ports, clock)
        need(current.state == 'RUNNING' and current.admission == 'ACTIVE' and
             current.invocation_id != request.before.invocation_id, 'ACTIVE_START_NOT_OBSERVED')
        journal.retain('active-started', encoded(current.binding()))
        _finish(request, ports, journal, clock, current, recovery=recovery)
        return Outcome('ACTIVE_RESTORED', request.digest, safe_state='RUNNING_ACTIVE')
    except Exception:
        contained = contain_transition(request, ports=ports, journal=journal, clock=clock)
        return Outcome('UNKNOWN', request.digest, unknown=True, safe_state=contained.safe_state)
