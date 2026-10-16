"""One explicit 0061 approval, from a separate restricted approver process.

The claim/ready files are evidence and timing hints, not authority. The SQL
function checks the finalized subset and exact live lease under a row lock.
No worker, collector, owner, or broad writer DSN is accepted here.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from .private_one_job_entrypoint import _PIPELINE_WRITE_TABLES
from .private_one_job_operator import _read_local_json, _safe_path, _write_json_exclusive
from .private_result_handoff import PrivateOneJobConfigurationError


_KEYS = frozenset({
    'expected_database', 'expected_host', 'expected_port', 'expected_approver_login',
    'expected_worker_login', 'expected_batch_id', 'expected_job_id',
    'expected_source_file_id', 'expected_attempt_count', 'admission_id',
    'source_fingerprint', 'worker_key', 'claim_receipt_path',
    'approval_signal_path', 'approver_dsn_file', 'approval_reference',
})

# The remaining Drive readback and private stage/read may exceed 30 seconds.
# Nine minutes leaves six minutes below the worker's 900-second lease renewal.
# SQL caps the request to the earlier live lease/subset deadline and requires
# at least three minutes of work budget; this process cannot extend either.
_APPROVAL_VALIDITY_SECONDS = 540

_IDENTITY_SQL = """
SELECT session_user::text AS login, current_user::text AS active_role,
       current_database()::text AS database_name, clock_timestamp() AS server_now,
       pg_has_role(session_user,'video_subset_approver','MEMBER') AS approver_member,
       pg_has_role(session_user,'video_subset_owner','MEMBER') AS owner_member,
       pg_has_role(session_user,'video_subset_worker_v2','MEMBER') AS worker_member,
       pg_has_role(session_user,'video_subset_collector','MEMBER') AS collector_member,
       pg_has_role(session_user,'video_subset_admitter','MEMBER') AS admitter_member,
       pg_has_role(session_user,'bridge_school_worker','MEMBER') AS broad_worker_member,
       pg_has_role(session_user,'bridge_school_app','MEMBER') AS app_member,
       (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
          FROM pg_catalog.pg_roles WHERE rolname=session_user) AS elevated,
       has_schema_privilege(session_user,'video_queue','USAGE') AS queue_usage,
       has_function_privilege(session_user,
         'video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text)',
         'EXECUTE') AS approve_execute,
       has_function_privilege(session_user,
         'video_queue.stage_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,text,text,text)',
         'EXECUTE') AS stage_execute,
       EXISTS (SELECT 1 FROM unnest(%s::text[]) AS t(name)
                WHERE has_table_privilege(session_user,t.name,'INSERT,UPDATE,DELETE')
                   OR has_any_column_privilege(session_user,t.name,'INSERT,UPDATE')) AS pipeline_dml
"""


def _uuid(value: object) -> str:
    try:
        result = str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_BINDING_INVALID') from exc
    if result != value:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_BINDING_INVALID')
    return result


def _read_approver_dsn(path: Path) -> str:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                or info.st_mode & 0o077
                or (os.name != 'nt' and info.st_uid != os.geteuid())):
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_DSN_FILE_UNSAFE')
        dsn = path.read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_DSN_FILE_UNAVAILABLE') from exc
    if not dsn:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_DSN_FILE_UNSAFE')
    return dsn


def _validate_binding(binding: dict) -> None:
    if set(binding) != _KEYS:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_BINDING_INVALID')
    for key in ('expected_batch_id', 'expected_job_id', 'admission_id'):
        _uuid(binding[key])
    if (type(binding['expected_attempt_count']) is not int
            or binding['expected_attempt_count'] not in (1, 2, 3)
            or not isinstance(binding['source_fingerprint'], str)
            or not re.fullmatch('[0-9a-f]{64}', binding['source_fingerprint'])
            or not isinstance(binding['worker_key'], str)
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,159}',
                                binding['worker_key'])
            or not isinstance(binding['expected_source_file_id'], str)
            or not re.fullmatch('[A-Za-z0-9_-]{10,200}',
                                binding['expected_source_file_id'])
            or not isinstance(binding['approval_reference'], str)
            or not 1 <= len(binding['approval_reference'].strip()) <= 500
            or not isinstance(binding['expected_host'], str)
            or not binding['expected_host']
            or type(binding['expected_port']) is not int
            or not 1 <= binding['expected_port'] <= 65535
            or not isinstance(binding['expected_database'], str)
            or not binding['expected_database']
            or not isinstance(binding['expected_approver_login'], str)
            or not binding['expected_approver_login']
            or not isinstance(binding['expected_worker_login'], str)
            or not binding['expected_worker_login']
            or binding['expected_approver_login'] == binding['expected_worker_login']):
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_BINDING_INVALID')
    for key in ('claim_receipt_path', 'approval_signal_path', 'approver_dsn_file'):
        _safe_path(binding[key])


def _assert_target(dsn: str, binding: dict) -> None:
    from psycopg.conninfo import conninfo_to_dict
    try:
        parts = conninfo_to_dict(dsn)
    except Exception as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_TARGET_INVALID') from exc
    host = (parts.get('host') or '').lower()
    if (host != binding['expected_host'].lower()
            or ',' in host or '/' in host or parts.get('hostaddr')
            or parts.get('service')
            or parts.get('dbname') != binding['expected_database']
            or str(parts.get('port') or '5432') != str(binding['expected_port'])
            or not parts.get('user')):
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_TARGET_INVALID')
    if host not in ('localhost', '127.0.0.1', '::1') and (
            parts.get('sslmode') not in ('require', 'verify-full')
            or parts.get('channel_binding') != 'require'):
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_TLS_REQUIRED')


def _assert_approver(connection, binding: dict):
    from psycopg.rows import dict_row
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_IDENTITY_SQL, (list(_PIPELINE_WRITE_TABLES),))
        row = cursor.fetchone()
    if (row is None or row['login'] != binding['expected_approver_login']
            or row['active_role'] != row['login']
            or row['database_name'] != binding['expected_database']
            or row['approver_member'] is not True
            or row['queue_usage'] is not True
            or row['approve_execute'] is not True
            or any(row[name] is not False for name in (
                'owner_member', 'worker_member', 'collector_member',
                'admitter_member', 'broad_worker_member', 'app_member',
                'elevated', 'stage_execute', 'pipeline_dml'))):
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_IDENTITY_OR_ACL_INVALID')
    return row['server_now']


def _exact_receipt(binding: dict, receipt: dict, *, state: bool) -> dict:
    expected = dict(admission_id=binding['admission_id'],
                    job_id=binding['expected_job_id'],
                    batch_id=binding['expected_batch_id'],
                    worker_key=binding['worker_key'],
                    attempt_count=binding['expected_attempt_count'],
                    source_file_id=binding['expected_source_file_id'],
                    source_fingerprint=binding['source_fingerprint'])
    if (not isinstance(receipt, dict)
            or set(receipt) != set(expected) | {'lease_token'} |
               ({'claim_state'} if state else set())
            or any(receipt[key] != value for key, value in expected.items())
            or (state and receipt['claim_state'] != 'COMMIT_ACKNOWLEDGED')):
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_RECEIPT_NOT_EXACT')
    _uuid(receipt['lease_token'])
    return receipt


def approve_once(binding_path: str) -> dict[str, str]:
    """Issue exactly one short-lived approval and signal only after DB COMMIT."""
    binding = _read_local_json(_safe_path(binding_path))
    if binding is None:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_BINDING_INVALID')
    _validate_binding(binding)
    if any(os.getenv(name, '').strip() for name in (
            'BRIDGE_VIDEO_QUEUE_DATABASE_URL', 'BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE',
            'BRIDGE_WORKER_DATABASE_URL', 'BRIDGE_APP_DATABASE_URL')):
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_WORKER_DSN_FORBIDDEN')
    claim_path = _safe_path(binding['claim_receipt_path'])
    signal_path = _safe_path(binding['approval_signal_path'])
    ready_path = signal_path.with_name(signal_path.name + '.ready')
    if signal_path.exists() or len({claim_path, signal_path, ready_path}) != 3:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_SIGNAL_EXISTS')
    receipt = _exact_receipt(binding, _read_local_json(claim_path), state=True)
    ready = _exact_receipt(binding, _read_local_json(ready_path), state=False)
    if ready != {key: value for key, value in receipt.items() if key != 'claim_state'}:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_READY_NOT_EXACT')
    age = time.time() - ready_path.stat().st_mtime
    if not -5 <= age <= 60:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_READY_STALE')
    dsn = _read_approver_dsn(_safe_path(binding['approver_dsn_file']))
    _assert_target(dsn, binding)
    parent = signal_path.parent
    try:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or (os.name != 'nt' and (
                    info.st_uid != os.geteuid() or info.st_mode & 0o022))):
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_SIGNAL_DIRECTORY_UNSAFE')
        probe = parent / f'.one-job-approver-probe-{uuid4().hex}'
        _write_json_exclusive(probe, {'preflight': True})
        probe.unlink()
    except OSError as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_SIGNAL_DIRECTORY_UNAVAILABLE') from exc
    from psycopg import connect
    from psycopg.rows import dict_row
    approval_id = str(uuid4())
    with connect(dsn, connect_timeout=8, row_factory=dict_row,
                 application_name='universal-video-one-job-approver',
                 options='-c statement_timeout=10000 -c lock_timeout=2000') as connection:
        server_now = _assert_approver(connection, binding)
        # The readiness window remains short even if connection setup stalls.
        if not -5 <= time.time() - ready_path.stat().st_mtime <= 60:
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_READY_STALE')
        expires_at = server_now + timedelta(seconds=_APPROVAL_VALIDITY_SECONDS)
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT video_queue.approve_one_job_staging_attempt_v1('
                '%s,%s,%s,%s,%s,%s,%s,%s,%s) AS admission_id',
                (binding['admission_id'], approval_id, binding['expected_job_id'],
                 receipt['lease_token'], binding['worker_key'],
                 binding['expected_attempt_count'], binding['source_fingerprint'],
                 expires_at, binding['approval_reference']))
            row = cursor.fetchone()
        if row is None or str(row['admission_id']) != binding['admission_id']:
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_SQL_MISMATCH')
    signal = dict(admission_id=binding['admission_id'],
                  job_id=binding['expected_job_id'],
                  lease_token=receipt['lease_token'], worker_key=binding['worker_key'],
                  attempt_count=binding['expected_attempt_count'],
                  source_fingerprint=binding['source_fingerprint'],
                  lease_approval_id=approval_id)
    _write_json_exclusive(signal_path, signal)
    return dict(status='APPROVED_ONCE', admission_id=binding['admission_id'],
                job_id=binding['expected_job_id'], lease_approval_id=approval_id)


def main() -> None:
    if len(sys.argv) != 2:
        raise PrivateOneJobConfigurationError('ONE_JOB_APPROVER_ARGUMENT_INVALID')
    print(json.dumps(approve_once(sys.argv[1]), sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
