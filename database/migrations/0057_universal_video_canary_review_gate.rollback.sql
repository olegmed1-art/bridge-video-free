\set ON_ERROR_STOP on
BEGIN;

-- Preserve all queue rows while returning CANARY_REVIEW batches to a safe
-- terminal state accepted by the 0056 lifecycle. Never release their pending
-- jobs as part of rollback.
UPDATE video_queue.batch
   SET status = 'CANARY_BLOCKED',
       completed_at = coalesce(completed_at, clock_timestamp()),
       updated_at = clock_timestamp()
 WHERE status = 'CANARY_REVIEW';

ALTER TABLE video_queue.batch
    DROP CONSTRAINT video_batch_status_check,
    DROP CONSTRAINT video_batch_completion_check;

ALTER TABLE video_queue.batch
    ADD CONSTRAINT video_batch_status_check CHECK (
        status IN ('QUEUED_CANARY','RUNNING','CANARY_BLOCKED','REVIEW')
    ),
    ADD CONSTRAINT video_batch_completion_check CHECK (
        (status IN ('CANARY_BLOCKED','REVIEW')) = (completed_at IS NOT NULL)
    );

CREATE OR REPLACE FUNCTION video_queue.finish_job(
    p_job_id uuid,
    p_lease_token uuid,
    p_worker_key text,
    p_outcome text,
    p_output jsonb,
    p_error_code text DEFAULT NULL
)
RETURNS TABLE(job_status text, batch_status text, released_jobs integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_released integer := 0;
    v_batch_status text;
BEGIN
    IF p_outcome NOT IN ('REVIEW_READY','AMBIGUOUS','FAILED')
       OR p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_output IS NULL OR jsonb_typeof(p_output) <> 'object'
       OR length(p_output::text) > 65536
       OR p_output->>'result_mode' <> 'SHADOW_REVIEW_ONLY'
       OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->>'publication_state' <> 'NOT_PUBLISHED'
       OR (p_error_code IS NOT NULL AND p_error_code !~ '^UV_[A-Z0-9_]{1,96}$') THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_FINISH_ARGUMENT_INVALID';
    END IF;
    SELECT * INTO v_job
      FROM video_queue.job j
     WHERE j.job_id = p_job_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_job.status <> 'LEASED'
       OR v_job.lease_token <> p_lease_token
       OR v_job.lease_owner <> p_worker_key
       OR v_job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_LEASE_LOST';
    END IF;
    SELECT * INTO v_batch
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id
     FOR UPDATE;
    IF p_output->>'source_file_id' <> v_job.source_file_id
       OR p_output->>'stable_job_key' <> v_job.stable_job_key
       OR p_output->>'algorithm_revision' <> v_batch.algorithm_revision THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_IDENTITY_MISMATCH';
    END IF;

    UPDATE video_queue.job
       SET status = p_outcome,
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           error_code = p_error_code,
           output = p_output,
           updated_at = clock_timestamp(),
           completed_at = clock_timestamp()
     WHERE job_id = p_job_id;
    INSERT INTO video_queue.job_event(batch_id, job_id, event_type, worker_key, lease_token, details)
    VALUES (
        v_job.batch_id,
        v_job.job_id,
        p_outcome,
        p_worker_key,
        p_lease_token,
        jsonb_build_object('error_code', p_error_code)
    );

    IF v_job.is_canary AND p_outcome = 'REVIEW_READY' THEN
        UPDATE video_queue.job
           SET status = 'QUEUED', updated_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id AND status = 'PENDING_CANARY';
        GET DIAGNOSTICS v_released = ROW_COUNT;
        UPDATE video_queue.batch
           SET status = 'RUNNING', updated_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id;
        IF v_released > 0 THEN
            PERFORM pg_notify('video_queue_ready', v_job.batch_id::text);
        END IF;
    ELSIF v_job.is_canary THEN
        UPDATE video_queue.batch
           SET status = 'CANARY_BLOCKED',
               updated_at = clock_timestamp(),
               completed_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id;
    END IF;

    IF NOT v_job.is_canary OR p_outcome = 'REVIEW_READY' THEN
        IF NOT EXISTS (
            SELECT 1 FROM video_queue.job
             WHERE batch_id = v_job.batch_id
               AND status IN ('PENDING_CANARY','QUEUED','LEASED')
        ) THEN
            UPDATE video_queue.batch
               SET status = 'REVIEW',
                   updated_at = clock_timestamp(),
                   completed_at = clock_timestamp()
             WHERE batch_id = v_job.batch_id;
        END IF;
    END IF;
    SELECT b.status INTO v_batch_status
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id;
    RETURN QUERY SELECT p_outcome, v_batch_status, v_released;
END;
$$;

REVOKE ALL ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text)
    TO bridge_school_worker;

DROP FUNCTION video_queue.precanary_idle_snapshot();
DROP FUNCTION video_queue.canonical_json_text(jsonb);

DELETE FROM schema_migration
 WHERE migration_key = '0057_universal_video_canary_review_gate';

COMMIT;
