\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_batch_id uuid;
    v_jobs integer;
    v_claim record;
    v_finish record;
    v_pending integer;
    v_queued integer;
BEGIN
    SELECT batch_id INTO v_batch_id
      FROM video_queue.batch
     WHERE request_key = 'issue881-rollback-preserve';
    IF v_batch_id IS NULL THEN
        RAISE EXCEPTION 'rollback fixture batch was lost';
    END IF;
    IF (SELECT status FROM video_queue.batch WHERE batch_id = v_batch_id) <> 'CANARY_BLOCKED' THEN
        RAISE EXCEPTION 'CANARY_REVIEW was not safely terminalized';
    END IF;
    IF (SELECT completed_at FROM video_queue.batch WHERE batch_id = v_batch_id) IS NULL THEN
        RAISE EXCEPTION 'rollback terminal timestamp missing';
    END IF;
    SELECT count(*) INTO v_jobs FROM video_queue.job WHERE batch_id = v_batch_id;
    IF v_jobs <> 2 THEN RAISE EXCEPTION 'rollback did not preserve jobs'; END IF;
    IF EXISTS (
        SELECT 1 FROM schema_migration
         WHERE migration_key = '0057_universal_video_canary_review_gate'
    ) THEN
        RAISE EXCEPTION 'rollback migration ledger mismatch';
    END IF;
    IF to_regprocedure('video_queue.precanary_idle_snapshot()') IS NOT NULL
       OR to_regprocedure('video_queue.canonical_json_text(jsonb)') IS NOT NULL THEN
        RAISE EXCEPTION 'rollback helper functions remain';
    END IF;

    PERFORM * FROM video_queue.enqueue_drive_batch(
        'issue881-rollback-post-finish',
        'source-folder-post-rollback-123456',
        'output-folder-post-rollback-123456',
        'work-folder-post-rollback-123456',
        'bridge_3_1_free',
        '3.1-free-r25.16',
        'source-file-post-rollback-123456',
        repeat('7',64),
        jsonb_build_array(
            jsonb_build_object('sequence',1,'file_id','source-file-post-rollback-123456','name','canary.mp4','mime_type','video/mp4','size_bytes',12339062,'checksum','md5:'||repeat('7',32)),
            jsonb_build_object('sequence',2,'file_id','source-file-post-rollback-223456','name','pending.mp4','mime_type','video/mp4','size_bytes',22339062,'checksum','md5:'||repeat('8',32))
        )
    );
    SELECT * INTO v_claim
      FROM video_queue.claim_job('worker-rollback-safety',900,'bridge_3_1_free','3.1-free-r25.16');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rollback safety canary was not claimable';
    END IF;
    IF NOT v_claim.is_canary THEN
        RAISE EXCEPTION 'rollback safety canary was not claimable';
    END IF;
    SELECT * INTO v_finish
      FROM video_queue.finish_job(
        v_claim.job_id,
        v_claim.lease_token,
        'worker-rollback-safety',
        'REVIEW_READY',
        jsonb_build_object(
            'result_mode','SHADOW_REVIEW_ONLY',
            'canonical_promotion_allowed',false,
            'database_persistence_allowed',false,
            'publication_state','NOT_PUBLISHED',
            'source_file_id',v_claim.source_file_id,
            'stable_job_key',v_claim.stable_job_key,
            'algorithm_revision',v_claim.algorithm_revision
        ),
        NULL
      );
    IF v_finish.batch_status <> 'CANARY_BLOCKED' OR v_finish.released_jobs <> 0 THEN
        RAISE EXCEPTION 'rollback restored automatic canary release';
    END IF;
    SELECT count(*) INTO v_pending
      FROM video_queue.job
     WHERE batch_id = v_claim.batch_id AND status = 'PENDING_CANARY';
    SELECT count(*) INTO v_queued
      FROM video_queue.job
     WHERE batch_id = v_claim.batch_id AND status = 'QUEUED';
    IF v_pending <> 1 OR v_queued <> 0 THEN
        RAISE EXCEPTION 'rollback released pending jobs';
    END IF;
    IF (SELECT completed_at FROM video_queue.batch WHERE batch_id = v_claim.batch_id) IS NULL THEN
        RAISE EXCEPTION 'rollback safety terminal timestamp missing';
    END IF;
END;
$$;

ROLLBACK;
