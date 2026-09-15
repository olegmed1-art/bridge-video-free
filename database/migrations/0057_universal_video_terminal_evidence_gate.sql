\set ON_ERROR_STOP on
BEGIN;

-- PostgreSQL's jsonb text form is not the compact encoding used by the Python
-- evidence producer.  Render the deliberately bounded evidence JSON with the
-- same sorted-key, no-whitespace contract before hashing it.
CREATE OR REPLACE FUNCTION video_queue.canonical_json(p_value jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $$
BEGIN
RETURN CASE jsonb_typeof(p_value)
    WHEN 'object' THEN '{' || coalesce((
        SELECT string_agg(to_jsonb(key)::text || ':' || video_queue.canonical_json(value), ',' ORDER BY key COLLATE "C")
          FROM jsonb_each(p_value)
    ), '') || '}'
    WHEN 'array' THEN '[' || coalesce((
        SELECT string_agg(video_queue.canonical_json(value), ',' ORDER BY ordinality)
          FROM jsonb_array_elements(p_value) WITH ORDINALITY AS item(value, ordinality)
    ), '') || ']'
    ELSE p_value::text
END;
END
$$;

CREATE OR REPLACE FUNCTION video_queue.assert_terminal_evidence(
    p_output jsonb,
    p_job video_queue.job,
    p_batch video_queue.batch
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_manifest jsonb := p_output->'manifest';
    v_locators jsonb := p_output->'artifact_locators';
    v_receipt jsonb := p_output->'terminal_receipt';
    v_source jsonb;
    v_master jsonb;
    v_done jsonb;
    v_manifest_sha text;
    v_evidence_core jsonb;
    v_evidence_sha text;
BEGIN
    v_source := jsonb_build_object(
        'file_id', p_job.source_file_id,
        'name', p_job.source_name,
        'mime_type', p_job.source_mime_type,
        'size_bytes', p_job.source_size_bytes,
        'parent_folder_id', p_batch.source_folder_id,
        'checksum', p_job.source_checksum
    );

    IF p_job.source_checksum IS NULL
       OR jsonb_typeof(v_manifest) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_locators) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_receipt) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID';
    END IF;
    IF jsonb_typeof(v_manifest->'artifacts') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(v_locators)) <> 2
       OR NOT (v_locators ?& ARRAY['master_pdf_drive_id','ai_done_drive_id'])
       OR ((v_locators->>'master_pdf_drive_id') ~ '^[A-Za-z0-9_-]{10,200}$') IS NOT TRUE
       OR ((v_locators->>'ai_done_drive_id') ~ '^[A-Za-z0-9_-]{10,200}$') IS NOT TRUE
       OR v_locators->>'master_pdf_drive_id' = v_locators->>'ai_done_drive_id'
       OR p_job.source_file_id IN (v_locators->>'master_pdf_drive_id', v_locators->>'ai_done_drive_id')
       OR v_manifest->>'schema' IS DISTINCT FROM 'universal-video-terminal-manifest-v1'
       OR v_manifest->>'job_id' IS DISTINCT FROM p_job.stable_job_key
       OR v_manifest->>'source_file_id' IS DISTINCT FROM p_job.source_file_id
       OR v_manifest->'source_identity' IS DISTINCT FROM v_source
       OR v_manifest->>'algorithm_revision' IS DISTINCT FROM p_batch.algorithm_revision
       OR v_manifest->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR v_manifest->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
       OR v_manifest->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_manifest->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR jsonb_array_length(v_manifest->'artifacts') <> 2 THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_EVIDENCE_INVALID';
    END IF;

    SELECT value INTO v_master FROM jsonb_array_elements(v_manifest->'artifacts') WHERE value->>'kind'='master_pdf';
    SELECT value INTO v_done FROM jsonb_array_elements(v_manifest->'artifacts') WHERE value->>'kind'='ai_done';
    IF v_master IS NULL OR v_done IS NULL
       OR (SELECT count(*) FROM jsonb_array_elements(v_manifest->'artifacts') WHERE value->>'kind' IN ('master_pdf','ai_done')) <> 2
       OR v_master->>'drive_file_id' IS DISTINCT FROM v_locators->>'master_pdf_drive_id'
       OR v_done->>'drive_file_id' IS DISTINCT FROM v_locators->>'ai_done_drive_id'
       OR v_master->>'mime_type' IS DISTINCT FROM 'application/pdf'
       OR v_done->>'mime_type' IS DISTINCT FROM 'application/json'
       OR v_master->>'parent_folder_id' IS DISTINCT FROM p_batch.output_folder_id
       OR v_done->>'parent_folder_id' IS DISTINCT FROM p_batch.output_folder_id
       OR coalesce((v_master->>'size_bytes') ~ '^[1-9][0-9]*$', false) IS NOT TRUE
       OR coalesce((v_done->>'size_bytes') ~ '^[1-9][0-9]*$', false) IS NOT TRUE
       OR coalesce((v_master->>'sha256') ~ '^[0-9a-f]{64}$', false) IS NOT TRUE
       OR coalesce((v_done->>'sha256') ~ '^[0-9a-f]{64}$', false) IS NOT TRUE
       OR coalesce((v_master->>'md5') ~ '^[0-9a-f]{32}$', false) IS NOT TRUE
       OR coalesce((v_done->>'md5') ~ '^[0-9a-f]{32}$', false) IS NOT TRUE
       OR nullif(btrim(v_master->>'name'),'') IS NULL
       OR nullif(btrim(v_done->>'name'),'') IS NULL THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_ARTIFACT_INVALID';
    END IF;

    v_manifest_sha := encode(public.digest(convert_to(video_queue.canonical_json(v_manifest), 'UTF8'), 'sha256'), 'hex');
    v_evidence_core := v_receipt - 'evidence_sha256';
    v_evidence_sha := encode(public.digest(convert_to(video_queue.canonical_json(v_evidence_core), 'UTF8'), 'sha256'), 'hex');
    IF (SELECT count(*) FROM jsonb_object_keys(v_receipt)) <> 15
       OR v_receipt->>'schema' IS DISTINCT FROM 'universal-video-terminal-receipt-v1'
       OR v_receipt->>'job_id' IS DISTINCT FROM p_job.stable_job_key
       OR v_receipt->>'source_file_id' IS DISTINCT FROM p_job.source_file_id
       OR v_receipt->'source_identity' IS DISTINCT FROM v_source
       OR v_receipt->'source_identity_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->'route_readback_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->'result_readback_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->'checksum_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->>'manifest_sha256' IS DISTINCT FROM v_manifest_sha
       OR v_receipt->'artifact_count' IS DISTINCT FROM '2'::jsonb
       OR v_receipt->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
       OR v_receipt->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_receipt->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_receipt->'media_execution_evidence_only' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->>'evidence_sha256' IS DISTINCT FROM v_evidence_sha
       OR p_output->>'terminal_evidence_sha256' IS DISTINCT FROM v_evidence_sha THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_RECEIPT_INVALID';
    END IF;
END;
$$;

-- Preserve the established transition and insert the new check after both rows
-- are locked, but before the first job/event/batch mutation.
CREATE OR REPLACE FUNCTION video_queue.finish_job(
    p_job_id uuid, p_lease_token uuid, p_worker_key text, p_outcome text,
    p_output jsonb, p_error_code text DEFAULT NULL
)
RETURNS TABLE(job_status text, batch_status text, released_jobs integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue
AS $$
DECLARE
    v_job video_queue.job%ROWTYPE; v_batch video_queue.batch%ROWTYPE;
    v_released integer := 0; v_batch_status text;
BEGIN
    IF p_outcome IS NULL OR p_outcome NOT IN ('REVIEW_READY','AMBIGUOUS','FAILED') OR p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_output IS NULL OR jsonb_typeof(p_output) <> 'object' OR length(p_output::text) > 65536
       OR p_output->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY' OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb OR p_output->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
       OR (p_error_code IS NOT NULL AND p_error_code !~ '^UV_[A-Z0-9_]{1,96}$') THEN RAISE EXCEPTION 'VIDEO_QUEUE_FINISH_ARGUMENT_INVALID'; END IF;
    SELECT * INTO v_job FROM video_queue.job j WHERE j.job_id=p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status<>'LEASED' OR v_job.lease_token IS DISTINCT FROM p_lease_token OR v_job.lease_owner IS DISTINCT FROM p_worker_key OR v_job.lease_expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'VIDEO_QUEUE_LEASE_LOST'; END IF;
    SELECT * INTO v_batch FROM video_queue.batch b WHERE b.batch_id=v_job.batch_id FOR UPDATE;
    IF p_output->>'source_file_id' IS DISTINCT FROM v_job.source_file_id OR p_output->>'stable_job_key' IS DISTINCT FROM v_job.stable_job_key OR p_output->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision THEN RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_IDENTITY_MISMATCH'; END IF;
    IF p_outcome='REVIEW_READY' THEN PERFORM video_queue.assert_terminal_evidence(p_output,v_job,v_batch); END IF;

    UPDATE video_queue.job SET status=p_outcome,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,error_code=p_error_code,output=p_output,updated_at=clock_timestamp(),completed_at=clock_timestamp() WHERE job_id=p_job_id;
    INSERT INTO video_queue.job_event(batch_id,job_id,event_type,worker_key,lease_token,details) VALUES(v_job.batch_id,v_job.job_id,p_outcome,p_worker_key,p_lease_token,jsonb_build_object('error_code',p_error_code));
    IF v_job.is_canary AND p_outcome='REVIEW_READY' THEN
      UPDATE video_queue.job SET status='QUEUED',updated_at=clock_timestamp() WHERE batch_id=v_job.batch_id AND status='PENDING_CANARY'; GET DIAGNOSTICS v_released=ROW_COUNT;
      UPDATE video_queue.batch SET status='RUNNING',updated_at=clock_timestamp() WHERE batch_id=v_job.batch_id;
      IF v_released>0 THEN PERFORM pg_notify('video_queue_ready',v_job.batch_id::text); END IF;
    ELSIF v_job.is_canary THEN UPDATE video_queue.batch SET status='CANARY_BLOCKED',updated_at=clock_timestamp(),completed_at=clock_timestamp() WHERE batch_id=v_job.batch_id; END IF;
    IF NOT v_job.is_canary OR p_outcome='REVIEW_READY' THEN
      IF NOT EXISTS(SELECT 1 FROM video_queue.job WHERE batch_id=v_job.batch_id AND status IN ('PENDING_CANARY','QUEUED','LEASED')) THEN UPDATE video_queue.batch SET status='REVIEW',updated_at=clock_timestamp(),completed_at=clock_timestamp() WHERE batch_id=v_job.batch_id; END IF;
    END IF;
    SELECT b.status INTO v_batch_status FROM video_queue.batch b WHERE b.batch_id=v_job.batch_id;
    RETURN QUERY SELECT p_outcome,v_batch_status,v_released;
END;
$$;

REVOKE ALL ON FUNCTION video_queue.canonical_json(jsonb) FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE ALL ON FUNCTION video_queue.assert_terminal_evidence(jsonb,video_queue.job,video_queue.batch) FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker;
REVOKE ALL ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.finish_job(uuid,uuid,text,text,jsonb,text) TO bridge_school_worker;

INSERT INTO schema_migration(migration_key) VALUES ('0057_universal_video_terminal_evidence_gate') ON CONFLICT DO NOTHING;
COMMIT;
