\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_files jsonb := '[
      {"sequence":1,"file_id":"sourceVideo000001","name":"Lesson 13.mp4","mime_type":"video/mp4","size_bytes":696237577,"checksum":"md5:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    ]'::jsonb;
    v_batch uuid;
    v_again uuid;
    v_job uuid;
    v_token1 uuid;
    v_token2 uuid;
    v_token3 uuid;
    v_state text;
    v_released integer;
    v_retry_status text;
    v_claim_count integer;
BEGIN
    SELECT e.batch_id INTO v_batch
      FROM video_queue.enqueue_drive_batch(
        'exact-canary-recovery-001','sourceFolder000001','outputFolder00001','workFolder0000001',
        'bridge_3_1_free_exact_canary','3.1-free-r25.16','sourceVideo000001',repeat('a',64),v_files
      ) e;
    SELECT e.batch_id INTO v_again
      FROM video_queue.enqueue_drive_batch(
        'exact-canary-recovery-001','sourceFolder000001','outputFolder00001','workFolder0000001',
        'bridge_3_1_free_exact_canary','3.1-free-r25.16','sourceVideo000001',repeat('a',64),v_files
      ) e;
    IF v_batch IS NULL OR v_again <> v_batch THEN
        RAISE EXCEPTION 'duplicate exact request created a second batch';
    END IF;
    IF (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch) <> 1
       OR (SELECT count(*) FROM video_queue.job WHERE batch_id=v_batch AND is_canary AND status='QUEUED') <> 1
       OR EXISTS (SELECT 1 FROM video_queue.job WHERE batch_id=v_batch AND status='PENDING_CANARY') THEN
        RAISE EXCEPTION 'exact canary batch contains more than one runnable item';
    END IF;

    SELECT c.job_id,c.lease_token INTO v_job,v_token1
      FROM video_queue.claim_job('exact-worker-1',900,'bridge_3_1_free_exact_canary','3.1-free-r25.16') c;
    IF v_job IS NULL THEN RAISE EXCEPTION 'first exact claim missing'; END IF;
    UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=v_job;
    SELECT c.job_id,c.lease_token INTO v_job,v_token2
      FROM video_queue.claim_job('exact-worker-2',900,'bridge_3_1_free_exact_canary','3.1-free-r25.16') c;
    IF v_token2 IS NULL OR v_token2=v_token1 THEN
        RAISE EXCEPTION 'stale lease was not fenced with a new token';
    END IF;
    BEGIN
        PERFORM * FROM video_queue.finish_job(
          v_job,v_token1,'exact-worker-1','REVIEW_READY',
          jsonb_build_object(
            'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
            'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
            'source_file_id','sourceVideo000001',
            'stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),
            'algorithm_revision','3.1-free-r25.16'
          ),NULL
        );
        RAISE EXCEPTION 'expired fencing token produced a false success';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'VIDEO_QUEUE_LEASE_LOST' THEN RAISE; END IF;
    END;

    UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=v_job;
    SELECT c.job_id,c.lease_token INTO v_job,v_token3
      FROM video_queue.claim_job('exact-worker-3',900,'bridge_3_1_free_exact_canary','3.1-free-r25.16') c;
    IF v_token3 IS NULL OR v_token3=v_token2 THEN
        RAISE EXCEPTION 'second interrupted worker was not fenced';
    END IF;
    UPDATE video_queue.job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=v_job;
    SELECT count(*) INTO v_claim_count
      FROM video_queue.claim_job('exact-worker-4',900,'bridge_3_1_free_exact_canary','3.1-free-r25.16');
    IF v_claim_count <> 0
       OR (SELECT status FROM video_queue.job WHERE job_id=v_job) <> 'FAILED'
       OR (SELECT error_code FROM video_queue.job WHERE job_id=v_job) <> 'UV_WORKER_CRASH_RETRY_EXHAUSTED'
       OR (SELECT status FROM video_queue.batch WHERE batch_id=v_batch) <> 'CANARY_BLOCKED' THEN
        RAISE EXCEPTION 'interrupted-worker exhaustion did not fail closed';
    END IF;

    SELECT e.batch_id INTO v_batch
      FROM video_queue.enqueue_drive_batch(
        'exact-canary-readback-retry-001','sourceFolder000002','outputFolder00002','workFolder0000002',
        'bridge_3_1_free_exact_canary','3.1-free-r25.16','sourceVideo000002',repeat('b',64),
        '[{"sequence":1,"file_id":"sourceVideo000002","name":"Recovery B.mp4","mime_type":"video/mp4","size_bytes":1000000,"checksum":"md5:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]'::jsonb
      ) e;
    SELECT c.job_id,c.lease_token INTO v_job,v_token1
      FROM video_queue.claim_job('exact-worker-5',900,'bridge_3_1_free_exact_canary','3.1-free-r25.16') c;
    SELECT r.job_status INTO v_retry_status
      FROM video_queue.retry_job(v_job,v_token1,'exact-worker-5','UV_DRIVE_READBACK_FAILED',3,3600) r;
    IF v_retry_status <> 'QUEUED'
       OR (SELECT status FROM video_queue.job WHERE job_id=v_job) = 'REVIEW_READY'
       OR (SELECT output IS NOT NULL FROM video_queue.job WHERE job_id=v_job) THEN
        RAISE EXCEPTION 'failed Drive readback produced a false terminal PASS';
    END IF;

    SELECT e.batch_id INTO v_batch
      FROM video_queue.enqueue_drive_batch(
        'exact-canary-success-001','sourceFolder000003','outputFolder00003','workFolder0000003',
        'bridge_3_1_free_exact_canary','3.1-free-r25.16','sourceVideo000003',repeat('c',64),
        '[{"sequence":1,"file_id":"sourceVideo000003","name":"Recovery C.mp4","mime_type":"video/mp4","size_bytes":2000000,"checksum":"md5:cccccccccccccccccccccccccccccccc"}]'::jsonb
      ) e;
    SELECT c.job_id,c.lease_token INTO v_job,v_token1
      FROM video_queue.claim_job('exact-worker-6',900,'bridge_3_1_free_exact_canary','3.1-free-r25.16') c;
    SELECT f.batch_status,f.released_jobs INTO v_state,v_released
      FROM video_queue.finish_job(
        v_job,v_token1,'exact-worker-6','REVIEW_READY',
        jsonb_build_object(
          'result_mode','SHADOW_REVIEW_ONLY','canonical_promotion_allowed',false,
          'database_persistence_allowed',false,'publication_state','NOT_PUBLISHED',
          'source_file_id','sourceVideo000003',
          'stable_job_key',(SELECT stable_job_key FROM video_queue.job WHERE job_id=v_job),
          'algorithm_revision','3.1-free-r25.16',
          'source_identity_gate','PASS','drive_upload_readback_gate','PASS','artifact_manifest_gate','PASS'
        ),NULL
      ) f;
    IF v_state <> 'REVIEW' OR v_released <> 0
       OR EXISTS (SELECT 1 FROM video_queue.job WHERE batch_id=v_batch AND status IN ('PENDING_CANARY','QUEUED','LEASED')) THEN
        RAISE EXCEPTION 'successful exact canary released another job';
    END IF;
END $$;

ROLLBACK;
