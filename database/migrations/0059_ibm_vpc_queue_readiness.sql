\set ON_ERROR_STOP on
BEGIN;

-- Aggregate-only observation contract for an on-demand compute controller.
-- Keep claim eligibility in sync with video_queue.claim_job:
--   * due QUEUED jobs in claimable batches;
--   * expired LEASED jobs while attempt_count < 3;
--   * unexpired leases keep compute busy;
--   * pending canary jobs and CANARY_REVIEW batches are never claimable.
CREATE OR REPLACE VIEW video_queue.compute_readiness AS
WITH observed AS (
    SELECT statement_timestamp() AS observed_at
)
SELECT
    observed.observed_at,
    b.processing_profile,
    b.algorithm_revision,
    b.status AS batch_status,
    count(j.job_id) FILTER (
        WHERE b.status IN ('QUEUED_CANARY','RUNNING')
          AND (
              (j.status = 'QUEUED' AND j.next_attempt_at <= observed.observed_at)
              OR
              (j.status = 'LEASED'
               AND j.lease_expires_at <= observed.observed_at
               AND j.attempt_count < 3)
          )
    )::bigint AS runnable_now_count,
    count(j.job_id) FILTER (
        WHERE j.status = 'LEASED'
          AND j.lease_expires_at > observed.observed_at
    )::bigint AS active_leases_count,
    count(j.job_id) FILTER (
        WHERE b.status IN ('QUEUED_CANARY','RUNNING')
          AND j.status = 'QUEUED'
          AND j.next_attempt_at > observed.observed_at
    )::bigint AS retry_waiting_count,
    min(j.next_attempt_at) FILTER (
        WHERE b.status IN ('QUEUED_CANARY','RUNNING')
          AND j.status = 'QUEUED'
          AND j.next_attempt_at > observed.observed_at
    ) AS next_retry_at,
    count(j.job_id) FILTER (
        WHERE j.status = 'PENDING_CANARY'
    )::bigint AS pending_canary_count,
    count(j.job_id) FILTER (
        WHERE b.status IN ('QUEUED_CANARY','RUNNING')
          AND j.status = 'LEASED'
          AND j.lease_expires_at <= observed.observed_at
          AND j.attempt_count >= 3
    )::bigint AS expired_attempts_exhausted_count,
    count(j.job_id) FILTER (
        WHERE j.status IN ('QUEUED','LEASED')
          AND b.status NOT IN ('QUEUED_CANARY','RUNNING')
    )::bigint AS blocked_nonterminal_count,
    count(j.job_id) FILTER (
        WHERE j.status NOT IN (
            'PENDING_CANARY','QUEUED','LEASED','REVIEW_READY','AMBIGUOUS','FAILED'
        )
    )::bigint AS unknown_job_status_count,
    count(j.job_id) FILTER (
        WHERE b.status NOT IN (
            'QUEUED_CANARY','RUNNING','CANARY_BLOCKED','CANARY_REVIEW','REVIEW'
        )
    )::bigint AS unknown_batch_status_count,
    count(j.job_id) FILTER (
        WHERE
            (j.status = 'LEASED' AND (
                j.lease_owner IS NULL OR j.lease_token IS NULL OR j.lease_expires_at IS NULL
            ))
            OR
            (j.status <> 'LEASED' AND (
                j.lease_owner IS NOT NULL OR j.lease_token IS NOT NULL OR j.lease_expires_at IS NOT NULL
            ))
    )::bigint AS invalid_lease_shape_count
FROM video_queue.batch AS b
CROSS JOIN observed
LEFT JOIN video_queue.job AS j ON j.batch_id = b.batch_id
GROUP BY
    observed.observed_at,
    b.processing_profile,
    b.algorithm_revision,
    b.status;

REVOKE ALL ON video_queue.compute_readiness FROM
    PUBLIC,
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker;
GRANT SELECT ON video_queue.compute_readiness TO bridge_school_reader;

COMMENT ON VIEW video_queue.compute_readiness IS
    'Aggregate-only, read-only compute readiness; no job identifiers, source data, worker identities, or lease tokens';

INSERT INTO schema_migration(migration_key)
VALUES ('0059_ibm_vpc_queue_readiness')
ON CONFLICT DO NOTHING;

COMMIT;
