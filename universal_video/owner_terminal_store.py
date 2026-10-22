"""Physical terminal CAS/ACK ledger, not issuance, authentication or a service.

SQLiteTerminalLedger is a portable persistent data engine. TerminalOwnerStore
adds the selected POSIX UID1001/file-identity boundary. Neither opens another
namespace, creates a DB/directory, changes access or discovers credentials.
An explicit initializer accepts only an already provisioned empty SQLite file.
Native installation and owner authorization are separate runtime prerequisites.

ACKED means durable owner acceptance, not successful wire delivery. The native
owner must call this inside its existing authorized ACK window. A crash or lost
reply is reconciled with read_ack; accepting twice never repeats deliver_once.
Whole-filesystem hostile rollback is not prevented without an independently
retained StoreWatermark; the constructor can enforce such a retained minimum.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading


class TerminalStoreError(RuntimeError):
    pass


def _need(value, code):
    if not value:
        raise TerminalStoreError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pin(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


def _parse(raw, limit):
    _need(type(raw) is bytes and 0 < len(raw) <= limit, 'TERMINAL_STORE_RAW_BOUND')
    try:
        value = json.loads(raw)
        _need(_canonical(value) == raw, 'TERMINAL_STORE_NONCANONICAL')
        return value
    except (ValueError, UnicodeError, TypeError):
        raise TerminalStoreError('TERMINAL_STORE_JSON') from None


@dataclass(frozen=True)
class StorageIdentity:
    file_device: int
    file_inode: int
    parent_device: int
    parent_inode: int

    @classmethod
    def observe(cls, path):
        """Metadata only; observing an identity does not approve the namespace."""
        p = Path(path)
        a, b = p.lstat(), p.parent.lstat()
        return cls(a.st_dev, a.st_ino, b.st_dev, b.st_ino)


@dataclass(frozen=True)
class StoreWatermark:
    sequence: int
    head_sha256: str


SCHEMA = ('CREATE TABLE terminal_events (seq INTEGER PRIMARY KEY, '
          'previous_sha TEXT NOT NULL, work_key TEXT NOT NULL, '
          'kind TEXT NOT NULL, raw BLOB NOT NULL, record_sha TEXT NOT NULL)')
ZERO = '0' * 64
MAX_EVENTS = 4096
MAX_PAYLOAD = 4 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
PHASES = ('ARMED', 'CLAIM_COMMITTED', 'BOUND', 'PREPARED', 'FINISH_ATTEMPTED',
          'READ_VERIFIED', 'ACK_ATTEMPTED', 'ACKED')
QUERY_FIELDS = ('work_key', 'intent_sha256', 'claim_sha256', 'selector',
    'assignment_sha256', 'receipt_sha256', 'receipt_size', 'ack_attempt_id',
    'enrollment_sha256', 'admission_record_sha256', 'admission_anchor_sha256',
    'target_identity_sha256', 'principal', 'transport_job_sha256',
    'stream_sha256', 'result_sha256')
HEAD_FIELDS = set(QUERY_FIELDS) | {'schema', 'seq', 'previous_sha256', 'phase', 'watermark'}


def _query(raw):
    value = _parse(raw, 4096)
    _need(type(value) is dict and set(value) == set(QUERY_FIELDS) | {'schema'}
          and value['schema'] == 'terminal-ack-query/v1', 'TERMINAL_STORE_QUERY')
    _need(all(_pin(value[k]) for k in QUERY_FIELDS if k not in ('principal', 'receipt_size'))
          and type(value['receipt_size']) is int and 0 < value['receipt_size'] <= 65536
          and type(value['principal']) is str
          and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', value['principal']),
          'TERMINAL_STORE_QUERY_BINDING')
    return value


def _head(raw, work_key):
    value = _parse(raw, 4096)
    _need(type(value) is dict and set(value) == HEAD_FIELDS
          and value['schema'] == 'terminal-queue-head/v1'
          and value['work_key'] == work_key and _pin(work_key)
          and value['phase'] in PHASES
          and type(value['seq']) is int and value['seq'] == PHASES.index(value['phase']) + 1,
          'TERMINAL_STORE_HEAD')
    index = PHASES.index(value['phase'])
    _need((value['previous_sha256'] is None if index == 0 else _pin(value['previous_sha256']))
          and _pin(value['intent_sha256']) and _pin(value['target_identity_sha256'])
          and type(value['principal']) is str
          and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', value['principal']),
          'TERMINAL_STORE_HEAD_BINDING')
    for key in QUERY_FIELDS:
        if key not in ('principal', 'receipt_size'):
            _need(value[key] is None or _pin(value[key]), 'TERMINAL_STORE_HEAD_PIN')
    _need(value['receipt_size'] is None or type(value['receipt_size']) is int
          and 0 < value['receipt_size'] <= 65536, 'TERMINAL_STORE_HEAD_SIZE')
    for after, fields in ((1, ('claim_sha256',)), (2, ('selector', 'assignment_sha256',
            'enrollment_sha256', 'admission_record_sha256', 'admission_anchor_sha256')),
            (3, ('watermark',)), (5, ('receipt_sha256', 'receipt_size',
            'transport_job_sha256', 'stream_sha256', 'result_sha256')),
            (6, ('ack_attempt_id',))):
        _need(all((value[k] is not None) if index >= after else (value[k] is None)
                  for k in fields), 'TERMINAL_STORE_HEAD_PHASE_FIELDS')
    if value['watermark'] is not None:
        _need(type(value['watermark']) is str and len(value['watermark']) <= 4096,
              'TERMINAL_STORE_WATERMARK_BOUND')
    return value


ENROLLMENT_KINDS = ('ENROLL_ATTEMPT', 'ENROLL_COMMIT', 'ENROLL_UNKNOWN')
ENROLLMENT_FIELDS = {'schema','work_key','scope_nonce_hash','intent_hash','policy_sha256',
    'claim_sha256','assignment_sha256','selector_sha256','target_identity_sha256','principal',
    'admission_record_sha256','admission_anchor_sha256'}
ENROLLMENT_RECEIPT_FIELDS = {'schema','intent_hash','policy_sha256','claim_sha256',
    'assignment_text','assignment_sha256','selector_sha256','target_identity_sha256',
    'principal','issued_at_utc','expires_at_utc','admission_record_sha256','admission_anchor_sha256'}


@dataclass(frozen=True)
class EnrollmentStatus:
    """Persistent DATA only. An ATTEMPT/UNKNOWN never authorizes retry."""
    state: str
    attempt_raw: bytes
    commit_raw: bytes | None = None


def _enrollment_attempt(raw):
    value = _parse(raw, 4096)
    _need(type(value) is dict and set(value) == ENROLLMENT_FIELDS
          and value['schema'] == 'owner-enrollment-attempt/v1'
          and all(_pin(value[k]) for k in ENROLLMENT_FIELDS - {'schema','principal'})
          and type(value['principal']) is str
          and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', value['principal']),
          'OWNER_ENROLLMENT_ATTEMPT_BINDING')
    return value


def _enrollment_receipt(raw, attempt):
    from datetime import datetime
    value = _parse(raw, 65536)
    _need(type(value) is dict and set(value) == ENROLLMENT_RECEIPT_FIELDS
          and value['schema'] == 'enrolled-assignment/v1'
          and all(value[k] == attempt[k] for k in ENROLLMENT_FIELDS -
                  {'schema','work_key','scope_nonce_hash'})
          and type(value['assignment_text']) is str
          and 0 < len(value['assignment_text'].encode('utf-8')) <= 16384
          and _sha(value['assignment_text'].encode('utf-8')) == value['assignment_sha256'],
          'OWNER_ENROLLMENT_RECEIPT_BINDING')
    _enrollment_dates(value)
    return value


def _enrollment_dates(value):
    from datetime import datetime
    dates = []
    for key in ('issued_at_utc','expires_at_utc'):
        _need(type(value[key]) is str, 'OWNER_ENROLLMENT_DATE')
        try:
            date = datetime.strptime(value[key], '%Y-%m-%dT%H:%M:%S.%fZ')
        except (ValueError, OverflowError):
            raise TerminalStoreError('OWNER_ENROLLMENT_DATE') from None
        _need(date.strftime('%Y-%m-%dT%H:%M:%S.%fZ') == value[key], 'OWNER_ENROLLMENT_DATE')
        dates.append(date)
    _need(0 < (dates[1]-dates[0]).total_seconds() <= 3600, 'OWNER_ENROLLMENT_LIFETIME')


def _enrollment_commit(raw, attempt):
    value = _parse(raw, 4096)
    fields = (ENROLLMENT_RECEIPT_FIELDS - {'assignment_text'}) | {'receipt_sha256'}
    _need(type(value) is dict and set(value) == fields
          and value['schema'] == 'owner-enrollment-committed/v1'
          and all(value[k] == attempt[k] for k in ENROLLMENT_FIELDS -
                  {'schema','work_key','scope_nonce_hash'})
          and _pin(value['receipt_sha256']), 'OWNER_ENROLLMENT_COMMIT_BINDING')
    # Full receipt is checked at append time, then returned to the caller for external retention by
    # hash (not established by this ledger). The ledger stores only a bounded descriptor, never full Assignment.
    _enrollment_dates(value)
    return value


def _enrollment_step(states, heads, kind, key, raw):
    previous = states.get(key)
    if kind == 'ENROLL_ATTEMPT':
        value = _enrollment_attempt(raw)
        _need(previous is None and value['work_key'] == key and key in heads,
              'OWNER_ENROLLMENT_ALREADY_ATTEMPTED_READ_ONLY')
        head = _head(heads[key], key)
        _need(head['phase'] == 'CLAIM_COMMITTED'
              and head['intent_sha256'] == value['intent_hash']
              and all(head[k] == value[k] for k in
                      ('claim_sha256','target_identity_sha256','principal')),
              'OWNER_ENROLLMENT_CLAIM_HEAD_REQUIRED')
        states[key] = EnrollmentStatus('ATTEMPTED', raw)
    else:
        _need(previous is not None and previous.state == 'ATTEMPTED',
              'OWNER_ENROLLMENT_ALREADY_FINAL_READ_ONLY')
        attempt = _enrollment_attempt(previous.attempt_raw)
        if kind == 'ENROLL_COMMIT':
            _enrollment_commit(raw, attempt)
            _need(_head(heads[key], key)['phase'] == 'CLAIM_COMMITTED',
                  'OWNER_ENROLLMENT_COMMIT_HEAD_REQUIRED')
            states[key] = EnrollmentStatus('COMMITTED', previous.attempt_raw, raw)
        else:
            value = _parse(raw, 4096)
            _need(type(value) is dict and set(value) == {'schema','work_key','attempt_sha256'}
                  and value['schema'] == 'owner-enrollment-unknown/v1'
                  and value['work_key'] == key
                  and value['attempt_sha256'] == _sha(previous.attempt_raw),
                  'OWNER_ENROLLMENT_UNKNOWN_BINDING')
            states[key] = EnrollmentStatus('UNKNOWN', previous.attempt_raw)


class SQLiteTerminalLedger:
    """Real bounded SQLite transactions; this class confers NO owner authority.

    write_enabled defaults OFF. Every access requires the caller's fresh guard.
    Path and inode are enrolled inputs, not self-authorizing handles. Linux
    production must use TerminalOwnerStore, which verifies private native UID
    binding; portable engine tests do not qualify native identity/ACL or fsync.
    """
    def __init__(self, *, path, storage_identity, owner_guard, write_enabled=False,
                 minimum=None):
        self.path = Path(path)
        _need(self.path.is_absolute() and type(storage_identity) is StorageIdentity
              and callable(owner_guard) and type(write_enabled) is bool,
              'TERMINAL_STORE_EXPLICIT_BINDING_REQUIRED')
        _need(minimum is None or type(minimum) is StoreWatermark
              and type(minimum.sequence) is int and minimum.sequence >= 0
              and _pin(minimum.head_sha256), 'TERMINAL_STORE_MINIMUM')
        self.identity, self.guard = storage_identity, owner_guard
        self.write_enabled, self.minimum = write_enabled, minimum
        self._failed = False
        self._lock = threading.Lock()
        self._check()

    def _check(self):
        _need(self.guard() is None, 'TERMINAL_STORE_OWNER_REFUSED')
        _need(self.path.resolve(strict=True) == self.path, 'TERMINAL_STORE_SYMLINK')
        a, b = self.path.lstat(), self.path.parent.lstat()
        _need(stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and stat.S_ISDIR(b.st_mode)
              and a.st_size <= MAX_FILE
              and StorageIdentity(a.st_dev, a.st_ino, b.st_dev, b.st_ino) == self.identity,
              'TERMINAL_STORE_STORAGE_CHANGED')
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(self.path) + suffix)
            try:
                info = sidecar.lstat()
            except FileNotFoundError:
                continue
            _need(suffix == '-journal' and stat.S_ISREG(info.st_mode)
                  and info.st_nlink == 1 and info.st_size <= MAX_FILE,
                  'TERMINAL_STORE_SIDECAR')

    def _open(self, write=False, initializing=False):
        self._check()
        if write:
            _need(self.write_enabled and not self._failed, 'TERMINAL_STORE_WRITE_DISABLED')
        if not initializing:
            _need(self.path.stat().st_size >= 100, 'TERMINAL_STORE_SCHEMA_REQUIRED')
        conn = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                               uri=True, timeout=0, isolation_level=None)
        try:
            conn.enable_load_extension(False)
            conn.execute('PRAGMA trusted_schema=OFF')
            _need(conn.execute('PRAGMA journal_mode').fetchone() == ('delete',),
                  'TERMINAL_STORE_JOURNAL')
            if write:
                conn.execute('PRAGMA synchronous=FULL')
                _need(conn.execute('PRAGMA synchronous').fetchone() == (2,),
                      'TERMINAL_STORE_SYNC')
                conn.execute('PRAGMA max_page_count=2048')
            else:
                conn.execute('PRAGMA query_only=ON')
            self._check()
            return conn
        except BaseException:
            conn.close()
            raise

    def _history(self, conn):
        schema = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
        _need(schema == [('table', 'terminal_events', SCHEMA)], 'TERMINAL_STORE_SCHEMA')
        rows = conn.execute('SELECT seq,previous_sha,work_key,kind,raw,record_sha '
                            'FROM terminal_events ORDER BY seq LIMIT ?', (MAX_EVENTS + 1,)).fetchall()
        _need(len(rows) <= MAX_EVENTS, 'TERMINAL_STORE_QUOTA')
        previous, total = ZERO, 0
        heads, acks, enrollments = {}, {}, {}
        for expected, (seq, prior, key, kind, raw, record_sha) in enumerate(rows, 1):
            _need(type(seq) is int and seq == expected and prior == previous and _pin(key)
                  and kind in ('HEAD', 'ACK') + ENROLLMENT_KINDS and type(raw) is bytes
                  and 0 < len(raw) <= 8192
                  and record_sha == _sha(_canonical([seq, prior, key, kind, _sha(raw)])),
                  'TERMINAL_STORE_HISTORY')
            total += len(raw)
            _need(total <= MAX_PAYLOAD, 'TERMINAL_STORE_QUOTA')
            if kind == 'HEAD':
                value = _head(raw, key)
                old = heads.get(key)
                if old is None:
                    _need(value['phase'] == 'ARMED', 'TERMINAL_STORE_HISTORY_HEAD')
                else:
                    before = _head(old, key)
                    _need(value['seq'] == before['seq'] + 1
                          and value['previous_sha256'] == _sha(old), 'TERMINAL_STORE_HISTORY_HEAD')
                    changing = {'phase', 'seq', 'previous_sha256', 'watermark'}
                    _need(all(before[k] is None or value[k] == before[k]
                              for k in HEAD_FIELDS - changing), 'TERMINAL_STORE_HEAD_REBIND')
                if key in enrollments and PHASES.index(value['phase']) >= PHASES.index('BOUND'):
                    enrolled = enrollments[key]
                    _need(enrolled.state == 'COMMITTED', 'OWNER_ENROLLMENT_BOUND_BEFORE_COMMIT')
                    receipt = _enrollment_commit(enrolled.commit_raw, _enrollment_attempt(enrolled.attempt_raw))
                    _need(value['enrollment_sha256'] == receipt['receipt_sha256']
                          and value['selector'] == receipt['selector_sha256']
                          and all(value[k] == receipt[k] for k in
                              ('assignment_sha256','admission_record_sha256','admission_anchor_sha256')),
                          'OWNER_ENROLLMENT_BOUND_CHANGED')
                if value['phase'] == 'ACKED':
                    _need(key in acks, 'TERMINAL_STORE_HEAD_ACK_MISSING')
                heads[key] = raw
            elif kind == 'ACK':
                ack = _parse(raw, 8192)
                _need(type(ack) is dict and set(ack) == {'schema', 'state', 'binding'}
                      and ack['schema'] == 'terminal-owner-acked/v1' and ack['state'] == 'ACKED'
                      and key not in acks, 'TERMINAL_STORE_ACK_HISTORY')
                query = _query(_canonical(ack['binding']))
                _need(query['work_key'] == key and key in heads,
                      'TERMINAL_STORE_ACK_HISTORY')
                head = _head(heads[key], key)
                _need(head['phase'] == 'ACK_ATTEMPTED'
                      and all(head[k] == query[k] for k in QUERY_FIELDS),
                      'TERMINAL_STORE_ACK_HEAD')
                acks[key] = raw
            else:
                _enrollment_step(enrollments, heads, kind, key, raw)
            previous = record_sha
        watermark = StoreWatermark(len(rows), previous)
        if self.minimum is not None:
            n = self.minimum.sequence
            _need(n <= len(rows) and (ZERO if n == 0 else rows[n-1][5])
                  == self.minimum.head_sha256, 'TERMINAL_STORE_BELOW_RETAINED_MINIMUM')
        return rows, heads, acks, watermark, total

    def _barrier(self):
        """FULL commit plus explicit DB/parent fsync on POSIX, no namespace changes."""
        self._check()
        fd = os.open(self.path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
        try:
            _need((os.fstat(fd).st_dev, os.fstat(fd).st_ino) ==
                  (self.identity.file_device, self.identity.file_inode),
                  'TERMINAL_STORE_STORAGE_CHANGED')
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.name == 'posix':
            fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                _need((os.fstat(fd).st_dev, os.fstat(fd).st_ino) ==
                      (self.identity.parent_device, self.identity.parent_inode),
                      'TERMINAL_STORE_STORAGE_CHANGED')
                os.fsync(fd)
            finally:
                os.close(fd)
        self._check()

    def _read(self):
        try:
            conn = self._open()
            try:
                conn.execute('BEGIN')
                result = self._history(conn)
                conn.commit()
                self._check()
                return result
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            raise TerminalStoreError('TERMINAL_STORE_READ_UNKNOWN') from None

    def initialize(self):
        """Explicit schema init only; already-existing empty file, writer enabled."""
        _need(self._lock.acquire(blocking=False), 'TERMINAL_STORE_LOCAL_BUSY')
        conn = None
        try:
            conn = self._open(write=True, initializing=True)
            conn.execute('BEGIN IMMEDIATE')
            _need(not conn.execute('SELECT name FROM sqlite_master').fetchall(),
                  'TERMINAL_STORE_NOT_EMPTY')
            conn.execute(SCHEMA)
            self._check()
            conn.commit()
            conn.close()
            conn = None
            self._barrier()
            _need(self._read()[3] == StoreWatermark(0, ZERO), 'TERMINAL_STORE_INIT_UNKNOWN')
        except BaseException as error:
            self._failed = True
            if isinstance(error, (sqlite3.Error, OSError)):
                raise TerminalStoreError('TERMINAL_STORE_INITIALIZE_UNKNOWN') from None
            raise
        finally:
            if conn is not None:
                conn.close()
            self._lock.release()

    def watermark(self):
        return self._read()[3]

    def read_head(self, work_key):
        _need(_pin(work_key), 'TERMINAL_STORE_WORK_KEY')
        return self._read()[1].get(work_key)

    def _append(self, kind, key, raw, before):
        rows, _, _, mark, total = before
        _need(len(rows) < MAX_EVENTS and total + len(raw) <= MAX_PAYLOAD,
              'TERMINAL_STORE_QUOTA')
        seq = mark.sequence + 1
        digest = _sha(_canonical([seq, mark.head_sha256, key, kind, _sha(raw)]))
        return seq, mark.head_sha256, key, kind, raw, digest

    def compare_and_swap(self, work_key, expected_sha, new_raw):
        new = _head(new_raw, work_key)
        _need(expected_sha is None or _pin(expected_sha), 'TERMINAL_STORE_EXPECTED_HEAD')
        _need(self._lock.acquire(blocking=False), 'TERMINAL_STORE_LOCAL_BUSY')
        conn = None
        try:
            conn = self._open(write=True)
            conn.execute('BEGIN IMMEDIATE')
            before = self._history(conn)
            prior = before[1].get(work_key)
            _need((None if prior is None else _sha(prior)) == expected_sha,
                  'TERMINAL_STORE_CAS_STALE')
            if prior is None:
                _need(new['phase'] == 'ARMED', 'TERMINAL_STORE_HEAD_ORDER')
            else:
                old = _head(prior, work_key)
                _need(new['seq'] == old['seq'] + 1 and new['previous_sha256'] == _sha(prior),
                      'TERMINAL_STORE_HEAD_ORDER')
            row = self._append('HEAD', work_key, new_raw, before)
            conn.execute('INSERT INTO terminal_events VALUES (?,?,?,?,?,?)', row)
            self._history(conn)  # Validate cross-record invariants before publication.
            self._check()
            conn.commit()
            conn.close()
            conn = None
            self._barrier()
            _need(self.read_head(work_key) == new_raw, 'TERMINAL_STORE_CAS_ACK_UNKNOWN')
        except BaseException as error:
            self._failed = True
            if isinstance(error, (sqlite3.Error, OSError)):
                raise TerminalStoreError('TERMINAL_STORE_CAS_UNKNOWN') from None
            raise
        finally:
            if conn is not None:
                conn.close()
            self._lock.release()

    def read_ack(self, query_raw):
        query = _query(query_raw)
        raw = self._read()[2].get(query['work_key'])
        if raw is None:
            return None
        _need(raw == _canonical(dict(schema='terminal-owner-acked/v1', state='ACKED',
                                    binding=query)), 'TERMINAL_STORE_ACK_CONFLICT')
        return raw

    def accept_ack(self, query_raw, deliver_once):
        """Durable owner acceptance first; the one original wire effect second.

        Existing acceptance never calls the writer again. Use read_ack after a
        crash/lost response; no automatically replayable success path exists.
        """
        query = _query(query_raw)
        _need(callable(deliver_once), 'TERMINAL_STORE_WIRE_REQUIRED')
        _need(self._lock.acquire(blocking=False), 'TERMINAL_STORE_LOCAL_BUSY')
        conn = None
        try:
            conn = self._open(write=True)
            conn.execute('BEGIN IMMEDIATE')
            before = self._history(conn)
            prior = before[2].get(query['work_key'])
            raw = _canonical(dict(schema='terminal-owner-acked/v1', state='ACKED', binding=query))
            if prior is not None:
                _need(prior == raw, 'TERMINAL_STORE_ACK_CONFLICT')
                raise TerminalStoreError('TERMINAL_STORE_ACK_ALREADY_ACCEPTED_READ_ONLY')
            head = before[1].get(query['work_key'])
            _need(head is not None, 'TERMINAL_STORE_ACK_HEAD')
            value = _head(head, query['work_key'])
            _need(value['phase'] == 'ACK_ATTEMPTED'
                  and all(value[k] == query[k] for k in QUERY_FIELDS),
                  'TERMINAL_STORE_ACK_HEAD')
            row = self._append('ACK', query['work_key'], raw, before)
            conn.execute('INSERT INTO terminal_events VALUES (?,?,?,?,?,?)', row)
            self._history(conn)
            self._check()
            conn.commit()
            conn.close()
            conn = None
            self._barrier()
            _need(self.read_ack(query_raw) == raw, 'TERMINAL_STORE_ACK_READBACK_UNKNOWN')
            self._check()  # Fresh owner/identity check immediately before the original writer.
            deliver_once()
            self._check()
            return raw
        except BaseException as error:
            self._failed = True
            if isinstance(error, (sqlite3.Error, OSError)):
                raise TerminalStoreError('TERMINAL_STORE_ACK_UNKNOWN_NO_RESEND') from None
            raise
        finally:
            if conn is not None:
                conn.close()
            self._lock.release()


    @staticmethod
    def _enrollment_from_rows(rows, work_key):
        status = None
        for row in rows:
            _, _, key, kind, raw, _ = row
            if key != work_key or kind not in ENROLLMENT_KINDS:
                continue
            if kind == 'ENROLL_ATTEMPT':
                status = EnrollmentStatus('ATTEMPTED', raw)
            elif kind == 'ENROLL_COMMIT':
                status = EnrollmentStatus('COMMITTED', status.attempt_raw, raw)
            else:
                status = EnrollmentStatus('UNKNOWN', status.attempt_raw)
        return status

    def inspect_enrollment(self, work_key):
        """Read-only reconciliation. Never INSERT/resume/authorize from a receipt."""
        _need(_pin(work_key), 'TERMINAL_STORE_WORK_KEY')
        return self._enrollment_from_rows(self._read()[0], work_key)

    def _append_enrollment(self, kind, work_key, raw, attempt_sha=None):
        _need(kind in ENROLLMENT_KINDS and _pin(work_key), 'OWNER_ENROLLMENT_KIND')
        _need(self._lock.acquire(blocking=False), 'TERMINAL_STORE_LOCAL_BUSY')
        conn = None
        try:
            conn = self._open(write=True)
            conn.execute('BEGIN IMMEDIATE')
            before = self._history(conn)
            status = self._enrollment_from_rows(before[0], work_key)
            if kind == 'ENROLL_ATTEMPT':
                _need(status is None, 'OWNER_ENROLLMENT_ALREADY_ATTEMPTED_READ_ONLY')
            else:
                _need(status is not None and status.state == 'ATTEMPTED'
                      and _sha(status.attempt_raw) == attempt_sha,
                      'OWNER_ENROLLMENT_ALREADY_FINAL_READ_ONLY')
            row = self._append(kind, work_key, raw, before)
            conn.execute('INSERT INTO terminal_events VALUES (?,?,?,?,?,?)', row)
            self._history(conn)
            self._check()
            conn.commit()
            conn.close()
            conn = None
            self._barrier()
            actual = self.inspect_enrollment(work_key)
            _need(actual is not None and (actual.attempt_raw == raw if kind == 'ENROLL_ATTEMPT'
                  else actual.commit_raw == raw if kind == 'ENROLL_COMMIT'
                  else actual.state == 'UNKNOWN'), 'OWNER_ENROLLMENT_LEDGER_ACK_UNKNOWN')
        except BaseException as error:
            self._failed = True
            if isinstance(error, (sqlite3.Error, OSError)):
                raise TerminalStoreError('OWNER_ENROLLMENT_LEDGER_UNKNOWN_NO_RETRY') from None
            raise
        finally:
            if conn is not None:
                conn.close()
            self._lock.release()

    def reserve_enrollment(self, attempt_raw):
        attempt = _enrollment_attempt(attempt_raw)
        self._append_enrollment('ENROLL_ATTEMPT', attempt['work_key'], attempt_raw)

    def commit_enrollment(self, work_key, attempt_sha256, receipt_raw):
        _need(_pin(attempt_sha256), 'OWNER_ENROLLMENT_ATTEMPT_SHA')
        status = self.inspect_enrollment(work_key)
        _need(status is not None and status.state == 'ATTEMPTED'
              and _sha(status.attempt_raw) == attempt_sha256,
              'OWNER_ENROLLMENT_ALREADY_FINAL_READ_ONLY')
        receipt = _enrollment_receipt(receipt_raw, _enrollment_attempt(status.attempt_raw))
        descriptor = {k:v for k,v in receipt.items() if k != 'assignment_text'}
        descriptor.update(schema='owner-enrollment-committed/v1', receipt_sha256=_sha(receipt_raw))
        self._append_enrollment('ENROLL_COMMIT', work_key, _canonical(descriptor), attempt_sha256)

    def unknown_enrollment(self, work_key, attempt_sha256):
        _need(_pin(attempt_sha256), 'OWNER_ENROLLMENT_ATTEMPT_SHA')
        raw = _canonical(dict(schema='owner-enrollment-unknown/v1', work_key=work_key,
                              attempt_sha256=attempt_sha256))
        self._append_enrollment('ENROLL_UNKNOWN', work_key, raw, attempt_sha256)


class TerminalOwnerStore(SQLiteTerminalLedger):
    """Selected native owner contour: existing Ubuntu UID1001, approved storage.

    No username resolution, credential discovery, chmod or directory creation.
    The launcher must pass a pre-enrolled identity and genuine authorization
    guard. Constructor/type presence alone proves no installed owner authority.
    """
    def _check(self):
        _need(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 1001,
              'TERMINAL_STORE_NATIVE_UID1001_REQUIRED')
        super()._check()
        a, b = self.path.lstat(), self.path.parent.lstat()
        _need(a.st_uid == b.st_uid == 1001 and stat.S_IMODE(a.st_mode) == 0o600
              and stat.S_IMODE(b.st_mode) == 0o700, 'TERMINAL_STORE_PRIVATE_NAMESPACE_REQUIRED')
        for parent in self.path.parents:
            info = parent.lstat()
            _need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, 1001)
                  and stat.S_IMODE(info.st_mode) & 0o022 == 0,
                  'TERMINAL_STORE_UNPROTECTED_ANCESTOR')
        journal = Path(str(self.path) + '-journal')
        if journal.exists():
            info = journal.lstat()
            _need(info.st_uid == 1001 and stat.S_IMODE(info.st_mode) == 0o600,
                  'TERMINAL_STORE_PRIVATE_JOURNAL_REQUIRED')
