-- LOCAL DESIGN CANDIDATE ONLY. Do not add this file to the migration runner.
-- Prerequisite: 0060_fixed_subset_admission. No role, membership, or EXECUTE
-- grant is created here. The current broad writer ACL still needs a separate
-- least-privilege change before this could be a strict production boundary.
BEGIN;

CREATE TABLE video_queue.one_job_staging_admission_v1 (
    admission_id uuid PRIMARY KEY,
    job_id uuid NOT NULL UNIQUE REFERENCES video_queue.job(job_id),
    batch_id uuid NOT NULL REFERENCES video_queue.batch(batch_id),
    subset_approval_id uuid NOT NULL REFERENCES video_queue.subset_approval(approval_id),
    contract_sha256 text NOT NULL CHECK (contract_sha256 ~ '^[0-9a-f]{64}$'),
    queue_manifest_sha256 text NOT NULL CHECK (queue_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    source_file_id text NOT NULL,
    stable_job_key text NOT NULL,
    algorithm_revision text NOT NULL,
    enabled boolean NOT NULL DEFAULT false,
    approved_by name NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > approved_at),
    CHECK (NOT enabled OR revoked_at IS NULL)
);
-- One active exception across all batches. Old batch/subset SHADOW flags stay false.
CREATE UNIQUE INDEX one_job_staging_only_one_enabled_v1
    ON video_queue.one_job_staging_admission_v1 ((true))
    WHERE enabled AND revoked_at IS NULL;

CREATE TABLE video_queue.one_job_staging_receipt_v1 (
    receipt_id uuid PRIMARY KEY,
    admission_id uuid NOT NULL REFERENCES video_queue.one_job_staging_admission_v1,
    job_id uuid NOT NULL REFERENCES video_queue.job(job_id),
    source_fingerprint text NOT NULL,
    analysis_run_id uuid NOT NULL,
    source_id uuid NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE video_queue.one_job_staging_receipt_attempt_v1 (
    job_id uuid NOT NULL REFERENCES video_queue.job(job_id),
    attempt_count integer NOT NULL CHECK (attempt_count BETWEEN 1 AND 3),
    receipt_id uuid NOT NULL REFERENCES video_queue.one_job_staging_receipt_v1,
    lease_token uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (job_id, attempt_count)
);
-- A prior committed receipt may be rebound to a new live lease only when its
-- immutable job, source revision, run, source and hash binding still matches.
-- The previous attempt remains as history; no UPDATE or identity substitution.

ALTER TABLE video_queue.job DROP CONSTRAINT video_job_output_shadow_check;
ALTER TABLE video_queue.job ADD CONSTRAINT video_job_output_mode_v1_check CHECK (
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
    ),false)
);

CREATE FUNCTION video_queue.check_one_job_staging_scope_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid,
    p_worker_key text, p_attempt_count integer, p_source_fingerprint text,
    p_actor text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_scope video_queue.one_job_staging_admission_v1%ROWTYPE;
    v_subset video_queue.subset_admission%ROWTYPE;
    v_approval video_queue.subset_approval%ROWTYPE;
    v_reservation video_queue.source_reservation%ROWTYPE;
    v_bound_job jsonb;
BEGIN
    IF p_actor='writer' THEN
        IF session_user <> 'bridge_school_worker_principal'
           OR NOT pg_has_role(session_user,'bridge_school_worker','MEMBER') THEN
            RAISE EXCEPTION 'ONE_JOB_WRITER_CAPABILITY_DENIED';
        END IF;
    ELSIF p_actor='queue' THEN
        PERFORM video_queue.subset_capability('video_subset_worker_v2');
    ELSE
        RAISE EXCEPTION 'ONE_JOB_ACTOR_INVALID';
    END IF;
    -- The separate writer login does not inherit queue-worker membership.
    -- Lock and verify the exact lease ourselves, through transaction commit.
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job
        WHERE q_job.job_id=p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status <> 'LEASED'
       OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_owner IS DISTINCT FROM p_worker_key
       OR v_job.attempt_count IS DISTINCT FROM p_attempt_count
       OR v_job.attempt_count NOT BETWEEN 1 AND 3
       OR v_job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'ONE_JOB_LEASE_LOST';
    END IF;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_job.batch_id;
    SELECT q_scope.* INTO v_scope FROM video_queue.one_job_staging_admission_v1 AS q_scope
        WHERE q_scope.admission_id=p_admission_id FOR SHARE;
    IF NOT FOUND OR NOT v_scope.enabled OR v_scope.revoked_at IS NOT NULL
       OR v_scope.expires_at <= clock_timestamp()
       OR v_scope.job_id IS DISTINCT FROM v_job.job_id
       OR v_scope.batch_id IS DISTINCT FROM v_job.batch_id
       OR v_scope.source_file_id IS DISTINCT FROM v_job.source_file_id
       OR v_scope.stable_job_key IS DISTINCT FROM v_job.stable_job_key
       OR v_scope.algorithm_revision IS DISTINCT FROM v_batch.algorithm_revision
       OR v_scope.source_fingerprint IS DISTINCT FROM p_source_fingerprint THEN
        RAISE EXCEPTION 'ONE_JOB_ADMISSION_HOLD';
    END IF;
    SELECT q_subset.* INTO v_subset FROM video_queue.subset_admission AS q_subset
        WHERE q_subset.batch_id=v_job.batch_id AND q_subset.state='FINALIZED';
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval
        WHERE q_approval.approval_id=v_subset.approval_id FOR SHARE;
    IF v_subset.approval_id IS DISTINCT FROM v_scope.subset_approval_id
       OR v_subset.contract_sha256 IS DISTINCT FROM v_scope.contract_sha256
       OR v_subset.queue_manifest_sha256 IS DISTINCT FROM v_scope.queue_manifest_sha256
       OR v_approval.revoked_at IS NOT NULL OR NOT v_approval.runtime_enabled
       OR v_approval.expires_at <= clock_timestamp()
       OR v_batch.result_mode <> 'SHADOW_REVIEW_ONLY'
       OR v_batch.database_persistence_allowed
       OR v_batch.canonical_promotion_allowed
       OR v_subset.binding->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_approval.contract->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_approval.contract#>>'{enqueue_manifest,result_mode}' <> 'SHADOW_REVIEW_ONLY'
       OR v_approval.contract#>'{enqueue_manifest,database_persistence_allowed}' IS DISTINCT FROM 'false'::jsonb THEN
        RAISE EXCEPTION 'ONE_JOB_MANIFEST_HOLD';
    END IF;
    v_bound_job := (v_subset.binding->'jobs')->(v_job.sequence-1);
    SELECT q_reservation.* INTO v_reservation FROM video_queue.source_reservation AS q_reservation
        WHERE q_reservation.job_id=v_job.job_id;
    IF v_bound_job->>'job_id' IS DISTINCT FROM v_job.job_id::text
       OR v_bound_job->>'source_identity' IS DISTINCT FROM 'google_drive:' || v_job.source_file_id
       OR v_bound_job->>'source_fingerprint' IS DISTINCT FROM v_scope.source_fingerprint
       OR v_reservation.source_fingerprint IS DISTINCT FROM v_scope.source_fingerprint
       OR v_reservation.batch_id IS DISTINCT FROM v_job.batch_id
       OR v_reservation.admission_request_key IS DISTINCT FROM v_subset.request_key
       OR NOT EXISTS (
           SELECT 1 FROM video_queue.source_observation AS q_observation
           WHERE q_observation.phase='ATTEMPT' AND q_observation.consumed_at IS NOT NULL
             AND q_observation.job_id=v_job.job_id
             AND q_observation.lease_token=v_job.lease_token
             AND q_observation.attempt_count=v_job.attempt_count
             AND q_observation.worker_key=v_job.lease_owner
             AND q_observation.approval_id=v_subset.approval_id
       ) THEN
        RAISE EXCEPTION 'ONE_JOB_SOURCE_OR_ATTEMPT_HOLD';
    END IF;
    IF v_job.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'ONE_JOB_LEASE_EXPIRED';
    END IF;
    RETURN v_scope.admission_id;
END $$;

CREATE FUNCTION video_queue.assert_one_job_staging_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid,
    p_worker_key text, p_attempt_count integer, p_source_fingerprint text
) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
    SELECT video_queue.check_one_job_staging_scope_v1($1,$2,$3,$4,$5,$6,'writer')
$$;

CREATE FUNCTION video_queue.attest_one_job_staging_receipt_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid,
    p_worker_key text, p_attempt_count integer, p_source_fingerprint text,
    p_receipt_id uuid, p_analysis_run_id uuid, p_source_id uuid, p_payload_hash text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue, public AS $$
DECLARE v_job video_queue.job%ROWTYPE;
    v_stored video_queue.one_job_staging_receipt_v1%ROWTYPE;
    v_attempt video_queue.one_job_staging_receipt_attempt_v1%ROWTYPE;
BEGIN
    PERFORM video_queue.assert_one_job_staging_v1(p_admission_id,p_job_id,p_lease_token,
        p_worker_key,p_attempt_count,p_source_fingerprint);
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id;
    IF p_payload_hash IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.analysis_candidate AS q_receipt
        JOIN public.analysis_run AS q_run ON q_run.analysis_run_id=q_receipt.analysis_run_id
        JOIN public.source AS q_source ON q_source.source_id=q_receipt.source_id
        JOIN public.analysis_run_output AS q_output ON q_output.analysis_run_id=q_run.analysis_run_id
            AND q_output.output_entity_id=q_receipt.analysis_candidate_id
            AND q_output.output_entity_type='analysis_candidate'
            AND q_output.output_role='derived' AND q_output.status='staging'
            AND q_output.artifact_version_id IS NULL AND q_output.publication_id IS NULL
        WHERE q_receipt.analysis_candidate_id=p_receipt_id
          AND q_receipt.analysis_run_id=p_analysis_run_id
          AND q_receipt.source_id=p_source_id
          AND q_receipt.candidate_type='video_staging_replay_snapshot'
          AND q_receipt.status='active' AND q_receipt.promotion_status='staging'
          AND q_receipt.method_version='video-staging-replay-snapshot-v1'
          AND q_receipt.payload_hash=p_payload_hash AND q_receipt.stable_key=p_payload_hash
          AND q_receipt.payload->>'schema'='video-staging-replay-snapshot-v1'
          AND q_receipt.payload#>>'{binding,job_id}'=v_job.stable_job_key
          AND q_receipt.payload#>>'{binding,analysis_run_id}'=p_analysis_run_id::text
          AND q_receipt.payload#>>'{binding,source_id}'=p_source_id::text
          AND q_run.parameters_snapshot->>'job_id'=v_job.stable_job_key
          AND q_run.parameters_snapshot->>'source_drive_id'=v_job.source_file_id
          AND q_source.canonical_locator='gdrive:file:' || v_job.source_file_id
    ) THEN RAISE EXCEPTION 'ONE_JOB_RECEIPT_BINDING_HOLD'; END IF;
    INSERT INTO video_queue.one_job_staging_receipt_v1
        (receipt_id,admission_id,job_id,source_fingerprint,analysis_run_id,source_id,payload_hash)
    VALUES (p_receipt_id,p_admission_id,p_job_id,p_source_fingerprint,
            p_analysis_run_id,p_source_id,p_payload_hash)
    ON CONFLICT DO NOTHING;
    SELECT q_receipt.* INTO v_stored FROM video_queue.one_job_staging_receipt_v1 AS q_receipt
        WHERE q_receipt.receipt_id=p_receipt_id;
    IF NOT FOUND OR v_stored.admission_id IS DISTINCT FROM p_admission_id
       OR v_stored.job_id IS DISTINCT FROM p_job_id
       OR v_stored.source_fingerprint IS DISTINCT FROM p_source_fingerprint
       OR v_stored.analysis_run_id IS DISTINCT FROM p_analysis_run_id
       OR v_stored.source_id IS DISTINCT FROM p_source_id
       OR v_stored.payload_hash IS DISTINCT FROM p_payload_hash THEN
        RAISE EXCEPTION 'ONE_JOB_RECEIPT_IDENTITY_CHANGED';
    END IF;
    -- A crash after candidate commit but before finish leaves a durable
    -- receipt. A later lease may attest it after this same live revision check.
    INSERT INTO video_queue.one_job_staging_receipt_attempt_v1
        (job_id,attempt_count,receipt_id,lease_token)
    VALUES (p_job_id,p_attempt_count,p_receipt_id,p_lease_token)
    ON CONFLICT DO NOTHING;
    SELECT q_attempt.* INTO v_attempt FROM video_queue.one_job_staging_receipt_attempt_v1 AS q_attempt
        WHERE q_attempt.job_id=p_job_id AND q_attempt.attempt_count=p_attempt_count;
    IF NOT FOUND OR v_attempt.receipt_id IS DISTINCT FROM p_receipt_id
       OR v_attempt.lease_token IS DISTINCT FROM p_lease_token THEN
        RAISE EXCEPTION 'ONE_JOB_ATTEMPT_RECEIPT_CHANGED';
    END IF;
    RETURN p_receipt_id;
END $$;

CREATE FUNCTION video_queue.finish_one_job_staging_v1(
    p_admission_id uuid, p_job_id uuid, p_lease_token uuid, p_worker_key text,
    p_attempt_count integer, p_source_fingerprint text, p_receipt_id uuid,
    p_postwork_observation_id uuid, p_output jsonb
) RETURNS TABLE(job_status text,batch_status text,released_jobs integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_released integer := 0;
    v_batch_status text;
BEGIN
    PERFORM video_queue.check_one_job_staging_scope_v1(p_admission_id,p_job_id,p_lease_token,
        p_worker_key,p_attempt_count,p_source_fingerprint,'queue');
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_job.batch_id FOR UPDATE;
    IF p_output IS NULL OR jsonb_typeof(p_output) <> 'object'
       OR length(p_output::text) > 65536
       OR p_output->>'result_mode' <> 'REPLAY_CANDIDATE_STAGING_ONLY'
       OR p_output->'database_persistence_allowed' IS DISTINCT FROM 'true'::jsonb
       OR p_output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR p_output->>'publication_state' <> 'NOT_PUBLISHED'
       OR p_output->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
       OR p_output->>'stable_job_key' IS DISTINCT FROM v_job.stable_job_key
       OR p_output->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision
       OR p_output#>>'{knowledge_staging,status}' <> 'PERSISTED'
       OR p_output#>>'{knowledge_staging,replay_receipt_id}' IS DISTINCT FROM p_receipt_id::text
       OR NOT EXISTS (SELECT 1 FROM video_queue.one_job_staging_receipt_v1 AS q_receipt
           WHERE q_receipt.receipt_id=p_receipt_id AND q_receipt.admission_id=p_admission_id
             AND q_receipt.job_id=p_job_id AND q_receipt.source_fingerprint=p_source_fingerprint)
       OR NOT EXISTS (SELECT 1 FROM video_queue.one_job_staging_receipt_attempt_v1 AS q_attempt
           WHERE q_attempt.job_id=p_job_id AND q_attempt.attempt_count=p_attempt_count
             AND q_attempt.receipt_id=p_receipt_id AND q_attempt.lease_token=p_lease_token) THEN
        RAISE EXCEPTION 'ONE_JOB_FINISH_BINDING_HOLD';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM video_queue.source_observation AS q_prework
        WHERE q_prework.job_id=p_job_id AND q_prework.phase='ATTEMPT'
          AND q_prework.lease_token=p_lease_token AND q_prework.worker_key=p_worker_key
          AND q_prework.attempt_count=p_attempt_count
          AND q_prework.consumed_at IS NOT NULL
          AND q_prework.observation_id IS DISTINCT FROM p_postwork_observation_id) THEN
        RAISE EXCEPTION 'ONE_JOB_PREWORK_OBSERVATION_REQUIRED';
    END IF;
    PERFORM video_queue.authorize_subset_attempt(p_job_id,p_lease_token,
        p_worker_key,p_postwork_observation_id);
    -- This is the only staging-mode transition. The old finish_job and
    -- finish_subset_job continue to require SHADOW_REVIEW_ONLY output.
    UPDATE video_queue.job AS q_job
       SET status='REVIEW_READY', lease_owner=NULL, lease_token=NULL,
           lease_expires_at=NULL, error_code=NULL, output=p_output,
           updated_at=clock_timestamp(), completed_at=clock_timestamp()
     WHERE q_job.job_id=p_job_id AND q_job.status='LEASED'
       AND q_job.lease_token=p_lease_token AND q_job.attempt_count=p_attempt_count;
    IF NOT FOUND THEN RAISE EXCEPTION 'ONE_JOB_FINISH_ATOMIC_HOLD'; END IF;
    INSERT INTO video_queue.job_event(batch_id,job_id,event_type,worker_key,lease_token,details)
    VALUES(v_job.batch_id,p_job_id,'REVIEW_READY',p_worker_key,p_lease_token,
        jsonb_build_object('attempt',p_attempt_count,'replay_receipt_id',p_receipt_id,
                           'result_mode','REPLAY_CANDIDATE_STAGING_ONLY'));
    -- The one-job exception never releases pending siblings, including when
    -- the admitted job is the canary. Further batch release needs its own
    -- separately reviewed decision; released_jobs is always zero here.
    IF NOT EXISTS (SELECT 1 FROM video_queue.job AS q_job
        WHERE q_job.batch_id=v_job.batch_id
          AND q_job.status IN ('PENDING_CANARY','QUEUED','LEASED')) THEN
        UPDATE video_queue.batch AS q_batch
           SET status='REVIEW', updated_at=clock_timestamp(),
               completed_at=clock_timestamp()
         WHERE q_batch.batch_id=v_job.batch_id;
    END IF;
    SELECT q_batch.status INTO v_batch_status FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_job.batch_id;
    RETURN QUERY SELECT 'REVIEW_READY'::text,v_batch_status,v_released;
END $$;

REVOKE ALL ON video_queue.one_job_staging_admission_v1,
    video_queue.one_job_staging_receipt_v1,
    video_queue.one_job_staging_receipt_attempt_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.check_one_job_staging_scope_v1(uuid,uuid,uuid,text,integer,text,text),
    video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text),
    video_queue.attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text),
    video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
    FROM PUBLIC;
COMMIT;
