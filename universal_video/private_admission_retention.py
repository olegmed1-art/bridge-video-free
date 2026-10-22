"""Admission records over existing approved RC2 Directory capabilities.

No directory creation, authentication primitive, credentials, queue or power API.
Directory.read/create are the existing pinned RC2 operations; create supplies
O_EXCL, file/directory fsync and readback. Callers supply already approved handles
for records and the anchor. Local tests may supply explicit durability models.
Recovery confirms uncertain existing bytes with fsync via the same Directory.fd;
it never opens another namespace or creates a directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from typing import Any, Mapping
from uuid import UUID

from .private_result_handoff import PrivateOneJobConfigurationError


class AdmissionRetentionError(PrivateOneJobConfigurationError):
    pass


def need(value, code):
    if not value:
        raise AdmissionRetentionError(code)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def is_pin(value) -> bool:
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def stamp(info):
    # The existing Directory.read contract returns this exact nine-field stamp.
    return tuple(getattr(info, key) for key in
                 ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                  'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def canonical(value: Any) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode()
    # Reject silent coercion of e.g. non-string dictionary keys/tuples.
    need(json.loads(raw) == value, 'ADMISSION_JSON_CHANGED')
    return raw


CLAIM_KEYS = frozenset({'job_id', 'batch_id', 'lease_token',
                        'attempt_count', 'source_file_id'})


def exact_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    need(isinstance(claim, Mapping) and CLAIM_KEYS <= set(claim), 'ADMISSION_CLAIM_FIELDS')
    value = {key: claim[key] for key in CLAIM_KEYS}
    try:
        valid = all(type(value[key]) is str and str(UUID(value[key])) == value[key]
                    for key in ('job_id', 'batch_id', 'lease_token'))
    except (TypeError, ValueError, AttributeError):
        valid = False
    need(valid and type(value['attempt_count']) is int and 1 <= value['attempt_count'] <= 3
         and type(value['source_file_id']) is str
         and re.fullmatch('[A-Za-z0-9_-]{10,200}', value['source_file_id']),
         'ADMISSION_CLAIM_NOT_EXACT')
    return value


class AdmissionRetention:
    """Immutable content + PREPARED -> anchor -> COMMITTED, never auto-retry.

    context_sha and owner_guard are the accepted RC2 context/actual external
    authenticated owner port. Their presence is not authentication by itself.
    No fake implementation or default is provided. Handles are not opened here.
    """
    MAX_REQUEST = 4259840
    CHUNK = 65536

    def __init__(self, *, records, anchors, context_sha, owner_guard):
        need(is_pin(context_sha) and callable(owner_guard), 'ADMISSION_OWNER_INPUT_REQUIRED')
        need(all(callable(getattr(directory, method, None))
                 for directory in (records, anchors) for method in ('read', 'create', 'check')),
             'ADMISSION_APPROVED_DIRECTORY_HANDLES_REQUIRED')
        need(all(type(getattr(directory, 'fd', None)) is int and directory.fd >= 0
                 for directory in (records, anchors)), 'ADMISSION_DIRECTORY_FD_REQUIRED')
        self.records, self.anchors = records, anchors
        self.context_sha, self.owner_guard = context_sha, owner_guard
        self.last_receipt = None
        self._confirmed = {}

    def check(self):
        need(self.owner_guard(self.context_sha) is None, 'ADMISSION_OWNER_REFUSED')

    def _read(self, directory, name):
        self.check()
        try:
            raw, _ = directory.read(name, limit=self.CHUNK)
        except FileNotFoundError:
            self.check()
            return None
        self.check()
        need(type(raw) is bytes and 0 < len(raw) <= self.CHUNK, 'ADMISSION_READ_BOUND')
        return raw

    def _put(self, directory, name, raw):
        prior = self._read(directory, name)
        if prior is None:
            # Existing Directory invokes check after metadata reads, immediately
            # before O_EXCL/write/fsync. An uncertain create ACK always raises;
            # this call never retries it or promotes readback into success.
            directory.create(name, raw, check=self.check)
        else:
            need(prior == raw, 'ADMISSION_IMMUTABLE_CONFLICT')
        # Bind even a successful create/readback to the exact fsynced inode.
        self._confirm(directory, name, raw)
        need(self._read(directory, name) == raw, 'ADMISSION_WRITE_READBACK')

    def _confirm(self, directory, name, expected):
        """Confirm durability of a prior uncertain write using its approved fd.

        Readback alone does not prove that an interrupted create reached fsync.
        The exposed Directory.fd is the existing pinned namespace capability;
        this opens only its existing owner-writable leaf without creating or
        truncating it. A writable descriptor supports portable fsync semantics.
        """
        directory.check()
        self.check()
        flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        flags |= getattr(os, 'O_CLOEXEC', 0)
        flags |= getattr(os, 'O_BINARY', 0)
        fd = os.open(name, flags, dir_fd=directory.fd)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                 and 0 < before.st_size <= self.CHUNK,
                 'ADMISSION_CONFIRM_FILE_BOUND')
            expected_stamp = stamp(before)
            def unchanged():
                need(expected_stamp == stamp(os.fstat(fd))
                     == stamp(os.stat(name, dir_fd=directory.fd, follow_symlinks=False)),
                     'ADMISSION_CONFIRM_CHANGED')
            raw = bytearray()
            while len(raw) <= self.CHUNK:
                part = os.read(fd, min(65536, self.CHUNK + 1 - len(raw)))
                if not part:
                    break
                raw.extend(part)
            need(bytes(raw) == expected, 'ADMISSION_CONFIRM_CHANGED')
            unchanged()
            directory.check()
            self.check()  # Fresh owner check after all potentially blocking reads.
            unchanged()  # A caller callback cannot substitute an un-fsynced leaf.
            os.fsync(fd)
            self.check()
            unchanged()
            directory.check()
            os.fsync(directory.fd)
            self.check()
            seen, seen_stamp = directory.read(name, limit=self.CHUNK)
            need(seen == expected and seen_stamp == expected_stamp,
                 'ADMISSION_CONFIRM_READBACK')
            unchanged()
            directory.check()
            self._confirmed[(id(directory), name)] = (directory, expected_stamp)
        finally:
            os.close(fd)

    def _check_confirmed(self):
        # Called after the final external owner assertion. No caller callbacks
        # follow these checks before the durability ACK. As in RC2 Directory,
        # the approved private namespace is the concurrency/security boundary.
        for (_, name), (directory, expected_stamp) in self._confirmed.items():
            directory.check()
            need(stamp(os.stat(name, dir_fd=directory.fd, follow_symlinks=False))
                 == expected_stamp, 'ADMISSION_ACK_FILE_CHANGED')

    def _store_blob(self, raw):
        chunks = []
        for offset in range(0, len(raw), self.CHUNK):
            chunk = raw[offset:offset + self.CHUNK]
            sha = digest(chunk)
            self._put(self.records, 'admission-blob-' + sha, chunk)
            chunks.append(sha)
        return dict(sha256=digest(raw), bytes=len(raw), chunks=chunks)

    def _load_blob(self, descriptor, limit, *, confirm=False):
        need(type(descriptor) is dict and set(descriptor) == {'sha256', 'bytes', 'chunks'}
             and is_pin(descriptor['sha256']) and type(descriptor['bytes']) is int
             and 0 < descriptor['bytes'] <= limit and type(descriptor['chunks']) is list
             and len(descriptor['chunks']) == (descriptor['bytes'] + self.CHUNK - 1) // self.CHUNK
             and all(is_pin(pin) for pin in descriptor['chunks']), 'ADMISSION_BLOB_DESCRIPTOR')
        parts = []
        for pin in descriptor['chunks']:
            raw = self._read(self.records, 'admission-blob-' + pin)
            need(raw is not None and digest(raw) == pin, 'ADMISSION_BLOB_READBACK')
            if confirm:
                self._confirm(self.records, 'admission-blob-' + pin, raw)
            parts.append(raw)
        raw = b''.join(parts)
        need(len(raw) == descriptor['bytes'] and digest(raw) == descriptor['sha256'],
             'ADMISSION_BLOB_BINDING')
        return raw

    def _names(self, claim):
        selected = exact_claim(claim)
        key = digest(canonical(selected))
        return selected, key, 'admission-' + key

    def record(self, claim, *, request_raw, request_sha256, saved_pins, verified, readback):
        """Return a receipt only after all content, anchor and commit read back."""
        self.last_receipt = None
        self._confirmed = {}
        # Snapshot all caller-owned mutable objects before the first callback.
        selected, key, prefix = self._names(claim)
        need(type(request_raw) is bytes and 0 < len(request_raw) <= self.MAX_REQUEST
             and digest(request_raw) == request_sha256, 'ADMISSION_REQUEST_PIN')
        values = {'request': request_raw, 'saved_pins': canonical(saved_pins),
                  'verified': canonical(verified), 'readback': canonical(readback)}
        need(all(0 < len(raw) <= self.CHUNK for kind, raw in values.items() if kind != 'request'),
             'ADMISSION_RESULT_BOUND')
        self.check()
        for directory, suffix in ((self.records, '.prepared'), (self.anchors, '.anchor'),
                                  (self.records, '.committed')):
            need(self._read(directory, prefix + suffix) is None, 'ADMISSION_RECONCILE_REQUIRED')
        objects = {kind: self._store_blob(raw) for kind, raw in values.items()}
        record = dict(kind='PRIVATE_ADMISSION_PREPARED_V1', claim=selected,
                      claim_sha256=key, context_sha256=self.context_sha, objects=objects)
        raw = canonical(record)
        need(len(raw) <= self.CHUNK, 'ADMISSION_RECORD_BOUND')
        self._put(self.records, prefix + '.prepared', raw)
        return self._commit(selected, digest(raw))

    def record_enrollment(self, work_key, receipt_raw):
        """Retain full enrolled receipt with the SAME original RC2 directories.

        SQL64 opt-in only. Does not grant READ_RESULT or replace terminal ACK.
        No path discovery, namespace creation, recovery retry or old receipt edit.
        Existing create/confirm supplies O_EXCL, fsync and exact inode readback.
        """
        from .owner_terminal_store import ENROLLMENT_RECEIPT_FIELDS, _enrollment_dates
        need(is_pin(work_key) and type(receipt_raw) is bytes
             and 0 < len(receipt_raw) <= self.CHUNK, 'ENROLLMENT_RETENTION_BOUND')
        value = json.loads(receipt_raw)
        need(type(value) is dict and set(value) == ENROLLMENT_RECEIPT_FIELDS
             and value['schema'] == 'enrolled-assignment/v1'
             and canonical(value) == receipt_raw
             and all(is_pin(value[k]) for k in ENROLLMENT_RECEIPT_FIELDS-
                     {'schema','principal','assignment_text','issued_at_utc','expires_at_utc'})
             and type(value['principal']) is str and re.fullmatch('[A-Za-z0-9_]{1,63}',value['principal'])
             and digest(value['assignment_text'].encode('utf8')) == value['assignment_sha256'],
             'ENROLLMENT_RETENTION_RECEIPT')
        _enrollment_dates(value)
        self.check()
        prefix = 'enrollment-' + work_key
        for directory, suffix in ((self.records,'.prepared'), (self.anchors,'.anchor'),
                                  (self.records,'.committed')):
            need(self._read(directory,prefix+suffix) is None,'ENROLLMENT_RECONCILE_NO_REPLAY')
        self._put(self.records,prefix+'.prepared',receipt_raw)
        anchor = canonical(dict(kind='OWNER_ENROLLMENT_ANCHOR_V1',work_key=work_key,
            receipt_sha256=digest(receipt_raw),context_sha256=self.context_sha))
        self._put(self.anchors,prefix+'.anchor',anchor)
        committed = canonical(dict(kind='OWNER_ENROLLMENT_COMMITTED_V1',work_key=work_key,
            receipt_sha256=digest(receipt_raw),anchor_sha256=digest(anchor),context_sha256=self.context_sha))
        self._put(self.records,prefix+'.committed',committed)
        self.check()
        self._check_confirmed()
        return committed

    def _prepared(self, claim, expected_record_sha, *, confirm=False):
        selected, key, prefix = self._names(claim)
        need(is_pin(expected_record_sha), 'ADMISSION_EXPECTED_RECORD_PIN')
        raw = self._read(self.records, prefix + '.prepared')
        need(raw is not None and digest(raw) == expected_record_sha, 'ADMISSION_PREPARED_PIN')
        record = json.loads(raw)
        need(canonical(record) == raw and type(record) is dict
             and set(record) == {'kind', 'claim', 'claim_sha256', 'context_sha256', 'objects'}
             and record['kind'] == 'PRIVATE_ADMISSION_PREPARED_V1'
             and type(record['claim']) is dict and set(record['claim']) == CLAIM_KEYS
             and exact_claim(record['claim']) == selected
             and digest(canonical(record['claim'])) == record['claim_sha256'] == key
             and record['context_sha256'] == self.context_sha
             and type(record['objects']) is dict
             and set(record['objects']) == {'request', 'saved_pins', 'verified', 'readback'},
             'ADMISSION_PREPARED_BINDING')
        for kind, descriptor in record['objects'].items():
            self._load_blob(descriptor, self.MAX_REQUEST if kind == 'request' else self.CHUNK,
                            confirm=confirm)
        if confirm:
            self._confirm(self.records, prefix + '.prepared', raw)
        anchor = canonical(dict(kind='PRIVATE_ADMISSION_ANCHOR_V1', claim_sha256=key,
                                record_sha256=expected_record_sha))
        commit = canonical(dict(kind='PRIVATE_ADMISSION_COMMITTED_V1', claim_sha256=key,
                                record_sha256=expected_record_sha, anchor_sha256=digest(anchor)))
        return record, prefix, anchor, commit

    def inspect(self, claim, expected_record_sha):
        """Read-only reconciliation. A saved digest grants no owner authority."""
        self.last_receipt = None
        record, prefix, anchor, commit = self._prepared(claim, expected_record_sha)
        seen_anchor = self._read(self.anchors, prefix + '.anchor')
        seen_commit = self._read(self.records, prefix + '.committed')
        need(seen_anchor is None or seen_anchor == anchor, 'ADMISSION_ANCHOR_CONFLICT')
        need(seen_commit is None or seen_commit == commit, 'ADMISSION_COMMIT_CONFLICT')
        need(seen_commit is None or seen_anchor is not None, 'ADMISSION_COMMIT_WITHOUT_ANCHOR')
        state = 'COMMITTED' if seen_commit else 'ANCHORED' if seen_anchor else 'PREPARED'
        self.check()
        return dict(state=state, durability_confirmed=False, record_sha256=expected_record_sha,
                    claim_sha256=record['claim_sha256'], anchor_sha256=digest(anchor),
                    request_sha256=record['objects']['request']['sha256'])

    def _commit(self, claim, expected_record_sha):
        _, prefix, anchor, commit = self._prepared(claim, expected_record_sha, confirm=True)
        self._put(self.anchors, prefix + '.anchor', anchor)
        self._put(self.records, prefix + '.committed', commit)
        receipt = self.inspect(claim, expected_record_sha)
        need(receipt['state'] == 'COMMITTED', 'ADMISSION_COMMIT_NOT_ACKNOWLEDGED')
        self.check()
        self._check_confirmed()
        receipt['durability_confirmed'] = True
        self.last_receipt = dict(receipt)
        return receipt

    def recover(self, claim, expected_record_sha):
        """Explicit fresh-owner completion of retained evidence; no queue/compute.

        Validate all retained bytes first. Partial/conflicting files are kept
        and refused; this method never deletes, overwrites or repairs their bytes.
        """
        self.last_receipt = None
        self._confirmed = {}
        self.inspect(claim, expected_record_sha)
        return self._commit(claim, expected_record_sha)


class TerminalHandoffRetention:
    """Typed terminal records in the SAME approved Directory/anchor contour.

    No directories/ACLs/DB capabilities are created. Successful returned
    watermark bytes MUST be retained outside this object by the owner before
    finish/ACK. A directory scan or RAM digest is never a replacement for it.
    Loading an old watermark fails if a newer step already exists.
    """
    PHASES = ('PREPARED', 'FINISH_ATTEMPTED', 'READ_VERIFIED', 'ACK_ATTEMPTED', 'ACKED', 'CLOSED')
    LIMITS = dict(assignment=16384, claim=16384, selected=4194304, bundle=4096)
    META = {'intent_hash', 'scope_nonce_hash', 'principal', 'target_identity_sha256',
            'enrollment_sha256', 'admission_record_sha256', 'admission_anchor_sha256'}

    def __init__(self, retention):
        import threading
        need(type(retention) is AdmissionRetention, 'TERMINAL_RETENTION_ORIGINAL_CONTOUR_REQUIRED')
        self.retention, self._failed, self._prepared = retention, False, False
        self._lock = threading.Lock()

    def _read(self, directory, name, expected=None):
        raw = self.retention._read(directory, name)
        need(raw is not None and (expected is None or digest(raw) == expected), 'TERMINAL_RECORD_PIN')
        return raw

    def _fresh(self, directory, name, raw):
        need(self.retention._read(directory, name) is None, 'TERMINAL_RECONCILE_NO_RETRY')
        self.retention._put(directory, name, raw)

    @staticmethod
    def _watermark(raw):
        need(type(raw) is bytes and 0 < len(raw) <= 4096, 'TERMINAL_WATERMARK_REQUIRED')
        from .diana20_transport_protocol import parse
        value = parse(raw)
        need(type(value) is dict and set(value) == {'selector', 'seq', 'record_sha256',
             'prepared_sha256', 'phase', 'ack_attempt_id'} and canonical(value) == raw
             and all(is_pin(value[k]) for k in ('selector', 'record_sha256', 'prepared_sha256'))
             and type(value['seq']) is int and 1 <= value['seq'] <= 7
             and value['phase'] in TerminalHandoffRetention.PHASES + ('UNKNOWN',)
             and (value['ack_attempt_id'] is None or is_pin(value['ack_attempt_id'])),
             'TERMINAL_WATERMARK_INVALID')
        return value

    def prepare(self, *, assignment, claim, selected, bundle, metadata):
        from .receipt_encoding import canonical_bytes
        from .terminal_finish_contract import Assignment, checked_bundle
        acquired = self._lock.acquire(blocking=False)
        try:
            need(acquired and not self._failed and not self._prepared, 'TERMINAL_SESSION_UNKNOWN')
            self._prepared = True
            need(type(assignment) is Assignment and type(metadata) is dict and set(metadata) == self.META,
                 'TERMINAL_PREPARE_INPUT')
            assignment.check_claim(claim)
            need(all(is_pin(metadata[k]) for k in self.META - {'principal'})
                 and type(metadata['principal']) is str
                 and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', metadata['principal']),
                 'TERMINAL_PREPARE_BINDING')
            checked_bundle(bundle)
            values = dict(assignment=assignment.raw,
                claim=canonical_bytes(dict(claim), max_bytes=self.LIMITS['claim']),
                selected=canonical_bytes(dict(selected), max_bytes=self.LIMITS['selected']),
                bundle=canonical_bytes(dict(bundle), max_bytes=self.LIMITS['bundle']))
            descriptors = {k: dict(sha256=digest(v), bytes=len(v)) for k, v in values.items()}
            envelope = dict(schema='terminal-handoff/v1', selector_sha256=assignment.selector_sha256,
                attempt_count=claim['attempt_count'], **metadata, objects=descriptors)
            raw = canonical(envelope)
            need(len(raw) <= 4096 and sum(map(len, values.values())) + len(raw) <= 4259840,
                 'TERMINAL_TOTAL_BOUND')
            r, prefix = self.retention, 'terminal-' + assignment.selector_sha256
            need(r._read(r.records, prefix + '.prepared') is None, 'TERMINAL_RECONCILE_NO_RETRY')
            for kind, content in values.items():
                descriptor = r._store_blob(content)
                self._fresh(r.records, prefix + '.' + kind, canonical(descriptor))
            self._fresh(r.records, prefix + '.prepared', raw)
            return self._step(envelope, digest(raw), None, 'PREPARED', None, None)
        except BaseException:
            self._failed = True
            raise
        finally:
            if acquired:
                self._lock.release()

    def _step(self, envelope, prepared_sha, previous, phase, evidence_raw, ack_attempt_id):
        r, selector = self.retention, envelope['selector_sha256']
        seq = 1 if previous is None else previous['seq'] + 1
        name = 'terminal-' + selector + '.step-' + str(seq)
        evidence_sha = None
        if evidence_raw is not None:
            need(type(evidence_raw) is bytes and 0 < len(evidence_raw) <= r.CHUNK, 'TERMINAL_EVIDENCE_BOUND')
            evidence_sha = digest(evidence_raw)
            r._put(r.records, 'terminal-evidence-' + evidence_sha, evidence_raw)
        step = dict(schema='terminal-handoff-step/v1', selector=selector, seq=seq,
            previous_sha256=None if previous is None else previous['record_sha256'],
            prepared_sha256=prepared_sha, phase=phase, evidence_sha256=evidence_sha,
            ack_attempt_id=ack_attempt_id)
        raw = canonical(step)
        self._fresh(r.records, name, raw)
        self._fresh(r.anchors, name, raw)
        r.check()
        r._check_confirmed()
        return canonical(dict(selector=selector, seq=seq, record_sha256=digest(raw),
            prepared_sha256=prepared_sha, phase=phase, ack_attempt_id=ack_attempt_id))

    def load(self, watermark_raw):
        """Read-only anchored input recovery. No queue, stage, finish or ACK."""
        from .diana20_transport_protocol import parse
        from .receipt_encoding import canonical_bytes, strict_loads
        from .terminal_finish_contract import Assignment, checked_bundle
        mark = self._watermark(watermark_raw)
        r, prefix = self.retention, 'terminal-' + mark['selector']
        raw = self._read(r.records, prefix + '.prepared', mark['prepared_sha256'])
        envelope = parse(raw)
        need(type(envelope) is dict and set(envelope) == self.META | {
             'schema', 'selector_sha256', 'attempt_count', 'objects'}
             and canonical(envelope) == raw and len(raw) <= 4096
             and envelope['schema'] == 'terminal-handoff/v1'
             and envelope['selector_sha256'] == mark['selector']
             and type(envelope['objects']) is dict and set(envelope['objects']) == set(self.LIMITS),
             'TERMINAL_ENVELOPE_BINDING')
        previous, phase, ack_id = None, None, None
        for seq in range(1, mark['seq'] + 1):
            name = prefix + '.step-' + str(seq)
            step_raw = self._read(r.records, name)
            need(self._read(r.anchors, name) == step_raw, 'TERMINAL_ANCHOR_CHANGED')
            step = parse(step_raw)
            need(type(step) is dict and set(step) == {'schema', 'selector', 'seq', 'previous_sha256',
                 'prepared_sha256', 'phase', 'evidence_sha256', 'ack_attempt_id'}
                 and canonical(step) == step_raw and step['schema'] == 'terminal-handoff-step/v1'
                 and step['selector'] == mark['selector'] and type(step['seq']) is int and step['seq'] == seq
                 and step['prepared_sha256'] == mark['prepared_sha256']
                 and step['previous_sha256'] == previous, 'TERMINAL_CHAIN_BINDING')
            allowed = 'PREPARED' if phase is None else (
                None if phase in ('UNKNOWN', 'CLOSED') else self.PHASES[self.PHASES.index(phase) + 1])
            need(step['phase'] == allowed or (phase is not None and phase not in ('UNKNOWN', 'CLOSED')
                 and step['phase'] == 'UNKNOWN'), 'TERMINAL_CHAIN_TRANSITION')
            if step['phase'] == 'ACK_ATTEMPTED':
                need(is_pin(step['ack_attempt_id']) and ack_id is None, 'TERMINAL_ACK_ID')
                ack_id = step['ack_attempt_id']
            need(step['ack_attempt_id'] == ack_id, 'TERMINAL_ACK_ID_CHANGED')
            evidence = step['evidence_sha256']
            if evidence is not None:
                need(is_pin(evidence), 'TERMINAL_EVIDENCE_PIN')
                self._read(r.records, 'terminal-evidence-' + evidence, evidence)
            need(step['phase'] not in ('READ_VERIFIED', 'ACKED') or evidence is not None,
                 'TERMINAL_EVIDENCE_REQUIRED')
            previous, phase = digest(step_raw), step['phase']
        need(previous == mark['record_sha256'] and phase == mark['phase'] and ack_id == mark['ack_attempt_id'],
             'TERMINAL_WATERMARK_CHANGED')
        need(r._read(r.records, prefix + '.step-' + str(mark['seq'] + 1)) is None
             and r._read(r.anchors, prefix + '.step-' + str(mark['seq'] + 1)) is None,
             'TERMINAL_STALE_WATERMARK')
        objects = {}
        for kind, limit in self.LIMITS.items():
            descriptor = parse(self._read(r.records, prefix + '.' + kind))
            content = r._load_blob(descriptor, limit)
            need(envelope['objects'][kind] == dict(sha256=digest(content), bytes=len(content)),
                 'TERMINAL_OBJECT_CHANGED')
            objects[kind] = content
        need(sum(map(len, objects.values())) + len(raw) <= 4259840, 'TERMINAL_TOTAL_BOUND')
        assignment = Assignment(objects['assignment'])
        claim = strict_loads(objects['claim'], max_bytes=16384)
        assignment.check_claim(claim)
        selected = strict_loads(objects['selected'], max_bytes=self.LIMITS['selected'])
        bundle = checked_bundle(strict_loads(objects['bundle'], max_bytes=4096))
        need(all(canonical_bytes(value, max_bytes=self.LIMITS[kind]) == objects[kind]
                 for kind, value in (('claim', claim), ('selected', selected), ('bundle', bundle)))
             and assignment.selector_sha256 == mark['selector']
             and type(envelope['attempt_count']) is int and envelope['attempt_count'] == claim['attempt_count'],
             'TERMINAL_RESTORED_INPUT_CHANGED')
        r.check()
        return dict(watermark=mark, envelope=envelope, assignment=assignment,
                    claim=claim, selected=selected, bundle=bundle)

    def advance(self, watermark_raw, phase, *, evidence_raw=None, ack_attempt_id=None):
        acquired = self._lock.acquire(blocking=False)
        try:
            need(acquired and not self._failed, 'TERMINAL_SESSION_UNKNOWN')
            saved = self.load(watermark_raw)
            mark = saved['watermark']
            need(mark['phase'] not in ('UNKNOWN', 'CLOSED'), 'TERMINAL_UNKNOWN_NO_RETRY')
            next_phase = self.PHASES[self.PHASES.index(mark['phase']) + 1]
            need(phase in (next_phase, 'UNKNOWN'), 'TERMINAL_PHASE_ORDER')
            if phase == 'ACK_ATTEMPTED':
                need(is_pin(ack_attempt_id) and mark['ack_attempt_id'] is None, 'TERMINAL_ACK_ID')
            else:
                need(ack_attempt_id is None, 'TERMINAL_ACK_REPLAY_NO_RETRY')
                ack_attempt_id = mark['ack_attempt_id']
            need(phase not in ('READ_VERIFIED', 'ACKED') or evidence_raw is not None,
                 'TERMINAL_EVIDENCE_REQUIRED')
            return self._step(saved['envelope'], mark['prepared_sha256'], mark, phase,
                              evidence_raw, ack_attempt_id)
        except BaseException:
            self._failed = True
            raise
        finally:
            if acquired:
                self._lock.release()
