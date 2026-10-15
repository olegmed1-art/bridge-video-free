"""Explicit one-shot resident launcher; local files are signals, not approval.

An independent approver reads the claim receipt, approves that live attempt
through the 0061 SQL function using a separate login, then writes the exact
signal file. The 0062 staging function independently enforces the approval.
Neither file grants a database capability.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .private_result_handoff import PrivateOneJobConfigurationError


_BINDING_KEYS = frozenset({
    'expected_database', 'expected_login', 'expected_batch_id',
    'expected_job_id', 'expected_source_file_id', 'expected_attempt_count',
    'admission_id', 'source_fingerprint', 'worker_key',
    'claim_receipt_path', 'approval_signal_path',
})


def _safe_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PrivateOneJobConfigurationError('ONE_JOB_LOCAL_PATH_INVALID')
    path = Path(value)
    if not path.is_absolute():
        raise PrivateOneJobConfigurationError('ONE_JOB_LOCAL_PATH_INVALID')
    return path


def _read_local_json(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 8192
            or info.st_mode & 0o007 or info.st_mode & 0o022):
        raise PrivateOneJobConfigurationError('ONE_JOB_LOCAL_FILE_UNSAFE')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_LOCAL_FILE_INVALID') from exc
    if not isinstance(data, dict):
        raise PrivateOneJobConfigurationError('ONE_JOB_LOCAL_FILE_INVALID')
    return data


def _receipt(claim: Mapping[str, Any], worker_key: str,
             admission_id: str, source_fingerprint: str) -> dict[str, Any]:
    return dict(admission_id=admission_id, job_id=str(claim['job_id']),
                   batch_id=str(claim['batch_id']),
                   lease_token=str(claim['lease_token']), worker_key=worker_key,
                   attempt_count=claim['attempt_count'],
                   source_file_id=claim['source_file_id'],
                   source_fingerprint=source_fingerprint)


def _fsync_parent(path: Path) -> None:
    if os.name == 'nt':
        return
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish a complete, durable JSON file without exposing partial bytes."""
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == 'nt':
            # Windows rename fails when the destination exists. Hard-link
            # creation is denied by some restricted Windows test sandboxes.
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
        _fsync_parent(path)
    except OSError as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_WRITE_FAILED') from exc
    finally:
        temporary.unlink(missing_ok=True)


def _replace_json(path: Path, payload: Mapping[str, Any]) -> None:
    if _read_local_json(path) is None:
        raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_MISSING')
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_parent(path)
    except OSError as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_WRITE_FAILED') from exc
    finally:
        temporary.unlink(missing_ok=True)


def write_claim_receipt(path: Path, claim: Mapping[str, Any], worker_key: str,
                        admission_id: str, source_fingerprint: str) -> None:
    """Publish one complete readiness receipt atomically."""
    _write_json_exclusive(path, _receipt(claim, worker_key, admission_id,
                                         source_fingerprint))


class ClaimReceiptJournal:
    """Durable claim intent before COMMIT; ambiguous outcomes require readback."""

    def __init__(self, path: Path, binding: Mapping[str, Any]):
        self.path = path
        self.binding = dict(binding)
        self.claim: dict[str, Any] | None = None

    def preflight(self) -> None:
        """Check the journal directory without creating a one-shot state."""
        try:
            parent = self.path.parent
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode)
                    or (os.name != 'nt' and (
                        info.st_uid != os.geteuid() or info.st_mode & 0o022))):
                raise PrivateOneJobConfigurationError('ONE_JOB_HANDOFF_DIRECTORY_UNSAFE')
        except OSError as exc:
            raise PrivateOneJobConfigurationError('ONE_JOB_HANDOFF_DIRECTORY_UNAVAILABLE') from exc

    def reserve(self) -> None:
        try:
            self.preflight()
            _write_json_exclusive(self.path, dict(
                claim_state='RESERVED', admission_id=self.binding['admission_id'],
                job_id=self.binding['expected_job_id'],
                batch_id=self.binding['expected_batch_id']))
        except OSError as exc:
            raise PrivateOneJobConfigurationError('ONE_JOB_HANDOFF_DIRECTORY_UNAVAILABLE') from exc

    def publish_precommit(self, claim: Mapping[str, Any], worker_key: str,
                          admission_id: str, source_fingerprint: str) -> None:
        expected = dict(claim_state='RESERVED',
                        admission_id=self.binding['admission_id'],
                        job_id=self.binding['expected_job_id'],
                        batch_id=self.binding['expected_batch_id'])
        if _read_local_json(self.path) != expected:
            raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_MISMATCH')
        self.claim = _receipt(claim, worker_key, admission_id, source_fingerprint)
        _replace_json(self.path, dict(self.claim,
                                      claim_state='PRECOMMIT_UNCONFIRMED'))

    def acknowledge_commit(self, claim: Mapping[str, Any]) -> None:
        if (self.claim is None or
                str(claim['lease_token']) != self.claim['lease_token']):
            raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_MISMATCH')
        if _read_local_json(self.path) != dict(
                self.claim, claim_state='PRECOMMIT_UNCONFIRMED'):
            raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_RECEIPT_MISMATCH')
        _replace_json(self.path, dict(self.claim,
                                      claim_state='COMMIT_ACKNOWLEDGED'))


def wait_for_approval_signal(path: Path, claim: Mapping[str, Any],
                             worker_key: str, admission_id: str,
                             source_fingerprint: str, *,
                             timeout_seconds: int = 1800) -> Mapping[str, Any]:
    """Wait for a hint from the separate approver; SQL remains authoritative."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVAL_WAIT_INVALID')
    # The approver waits for this marker before issuing its short-lived SQL
    # approval. It is a readiness notice, never an authorization receipt.
    ready_path = path.with_name(path.name + '.ready')
    write_claim_receipt(ready_path, claim, worker_key,
                        admission_id, source_fingerprint)
    deadline = time.monotonic() + timeout_seconds
    while True:
        signal = _read_local_json(path)
        if signal is not None:
            # The caller validates the complete exact signal before staging.
            return signal
        if time.monotonic() >= deadline:
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVAL_SIGNAL_TIMEOUT')
        time.sleep(min(2, max(0.1, deadline - time.monotonic())))


def run_from_binding_file(binding_path: str) -> dict[str, Any]:
    """Use only the existing restricted queue and collector secret facilities."""
    binding = _read_local_json(_safe_path(binding_path))
    if binding is None or set(binding) != _BINDING_KEYS:
        raise PrivateOneJobConfigurationError('ONE_JOB_BINDING_INVALID')
    claim_path = _safe_path(binding.pop('claim_receipt_path'))
    signal_path = _safe_path(binding.pop('approval_signal_path'))
    ready_path = signal_path.with_name(signal_path.name + '.ready')
    if (len({claim_path, signal_path, ready_path}) != 3
            or claim_path.exists() or signal_path.exists() or ready_path.exists()):
        raise PrivateOneJobConfigurationError('ONE_JOB_LOCAL_FILE_ALREADY_EXISTS')
    if os.getenv('BRIDGE_WORKER_DATABASE_URL', '').strip():
        raise PrivateOneJobConfigurationError('ONE_JOB_BROAD_WRITER_FORBIDDEN')
    queue_dsn = os.getenv('BRIDGE_VIDEO_QUEUE_DATABASE_URL', '').strip()
    if not queue_dsn:
        file_name = os.getenv('BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE', '').strip()
        if not file_name:
            raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_DSN_REQUIRED')
        from .video_queue import _read_dsn_file
        queue_dsn = _read_dsn_file(file_name)
    from .spool_worker import process_private_one_job_once

    journal = ClaimReceiptJournal(claim_path, binding)
    journal.preflight()

    return process_private_one_job_once(
        queue_dsn=queue_dsn, **binding,
        claim_reserver=journal.reserve,
        claim_publisher=journal.publish_precommit,
        claim_committer=journal.acknowledge_commit,
        approval_waiter=lambda claim, key, admission, fingerprint:
            wait_for_approval_signal(signal_path, claim, key, admission,
                                     fingerprint))


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != '--private-one-job':
        raise PrivateOneJobConfigurationError('ONE_JOB_OPERATOR_ARGUMENT_INVALID')
    result = run_from_binding_file(sys.argv[2])
    # The terminal status contains no lease token, DSN, or private bundle.
    print(json.dumps({key: result[key] for key in
                      ('job_status', 'batch_status', 'released_jobs')},
                     sort_keys=True), flush=True)


__all__ = ['run_from_binding_file', 'wait_for_approval_signal',
           'write_claim_receipt', 'ClaimReceiptJournal']
