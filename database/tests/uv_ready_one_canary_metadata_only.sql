\set ON_ERROR_STOP on
SET client_min_messages = warning;

-- Queue-only safety proof. This script creates exactly two synthetic metadata
-- rows in an exclusive disposable Neon branch. It never references media bytes,
-- Drive credentials, production routing, ASR, OCR, training, DDS3, or BEN.

DO $check$
DECLARE
    v_media_byte_columns integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
        WHERE migration_key = '0056_universal_video_queue'
    ) THEN
        RAISE EXCEPTION 'UV_META_MIGRATION_REGISTRY_MISSING';
    END IF;
    IF to_regclass('video_queue.batch') IS NULL
       OR to_regclass('video_queue.job') IS NULL
       OR to_regclass('video_queue.job_event') IS NULL
       OR to_regclass('video_queue.batch_status') IS NULL
       OR to_regclass('video_queue.job_status') IS NULL THEN
        RAISE EXCEPTION 'UV_META_QUEUE_OBJECT_MISSING';
    END IF;
    IF to_regprocedure('video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)') IS NULL
       OR to_regprocedure('video_queue.claim_job(text,integer,text,text)') IS NULL
       OR to_regprocedure('video_queue.heartbeat_job(uuid,uuid,text,integer)') IS NULL
       OR to_regprocedure('video_queue.retry_job(uuid,uuid,text,text,integer,integer)') IS NULL
       OR to_regprocedure('video_queue.finish_job(uuid,uuid,text,text,jsonb,text)') IS NULL THEN
        RAISE EXCEPTION 'UV_META_QUEUE_FUNCTION_MISSING';
    END IF;
    SELECT count(*) INTO v_media_byte_columns
    FROM information_schema.columns
    WHERE table_schema = 'video_queue'
      AND (data_type = 'bytea' OR udt_name IN ('bytea','oid'));
    IF v_media_byte_columns <> 0 THEN
        RAISE EXCEPTION 'UV_META_MEDIA_BYTE_COLUMN_PRESENT';
    END IF;
    IF has_schema_privilege('public', 'video_queue', 'USAGE') THEN
        RAISE EXCEPTION 'UV_META_PUBLIC_SCHEMA_ACCESS_PRESENT';
    END IF;
    IF NOT has_function_privilege(
        'bridge_school_app',
        'video_queue.enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)',
        'EXECUTE'
    ) OR NOT has_function_privilege(
        'bridge_school_worker',
        'video_queue.claim_job(text,integer,text,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'UV_META_REQUIRED_GRANT_MISSING';
    END IF;
END
$check$;

CREATE TEMP TABLE uv_batch_first AS
SELECT *
FROM video_queue.enqueue_drive_batch(
    'uv-meta-only-20260901',
    'uvsourcefolder01',
    'uvoutputfolder01',
    'uvworkfolder0001',
    'bridge_3_1_free',
    '3.1-free-r25.16',
    'uvcanaryfile001',
    repeat('a', 64),
    jsonb_build_array(
        jsonb_build_object(
            'sequence', 1,
            'file_id', 'uvcanaryfile001',
            'name', 'synthetic-canary.mp4',
            'mime_type', 'video/mp4',
            'size_bytes', 1048576,
            'checksum', 'sha256:' || repeat('1', 64)
        ),
        jsonb_build_object(
            'sequence', 2,
            'file_id', 'uvpendingfile02',
            'name', 'synthetic-pending.mp4',
            'mime_type', 'video/mp4',
            'size_bytes', 1048577,
            'checksum', 'sha256:' || repeat('2', 64)
        )
    )
);

CREATE TEMP TABLE uv_batch_repeat AS
SELECT *
FROM video_queue.enqueue_drive_batch(
    'uv-meta-only-20260901',
    'uvsourcefolder01',
    'uvoutputfolder01',
    'uvworkfolder0001',
    'bridge_3_1_free',
    '3.1-free-r25.16',
    'uvcanaryfile001',
    repeat('a', 64),
    jsonb_build_array(
        jsonb_build_object(
            'sequence', 1,
            'file_id', 'uvcanaryfile001',
            'name', 'synthetic-canary.mp4',
            'mime_type', 'video/mp4',
            'size_bytes', 1048576,
            'checksum', 'sha256:' || repeat('1', 64)
        ),
        jsonb_build_object(
            'sequence', 2,
            'file_id', 'uvpendingfile02',
            'name', 'synthetic-pending.mp4',
            'mime_type', 'video/mp4',
            'size_bytes', 1048577,
            'checksum', 'sha256:' || repeat('2', 64)
        )
    )
);

DO $idempotence$
DECLARE
    v_first uuid;
    v_repeat uuid;
BEGIN
    SELECT batch_id INTO v_first FROM uv_batch_first;
    SELECT batch_id INTO v_repeat FROM uv_batch_repeat;
    IF v_first IS NULL OR v_first <> v_repeat THEN
        RAISE EXCEPTION 'UV_META_IDEMPOTENCE_FAILED';
    END IF;
    IF (SELECT count(*) FROM video_queue.batch) <> 1
       OR (SELECT count(*) FROM video_queue.job) <> 2
       OR (SELECT count(*) FROM video_queue.job WHERE status = 'QUEUED' AND is_canary) <> 1
       OR (SELECT count(*) FROM video_queue.job WHERE status = 'PENDING_CANARY' AND NOT is_canary) <> 1 THEN
        RAISE EXCEPTION 'UV_META_INITIAL_STATE_INVALID';
    END IF;
    IF EXISTS (
        SELECT 1 FROM video_queue.batch
        WHERE result_mode <> 'SHADOW_REVIEW_ONLY'
           OR canonical_promotion_allowed
           OR database_persistence_allowed
    ) THEN
        RAISE EXCEPTION 'UV_META_SHADOW_POLICY_INVALID';
    END IF;
END
$idempotence$;

DO $duplicate$
BEGIN
    BEGIN
        PERFORM * FROM video_queue.enqueue_drive_batch(
            'uv-meta-duplicate-source',
            'uvsourcefolder02',
            'uvoutputfolder02',
            'uvworkfolder0002',
            'bridge_3_1_free',
            '3.1-free-r25.16',
            'uvduplicate0001',
            repeat('b', 64),
            jsonb_build_array(
                jsonb_build_object(
                    'sequence', 1, 'file_id', 'uvduplicate0001',
                    'name', 'one.mp4', 'mime_type', 'video/mp4',
                    'size_bytes', 1048576, 'checksum', null
                ),
                jsonb_build_object(
                    'sequence', 2, 'file_id', 'uvduplicate0001',
                    'name', 'two.mp4', 'mime_type', 'video/mp4',
                    'size_bytes', 1048576, 'checksum', null
                )
            )
        );
        RAISE EXCEPTION 'UV_META_DUPLICATE_SOURCE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF position('VIDEO_BATCH_DUPLICATE_SOURCE' in SQLERRM) = 0 THEN
            RAISE;
        END IF;
    END;
END
$duplicate$;

CREATE TEMP TABLE uv_claim_one AS
SELECT * FROM video_queue.claim_job(
    'uv-meta-worker-a', 60, 'bridge_3_1_free', '3.1-free-r25.16'
);

DO $claim_one$
BEGIN
    IF (SELECT count(*) FROM uv_claim_one) <> 1
       OR NOT (SELECT is_canary FROM uv_claim_one)
       OR (SELECT attempt_count FROM uv_claim_one) <> 1 THEN
        RAISE EXCEPTION 'UV_META_FIRST_CLAIM_INVALID';
    END IF;
END
$claim_one$;

SELECT video_queue.heartbeat_job(job_id, lease_token, 'uv-meta-worker-a', 60)
FROM uv_claim_one;

DO $stale_token_before_reclaim$
DECLARE
    v_job uuid;
BEGIN
    SELECT job_id INTO v_job FROM uv_claim_one;
    BEGIN
        PERFORM video_queue.heartbeat_job(v_job, gen_random_uuid(), 'uv-meta-worker-a', 60);
        RAISE EXCEPTION 'UV_META_STALE_FENCE_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF position('VIDEO_QUEUE_LEASE_LOST' in SQLERRM) = 0 THEN
            RAISE;
        END IF;
    END;
END
$stale_token_before_reclaim$;

UPDATE video_queue.job
SET lease_expires_at = clock_timestamp() - interval '1 second'
WHERE job_id = (SELECT job_id FROM uv_claim_one);

CREATE TEMP TABLE uv_claim_two AS
SELECT * FROM video_queue.claim_job(
    'uv-meta-worker-b', 60, 'bridge_3_1_free', '3.1-free-r25.16'
);

DO $reclaim$
DECLARE
    v_old uuid;
    v_new uuid;
BEGIN
    SELECT lease_token INTO v_old FROM uv_claim_one;
    SELECT lease_token INTO v_new FROM uv_claim_two;
    IF (SELECT count(*) FROM uv_claim_two) <> 1
       OR (SELECT job_id FROM uv_claim_one) <> (SELECT job_id FROM uv_claim_two)
       OR v_old = v_new
       OR (SELECT attempt_count FROM uv_claim_two) <> 2 THEN
        RAISE EXCEPTION 'UV_META_STALE_LEASE_RECOVERY_FAILED';
    END IF;
    BEGIN
        PERFORM video_queue.heartbeat_job(
            (SELECT job_id FROM uv_claim_one), v_old, 'uv-meta-worker-a', 60
        );
        RAISE EXCEPTION 'UV_META_OLD_FENCE_ACCEPTED_AFTER_RECLAIM';
    EXCEPTION WHEN OTHERS THEN
        IF position('VIDEO_QUEUE_LEASE_LOST' in SQLERRM) = 0 THEN
            RAISE;
        END IF;
    END;
END
$reclaim$;

CREATE TEMP TABLE uv_retry AS
SELECT * FROM video_queue.retry_job(
    (SELECT job_id FROM uv_claim_two),
    (SELECT lease_token FROM uv_claim_two),
    'uv-meta-worker-b',
    'UV_SYNTHETIC_RETRY',
    3,
    1
);

DO $retry$
BEGIN
    IF (SELECT job_status FROM uv_retry) <> 'QUEUED'
       OR (SELECT retry_after FROM uv_retry) IS NULL THEN
        RAISE EXCEPTION 'UV_META_RETRY_STATE_INVALID';
    END IF;
END
$retry$;

UPDATE video_queue.job
SET next_attempt_at = clock_timestamp() - interval '1 second'
WHERE job_id = (SELECT job_id FROM uv_claim_two);

CREATE TEMP TABLE uv_claim_three AS
SELECT * FROM video_queue.claim_job(
    'uv-meta-worker-c', 60, 'bridge_3_1_free', '3.1-free-r25.16'
);

DO $fail_closed_output$
DECLARE
    c record;
BEGIN
    SELECT * INTO c FROM uv_claim_three;
    IF c.attempt_count <> 3 THEN
        RAISE EXCEPTION 'UV_META_RETRY_ATTEMPT_INVALID';
    END IF;
    BEGIN
        PERFORM * FROM video_queue.finish_job(
            c.job_id,
            c.lease_token,
            'uv-meta-worker-c',
            'REVIEW_READY',
            jsonb_build_object(
                'result_mode', 'SHADOW_REVIEW_ONLY',
                'canonical_promotion_allowed', true,
                'database_persistence_allowed', false,
                'publication_state', 'NOT_PUBLISHED',
                'source_file_id', c.source_file_id,
                'stable_job_key', c.stable_job_key,
                'algorithm_revision', c.algorithm_revision
            ),
            null
        );
        RAISE EXCEPTION 'UV_META_INVALID_OUTPUT_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF position('VIDEO_QUEUE_FINISH_ARGUMENT_INVALID' in SQLERRM) = 0 THEN
            RAISE;
        END IF;
    END;
END
$fail_closed_output$;

CREATE TEMP TABLE uv_finish_canary AS
SELECT * FROM uv_claim_three c
CROSS JOIN LATERAL video_queue.finish_job(
    c.job_id,
    c.lease_token,
    'uv-meta-worker-c',
    'REVIEW_READY',
    jsonb_build_object(
        'result_mode', 'SHADOW_REVIEW_ONLY',
        'canonical_promotion_allowed', false,
        'database_persistence_allowed', false,
        'publication_state', 'NOT_PUBLISHED',
        'source_file_id', c.source_file_id,
        'stable_job_key', c.stable_job_key,
        'algorithm_revision', c.algorithm_revision,
        'artifact_locators', jsonb_build_object(
            'manifest', 'metadata-only://manifest/canary'
        ),
        'terminal_receipt', jsonb_build_object(
            'readback_verified', true,
            'media_execution', 'DISABLED'
        )
    ),
    null
) f;

DO $release$
BEGIN
    IF (SELECT released_jobs FROM uv_finish_canary) <> 1
       OR (SELECT count(*) FROM video_queue.job WHERE status = 'QUEUED' AND NOT is_canary) <> 1 THEN
        RAISE EXCEPTION 'UV_META_CANARY_RELEASE_INVALID';
    END IF;
END
$release$;

CREATE TEMP TABLE uv_claim_final AS
SELECT * FROM video_queue.claim_job(
    'uv-meta-worker-d', 60, 'bridge_3_1_free', '3.1-free-r25.16'
);

CREATE TEMP TABLE uv_finish_final AS
SELECT * FROM uv_claim_final c
CROSS JOIN LATERAL video_queue.finish_job(
    c.job_id,
    c.lease_token,
    'uv-meta-worker-d',
    'REVIEW_READY',
    jsonb_build_object(
        'result_mode', 'SHADOW_REVIEW_ONLY',
        'canonical_promotion_allowed', false,
        'database_persistence_allowed', false,
        'publication_state', 'NOT_PUBLISHED',
        'source_file_id', c.source_file_id,
        'stable_job_key', c.stable_job_key,
        'algorithm_revision', c.algorithm_revision,
        'artifact_locators', jsonb_build_object(
            'manifest', 'metadata-only://manifest/final'
        ),
        'terminal_receipt', jsonb_build_object(
            'readback_verified', true,
            'media_execution', 'DISABLED'
        )
    ),
    null
) f;

DO $terminal$
BEGIN
    IF (SELECT count(*) FROM uv_claim_final) <> 1
       OR (SELECT count(*) FROM video_queue.job WHERE status = 'REVIEW_READY') <> 2
       OR EXISTS (
            SELECT 1 FROM video_queue.job
            WHERE status IN ('PENDING_CANARY','QUEUED','LEASED')
       )
       OR (SELECT status FROM video_queue.batch) <> 'REVIEW'
       OR EXISTS (
            SELECT 1 FROM video_queue.job
            WHERE output->>'publication_state' <> 'NOT_PUBLISHED'
               OR output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
               OR output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       ) THEN
        RAISE EXCEPTION 'UV_META_TERMINAL_STATE_INVALID';
    END IF;
END
$terminal$;

SELECT json_build_object(
    'gate', 'UV_QUEUE_METADATA_ONLY',
    'status', 'PASS',
    'synthetic_jobs', 2,
    'media_execution', 'DISABLED',
    'publication_state', 'NOT_PUBLISHED',
    'canonical_promotion_allowed', false,
    'database_persistence_allowed', false
)::text AS bounded_receipt;
