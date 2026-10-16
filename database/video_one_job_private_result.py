"""Opt-in one-job private result writer; no public-table DML or resident hook.

The supplied connection must authenticate as the existing 0060 queue worker.
0062 is an unapplied SQL proposal. A separate connection confirms commit before
the caller can use a private receipt in the versioned finish function.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5


def _scope(admission: Mapping[str, object]) -> tuple[str, str, str, str, int, str]:
    try:
        admission_id = str(UUID(str(admission['admission_id'])))
        job_id = str(UUID(str(admission['job_id'])))
        lease_token = str(UUID(str(admission['lease_token'])))
        worker_key = admission['worker_key']
        attempt_count = admission['attempt_count']
        source_fingerprint = admission['source_fingerprint']
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('ONE_JOB_PRIVATE_SCOPE_INVALID') from exc
    if (not isinstance(worker_key, str) or not worker_key
            or type(attempt_count) is not int or not 1 <= attempt_count <= 3
            or not isinstance(source_fingerprint, str)
            or len(source_fingerprint) != 64
            or any(c not in '0123456789abcdef' for c in source_fingerprint)):
        raise ValueError('ONE_JOB_PRIVATE_SCOPE_INVALID')
    return admission_id, job_id, lease_token, worker_key, attempt_count, source_fingerprint


def _payload(master_json_text: str, ai_done_json_text: str,
             ai_done_drive_id: str) -> tuple[str, str]:
    if (not isinstance(master_json_text, str)
            or not isinstance(ai_done_json_text, str)
            or not isinstance(ai_done_drive_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', ai_done_drive_id)):
        raise ValueError('ONE_JOB_PRIVATE_MASTER_JSON_INVALID')
    raw = master_json_text.encode('utf-8')
    if not 2 <= len(raw) <= 33554432:
        raise ValueError('ONE_JOB_PRIVATE_MASTER_JSON_INVALID')
    try:
        master = json.loads(master_json_text)
        done = json.loads(ai_done_json_text)
        if not isinstance(master, dict) or not isinstance(done, dict):
            raise ValueError('ONE_JOB_PRIVATE_PAYLOAD_INVALID')
        json.dumps(done, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('ONE_JOB_PRIVATE_PAYLOAD_INVALID') from exc
    done_raw = ai_done_json_text.encode('utf-8')
    if not 2 <= len(done_raw) <= 4194304:
        raise ValueError('ONE_JOB_PRIVATE_PAYLOAD_INVALID')
    return hashlib.sha256(raw).hexdigest(), hashlib.sha256(done_raw).hexdigest()


def _private_load_done(token: str, stable_job_key: str, *, return_raw: bool,
                       output_folder_id: str):
    """Read AI_DONE bytes from the exact routed folder, without DB writes."""
    import run_drive_3_1_free as io

    if not re.fullmatch(r'[0-9a-f]{32}', stable_job_key):
        raise ValueError('ONE_JOB_PRIVATE_STABLE_KEY_INVALID')
    name = f'AI_DONE_{stable_job_key}.json'
    query = f"trashed=false and name='{name}' and '{output_folder_id}' in parents"
    candidates = io.search(token, query)
    candidates.sort(key=lambda item: item.get('modifiedTime') or '', reverse=True)
    for candidate in candidates:
        drive_id = str(candidate.get('id') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', drive_id):
            continue
        with tempfile.TemporaryDirectory(prefix='one-job-private-done-') as td:
            path = Path(td) / name
            io.download(token, drive_id, path)
            try:
                raw_text = path.read_bytes().decode('utf-8')
                done = json.loads(raw_text)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        if (isinstance(done, dict) and done.get('job_id') == stable_job_key
                and done.get('status') == 'AI_DONE'):
            return (done, drive_id, raw_text) if return_raw else done
    raise ValueError('ONE_JOB_PRIVATE_AI_DONE_NOT_FOUND')


def _private_load_embedded_master(token: str, done: dict, *, return_raw: bool):
    """Verify PDF bytes and embedded JSON hash before submitting the bundle."""
    import fitz
    import run_drive_3_1_free as io

    pdf = done.get('masterPdf') or {}
    drive_id = str(pdf.get('driveId') or '')
    pdf_sha = str(pdf.get('sha256') or '').lower()
    master_sha = str(pdf.get('masterJsonSha256') or '').lower()
    if (not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', drive_id)
            or not re.fullmatch(r'[0-9a-f]{64}', pdf_sha)
            or not re.fullmatch(r'[0-9a-f]{64}', master_sha)):
        raise ValueError('ONE_JOB_PRIVATE_PDF_IDENTITY_INVALID')
    with tempfile.TemporaryDirectory(prefix='one-job-private-master-') as td:
        path = Path(td) / 'master.pdf'
        io.download(token, drive_id, path)
        if io.sha(path).lower() != pdf_sha:
            raise ValueError('ONE_JOB_PRIVATE_PDF_SHA_MISMATCH')
        document = fitz.open(path)
        try:
            if 'master_analysis.json' not in document.embfile_names():
                raise ValueError('ONE_JOB_PRIVATE_MASTER_NOT_EMBEDDED')
            raw = document.embfile_get('master_analysis.json')
        finally:
            document.close()
    if hashlib.sha256(raw).hexdigest() != master_sha:
        raise ValueError('ONE_JOB_PRIVATE_MASTER_SHA_MISMATCH')
    try:
        raw_text = raw.decode('utf-8')
        master = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('ONE_JOB_PRIVATE_MASTER_JSON_INVALID') from exc
    return (master, raw_text) if return_raw else master


def stage_private_result_on_connection(connection, *, admission, master_json_text,
                                       ai_done_json_text, ai_done_drive_id,
                                       receipt_id=None):
    """Commit one immutable private bundle through the exact SQL lease gate."""
    scope = _scope(admission)
    master_sha, done_sha = _payload(master_json_text, ai_done_json_text,
                                    ai_done_drive_id)
    expected_receipt = str(uuid5(NAMESPACE_URL,
        'bridge-school:one-job-private-result:' + ':'.join(
            (scope[0], scope[1], master_sha, ai_done_drive_id, done_sha))))
    if receipt_id is not None and str(UUID(str(receipt_id))) != expected_receipt:
        raise ValueError('ONE_JOB_PRIVATE_RECEIPT_ID_CHANGED')
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT video_queue.stage_one_job_private_result_v1('
                '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (*scope, expected_receipt, master_json_text,
                 ai_done_drive_id, ai_done_json_text),
            )
            row = cursor.fetchone()
            result = row[0] if row else None
            if (not isinstance(result, dict)
                    or result.get('status') != 'PRIVATE_RESULT_STAGED'
                    or result.get('receipt_id') != expected_receipt
                    or result.get('master_json_sha256') != master_sha
                    or result.get('ai_done_sha256') != done_sha
                    or not isinstance(result.get('bundle_sha256'), str)
                    or len(result['bundle_sha256']) != 64):
                raise ValueError('ONE_JOB_PRIVATE_STAGE_NOT_CONFIRMED')
    return result


def read_committed_private_result_on_connection(connection, *, admission,
                                                receipt_id, bundle_sha256):
    """Check the committed row from a new connection under the same live lease."""
    scope = _scope(admission)
    receipt = str(UUID(str(receipt_id)))
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT video_queue.read_one_job_private_result_v1('
            '%s,%s,%s,%s,%s,%s,%s)', (*scope, receipt),
        )
        row = cursor.fetchone()
    result = row[0] if row else None
    if (not isinstance(result, dict)
            or result.get('status') != 'COMMITTED_PRIVATE_RESULT'
            or result.get('receipt_id') != receipt
            or result.get('bundle_sha256') != bundle_sha256):
        raise ValueError('ONE_JOB_PRIVATE_COMMITTED_READBACK_MISSING')
    return result


def stage_and_read_private_result(open_connection: Callable, *, admission,
                                  master_json_text, ai_done_json_text,
                                  ai_done_drive_id):
    """Two physical connections; stage commit must precede durable readback."""
    with open_connection() as writer:
        staged = stage_private_result_on_connection(
            writer, admission=admission, master_json_text=master_json_text,
            ai_done_json_text=ai_done_json_text,
            ai_done_drive_id=ai_done_drive_id)
    with open_connection() as reader:
        if reader is writer:
            raise ValueError('ONE_JOB_PRIVATE_SEPARATE_READBACK_REQUIRED')
        read_committed_private_result_on_connection(
            reader, admission=admission, receipt_id=staged['receipt_id'],
            bundle_sha256=staged['bundle_sha256'])
    return staged


def stage_completed_drive_result_private(open_connection: Callable, *, token,
                                         admission, stable_job_key,
                                         algorithm_revision, output_folder_id,
                                         load_done=None,
                                         load_embedded_master=None):
    """Read the same verified Drive artifacts, then use only the private gate.

    This opt-in helper never calls persist_video_result, source identity,
    checkpoint, candidate, or outbox writers. The resident still uses its
    original path until a separately reviewed cutover is authorized.
    """
    if (not isinstance(output_folder_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', output_folder_id)):
        raise ValueError('ONE_JOB_PRIVATE_OUTPUT_FOLDER_REQUIRED')
    load_done = _private_load_done if load_done is None else load_done
    load_embedded_master = (_private_load_embedded_master if load_embedded_master is None
                            else load_embedded_master)
    done, done_drive_id, done_raw_text = load_done(
        token, stable_job_key, return_raw=True,
        output_folder_id=output_folder_id)
    master, raw_text = load_embedded_master(token, done, return_raw=True)
    if (not isinstance(master, dict) or not isinstance(done, dict)
            or master.get('job_id') != stable_job_key
            or done.get('job_id') != stable_job_key
            or master.get('algorithmRevision') != algorithm_revision
            or done.get('algorithmRevision') != algorithm_revision):
        raise ValueError('ONE_JOB_PRIVATE_DRIVE_RESULT_MISMATCH')
    return stage_and_read_private_result(open_connection, admission=admission,
        master_json_text=raw_text, ai_done_json_text=done_raw_text,
        ai_done_drive_id=done_drive_id)


__all__ = ['stage_private_result_on_connection',
           'read_committed_private_result_on_connection',
           'stage_and_read_private_result', 'stage_completed_drive_result_private']
