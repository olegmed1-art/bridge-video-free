\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_batch uuid;
    v_canary uuid;
    v_pending uuid;
    r record;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM schema_migration
         WHERE migration_key = '0059_ibm_vpc_queue_readiness'
    ) THEN
        RAISE EXCEPTION 'readiness migration is not registered';
    END IF;

    IF NOT has_table_privilege('bridge_school_reader','video_queue.compute_readiness','SELECT')
       OR has_table_privilege('bridge_school_reader','video_queue.job','SELECT')
       OR has_table_privilege('bridge_school_reader','video_queue.batch','SELECT') THEN
        RAISE EXCEPTION 'readiness ACL is not aggregate-only';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_attribute
         WHERE attrelid = 'video_queue.compute_readiness'::regclass
           AND NOT attisdropped
           AND attname IN ('job_id','source_file_id','source_name','lease_owner','lease_token')
    ) THEN
        RAISE EXCEPTION 'readiness view exposes job or worker identity data';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_attribute
         WHERE attrelid = 'video_queue.job_status'::regclass
           AND NOT attisdropped
           AND attname IN ('next_attempt_at','lease_expires_at','lease_owner','lease_token')
    ) THEN
        RAISE EXCEPTION 'public job status view unexpectedly exposes internal scheduling or lease data';
    END IF;

    INSERT INTO video_queue.batch(
        request_key, source_folder_id, output_folder_id, work_folder_id,
        processing_profile, algorithm_revision, inventory_sha256,
        expected_count, total_size_bytes, canary_source_file_id, status
    ) VALUES (
        'ibm-readiness-test-' || replace(gen_random_uuid()::text,'-',''),
        'driveSourceFolder0001', 'driveOutputFolder0001', 'driveWorkFolder0001',
        'bridge_3_1_free', '3.1-free-r25.16', repeat('a',64),
        2, 4000000, 'driveCanaryFile00001', 'QUEUED_CANARY'
    ) RETURNING batch_id INTO v_batch;

    INSERT INTO video_queue.job(
        batch_id, sequence, source_file_id, source_name, source_mime_type,
        source_size_bytes, stable_job_key, is_canary, status, next_attempt_at
    ) VALUES (
        v_batch, 1, 'driveCanaryFile00001', 'canary.mp4', 'video/mp4',
        2000000, repeat('1',32), true, 'QUEUED', clock_timestamp() + interval '5 minutes'
    ) RETURNING job_id INTO v_canary;

    INSERT INTO video_queue.job(
        batch_id, sequence, source_file_id, source_name, source_mime_type,
        source_size_bytes, stable_job_key, is_canary, status
    ) VALUES (
        v_batch, 2, 'drivePendingFile00001', 'pending.mp4', 'video/mp4',
        2000000, repeat('2',32), false, 'PENDING_CANARY'
    ) RETURNING job_id INTO v_pending;

    SELECT * INTO STRICT r
      FROM video_queue.compute_readiness
     WHERE processing_profile = 'bridge_3_1_free'
       AND algorithm_revision = '3.1-free-r25.16'
       AND batch_status = 'QUEUED_CANARY';
    IF r.runnable_now_count <> 0
       OR r.active_leases_count <> 0
       OR r.retry_waiting_count <> 1
       OR r.pending_canary_count <> 1
       OR r.next_retry_at IS NULL
       OR r.blocked_nonterminal_count <> 0
       OR r.unknown_job_status_count <> 0
       OR r.unknown_batch_status_count <> 0
       OR r.invalid_lease_shape_count <> 0 THEN
        RAISE EXCEPTION 'future retry or pending canary readiness classification failed';
    END IF;

    UPDATE video_queue.job
       SET next_attempt_at = clock_timestamp() - interval '1 second'
     WHERE job_id = v_canary;
    SELECT * INTO STRICT r
      FROM video_queue.compute_readiness
     WHERE processing_profile = 'bridge_3_1_free'
       AND algorithm_revision = '3.1-free-r25.16'
       AND batch_status = 'QUEUED_CANARY';
    IF r.runnable_now_count <> 1 OR r.retry_waiting_count <> 0 OR r.next_retry_at IS NOT NULL
       OR r.pending_canary_count <> 1 THEN
        RAISE EXCEPTION 'due canary job was not classified as runnable';
    END IF;

    UPDATE video_queue.job
       SET status = 'LEASED',
           attempt_count = 1,
           lease_owner = 'readiness-test-worker',
           lease_token = gen_random_uuid(),
           lease_expires_at = clock_timestamp() + interval '2 minutes'
     WHERE job_id = v_canary;
    SELECT * INTO STRICT r
      FROM video_queue.compute_readiness
     WHERE processing_profile = 'bridge_3_1_free'
       AND algorithm_revision = '3.1-free-r25.16'
       AND batch_status = 'QUEUED_CANARY';
    IF r.runnable_now_count <> 0 OR r.active_leases_count <> 1 THEN
        RAISE EXCEPTION 'unexpired lease was not classified as active';
    END IF;

    UPDATE video_queue.job
       SET attempt_count = 2,
           lease_expires_at = clock_timestamp() - interval '1 second'
     WHERE job_id = v_canary;
    SELECT * INTO STRICT r
      FROM video_queue.compute_readiness
     WHERE processing_profile = 'bridge_3_1_free'
       AND algorithm_revision = '3.1-free-r25.16'
       AND batch_status = 'QUEUED_CANARY';
    IF r.runnable_now_count <> 1 OR r.active_leases_count <> 0 THEN
        RAISE EXCEPTION 'expired retryable lease was not classified as runnable';
    END IF;

    UPDATE video_queue.job
       SET attempt_count = 3,
           lease_expires_at = clock_timestamp() - interval '1 second'
     WHERE job_id = v_canary;
    SELECT * INTO STRICT r
      FROM video_queue.compute_readiness
     WHERE processing_profile = 'bridge_3_1_free'
       AND algorithm_revision = '3.1-free-r25.16'
       AND batch_status = 'QUEUED_CANARY';
    IF r.runnable_now_count <> 0
       OR r.active_leases_count <> 0
       OR r.expired_attempts_exhausted_count <> 1 THEN
        RAISE EXCEPTION 'retry-exhausted expired lease was classified as runnable';
    END IF;

    UPDATE video_queue.job
       SET status = 'QUEUED',
           attempt_count = 0,
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           next_attempt_at = clock_timestamp() - interval '1 second'
     WHERE job_id = v_canary;
    UPDATE video_queue.batch SET status = 'CANARY_REVIEW' WHERE batch_id = v_batch;
    SELECT * INTO STRICT r
      FROM video_queue.compute_readiness
     WHERE processing_profile = 'bridge_3_1_free'
       AND algorithm_revision = '3.1-free-r25.16'
       AND batch_status = 'CANARY_REVIEW';
    IF r.runnable_now_count <> 0 OR r.blocked_nonterminal_count <> 1 THEN
        RAISE EXCEPTION 'canary review gate was treated as claimable';
    END IF;
END;
$$;

ROLLBACK;
