\set ON_ERROR_STOP on
BEGIN;

ALTER TABLE video_queue.batch
    DROP CONSTRAINT video_batch_status_check,
    DROP CONSTRAINT video_batch_completion_check;

ALTER TABLE video_queue.batch
    ADD CONSTRAINT video_batch_status_check CHECK (
        status IN ('QUEUED_CANARY','RUNNING','CANARY_BLOCKED','CANARY_REVIEW','REVIEW')
    ),
    ADD CONSTRAINT video_batch_completion_check CHECK (
        (status IN ('CANARY_BLOCKED','REVIEW')) = (completed_at IS NOT NULL)
    );

CREATE OR REPLACE FUNCTION video_queue.canonical_json_text(p_value jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_type text := jsonb_typeof(p_value);
    v_result text;
BEGIN
    CASE v_type
      WHEN 'object' THEN
        SELECT '{' || coalesce(
            string_agg(
                to_json(e.key)::text || ':' || video_queue.canonical_json_text(e.value),
                ',' ORDER BY e.key COLLATE "C"
            ),
            ''
        ) || '}'
          INTO v_result
          FROM jsonb_each(p_value) AS e(key, value);
      WHEN 'array' THEN
        SELECT '[' || coalesce(
            string_agg(video_queue.canonical_json_text(e.value), ',' ORDER BY e.ordinality),
            ''
        ) || ']'
          INTO v_result
          FROM jsonb_array_elements(p_value) WITH ORDINALITY AS e(value, ordinality);
      ELSE
        v_result := p_value::text;
    END CASE;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION video_queue.canonical_json_text(jsonb) FROM PUBLIC;
COMMENT ON FUNCTION video_queue.canonical_json_text(jsonb) IS
    'Internal compact UTF-8 canonical JSON used to bind Universal Video terminal evidence';

CREATE OR REPLACE FUNCTION video_queue.precanary_idle_snapshot()
RETURNS TABLE(claimable_jobs bigint, leased_jobs bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
    SELECT
        count(*) FILTER (
            WHERE status IN ('PENDING_CANARY','QUEUED','LEASED')
        )::bigint,
        count(*) FILTER (
            WHERE status = 'LEASED' AND lease_expires_at > CURRENT_TIMESTAMP
        )::bigint
    FROM video_queue.job
$$;

REVOKE ALL ON FUNCTION video_queue.precanary_idle_snapshot() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.precanary_idle_snapshot() TO bridge_school_worker;
COMMENT ON FUNCTION video_queue.precanary_idle_snapshot() IS
    'Bounded aggregate queue state for fail-closed pre-canary no-media proof';

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
    v_batch_status text;
    v_manifest jsonb;
    v_artifacts jsonb;
    v_source_identity jsonb;
    v_receipt jsonb;
    v_receipt_core jsonb;
    v_locators jsonb;
    v_manifest_sha text;
    v_manifest_computed_sha text;
    v_evidence_sha text;
    v_evidence_computed_sha text;
    v_master_id text;
    v_master_sha text;
    v_ai_done_id text;
    v_ai_done_sha text;
BEGIN
    IF p_outcome IS NULL OR p_outcome NOT IN ('REVIEW_READY','AMBIGUOUS','FAILED')
       OR p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_output IS NULL OR jsonb_typeof(p_output) IS DISTINCT FROM 'object'
       OR length(p_output::text) > 131072
       OR p_output->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
       OR (p_error_code IS NOT NULL AND p_error_code !~ '^UV_[A-Z0-9_]{1,96}$') THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_FINISH_ARGUMENT_INVALID';
    END IF;

    SELECT * INTO v_job
      FROM video_queue.job j
     WHERE j.job_id = p_job_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_job.status IS DISTINCT FROM 'LEASED'
       OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_owner IS DISTINCT FROM p_worker_key
       OR v_job.lease_expires_at IS NULL
       OR v_job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_LEASE_LOST';
    END IF;

    SELECT * INTO v_batch
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_BATCH_MISSING';
    END IF;

    IF p_output->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
       OR p_output->>'stable_job_key' IS DISTINCT FROM v_job.stable_job_key
       OR p_output->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_IDENTITY_MISMATCH';
    END IF;

    IF p_outcome = 'REVIEW_READY' THEN
        v_manifest := p_output->'artifact_manifest';
        v_locators := p_output->'artifact_locators';
        v_receipt := p_output->'terminal_receipt';
        v_manifest_sha := p_output->>'artifact_manifest_sha256';
        v_evidence_sha := p_output->>'terminal_evidence_sha256';
        v_master_id := p_output->>'master_pdf_drive_id';
        v_master_sha := p_output->>'master_pdf_sha256';
        v_ai_done_id := p_output->>'ai_done_drive_id';
        v_ai_done_sha := p_output->>'ai_done_sha256';

        IF jsonb_typeof(v_manifest) IS DISTINCT FROM 'object'
           OR jsonb_typeof(v_locators) IS DISTINCT FROM 'object'
           OR jsonb_typeof(v_receipt) IS DISTINCT FROM 'object'
           OR jsonb_typeof(v_manifest->'source_identity') IS DISTINCT FROM 'object'
           OR jsonb_typeof(v_manifest->'artifacts') IS DISTINCT FROM 'array'
           OR jsonb_array_length(v_manifest->'artifacts') IS DISTINCT FROM 2 THEN
            RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_CONTRACT_INVALID';
        END IF;

        v_artifacts := v_manifest->'artifacts';
        v_source_identity := v_manifest->'source_identity';
        v_receipt_core := v_receipt - 'evidence_sha256';

        v_manifest_computed_sha := encode(
            public.digest(
                convert_to(video_queue.canonical_json_text(v_manifest), 'UTF8'),
                'sha256'
            ),
            'hex'
        );
        v_evidence_computed_sha := encode(
            public.digest(
                convert_to(video_queue.canonical_json_text(v_receipt_core), 'UTF8'),
                'sha256'
            ),
            'hex'
        );

        IF v_manifest_sha IS NULL OR v_manifest_sha !~ '^[0-9a-f]{64}$'
           OR v_manifest_computed_sha IS DISTINCT FROM v_manifest_sha
           OR v_evidence_sha IS NULL OR v_evidence_sha !~ '^[0-9a-f]{64}$'
           OR v_evidence_computed_sha IS DISTINCT FROM v_evidence_sha
           OR v_receipt->>'evidence_sha256' IS DISTINCT FROM v_evidence_sha
           OR v_master_id IS NULL OR v_master_id !~ '^[A-Za-z0-9_-]{10,200}$'
           OR v_ai_done_id IS NULL OR v_ai_done_id !~ '^[A-Za-z0-9_-]{10,200}$'
           OR v_master_id IS NOT DISTINCT FROM v_ai_done_id
           OR v_master_id IS NOT DISTINCT FROM v_job.source_file_id
           OR v_ai_done_id IS NOT DISTINCT FROM v_job.source_file_id
           OR v_master_sha IS NULL OR v_master_sha !~ '^[0-9a-f]{64}$'
           OR v_ai_done_sha IS NULL OR v_ai_done_sha !~ '^[0-9a-f]{64}$'
           OR v_locators->>'master_pdf' IS DISTINCT FROM v_master_id
           OR v_locators->>'ai_done' IS DISTINCT FROM v_ai_done_id

           OR v_manifest->>'schema_version' IS DISTINCT FROM 'universal-video-artifact-manifest/v1'
           OR v_manifest->>'job_id' IS DISTINCT FROM v_job.stable_job_key
           OR v_manifest->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
           OR v_manifest->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision
           OR v_manifest->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
           OR v_manifest->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
           OR v_manifest->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
           OR v_manifest->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'

           OR v_source_identity->>'file_id' IS DISTINCT FROM v_job.source_file_id
           OR v_source_identity->>'name' IS DISTINCT FROM v_job.source_name
           OR v_source_identity->>'mime_type' IS DISTINCT FROM v_job.source_mime_type
           OR jsonb_typeof(v_source_identity->'size_bytes') IS DISTINCT FROM 'number'
           OR CASE
                WHEN jsonb_typeof(v_source_identity->'size_bytes') = 'number'
                THEN (v_source_identity->>'size_bytes')::numeric IS DISTINCT FROM v_job.source_size_bytes::numeric
                ELSE true
              END
           OR v_source_identity->>'parent_folder_id' IS DISTINCT FROM v_batch.source_folder_id
           OR v_source_identity->>'checksum' IS DISTINCT FROM v_job.source_checksum

           OR jsonb_typeof(v_artifacts->0) IS DISTINCT FROM 'object'
           OR v_artifacts->0->>'kind' IS DISTINCT FROM 'master_pdf'
           OR v_artifacts->0->>'drive_id' IS DISTINCT FROM v_master_id
           OR v_artifacts->0->>'locator' IS DISTINCT FROM ('gdrive:file:' || v_master_id)
           OR v_artifacts->0->>'mime_type' IS DISTINCT FROM 'application/pdf'
           OR v_artifacts->0->>'parent_id' IS DISTINCT FROM v_batch.output_folder_id
           OR v_artifacts->0->>'sha256' IS DISTINCT FROM v_master_sha
           OR jsonb_typeof(v_artifacts->0->'size_bytes') IS DISTINCT FROM 'number'
           OR CASE
                WHEN jsonb_typeof(v_artifacts->0->'size_bytes') = 'number'
                THEN (v_artifacts->0->>'size_bytes')::numeric <= 0
                ELSE true
              END

           OR jsonb_typeof(v_artifacts->1) IS DISTINCT FROM 'object'
           OR v_artifacts->1->>'kind' IS DISTINCT FROM 'ai_done'
           OR v_artifacts->1->>'drive_id' IS DISTINCT FROM v_ai_done_id
           OR v_artifacts->1->>'locator' IS DISTINCT FROM ('gdrive:file:' || v_ai_done_id)
           OR v_artifacts->1->>'mime_type' IS DISTINCT FROM 'application/json'
           OR v_artifacts->1->>'parent_id' IS DISTINCT FROM v_batch.output_folder_id
           OR v_artifacts->1->>'sha256' IS DISTINCT FROM v_ai_done_sha
           OR jsonb_typeof(v_artifacts->1->'size_bytes') IS DISTINCT FROM 'number'
           OR CASE
                WHEN jsonb_typeof(v_artifacts->1->'size_bytes') = 'number'
                THEN (v_artifacts->1->>'size_bytes')::numeric <= 0
                ELSE true
              END

           OR v_receipt->>'schema_version' IS DISTINCT FROM 'universal-video-terminal-receipt/v1'
           OR v_receipt->>'status' IS DISTINCT FROM 'PASS'
           OR v_receipt->>'job_id' IS DISTINCT FROM v_job.stable_job_key
           OR v_receipt->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
           OR v_receipt->'source_identity_verified' IS DISTINCT FROM 'true'::jsonb
           OR v_receipt->'drive_readback_verified' IS DISTINCT FROM 'true'::jsonb
           OR v_receipt->'result_readback_verified' IS DISTINCT FROM 'true'::jsonb
           OR v_receipt->'checksum_verified' IS DISTINCT FROM 'true'::jsonb
           OR jsonb_typeof(v_receipt->'artifact_count') IS DISTINCT FROM 'number'
           OR CASE
                WHEN jsonb_typeof(v_receipt->'artifact_count') = 'number'
                THEN (v_receipt->>'artifact_count')::numeric IS DISTINCT FROM 2::numeric
                ELSE true
              END
           OR v_receipt->>'artifact_manifest_sha256' IS DISTINCT FROM v_manifest_sha
           OR v_receipt->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
           OR v_receipt->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
           OR v_receipt->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED' THEN
            RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_CONTRACT_INVALID';
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
            'artifact_manifest_sha256', v_manifest_sha,
            'terminal_evidence_sha256', v_evidence_sha,
            'released_jobs', 0
        )
    );

    IF v_job.is_canary AND p_outcome = 'REVIEW_READY' THEN
        UPDATE video_queue.batch
           SET status = 'CANARY_REVIEW', updated_at = clock_timestamp(), completed_at = NULL
         WHERE batch_id = v_job.batch_id;
    ELSIF v_job.is_canary THEN
        UPDATE video_queue.batch
           SET status = 'CANARY_BLOCKED', updated_at = clock_timestamp(), completed_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id;
    ELSIF NOT EXISTS (
        SELECT 1 FROM video_queue.job
         WHERE batch_id = v_job.batch_id
           AND status IN ('PENDING_CANARY','QUEUED','LEASED')
    ) THEN
        UPDATE video_queue.batch
           SET status = 'REVIEW', updated_at = clock_timestamp(), completed_at = clock_timestamp()
         WHERE batch_id = v_job.batch_id;
    END IF;

    SELECT b.status INTO v_batch_status
      FROM video_queue.batch b
     WHERE b.batch_id = v_job.batch_id;
    RETURN QUERY SELECT p_outcome, v_batch_status, 0;
END;
$$;

REVOKE ALL ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) TO bridge_school_worker;
COMMENT ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) IS
    'Fail-closed v2 terminal finalization; verified canary enters CANARY_REVIEW and releases zero jobs';

INSERT INTO schema_migration(migration_key)
VALUES ('0057_universal_video_canary_review_gate')
ON CONFLICT DO NOTHING;

COMMIT;
