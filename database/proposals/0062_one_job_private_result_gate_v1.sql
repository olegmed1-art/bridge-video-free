-- LOCAL DESIGN CANDIDATE ONLY; after unapplied 0061. No production install.
-- The video_subset_worker_v2 login gets no public result/candidate DML.
-- Its one-job write surface is this SECURITY DEFINER function only.
BEGIN;

CREATE TABLE video_queue.one_job_private_result_v1 (
    receipt_id uuid PRIMARY KEY,
    admission_id uuid NOT NULL REFERENCES video_queue.one_job_staging_admission_v1,
    job_id uuid NOT NULL REFERENCES video_queue.job,
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    master_json_sha256 text NOT NULL CHECK (master_json_sha256 ~ '^[0-9a-f]{64}$'),
    ai_done_drive_id text NOT NULL CHECK (ai_done_drive_id ~ '^[A-Za-z0-9_-]{10,200}$'),
    ai_done_sha256 text NOT NULL CHECK (ai_done_sha256 ~ '^[0-9a-f]{64}$'),
    bundle_sha256 text NOT NULL CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    master_json_text text NOT NULL CHECK (octet_length(master_json_text) BETWEEN 2 AND 33554432),
    ai_done_json_text text NOT NULL CHECK (octet_length(ai_done_json_text) BETWEEN 2 AND 4194304),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (admission_id,bundle_sha256)
);
CREATE TABLE video_queue.one_job_private_result_attempt_v1 (
    job_id uuid NOT NULL REFERENCES video_queue.job,
    attempt_count integer NOT NULL CHECK (attempt_count BETWEEN 1 AND 3),
    receipt_id uuid NOT NULL REFERENCES video_queue.one_job_private_result_v1,
    lease_token uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (job_id,attempt_count)
);
-- The base result is immutable. A later approved retry may attest the same
-- exact bundle under a new lease, never replace the prior attempt's token.

CREATE FUNCTION video_queue.stage_one_job_private_result_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid,
    p_worker_key text, p_attempt_count integer, p_source_fingerprint text,
    p_receipt_id uuid, p_master_json_text text,
    p_ai_done_drive_id text, p_ai_done_json_text text
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_approval video_queue.subset_approval%ROWTYPE;
    v_scope video_queue.one_job_staging_admission_v1%ROWTYPE;
    v_stored video_queue.one_job_private_result_v1%ROWTYPE;
    v_attempt video_queue.one_job_private_result_attempt_v1%ROWTYPE;
    v_master jsonb;
    v_done jsonb;
    v_source_file jsonb;
    v_master_sha text;
    v_done_sha text;
    v_bundle_sha text;
BEGIN
    IF pg_has_role(session_user,'bridge_school_worker','MEMBER')
       OR pg_has_role(session_user,'bridge_school_app','MEMBER')
       OR pg_has_role(session_user,'video_subset_approver','MEMBER') THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_ROLE_CONFLICT';
    END IF;
    -- This check holds the live job row lock through transaction commit and
    -- requires the separate exact-attempt approver decision from 0061.
    PERFORM video_queue.check_one_job_staging_scope_v1(
        p_admission_id,p_job_id,p_lease_token,p_worker_key,
        p_attempt_count,p_source_fingerprint,'queue');
    IF p_receipt_id IS NULL OR p_master_json_text IS NULL
       OR octet_length(p_master_json_text) NOT BETWEEN 2 AND 33554432
       OR p_ai_done_drive_id IS NULL
       OR p_ai_done_drive_id !~ '^[A-Za-z0-9_-]{10,200}$'
       OR p_ai_done_json_text IS NULL
       OR octet_length(p_ai_done_json_text) NOT BETWEEN 2 AND 4194304 THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_RESULT_ARGUMENT_INVALID';
    END IF;
    v_master := p_master_json_text::jsonb;
    v_done := p_ai_done_json_text::jsonb;
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch WHERE q_batch.batch_id=v_job.batch_id;
    SELECT q_scope.* INTO v_scope FROM video_queue.one_job_staging_admission_v1 AS q_scope
        WHERE q_scope.admission_id=p_admission_id;
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval
        WHERE q_approval.approval_id=v_scope.subset_approval_id;
    SELECT q_file.value INTO v_source_file
      FROM jsonb_array_elements(v_approval.contract->'files') AS q_file(value)
     WHERE q_file.value->>'file_id'=v_job.source_file_id;
    v_master_sha := encode(public.digest(convert_to(p_master_json_text,'UTF8'),'sha256'),'hex');
    v_done_sha := encode(public.digest(convert_to(p_ai_done_json_text,'UTF8'),'sha256'),'hex');
    IF jsonb_typeof(v_master) IS DISTINCT FROM 'object'
       OR jsonb_typeof(v_done) IS DISTINCT FROM 'object'
       OR v_master->>'job_id' IS DISTINCT FROM v_job.stable_job_key
       OR v_done->>'job_id' IS DISTINCT FROM v_job.stable_job_key
       OR v_done->>'status' IS DISTINCT FROM 'AI_DONE'
       OR v_master->>'algorithmRevision' IS DISTINCT FROM v_batch.algorithm_revision
       OR v_done->>'algorithmRevision' IS DISTINCT FROM v_batch.algorithm_revision
       OR v_master#>>'{source,driveId}' IS DISTINCT FROM v_job.source_file_id
       OR v_master#>>'{source,name}' IS DISTINCT FROM v_job.source_name
       OR v_master#>>'{source,mimeType}' IS DISTINCT FROM v_job.source_mime_type
       OR v_master#>'{source,sizeBytes}' IS DISTINCT FROM to_jsonb(v_job.source_size_bytes)
       OR v_master#>>'{source,parentFolderId}' IS DISTINCT FROM v_batch.source_folder_id
       OR NOT coalesce((v_master#>>'{source,sha256}') ~ '^[0-9a-f]{64}$',false)
       OR (v_job.source_checksum LIKE 'sha256:%' AND
           'sha256:' || (v_master#>>'{source,sha256}') IS DISTINCT FROM v_job.source_checksum)
       OR v_source_file->>'source_fingerprint' IS DISTINCT FROM p_source_fingerprint
       OR v_done#>>'{masterPdf,masterJsonSha256}' IS DISTINCT FROM v_master_sha
       OR NOT coalesce((v_done#>>'{masterPdf,sha256}') ~ '^[0-9a-f]{64}$',false)
       OR NULLIF(v_done#>>'{masterPdf,driveId}','') IS NULL THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_RESULT_SOURCE_OR_GENERATION_HOLD';
    END IF;
    v_bundle_sha := encode(public.digest(convert_to(
        video_queue.canonical_json_text(jsonb_build_object(
            'schema','one-job-private-result-v1','master_json_sha256',v_master_sha,
            'ai_done_drive_id',p_ai_done_drive_id,
            'ai_done_sha256',v_done_sha)),'UTF8'),'sha256'),'hex');
    INSERT INTO video_queue.one_job_private_result_v1
        (receipt_id,admission_id,job_id,source_fingerprint,master_json_sha256,
         ai_done_drive_id,ai_done_sha256,bundle_sha256,master_json_text,ai_done_json_text)
    VALUES (p_receipt_id,p_admission_id,p_job_id,p_source_fingerprint,v_master_sha,
            p_ai_done_drive_id,v_done_sha,v_bundle_sha,p_master_json_text,p_ai_done_json_text)
    ON CONFLICT DO NOTHING;
    SELECT q_result.* INTO v_stored FROM video_queue.one_job_private_result_v1 AS q_result
        WHERE q_result.receipt_id=p_receipt_id FOR SHARE;
    IF NOT FOUND OR v_stored.admission_id IS DISTINCT FROM p_admission_id
       OR v_stored.job_id IS DISTINCT FROM p_job_id
       OR v_stored.source_fingerprint IS DISTINCT FROM p_source_fingerprint
       OR v_stored.master_json_sha256 IS DISTINCT FROM v_master_sha
       OR v_stored.ai_done_drive_id IS DISTINCT FROM p_ai_done_drive_id
       OR v_stored.ai_done_sha256 IS DISTINCT FROM v_done_sha
       OR v_stored.bundle_sha256 IS DISTINCT FROM v_bundle_sha
       OR v_stored.master_json_text IS DISTINCT FROM p_master_json_text
       OR v_stored.ai_done_json_text IS DISTINCT FROM p_ai_done_json_text THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_RESULT_REPLAY_CHANGED';
    END IF;
    INSERT INTO video_queue.one_job_private_result_attempt_v1
        (job_id,attempt_count,receipt_id,lease_token)
    VALUES (p_job_id,p_attempt_count,p_receipt_id,p_lease_token)
    ON CONFLICT DO NOTHING;
    SELECT q_attempt.* INTO v_attempt FROM video_queue.one_job_private_result_attempt_v1 AS q_attempt
        WHERE q_attempt.job_id=p_job_id AND q_attempt.attempt_count=p_attempt_count FOR SHARE;
    IF NOT FOUND OR v_attempt.receipt_id IS DISTINCT FROM p_receipt_id
       OR v_attempt.lease_token IS DISTINCT FROM p_lease_token THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_ATTEMPT_REPLAY_CHANGED';
    END IF;
    PERFORM video_queue.check_one_job_staging_scope_v1(
        p_admission_id,p_job_id,p_lease_token,p_worker_key,
        p_attempt_count,p_source_fingerprint,'queue');
    RETURN jsonb_build_object('status','PRIVATE_RESULT_STAGED',
        'receipt_id',p_receipt_id,'bundle_sha256',v_bundle_sha,
        'master_json_sha256',v_master_sha,'ai_done_sha256',v_done_sha);
END $$;

CREATE FUNCTION video_queue.read_one_job_private_result_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid,
    p_worker_key text, p_attempt_count integer, p_source_fingerprint text,
    p_receipt_id uuid
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_result video_queue.one_job_private_result_v1%ROWTYPE;
BEGIN
    IF pg_has_role(session_user,'bridge_school_worker','MEMBER')
       OR pg_has_role(session_user,'bridge_school_app','MEMBER')
       OR pg_has_role(session_user,'video_subset_approver','MEMBER') THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_ROLE_CONFLICT';
    END IF;
    PERFORM video_queue.check_one_job_staging_scope_v1(
        p_admission_id,p_job_id,p_lease_token,p_worker_key,
        p_attempt_count,p_source_fingerprint,'queue');
    SELECT q_result.* INTO v_result FROM video_queue.one_job_private_result_v1 AS q_result
        JOIN video_queue.one_job_private_result_attempt_v1 AS q_attempt
          ON q_attempt.receipt_id=q_result.receipt_id
         AND q_attempt.job_id=p_job_id AND q_attempt.attempt_count=p_attempt_count
         AND q_attempt.lease_token=p_lease_token
        WHERE q_result.receipt_id=p_receipt_id AND q_result.admission_id=p_admission_id
          AND q_result.job_id=p_job_id AND q_result.source_fingerprint=p_source_fingerprint;
    IF NOT FOUND THEN RAISE EXCEPTION 'ONE_JOB_PRIVATE_RESULT_READBACK_MISSING'; END IF;
    RETURN jsonb_build_object('status','COMMITTED_PRIVATE_RESULT',
        'receipt_id',v_result.receipt_id,'bundle_sha256',v_result.bundle_sha256,
        'master_json_sha256',v_result.master_json_sha256);
END $$;

ALTER TABLE video_queue.job DROP CONSTRAINT video_job_output_mode_v1_check;
ALTER TABLE video_queue.job ADD CONSTRAINT video_job_output_mode_v2_check CHECK (
    output = '{}'::jsonb OR coalesce((
        output->>'result_mode' = 'SHADOW_REVIEW_ONLY'
        AND output->'canonical_promotion_allowed' IS NOT DISTINCT FROM 'false'::jsonb
        AND output->'database_persistence_allowed' IS NOT DISTINCT FROM 'false'::jsonb
        AND output->>'publication_state' = 'NOT_PUBLISHED'
    ),false) OR coalesce((
        status = 'REVIEW_READY'
        AND output->>'result_mode' = 'REPLAY_CANDIDATE_STAGING_ONLY'
        AND output->'canonical_promotion_allowed' IS NOT DISTINCT FROM 'false'::jsonb
        AND output->'database_persistence_allowed' IS NOT DISTINCT FROM 'true'::jsonb
        AND output->>'publication_state' = 'NOT_PUBLISHED'
        AND output#>>'{knowledge_staging,status}' = 'PERSISTED'
        AND output#>>'{knowledge_staging,replay_receipt_id}' IS NOT NULL
    ),false) OR coalesce((
        status = 'REVIEW_READY'
        AND output->>'result_mode' = 'PRIVATE_RESULT_STAGING_ONLY'
        AND output->'canonical_promotion_allowed' IS NOT DISTINCT FROM 'false'::jsonb
        AND output->'database_persistence_allowed' IS NOT DISTINCT FROM 'true'::jsonb
        AND output->>'publication_state' = 'NOT_PUBLISHED'
        AND output#>>'{private_staging,status}' = 'PRIVATE_RESULT_STAGED'
        AND output#>>'{private_staging,receipt_id}' IS NOT NULL
        AND output#>>'{private_staging,bundle_sha256}' ~ '^[0-9a-f]{64}$'
    ),false)
);

CREATE FUNCTION video_queue.finish_one_job_private_result_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid, p_worker_key text,
    p_attempt_count integer, p_source_fingerprint text, p_receipt_id uuid,
    p_postwork_observation_id uuid, p_output jsonb
) RETURNS TABLE(job_status text,batch_status text,released_jobs integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_result video_queue.one_job_private_result_v1%ROWTYPE;
    v_batch_status text;
BEGIN
    IF pg_has_role(session_user,'bridge_school_worker','MEMBER')
       OR pg_has_role(session_user,'bridge_school_app','MEMBER')
       OR pg_has_role(session_user,'video_subset_approver','MEMBER') THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_ROLE_CONFLICT';
    END IF;
    PERFORM video_queue.check_one_job_staging_scope_v1(
        p_admission_id,p_job_id,p_lease_token,p_worker_key,
        p_attempt_count,p_source_fingerprint,'queue');
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_job.batch_id FOR UPDATE;
    SELECT q_result.* INTO v_result FROM video_queue.one_job_private_result_v1 AS q_result
        JOIN video_queue.one_job_private_result_attempt_v1 AS q_attempt
          ON q_attempt.receipt_id=q_result.receipt_id
         AND q_attempt.job_id=p_job_id AND q_attempt.attempt_count=p_attempt_count
         AND q_attempt.lease_token=p_lease_token
       WHERE q_result.receipt_id=p_receipt_id AND q_result.admission_id=p_admission_id
         AND q_result.job_id=p_job_id AND q_result.source_fingerprint=p_source_fingerprint;
    IF NOT FOUND OR p_output IS NULL OR jsonb_typeof(p_output) IS DISTINCT FROM 'object'
       OR length(p_output::text) > 131072
       OR p_output->>'result_mode' IS DISTINCT FROM 'PRIVATE_RESULT_STAGING_ONLY'
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'true'::jsonb
       OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'
       OR p_output->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
       OR p_output->>'stable_job_key' IS DISTINCT FROM v_job.stable_job_key
       OR p_output->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision
       OR p_output#>>'{private_staging,status}' IS DISTINCT FROM 'PRIVATE_RESULT_STAGED'
       OR p_output#>>'{private_staging,receipt_id}' IS DISTINCT FROM p_receipt_id::text
       OR p_output#>>'{private_staging,bundle_sha256}' IS DISTINCT FROM v_result.bundle_sha256
       OR p_output->>'ai_done_drive_id' IS DISTINCT FROM v_result.ai_done_drive_id
       OR p_output->>'ai_done_sha256' IS DISTINCT FROM v_result.ai_done_sha256
       OR p_output->>'master_pdf_drive_id' IS DISTINCT FROM
          ((v_result.ai_done_json_text::jsonb) #>> '{masterPdf,driveId}')
       OR p_output->>'master_pdf_sha256' IS DISTINCT FROM
          ((v_result.ai_done_json_text::jsonb) #>> '{masterPdf,sha256}') THEN
        RAISE EXCEPTION 'ONE_JOB_PRIVATE_FINISH_BINDING_HOLD';
    END IF;
    PERFORM video_queue.assert_one_job_terminal_evidence_v1(p_job_id,p_output);
    IF NOT EXISTS (SELECT 1 FROM video_queue.source_observation AS q_prework
        WHERE q_prework.job_id=p_job_id AND q_prework.phase='ATTEMPT'
          AND q_prework.lease_token=p_lease_token AND q_prework.worker_key=p_worker_key
          AND q_prework.attempt_count=p_attempt_count
          AND q_prework.consumed_at IS NOT NULL
          AND q_prework.observation_id IS DISTINCT FROM p_postwork_observation_id) THEN
        RAISE EXCEPTION 'ONE_JOB_PREWORK_OBSERVATION_REQUIRED';
    END IF;
    PERFORM video_queue.authorize_subset_attempt(
        p_job_id,p_lease_token,p_worker_key,p_postwork_observation_id);
    UPDATE video_queue.job AS q_job
       SET status='REVIEW_READY', lease_owner=NULL, lease_token=NULL,
           lease_expires_at=NULL, error_code=NULL, output=p_output,
           updated_at=clock_timestamp(), completed_at=clock_timestamp()
     WHERE q_job.job_id=p_job_id AND q_job.status='LEASED'
       AND q_job.lease_token=p_lease_token AND q_job.attempt_count=p_attempt_count;
    IF NOT FOUND THEN RAISE EXCEPTION 'ONE_JOB_PRIVATE_FINISH_ATOMIC_HOLD'; END IF;
    INSERT INTO video_queue.job_event(batch_id,job_id,event_type,worker_key,lease_token,details)
    VALUES(v_job.batch_id,p_job_id,'REVIEW_READY',p_worker_key,p_lease_token,
        jsonb_build_object('attempt',p_attempt_count,'private_receipt_id',p_receipt_id,
                           'bundle_sha256',v_result.bundle_sha256,
                           'result_mode','PRIVATE_RESULT_STAGING_ONLY',
                           'artifact_manifest_sha256',p_output->>'artifact_manifest_sha256',
                           'terminal_evidence_sha256',p_output->>'terminal_evidence_sha256',
                           'released_jobs',0));
    -- Never release a pending sibling, even when this job was the canary.
    IF NOT EXISTS (SELECT 1 FROM video_queue.job AS q_job
        WHERE q_job.batch_id=v_batch.batch_id
          AND q_job.status IN ('PENDING_CANARY','QUEUED','LEASED')) THEN
        UPDATE video_queue.batch AS q_batch
           SET status='REVIEW',updated_at=clock_timestamp(),
               completed_at=clock_timestamp()
         WHERE q_batch.batch_id=v_batch.batch_id;
    END IF;
    SELECT q_batch.status INTO v_batch_status FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_batch.batch_id;
    RETURN QUERY SELECT 'REVIEW_READY'::text,v_batch_status,0;
END $$;

REVOKE ALL ON video_queue.one_job_private_result_v1,
    video_queue.one_job_private_result_attempt_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.stage_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,text,text,text),
    video_queue.read_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid),
    video_queue.finish_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb) FROM PUBLIC;
COMMIT;
