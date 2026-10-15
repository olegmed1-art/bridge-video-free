"""Opt-in local candidate for one-job SQL admission around candidate staging.

The resident worker does not call this adapter. The database functions are an
unapplied proposal, and the current broad worker writer is not a strict ACL.
"""
from __future__ import annotations

from uuid import UUID


def _uuid(value):
    return str(UUID(str(value)))


def stage_admitted_candidate_on_connection(
    connection, payload, persisted_result, *, admission, writer=None,
):
    """Hold the SQL admission/lease lock through candidate write and attestation.

    Caller owns the connection and its commit. A nested writer transaction is a
    savepoint. SQL may rebind a previously committed deterministic receipt only
    after validating this attempt's live lease and unchanged receipt identity.
    """
    if writer is None:
        from database.video_candidate_persistence import persist_quality_candidates_on_connection
        writer = persist_quality_candidates_on_connection
    try:
        admission_id = _uuid(admission['admission_id'])
        job_id = _uuid(admission['job_id'])
        lease_token = _uuid(admission['lease_token'])
        attempt_count = admission['attempt_count']
        worker_key = admission['worker_key']
        source_fingerprint = admission['source_fingerprint']
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('ONE_JOB_ADMISSION_ARGUMENT_INVALID') from exc
    if (type(attempt_count) is not int or not 1 <= attempt_count <= 3
            or not isinstance(worker_key, str) or not worker_key
            or not isinstance(source_fingerprint, str) or len(source_fingerprint) != 64
            or any(c not in '0123456789abcdef' for c in source_fingerprint)):
        raise ValueError('ONE_JOB_ADMISSION_ARGUMENT_INVALID')
    scope = (admission_id, job_id, lease_token, worker_key,
             attempt_count, source_fingerprint)
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT video_queue.assert_one_job_staging_v1(%s,%s,%s,%s,%s,%s)',
                scope,
            )
            if str(cursor.fetchone()[0]) != admission_id:
                raise ValueError('ONE_JOB_ADMISSION_NOT_CONFIRMED')
        staged = writer(connection, payload, persisted_result=persisted_result)
        if not isinstance(staged, dict) or staged.get('status') != 'PERSISTED':
            raise ValueError('ONE_JOB_STAGING_NOT_PERSISTED')
        try:
            receipt_id = _uuid(staged['replay_receipt_id'])
            run_id = _uuid(staged['analysis_run_id'])
            source_id = _uuid(staged['source_id'])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError('ONE_JOB_STAGING_RECEIPT_MISSING') from exc
        # SQL rechecks the live lease, source revision, receipt transaction,
        # run/source identity and one-attempt-only reuse rule.
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT payload_hash FROM public.analysis_candidate '
                'WHERE analysis_candidate_id=%s', (receipt_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError('ONE_JOB_STAGING_RECEIPT_MISSING')
            cursor.execute(
                'SELECT video_queue.attest_one_job_staging_receipt_v1('
                '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (*scope, receipt_id, run_id, source_id, row[0]),
            )
            if str(cursor.fetchone()[0]) != receipt_id:
                raise ValueError('ONE_JOB_STAGING_RECEIPT_NOT_ATTESTED')
    return staged | {'admission_id': admission_id, 'attempt_count': attempt_count}


__all__ = ['stage_admitted_candidate_on_connection']
