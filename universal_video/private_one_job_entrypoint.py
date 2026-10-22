"""Explicit queue-only entrypoint for one already-claimed private result job.

There is no CLI, environment DSN fallback or automatic resident polling.
An explicit resident one-shot path may claim the exact selected job, then
wait for a separate 0061 approver before private staging. This module never
imports the broad public-result writer.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Callable
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

_CLAIM_COLUMNS = (
    'job_id', 'batch_id', 'lease_token', 'sequence', 'source_folder_id',
    'output_folder_id', 'work_folder_id', 'processing_profile',
    'algorithm_revision', 'source_file_id', 'source_name', 'source_mime_type',
    'source_size_bytes', 'source_checksum', 'stable_job_key', 'is_canary',
    'attempt_count',
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

# This is a read-only capability check, not an ACL installer or issuer. The
# enrolled caller must have the v2 API and must NOT retain the v1 finish bypass.
_POST_FINISH_IDENTITY_COLUMNS = (
    'can_finish_v2', 'can_check_assignment', 'can_check_stage_assignment',
    'can_read_terminal', 'can_assert_read_authority', 'can_finish_subset',
    'has_terminal_dml',
)
_POST_FINISH_WRITE_TABLES = _PIPELINE_WRITE_TABLES + (
    'video_queue.job', 'video_queue.batch', 'video_queue.job_event',
    'video_queue.terminal_assignment_v1', 'video_queue.terminal_read_grant_v1',
)
_POST_FINISH_IDENTITY_SQL = """
SELECT has_function_privilege(session_user,
           'video_queue.finish_one_job_private_result_v2(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb,text,text)',
           'EXECUTE') AS can_finish_v2,
       has_function_privilege(session_user,
           'video_queue.check_terminal_assignment_v1(text,text)',
           'EXECUTE') AS can_check_assignment,
       has_function_privilege(session_user,
           'video_queue.check_terminal_stage_assignment_v1(text,text)',
           'EXECUTE') AS can_check_stage_assignment,
       has_function_privilege(session_user,
           'video_queue.read_terminal_receipt_v1(text,text)',
           'EXECUTE') AS can_read_terminal,
       has_function_privilege(session_user,
           'video_queue.assert_terminal_read_authority_v1(text,text)',
           'EXECUTE') AS can_assert_read_authority,
       has_function_privilege(session_user,
           'video_queue.finish_subset_job(uuid,uuid,text,text,jsonb,uuid,text)',
           'EXECUTE') AS can_finish_subset,
       EXISTS (
           SELECT 1 FROM unnest(%s::text[]) AS q_table(table_name)
            WHERE has_table_privilege(session_user,q_table.table_name,'INSERT,UPDATE,DELETE,TRUNCATE')
               OR has_any_column_privilege(session_user,q_table.table_name,'INSERT,UPDATE')
       ) AS has_terminal_dml
"""


def _connect_queue(queue_dsn: str):
    from psycopg import connect
    return connect(queue_dsn, connect_timeout=8,
                   application_name='universal-video-private-one-job',
                   options='-c statement_timeout=30000 -c lock_timeout=2000')


def _process_private_claim(queue_dsn, claim, worker_key, *, subset_adapter,
                           staging_handoff, approval_waiter=None, processor=None,
                            private_failure_handler=None, native_budget=None):
    from .neon_worker import process_claim
    options = dict(subset_adapter=subset_adapter,
                   staging_handoff=staging_handoff,
                   private_approval_waiter=approval_waiter)
    if processor is not None:
        options['processor'] = processor
    if private_failure_handler is not None:
        options['private_failure_handler'] = private_failure_handler
    if native_budget is not None:
        options['native_budget'] = native_budget
    return process_claim(queue_dsn, claim, worker_key, **options)


def _assert_queue_only(connection, *, expected_login: str,
                       expected_database: str, post_finish: bool = False) -> None:
    if type(post_finish) is not bool:
        raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_POLICY_INVALID')
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
                'can_use_queue_schema', 'can_stage_private', 'can_read_private'))
            or values['can_finish_private'] is not (False if post_finish else True)
            or any(values[name] is not False for name in (
                'can_finish_old', 'can_assert_old', 'can_attest_old',
                'has_pipeline_dml'))
            or values['active_role_matches_login'] is not True):
        raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_IDENTITY_OR_ACL_INVALID')
    if post_finish:
        _assert_post_finish_capabilities(connection)


def _assert_post_finish_capabilities(connection) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(_POST_FINISH_IDENTITY_SQL, (list(_POST_FINISH_WRITE_TABLES),))
            row = cursor.fetchone()
    except Exception as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_POST_FINISH_PREFLIGHT_FAILED') from exc
    if isinstance(row, Mapping):
        values = row if set(row) == set(_POST_FINISH_IDENTITY_COLUMNS) else None
    elif isinstance(row, (tuple, list)) and len(row) == len(_POST_FINISH_IDENTITY_COLUMNS):
        values = dict(zip(_POST_FINISH_IDENTITY_COLUMNS, row))
    else:
        values = None
    if (values is None or values['has_terminal_dml'] is not False
            or values['can_finish_subset'] is not False
            or any(values[name] is not True for name in _POST_FINISH_IDENTITY_COLUMNS[:-2])):
        raise PrivateOneJobConfigurationError('ONE_JOB_POST_FINISH_IDENTITY_OR_ACL_INVALID')


@contextmanager
def _open_checked_queue(queue_dsn: str, *, expected_login: str,
                        expected_database: str, post_finish: bool = False):
    if type(post_finish) is not bool:
        raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_POLICY_INVALID')
    try:
        connection = _connect_queue(queue_dsn)
    except Exception as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_QUEUE_CONNECTION_FAILED') from exc
    with connection:
        _assert_queue_only(connection, expected_login=expected_login,
                           expected_database=expected_database,
                           **({'post_finish': True} if post_finish else {}))
        yield connection


def claim_exact_private_job(*, queue_dsn: str, expected_database: str,
                            expected_login: str, expected_batch_id: str,
                            expected_job_id: str, expected_source_file_id: str,
                            expected_attempt_count: int, worker_key: str,
                            processing_profile: str,
                            algorithm_revision: str,
                            claim_publisher: Callable[[Mapping[str, Any]], None],
                            post_finish: bool = False) -> dict[str, Any]:
    """Commit 0060's subset claim only when it returns the selected job.

    The SQL call and exact-result check share one transaction. A different
    job, absent row, malformed row or lost connection never commits a claim.
    The separate approver must authorize the resulting lease afterward.
    """
    if (not isinstance(queue_dsn, str) or not queue_dsn.strip()
            or not isinstance(expected_database, str) or not expected_database
            or not isinstance(expected_login, str) or not expected_login
            or not isinstance(worker_key, str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', worker_key)
            or not isinstance(expected_source_file_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', expected_source_file_id)
            or type(expected_attempt_count) is not int
            or not 1 <= expected_attempt_count <= 3
            or not isinstance(processing_profile, str) or not processing_profile
            or not isinstance(algorithm_revision, str) or not algorithm_revision
            or not callable(claim_publisher) or type(post_finish) is not bool
            or os.getenv('BRIDGE_WORKER_DATABASE_URL', '').strip()):
        raise PrivateOneJobConfigurationError('ONE_JOB_PRIVATE_CONFIG_INVALID')
    batch_id = _exact_uuid(expected_batch_id)
    job_id = _exact_uuid(expected_job_id)
    with _open_checked_queue(queue_dsn, expected_login=expected_login,
                             expected_database=expected_database,
                             **({'post_finish': True} if post_finish else {})) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM video_queue.subset_claim_job(%s,%s,%s,%s,%s)',
                (batch_id, worker_key, 900, processing_profile,
                 algorithm_revision))
            row = cursor.fetchone()
            names = (tuple(column.name for column in cursor.description)
                     if row is not None and not isinstance(row, Mapping) else ())
        if isinstance(row, Mapping):
            claim = dict(row)
        elif isinstance(row, (tuple, list)) and names == _CLAIM_COLUMNS:
            claim = dict(zip(names, row))
        else:
            claim = None
        if (claim is None or set(claim) != set(_CLAIM_COLUMNS)
                or str(claim['job_id']) != job_id
                or str(claim['batch_id']) != batch_id
                or claim['source_file_id'] != expected_source_file_id
                or claim['processing_profile'] != processing_profile
                or claim['algorithm_revision'] != algorithm_revision
                or type(claim['attempt_count']) is not int
                or claim['attempt_count'] != expected_attempt_count
                or type(claim['is_canary']) is not bool
                or type(claim['sequence']) is not int
                or claim['sequence'] < 1
                or not isinstance(claim['stable_job_key'], str)
                or not re.fullmatch(r'[0-9a-f]{32}', claim['stable_job_key'])
                or any(not isinstance(claim[name], str)
                       or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', claim[name])
                       for name in ('source_folder_id', 'output_folder_id',
                                    'work_folder_id'))
                or not isinstance(claim['source_mime_type'], str)
                or not claim['source_mime_type'].startswith('video/')
                or type(claim['source_size_bytes']) is not int
                or claim['source_size_bytes'] <= 0):
            raise PrivateOneJobConfigurationError('ONE_JOB_EXACT_CLAIM_MISMATCH')
        try:
            claim['lease_token'] = _exact_uuid(claim['lease_token'])
        except PrivateOneJobConfigurationError as exc:
            raise PrivateOneJobConfigurationError('ONE_JOB_EXACT_CLAIM_MISMATCH') from exc
        claim['job_id'] = job_id
        claim['batch_id'] = batch_id
        # The durable receipt must exist before COMMIT. A lost commit ACK then
        # leaves the exact token for read-only reconciliation, never a blind
        # second claim. Publication failure rolls this transaction back.
        claim_publisher(dict(claim))
    return claim


def _exact_uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_EXACT_ID_INVALID') from exc


class PreparedPrivateOneJob:
    """Process-local checked composition, never a wire/owner capability."""
    def __init__(self, *, handoff, adapter, binding, claim_raw, ports):
        import threading
        from .receipt_encoding import canonical_bytes
        self.handoff, self.adapter = handoff, adapter
        self._handoff, self._adapter = handoff, adapter
        self._handoff_binding = canonical_bytes(handoff.binding)
        self._validator = adapter.connection_validator
        self._binding, self._claim_raw, self._ports = binding, claim_raw, ports
        self._lock, self._used = threading.Lock(), False

    def check(self):
        from .receipt_encoding import canonical_bytes
        if (self.handoff is not self._handoff or self.adapter is not self._adapter
                or self.handoff.assignment is not self._ports[-1]
                or canonical_bytes(self.handoff.binding) != self._handoff_binding
                or self.adapter.connection_validator is not self._validator
                or self.adapter.observation_provider is not self._ports[0].observation_provider):
            raise PrivateOneJobConfigurationError('ONE_JOB_PREPARED_COMPOSITION_CHANGED')

    def consume(self, binding, claim, ports):
        from .receipt_encoding import canonical_bytes
        with self._lock:
            if self._used:
                raise PrivateOneJobConfigurationError('ONE_JOB_PREPARED_REPLAY_NO_RETRY')
            self._used = True  # Sticky before all callbacks and connection IO.
            self.check()
            if (binding != self._binding or canonical_bytes(dict(claim), max_bytes=16384) != self._claim_raw
                    or any(a is not b for a, b in zip(ports, self._ports))):
                raise PrivateOneJobConfigurationError('ONE_JOB_PREPARED_BINDING_CHANGED')


def _prepared_binding(queue_dsn, expected_database, expected_login, expected_batch_id,
                      expected_job_id, expected_lease_token, expected_attempt_count,
                      expected_source_file_id, worker_key, admission_id, source_fingerprint, assignment):
    return (queue_dsn, expected_database, expected_login, expected_batch_id, expected_job_id,
            expected_lease_token, expected_attempt_count, expected_source_file_id, worker_key,
            admission_id, source_fingerprint, None if assignment is None else assignment.raw)


def prepare_private_one_job(*, queue_dsn: str, expected_database: str,
                        expected_login: str, claim: Mapping[str, Any],
                        expected_batch_id: str, expected_job_id: str,
                        expected_lease_token: str, expected_attempt_count: int,
                        expected_source_file_id: str, worker_key: str,
                        admission_id: str, source_fingerprint: str,
                        subset_adapter: SubsetWorkerAdapter,
                        approval_waiter=None, processor=None,
                        private_failure_handler=None, assignment=None) -> PreparedPrivateOneJob:
    """Prepare one checked adapter/handoff before work, with no compute/finish.

    The claim is already leased. This entrypoint neither claims another job
    nor falls back to the broad writer, old public-candidate path, or polling.
    Every private queue connection repeats the ACL identity check at use.
    A source-bound Assignment opts into the v2 handoff and stricter capability
    policy. It is not an issuer, a native binding, or an automatic CLI switch.
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
            or (private_failure_handler is not None and not callable(private_failure_handler))
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

    post_finish = assignment is not None
    if post_finish:
        if not callable(private_failure_handler):
            raise PrivateOneJobConfigurationError('ONE_JOB_POST_FINISH_FAILURE_HANDLER_REQUIRED')
        from .terminal_finish_contract import Assignment
        if type(assignment) is not Assignment:
            raise PrivateOneJobConfigurationError('ONE_JOB_ASSIGNMENT_TYPE_INVALID')
        assignment.check_claim(claim, worker_key)
        assigned = assignment.value
        if (assigned['admission_id'] != admission_id
                or assigned['source_fingerprint'] != source_fingerprint):
            raise PrivateOneJobConfigurationError('ONE_JOB_ASSIGNMENT_ADMISSION_CHANGED')

    @contextmanager
    def open_checked_connection():
        with _open_checked_queue(queue_dsn, expected_login=expected_login,
                                 expected_database=expected_database,
                                 **({'post_finish': True} if post_finish else {})) as connection:
            yield connection

    # Check once before Drive processing, then at every queue SQL connection.
    with open_checked_connection() as connection:
        if post_finish:
            from database.video_post_finish_receipt import assert_enrolled_assignment_on_connection
            assert_enrolled_assignment_on_connection(connection, assignment)
    def validate_queue_connection(connection) -> None:
        _assert_queue_only(connection, expected_login=expected_login,
                           expected_database=expected_database,
                           **({'post_finish': True} if post_finish else {}))

    checked_adapter = SubsetWorkerAdapter(
        subset_adapter.observation_provider,
        connection_validator=validate_queue_connection)
    handoff = PrivateResultStagingHandoff(
        claim=claim, worker_key=worker_key, admission_id=admission_id,
        source_fingerprint=source_fingerprint,
        open_connection=open_checked_connection, assignment=assignment)
    if approval_waiter is not None:
        if not callable(approval_waiter):
            raise PrivateOneJobConfigurationError('ONE_JOB_APPROVAL_WAITER_INVALID')
    if processor is not None:
        if not callable(processor):
            raise PrivateOneJobConfigurationError('ONE_JOB_PROCESSOR_INVALID')
    from .receipt_encoding import canonical_bytes
    return PreparedPrivateOneJob(handoff=handoff, adapter=checked_adapter,
        binding=_prepared_binding(queue_dsn, expected_database, expected_login, expected_batch_id,
            expected_job_id, expected_lease_token, expected_attempt_count, expected_source_file_id,
            worker_key, admission_id, source_fingerprint, assignment),
        claim_raw=canonical_bytes(dict(claim), max_bytes=16384),
        ports=(subset_adapter, approval_waiter, processor, private_failure_handler, assignment))


def run_private_one_job(*, queue_dsn: str, expected_database: str,
                        expected_login: str, claim: Mapping[str, Any],
                        expected_batch_id: str, expected_job_id: str,
                        expected_lease_token: str, expected_attempt_count: int,
                        expected_source_file_id: str, worker_key: str,
                        admission_id: str, source_fingerprint: str,
                        subset_adapter: SubsetWorkerAdapter,
                        approval_waiter=None, processor=None,
                         private_failure_handler=None, assignment=None, prepared=None,
                         native_budget=None) -> dict[str, Any]:
    from .native_video_lease import require_native_budget
    require_native_budget(claim, worker_key, native_budget)
    if native_budget is not None:
        from .terminal_finish_contract import Assignment
        if (type(assignment) is not Assignment
                or native_budget.assignment_sha256 != assignment.sha256
                or assignment.value['autopilot']['task_id'] != native_budget.task_id
                or assignment.value['autopilot']['lease_epoch'] != native_budget.lease_epoch):
            raise PrivateOneJobConfigurationError('ONE_JOB_NATIVE_BUDGET_ASSIGNMENT_CHANGED')
    arguments = dict(queue_dsn=queue_dsn, expected_database=expected_database,
        expected_login=expected_login, claim=claim, expected_batch_id=expected_batch_id,
        expected_job_id=expected_job_id, expected_lease_token=expected_lease_token,
        expected_attempt_count=expected_attempt_count, expected_source_file_id=expected_source_file_id,
        worker_key=worker_key, admission_id=admission_id, source_fingerprint=source_fingerprint,
        subset_adapter=subset_adapter, approval_waiter=approval_waiter, processor=processor,
        private_failure_handler=private_failure_handler, assignment=assignment)
    reused = prepared is not None
    if prepared is None:
        prepared = prepare_private_one_job(**arguments)
    if type(prepared) is not PreparedPrivateOneJob:
        raise PrivateOneJobConfigurationError('ONE_JOB_PREPARED_TYPE')
    prepared.consume(_prepared_binding(queue_dsn, expected_database, expected_login, expected_batch_id,
        expected_job_id, expected_lease_token, expected_attempt_count, expected_source_file_id,
        worker_key, admission_id, source_fingerprint, assignment), claim,
        (subset_adapter, approval_waiter, processor, private_failure_handler, assignment))
    if reused:
        with prepared.handoff.open_connection() as connection:
            if assignment is not None:
                from database.video_post_finish_receipt import assert_enrolled_assignment_on_connection
                assert_enrolled_assignment_on_connection(connection, assignment)
        prepared.check()
    # A retained prepare result is no authorization cache. Every actual stage/
    # finish connection still uses the original checked connection validator.
    selected = dict(subset_adapter=prepared.adapter, staging_handoff=prepared.handoff)
    if approval_waiter is not None:
        selected['approval_waiter'] = approval_waiter
    if processor is not None:
        selected['processor'] = processor
    if private_failure_handler is not None:
        selected['private_failure_handler'] = private_failure_handler
    if native_budget is not None:
        # Explicit original-owner object injection, never a payload field or a
        # duration inferred from the source video. It cannot confer authority.
        native_budget.check(claim, worker_key)
        selected['native_budget'] = native_budget
    return _process_private_claim(queue_dsn, claim, worker_key, **selected)


__all__ = ['claim_exact_private_job', 'prepare_private_one_job', 'run_private_one_job',
           'PrivateOneJobConfigurationError']
