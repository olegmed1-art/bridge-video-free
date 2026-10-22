"""Bounded transport ABI; no credentials, owner authentication or activation.

Authority/assignment is supplied by the separate RC2 owner. A Job value is
data, NEVER evidence that its creator is authenticated. Production I/O uses
FdBytes with a fresh owner guard; BytesIO is only an explicit test double.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
import re
import select
import socket
import stat
import struct
import time
from uuid import UUID

SOURCE = 'e5a600d8d532a4f10adef191a9bfdfbb962854429849cd55821266ad71d6868f'
COMPONENT = '26843f04d258834d497affd0a340cef1ef1f657c2d2852c0d26ffea2ea11e626'
RECOGNIZER_JOB = '78ba90f069aeaf4759b9c1a67942cc95'
RUNTIME = dict(version='diana20-installed-margin-worker-r1', margin_enabled=True,
    geometry_enabled=False, factor_identity_fix_enabled=False,
    worker_path='/opt/bridge-school/universal-video/code-bundles/diana20-margin-installed-20261020-r1/geometry/diana20_geometry_worker.py',
    worker_sha256='d907fc1fbbe2b1ee3f061b81b4fd60180d58ee14b1478c94ea53bfb9a674e56a')
CLAIM = ('job_id', 'batch_id', 'lease_token', 'attempt_count', 'source_file_id')
MAX_FRAME = 16 * 1024**2
MAX_IMAGES = 128 * 1024**2
MAX_OBSERVATIONS = 32 * 1024**2
MAX_FRAMES = 232
MAX_TICKET = 65536
OPEN, FRAME, RUN, OBSERVATION, RESULT, SEAL, ACK, DONE = range(1, 9)
CONTROL_CHALLENGE, CONTROL_REPLY, CONTROL_CHECK, CONTROL_CHECKED, CONTROL_TERMINAL_ACK, CONTROL_ACKED, CONTROL_CLOSE, CONTROL_CLOSED, CONTROL_READY = range(9, 18)
PRECLAIM, INTENT_READY, BIND_JOB, JOB_READY = range(18, 22)
CAPS = {OPEN: MAX_TICKET + 32, FRAME: MAX_FRAME + 65540, RUN: 0,
        OBSERVATION: MAX_FRAME, RESULT: 8 * 1024**2, SEAL: 65536,
        ACK: 0, DONE: 65536}
CAPS.update({kind: 65536 for kind in range(CONTROL_CHALLENGE, CONTROL_READY + 1)})
CAPS.update({PRECLAIM: 8192, INTENT_READY: 4096, BIND_JOB: 65536, JOB_READY: 4096})
_SHA = re.compile('[a-f0-9]{64}\\Z')


class TransportError(RuntimeError):
    """Public messages contain fixed codes only, never tickets/claims/argv."""


def need(ok, code):
    if not ok:
        raise TransportError('DIANA20_TRANSPORT_' + code) from None


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        need(False, 'JSON')


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_KEY')
            result[key] = value
        return result
    def finite_float(value):
        number = float(value)
        need(math.isfinite(number), 'JSON_CONSTANT')
        return number
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_float=finite_float,
                          parse_constant=lambda _: need(False, 'JSON_CONSTANT'))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        need(False, 'JSON')


def pin(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def exact_claim(value):
    need(type(value) is dict and set(value) == set(CLAIM), 'CLAIM_FIELDS')
    try:
        for key in CLAIM[:3]:
            need(type(value[key]) is str and str(UUID(value[key])) == value[key], 'CLAIM_UUID')
    except (ValueError, TypeError, AttributeError):
        need(False, 'CLAIM_UUID')
    need(type(value['attempt_count']) is int and 1 <= value['attempt_count'] <= 3,
         'CLAIM_ATTEMPT')
    need(type(value['source_file_id']) is str and
         re.fullmatch('[A-Za-z0-9_-]{10,200}', value['source_file_id']), 'CLAIM_SOURCE')
    return value


@dataclass(frozen=True)
class Intent:
    """V3 pre-claim INPUT. No lease, credential, enrollment or authority."""
    raw: bytes

    def __post_init__(self):
        need(type(self.raw) is bytes and 0 < len(self.raw) <= 4096, 'INTENT_SIZE')
        value = parse(self.raw)
        pins = ('source_fingerprint', 'target_identity_sha256', 'source_sha256',
                'unit_sha256', 'binding_sha256', 'generation_sha256', 'manifest_sha256')
        fields = set(pins) | {'schema', 'batch_id', 'job_id', 'admission_id',
            'source_file_id', 'attempt_count', 'worker_key', 'worker_login',
            'processing_profile', 'algorithm_revision', 'policy_id', 'policy_epoch'}
        need(type(value) is dict and set(value) == fields
             and value['schema'] == 'diana20-preclaim/v1', 'INTENT_FIELDS_NO_LEASE')
        # Use existing claim validators without introducing a placeholder lease.
        for key in ('batch_id', 'job_id', 'admission_id'):
            try:
                need(type(value[key]) is str and str(UUID(value[key])) == value[key], 'INTENT_UUID')
            except (ValueError, TypeError, AttributeError):
                need(False, 'INTENT_UUID')
        need(type(value['attempt_count']) is int and 1 <= value['attempt_count'] <= 3,
             'INTENT_ATTEMPT')
        need(type(value['source_file_id']) is str and
             re.fullmatch('[A-Za-z0-9_-]{10,200}', value['source_file_id']), 'INTENT_SOURCE')
        need(all(pin(value[k]) for k in pins), 'INTENT_PINS')
        need(all(type(value[k]) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', value[k])
                 for k in ('worker_key', 'worker_login', 'processing_profile', 'algorithm_revision', 'policy_id')),
             'INTENT_NAMES')
        need(type(value['policy_epoch']) is int and 0 < value['policy_epoch'] <= 2**53,
             'INTENT_EPOCH')
        need(canonical(value) == self.raw, 'INTENT_ENCODING')

    @property
    def value(self):
        return parse(self.raw)

    @property
    def sha256(self):
        return digest(self.raw)


class PreclaimHandshake:
    """Same-channel v3 codecs/state, NOT an owner or runnable grant.

    DATA_BOUND means only that detached inputs agree. The existing broker
    refuses PRECLAIM until a real pinned UID1001 execution adapter exists.
    A public constructor/receipt cannot turn this into runtime authority.
    Every failed transition consumes this object; no downgrade or retry.
    """
    def __init__(self, intent, policy_sha256):
        need(type(intent) is Intent and pin(policy_sha256), 'PRECLAIM_INPUT')
        self.intent, self.policy_sha256 = intent, policy_sha256
        self.state, self.scope_nonce = 'NEW', None

    def opening(self):
        try:
            need(self.state == 'NEW', 'PRECLAIM_REPLAY')
            self.state = 'SENT'
            return canonical(dict(version=3, intent=self.intent.value,
                                  intent_sha256=self.intent.sha256, policy_sha256=self.policy_sha256))
        except BaseException:
            self.state = 'UNKNOWN'
            raise

    def accept_ready(self, raw):
        try:
            need(self.state == 'SENT' and type(raw) is bytes and len(raw) <= CAPS[INTENT_READY],
                 'PRECLAIM_PHASE')
            value = parse(raw)
            need(type(value) is dict and set(value) == {'version', 'intent_sha256', 'scope_nonce'}
                 and type(value['version']) is int and value['version'] == 3
                 and value['intent_sha256'] == self.intent.sha256 and pin(value['scope_nonce'])
                 and canonical(value) == raw, 'INTENT_READY')
            self.scope_nonce, self.state = value['scope_nonce'], 'INTENT_READY'
        except BaseException:
            self.state = 'UNKNOWN'
            raise

    def bind_data(self, *, job, claim, commit_evidence_raw, assignment, enrollment_raw):
        """Check post-COMMIT data; independent live DB/owner checks still required."""
        from .receipt_encoding import canonical_bytes
        from .terminal_finish_contract import Assignment
        try:
            need(self.state == 'INTENT_READY' and type(job) is Job and type(assignment) is Assignment,
                 'BIND_PHASE')
            full_raw = canonical_bytes(dict(claim), max_bytes=16384)
            need(type(commit_evidence_raw) is bytes and len(commit_evidence_raw) <= 4096,
                 'BIND_COMMIT_EVIDENCE')
            evidence = parse(commit_evidence_raw)
            need(type(evidence) is dict and set(evidence) ==
                 {'schema', 'state', 'claim_sha256', 'journal_sha256'}
                 and evidence['schema'] == 'claim-commit-evidence/v1'
                 and evidence['state'] == 'COMMIT_ACKNOWLEDGED'
                 and evidence['claim_sha256'] == digest(full_raw) and pin(evidence['journal_sha256'])
                 and canonical(evidence) == commit_evidence_raw, 'BIND_COMMIT_EVIDENCE')
            need(type(enrollment_raw) is bytes and 0 < len(enrollment_raw) <= 65536, 'BIND_ENROLLMENT')
            e = parse(enrollment_raw)
            need(type(e) is dict and set(e) == {'schema', 'intent_hash', 'policy_sha256',
                 'claim_sha256', 'assignment_text', 'assignment_sha256', 'selector_sha256',
                 'target_identity_sha256', 'principal', 'issued_at_utc', 'expires_at_utc',
                 'admission_record_sha256', 'admission_anchor_sha256'}
                 and canonical(e) == enrollment_raw, 'BIND_ENROLLMENT_FIELDS')
            i, a = self.intent.value, assignment.value
            assignment.check_claim(claim, i['worker_key'])
            need(all(claim.get(k) == i[k] and type(claim.get(k)) is type(i[k]) for k in
                     ('batch_id', 'job_id', 'source_file_id', 'attempt_count',
                      'processing_profile', 'algorithm_revision')), 'BIND_INTENT_CLAIM')
            need(canonical(exact_claim({k: claim[k] for k in CLAIM})) == job.claim_raw,
                 'BIND_JOB_CLAIM')
            need(all(a[k] == i[k] for k in ('admission_id', 'source_fingerprint', 'source_sha256',
                 'unit_sha256', 'binding_sha256', 'generation_sha256', 'manifest_sha256',
                 'policy_id', 'policy_epoch')), 'BIND_ASSIGNMENT_INTENT')
            need(job.source_sha256 == a['source_sha256'] and job.context_sha256 == a['context_sha256']
                 and job.request_sha256 == a['request_sha256'] and job.generation_sha256 == a['generation_sha256']
                 and job.manifest_sha256 == a['manifest_sha256'] and job.policy_id == a['policy_id']
                 and job.policy_epoch == a['policy_epoch'], 'BIND_JOB_ASSIGNMENT')
            from datetime import datetime
            def timestamp(value):
                need(type(value) is str, 'BIND_ENROLLMENT_DATE')
                try:
                    parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ')
                except (ValueError, OverflowError):
                    need(False, 'BIND_ENROLLMENT_DATE')
                need(parsed.strftime('%Y-%m-%dT%H:%M:%S.%fZ') == value, 'BIND_ENROLLMENT_DATE')
                return parsed
            lifetime = (timestamp(e['expires_at_utc']) - timestamp(e['issued_at_utc'])).total_seconds()
            need(e['schema'] == 'enrolled-assignment/v1' and e['intent_hash'] == self.intent.sha256
                 and e['policy_sha256'] == self.policy_sha256 and e['claim_sha256'] == digest(full_raw)
                 and e['assignment_text'] == assignment.raw.decode('utf-8')
                 and e['assignment_sha256'] == assignment.sha256
                 and e['selector_sha256'] == assignment.selector_sha256
                 and e['target_identity_sha256'] == i['target_identity_sha256']
                 and e['principal'] == i['worker_login'] and 0 < lifetime <= 3600
                 and pin(e['admission_record_sha256']) and pin(e['admission_anchor_sha256']),
                 'BIND_ENROLLMENT_BINDING')
            packet = canonical(dict(version=3, scope_nonce=self.scope_nonce,
                intent_hash=self.intent.sha256, job_sha256=job.sha256,
                assignment_sha256=assignment.sha256, enrollment_sha256=digest(enrollment_raw)))
            self.state = 'DATA_BOUND'
            return packet
        except BaseException:
            self.state = 'UNKNOWN'
            raise


@dataclass(frozen=True)
class Frame:
    id: str
    sha256: str
    timestamp_ms: int
    size: int

    def __post_init__(self):
        need(type(self.id) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}', self.id), 'FRAME_ID')
        need(pin(self.sha256), 'FRAME_HASH')
        need(type(self.timestamp_ms) is int and 0 <= self.timestamp_ms <= 2**53, 'PTS')
        need(type(self.size) is int and 0 < self.size <= MAX_FRAME, 'FRAME_SIZE')


@dataclass(frozen=True)
class Job:
    """Immutable RC2-owner assignment snapshot, not an authentication token."""
    claim_raw: bytes
    context_sha256: str
    request_sha256: str
    generation_sha256: str
    policy_id: str
    policy_epoch: int
    frames: tuple[Frame, ...]
    source_sha256: str = SOURCE

    def __post_init__(self):
        need(type(self.claim_raw) is bytes and len(self.claim_raw) <= 4096, 'CLAIM_SIZE')
        need(canonical(exact_claim(parse(self.claim_raw))) == self.claim_raw, 'CLAIM_ENCODING')
        need(all(pin(v) for v in (self.context_sha256, self.request_sha256,
                                 self.generation_sha256)), 'JOB_PINS')
        need(self.source_sha256 == SOURCE, 'SOURCE_ALLOWLIST')
        need(type(self.policy_id) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}', self.policy_id), 'POLICY')
        need(type(self.policy_epoch) is int and 1 <= self.policy_epoch <= 2**53, 'EPOCH')
        need(type(self.frames) is tuple and 1 <= len(self.frames) <= MAX_FRAMES
             and all(type(f) is Frame for f in self.frames), 'FRAME_COUNT')
        need(len({f.id for f in self.frames}) == len(self.frames), 'DUPLICATE_FRAME')
        need(sum(f.size for f in self.frames) <= MAX_IMAGES, 'IMAGE_TOTAL')
        need(all(a.timestamp_ms <= b.timestamp_ms for a, b in zip(self.frames, self.frames[1:])), 'PTS_ORDER')

    @property
    def claim(self):
        return parse(self.claim_raw)

    @property
    def manifest_sha256(self):
        return digest(canonical([asdict(f) for f in self.frames]))

    @property
    def sha256(self):
        return digest(canonical(dict(claim=self.claim, context=self.context_sha256,
            request=self.request_sha256, generation=self.generation_sha256,
            policy=self.policy_id, epoch=self.policy_epoch,
            source=self.source_sha256, manifest=self.manifest_sha256)))

    @property
    def replay_key(self):
        # No lease-token in filenames. A changed lease does not authorize rerun.
        return digest(canonical([self.policy_id, self.policy_epoch, SOURCE,
                                 self.generation_sha256, self.manifest_sha256]))


class Guard:
    def __init__(self, assert_live, *, lifetime=300, clock=time.monotonic):
        need(callable(assert_live) and type(lifetime) in (int, float)
             and math.isfinite(lifetime) and 0 < lifetime <= 300, 'GUARD_INPUT')
        self.assert_live, self.clock = assert_live, clock
        self.deadline = clock() + lifetime

    def __call__(self):
        need(self.clock() < self.deadline, 'EXPIRED')
        # RC2 owner does the authentication/expiry/revocation, not this transport.
        need(self.assert_live() is None, 'OWNER_GUARD')


class FdBytes:
    """Nonblocking existing descriptors; polling rechecks guard while stalled."""
    def __init__(self, read_fd, write_fd, guard):
        self.read_fd, self.write_fd, self.guard = read_fd, write_fd, guard
        for fd in set((read_fd, write_fd)):
            os.set_blocking(fd, False)

    def _wait(self, fd, writing):
        while True:
            self.guard()
            r, w, _ = select.select([] if writing else [fd], [fd] if writing else [], [], .1)
            if r or w:
                return

    def read(self, count):
        while True:
            self._wait(self.read_fd, False)
            try:
                return os.read(self.read_fd, min(count, 65536))
            except BlockingIOError:
                pass

    def write(self, raw):
        while True:
            self._wait(self.write_fd, True)
            try:
                return os.write(self.write_fd, raw[:65536])
            except BlockingIOError:
                pass


class Records:
    def __init__(self, stream, guard):
        self.stream, self.guard = stream, guard

    def _read(self, count):
        chunks = []
        while count:
            self.guard()
            raw = self.stream.read(min(count, 65536))
            need(type(raw) is bytes and 0 < len(raw) <= count, 'TRUNCATED')
            chunks.append(raw)
            count -= len(raw)
        return b''.join(chunks)

    def read(self, expected):
        kind, size = struct.unpack('!BI', self._read(5))
        need(kind in CAPS and size <= CAPS[kind], 'RECORD_LIMIT')
        need(kind == expected, 'RECORD_ORDER')
        return self._read(size)

    def read_any(self, *, allow_eof=False):
        if allow_eof:
            self.guard()
            first = self.stream.read(1)
            if first == b'':
                return None  # EOF exactly at a record boundary only
            need(type(first) is bytes and len(first) == 1, 'TRUNCATED')
            header = first + self._read(4)
        else:
            header = self._read(5)
        kind, size = struct.unpack('!BI', header)
        need(kind in CAPS and size <= CAPS[kind], 'RECORD_LIMIT')
        return kind, self._read(size)

    def write(self, kind, raw=b''):
        need(kind in CAPS and type(raw) is bytes and len(raw) <= CAPS[kind], 'RECORD_LIMIT')
        value = struct.pack('!BI', kind, len(raw)) + raw
        while value:
            self.guard()
            size = self.stream.write(value[:65536])
            need(type(size) is int and 0 < size <= min(len(value), 65536), 'WRITE')
            value = value[size:]

    def eof(self):
        self.guard()
        need(self.stream.read(1) == b'', 'TRAILING_BYTES')


def frame_bytes(spec, raw):
    need(type(spec) is Frame and type(raw) is bytes and len(raw) == spec.size
         and digest(raw) == spec.sha256, 'FRAME_CORRELATION')
    header = canonical(asdict(spec))
    return struct.pack('!I', len(header)) + header + raw


def unpack_frame(spec, raw):
    need(len(raw) >= 4, 'FRAME_HEADER')
    size, = struct.unpack('!I', raw[:4])
    need(0 < size <= 65536 and len(raw) >= size + 4, 'FRAME_HEADER')
    need(canonical(parse(raw[4:size+4])) == canonical(asdict(spec)), 'FRAME_CORRELATION')
    payload = raw[size+4:]
    frame_bytes(spec, payload)
    return payload


def observation(job, spec, raw):
    need(type(raw) is bytes and len(raw) <= MAX_FRAME and raw.endswith(b'\n')
         and raw.count(b'\n') == 1, 'OBSERVATION_LINE')
    row = parse(raw)
    need(type(row) is dict and set(row) == {'id', 'status', 'result'}
         and row['id'] == spec.id and row['status'] == 'OK', 'OBSERVATION_STATUS')
    value = row['result']
    need(type(value) is dict and value.get('source_video_sha256') == job.source_sha256
         and value.get('frame_sha256') == spec.sha256
         and type(value.get('source_timestamp_ms')) is int
         and value['source_timestamp_ms'] == spec.timestamp_ms, 'OBSERVATION_CORRELATION')
    update = value.get('recognizer_update')
    need(value.get('canonical_allowed') is False and value.get('complete_deal') is False
         and type(update) is dict and update.get('human_verified') is False, 'AUTHORITY_FLAGS')
    runtime = update.get('runtime')
    need(type(runtime) is dict and all(type(runtime.get(k)) is type(v) and runtime[k] == v
         for k, v in RUNTIME.items()), 'RUNTIME')
    return row


def provenance(job, raw):
    return dict(schema_version='diana20-sealed-observations/v1', claim=job.claim,
        recognizer_job_id=RECOGNIZER_JOB, source_sha256=SOURCE, component_sha256=COMPONENT,
        sha256=digest(raw), size_bytes=len(raw), frame_count=len(job.frames),
        result_mode='SHADOW_REVIEW_ONLY', canonical_promotion_allowed=False,
        database_persistence_allowed=False, publication_state='NOT_PUBLISHED',
        report_relationship='SUPPLEMENT_TO_COMPLETED_REPORT', master_pdf_recomputed=False)


def result_binding(job, result, raw):
    need(type(result) is dict and canonical(result.get('saved_frame_observations')) ==
         canonical(provenance(job, raw)),
         'TERMINAL_CORRELATION')


class LinuxPeer:
    """Kernel-derived identity of a connected pre-existing AF_UNIX socket.

    No socket is created. Same-UID hostile-process isolation is NOT claimed.
    Unit/cgroup is a gate in addition to the separate RC2 owner authority.
    """
    UNIT = 'school-autopilot-native-light.service'

    def __init__(self, connected, *, uid=996, gid=988, unit=None):
        need(hasattr(socket, 'AF_UNIX') and connected.family == socket.AF_UNIX
             and hasattr(socket, 'SO_PEERCRED'),
             'KERNEL_PEER_UNSUPPORTED')
        self.connected, self.uid, self.gid = connected, uid, gid
        self.unit = self.UNIT if unit is None else unit
        need(type(self.unit) is str and re.fullmatch('[A-Za-z0-9_-]+[.]service', self.unit), 'PEER_UNIT')
        self.pidfd = None
        try:
            self.pid, actual_uid, actual_gid = struct.unpack('3i', connected.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            need(self.pid > 0 and (actual_uid, actual_gid) == (uid, gid), 'KERNEL_PEER')
            # Atomic peer pidfd, not pidfd_open(numeric PID) after SO_PEERCRED:
            # the latter can bind a recycled PID before the initial proc read.
            # Linux UAPI SO_PEERPIDFD=77; older kernels fail closed (no fallback).
            fd = connected.getsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_PEERPIDFD', 77))
            need(type(fd) is int and fd >= 0, 'KERNEL_PEER_PIDFD')
            self.pidfd = fd
            os.set_inheritable(fd, False)
            self.start, self.cgroup = self._identity()
            self.check()
        except BaseException:
            self.close()
            raise

    def _identity(self):
        # starttime field22; the parenthesized comm may contain spaces or ')'.
        with open(f'/proc/{self.pid}/stat', encoding='ascii') as f:
            line = f.read(8193)
        need(len(line) <= 8192 and ') ' in line, 'PROC_STAT')
        fields = line.rsplit(') ', 1)[1].split()
        need(len(fields) >= 20, 'PROC_STAT')
        with open(f'/proc/{self.pid}/cgroup', encoding='ascii') as f:
            groups = f.read(8193)
        need(len(groups) <= 8192, 'CGROUP_SIZE')
        expected = '/system.slice/' + self.unit
        need(any(row.split(':', 2)[-1] == expected for row in groups.splitlines()), 'PEER_UNIT')
        return fields[19], groups

    def check(self):
        need(self.pidfd is not None and not select.select([self.pidfd], [], [], 0)[0], 'PEER_EXITED')
        need(struct.unpack('3i', self.connected.getsockopt(socket.SOL_SOCKET,
             socket.SO_PEERCRED, struct.calcsize('3i'))) == (self.pid, self.uid, self.gid), 'PEER_CHANGED')
        need(self._identity() == (self.start, self.cgroup), 'PEER_REUSED')

    def close(self):
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None


class DirReplay:
    """Create-only durable tombstones: uncertain dispatch is never retried.

    Receives an already approved private directory fd, opens no caller paths.
    Keep its reservation context open for the entire job (cross-process flock).
    """
    def __init__(self, directory_fd):
        self.fd = directory_fd
        s = os.fstat(directory_fd)
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid()
             and stat.S_IMODE(s.st_mode) == 0o700 and hasattr(os, 'O_NOFOLLOW'), 'REPLAY_DIRECTORY')

    def reserve(self, key):
        from contextlib import contextmanager
        @contextmanager
        def reservation():
            import fcntl
            need(pin(key), 'REPLAY_KEY')
            fd = os.open('active.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                         0o600, dir_fd=self.fd)
            try:
                s = os.fstat(fd)
                need(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                     and stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1, 'REPLAY_LOCK')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    need(False, 'BUSY')
                need(len(os.listdir(self.fd)) <= 16384, 'REPLAY_QUOTA')
                try:
                    marker = os.open(key + '.used', os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
                except FileExistsError:
                    need(False, 'REPLAY')
                try:
                    payload = b'RESERVED_NO_AUTO_RETRY\n'
                    need(os.write(marker, payload) == len(payload), 'REPLAY_WRITE')
                    os.fsync(marker)
                finally:
                    os.close(marker)
                os.fsync(self.fd)
                yield
            finally:
                os.close(fd)  # tombstone deliberately retained after EVERY outcome
        return reservation()
