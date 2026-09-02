\set ON_ERROR_STOP on
BEGIN;

-- Follow-up hardening for 0056. REVIEW_READY is terminal only after the worker
-- presents bounded, hash-bound Drive readback evidence. The queue continues to
-- store metadata and locators only; no media bytes are persisted here.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0056_universal_video_queue'
    ) OR to_regprocedure('video_queue.finish_job(uuid,uuid,text,text,jsonb,text)') IS NULL THEN
        RAISE EXCEPTION 'VIDEO_TERMINAL_EVIDENCE_REQUIRES_0056';
    END IF;
END;
$$;

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
    v_master_locator text;
    v_ai_done_locator text;
    v_terminal_hash text;
BEGIN
    IF p_outcome NOT IN ('REVIEW_READY','AMBIGUOUS','FAILED')
       OR p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_output IS NULL OR jsonb_typeof(p_output) <> 'object'
       OR length(p_output::text) > 65536
       OR p_output->>'result_mode' <> 'SHADOW_REVIEW_ONLY'
       OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->>'publication_state' <> 'NOT_PUBLISHED'
       OR (p_error_code IS NOT NULL AND p_error_code !~ '^UV_[A-Z0-9_]{1,96}$')
       OR (p_outcome = 'REVIEW_READY' AND p_error_code IS NOT NULL) THEN
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

    IF p_outcome = 'REVIEW_READY' THEN
        IF jsonb_typeof(p_output->'manifest') IS DISTINCT FROM 'object'
           OR jsonb_typeof(p_output->'artifact_locators') IS DISTINCT FROM 'object'
           OR jsonb_typeof(p_output->'terminal_receipt') IS DISTINCT FROM 'object'
           OR p_output->>'terminal_evidence_sha256' !~ '^[0-9a-f]{64}$'
           OR p_output->'terminal_receipt'->>'evidence_sha256' !~ '^[0-9a-f]{64}$'
           OR p_output->'terminal_receipt'->>'manifest_sha256' !~ '^[0-9a-f]{64}$'
           OR p_output->'terminal_receipt'->>'evidence_sha256'
                IS DISTINCT FROM p_output->>'terminal_evidence_sha256'
           OR p_output->'terminal_receipt'->>'schema'
                IS DISTINCT FROM 'universal-video-terminal-receipt-v1'
           OR p_output->'terminal_receipt'->>'job_id' IS DISTINCT FROM v_job.stable_job_key
           OR p_output->'terminal_receipt'->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
           OR p_output->'terminal_receipt'->'source_identity_verified' IS DISTINCT FROM 'true'::jsonb
           OR p_output->'terminal_receipt'->'route_readback_verified' IS DISTINCT FROM 'true'::jsonb
           OR p_output->'terminal_receipt'->'result_readback_verified' IS DISTINCT FROM 'true'::jsonb
           OR p_output->'terminal_receipt'->'checksum_verified' IS DISTINCT FROM 'true'::jsonb
           OR p_output->'terminal_receipt'->>'artifact_count' IS DISTINCT FROM '2'
           OR p_output->'terminal_receipt'->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
           OR p_output->'manifest'->>'schema' IS DISTINCT FROM 'universal-video-terminal-manifest-v1'
           OR p_output->'manifest'->>'job_id' IS DISTINCT FROM v_job.stable_job_key
           OR p_output->'manifest'->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
           OR p_output->'manifest'->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision
           OR p_output->'manifest'->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
           OR p_output->'manifest'->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
           OR p_output->'manifest'->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
           OR p_output->'manifest'->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
           OR jsonb_typeof(p_output->'manifest'->'artifacts') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID';
        END IF;
        IF jsonb_array_length(p_output->'manifest'->'artifacts') <> 2 THEN
            RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID';
        END IF;

        v_master_locator := p_output->'artifact_locators'->>'master_pdf_drive_id';
        v_ai_done_locator := p_output->'artifact_locators'->>'ai_done_drive_id';
        v_terminal_hash := p_output->>'terminal_evidence_sha256';
        IF v_master_locator !~ '^[A-Za-z0-9_-]{10,200}$'
           OR v_ai_done_locator !~ '^[A-Za-z0-9_-]{10,200}$'
           OR v_master_locator = v_ai_done_locator
           OR v_master_locator = v_job.source_file_id
           OR v_ai_done_locator = v_job.source_file_id
           OR v_terminal_hash IS DISTINCT FROM p_output->'terminal_receipt'->>'evidence_sha256'
           OR (SELECT count(*)
                 FROM jsonb_array_elements(p_output->'manifest'->'artifacts') AS artifact
                WHERE artifact->>'kind' = 'master_pdf'
                  AND artifact->>'drive_file_id' = v_master_locator
                  AND artifact->>'mime_type' = 'application/pdf'
                  AND artifact->>'parent_folder_id' = v_batch.output_folder_id
                  AND artifact->>'sha256' ~ '^[0-9a-f]{64}$'
                  AND artifact->>'md5' ~ '^[0-9a-f]{32}$'
                  AND artifact->>'size_bytes' ~ '^[1-9][0-9]*$') <> 1
           OR (SELECT count(*)
                 FROM jsonb_array_elements(p_output->'manifest'->'artifacts') AS artifact
                WHERE artifact->>'kind' = 'ai_done'
                  AND artifact->>'drive_file_id' = v_ai_done_locator
                  AND artifact->>'mime_type' IN ('application/json','text/json','text/plain')
                  AND artifact->>'parent_folder_id' = v_batch.output_folder_id
                  AND artifact->>'sha256' ~ '^[0-9a-f]{64}$'
                  AND artifact->>'md5' ~ '^[0-9a-f]{32}$'
                  AND artifact->>'size_bytes' ~ '^[1-9][0-9]*$') <> 1 THEN
            RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID';
        END IF;
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
        jsonb_build_object(
            'error_code', p_error_code,
            'terminal_evidence_sha256',
            CASE WHEN p_outcome = 'REVIEW_READY' THEN v_terminal_hash ELSE NULL END
        )
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

COMMENT ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) IS
    'Fenced terminal transition; REVIEW_READY requires Drive readback, checksums, manifest, artifact locators and a hash-bound receipt';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0057_universal_video_terminal_evidence_gate')
ON CONFLICT DO NOTHING;

COMMIT;
