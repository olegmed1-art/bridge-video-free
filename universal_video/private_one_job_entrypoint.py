"""Explicit queue-only entrypoint for one already-claimed private result job.

There is no CLI, environment DSN fallback, claim selection, or resident hook.
The caller supplies the existing queue DSN and exact claim after an external
approver has handled the 0061 lease decision. This module never imports the
broad public-result writer.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from .private_result_handoff import (
    PrivateOneJobConfigurationError, PrivateResultStagingHandoff,
)
from .subset_worker import SubsetWorkerAdapter


_PIPELINE_WRITE_TABLES = (
    'public.changeset', 'public.ingestion_run', 'public.learning_interaction',
    'public.episode', 'public.decision', 'public.asset', 'public.media_asset',
    'public.asset_location', 'public.source', 'public.source_asset',
    'public.ingestion_item', 'public.transcript', 'public.transcript_segment',
    'public.analysis_run', 'public.analysis_run_input', 'public.evidence',
    'public.evidence_link', 'public.artifact', 'public.artifact_version',
    'public.analysis_run_output', 'public.domain_event', 'public.outbox_message',
    'public.source_identity', 'public.run_checkpoint_event',
    'public.analysis_candidate', 'video_queue.one_job_private_result_v1',
    'video_queue.one_job_private_result_attempt_v1',
)

_QUEUE_IDENTITY_COLUMNS = (
    'session_login', 'database_name', 'is_v2_member', 'is_worker_member',
    'is_app_member', 'is_approver_member', 'has_elevated_role',
    'can_use_queue_schema', 'can_stage_private', 'can_read_private',
    'can_finish_private', 'can_finish_old', 'can_assert_old',
    'can_attest_old', 'has_pipeline_dml', 'active_role_matches_login',
)

_QUEUE_IDENTITY_SQL = """
SELECT session_user::text AS session_login,
       current_database()::text AS database_name,
       pg_has_role(session_user,'video_subset_worker_v2','MEMBER') AS is_v2_member,
       pg_has_role(session_user,'bridge_school_worker','MEMBER') AS is_worker_member,
       pg_has_role(session_user,'bridge_school_app','MEMBER') AS is_app_member,
       pg_has_role(session_user,'video_subset_approver','MEMBER') AS is_approver_member,
       (SELECT rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication
          FROM pg_catalog.pg_roles WHERE rolname=session_user) AS has_elevated_role,
       has_schema_privilege(session_user,'video_queue','USAGE') AS can_use_queue_schema,
       has_function_privilege(session_user,
           'video_queue.stage_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,text,text,text)',
           'EXECUTE') AS can_stage_private,
       has_function_privilege(session_user,
           'video_queue.read_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid)',
           'EXECUTE') AS can_read_private,
       has_function_privilege(session_user,
           'video_queue.finish_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)',
           'EXECUTE') AS can_finish_private,
       has_function_privilege(session_user,
           'video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)',
           'EXECUTE') AS can_finish_old,
       has_function_privilege(session_user,
           'video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text)',
           'EXECUTE') AS can_assert_old,
       has_function_privilege(session_user,
           'video_queue.attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)',
           'EXECUTE') AS can_attest_old,
       EXISTS (
           SELECT 1 FROM unnest(%s::text[]) AS q_table(table_name)
            WHERE has_table_privilege(session_user,q_table.table_name,'INSERT,UPDATE,DELETE')
               OR has_any_column_privilege(session_user,q_table.table_name,'INSERT,UPDATE')
       ) AS has_pipeline_dml,
       current_user::text = session_user::text AS active_role_matches_login
"""


def _connect_queue(queue_dsn: str):
    from psycopg import connect
    return connect(queue_dsn, connect_timeout=8,
                   application_name='universal-video-private-one-job',
                   options='-c statement_timeout=30000 -c lock_timeout=2000')


def _process_private_claim(queue_dsn, claim, worker_key, *, subset_adapter,
                           staging_handoff):
    from .neon_worker import process_claim
    return process_claim(queue_dsn, claim, worker_key,
                         subset_adapter=subset_adapter,
                         staging_handoff=staging_handoff)


def _assert_queue_only(connection, *, expected_login: str,
                       expected_database: str) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(_QUEUE_IDENTITY_SQL, (list(_PIPELINE_WRITE_TABLES),))
            row = cursor.fetchone()
    except Exception as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_PREFLIGHT_FAILED') from exc
    if isinstance(row, Mapping):
        values = row if set(row) == set(_QUEUE_IDENTITY_COLUMNS) else None
    elif isinstance(row, (tuple, list)) and len(row) == len(_QUEUE_IDENTITY_COLUMNS):
        values = dict(zip(_QUEUE_IDENTITY_COLUMNS, row))
    else:
        values = None
    if (values is None or values['session_login'] != expected_login
            or values['database_name'] != expected_database
            or values['is_v2_member'] is not True
            or any(values[name] is not False for name in (
                'is_worker_member', 'is_app_member', 'is_approver_member',
                'has_elevated_role'))
            or any(values[name] is not True for name in (
                'can_use_queue_schema', 'can_stage_private',
                'can_read_private', 'can_finish_private'))
            or any(values[name] is not False for name in (
                'can_finish_old', 'can_assert_old', 'can_attest_old',
                'has_pipeline_dml'))
            or values['active_role_matches_login'] is not True):
        raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID')


def _exact_uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_EXACT_ID_INVALID') from exc


def run_private_one_job(*, queue_dsn: str, expected_database: str,
                        expected_login: str, claim: Mapping[str, Any],
                        expected_batch_id: str, expected_job_id: str,
                        expected_lease_token: str, expected_attempt_count: int,
                        expected_source_file_id: str, worker_key: str,
                        admission_id: str, source_fingerprint: str,
                        subset_adapter: SubsetWorkerAdapter) -> dict[str, Any]:
    """Run only the explicitly selected lease with the private 0062 route.

    The claim is already leased. This entrypoint neither claims another job
    nor falls back to the broad writer, old public-candidate path, or resident.
    Every private queue connection repeats the ACL identity check at use.
    """
    if (not isinstance(queue_dsn, str) or not queue_dsn.strip()
            or not isinstance(expected_database, str) or not expected_database
            or not isinstance(expected_login, str) or not expected_login
            or not isinstance(worker_key, str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', worker_key)
            or not isinstance(claim, Mapping)
            or type(subset_adapter) is not SubsetWorkerAdapter
            or type(expected_attempt_count) is not int
            or not 1 <= expected_attempt_count <= 3
            or not isinstance(expected_source_file_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', expected_source_file_id)
            or not isinstance(source_fingerprint, str)
            or not re.fullmatch(r'[0-9a-f]{64}', source_fingerprint)
            or os.getenv('BRIDGE_WORKER_DATABASE_URL', '').strip()):
        raise PrivateOneJobConfigurationError('ONE_JOB_PRIVATE_CONFIG_INVALID')
    batch_id = _exact_uuid(expected_batch_id)
    job_id = _exact_uuid(expected_job_id)
    lease_token = _exact_uuid(expected_lease_token)
    admission_id = _exact_uuid(admission_id)
    if (claim.get('batch_id') != batch_id or claim.get('job_id') != job_id
            or claim.get('lease_token') != lease_token
            or claim.get('attempt_count') != expected_attempt_count
            or claim.get('source_file_id') != expected_source_file_id):
        raise PrivateOneJobConfigurationError('ONE_JOB_CLAIM_NOT_EXACT')

    @contextmanager
    def open_checked_connection():
        try:
            connection = _connect_queue(queue_dsn)
        except Exception as exc:
            raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_CONNECTION_FAILED') from exc
        with connection:
            _assert_queue_only(connection, expected_login=expected_login,
                               expected_database=expected_database)
            yield connection

    # Check once before Drive processing, then at every queue SQL connection.
    with open_checked_connection():
        pass
    def validate_queue_connection(connection) -> None:
        _assert_queue_only(connection, expected_login=expected_login,
                           expected_database=expected_database)

    checked_adapter = SubsetWorkerAdapter(
        subset_adapter.observation_provider,
        connection_validator=validate_queue_connection)
    handoff = PrivateResultStagingHandoff(
        claim=claim, worker_key=worker_key, admission_id=admission_id,
        source_fingerprint=source_fingerprint,
        open_connection=open_checked_connection)
    return _process_private_claim(queue_dsn, claim, worker_key,
                                  subset_adapter=checked_adapter,
                                  staging_handoff=handoff)


__all__ = ['run_private_one_job', 'PrivateOneJobConfigurationError']
