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

CREATE TABLE video_queue.one_job_staging_lease_approval_v1 (
    lease_approval_id uuid PRIMARY KEY,
    admission_id uuid NOT NULL REFERENCES video_queue.one_job_staging_admission_v1,
    job_id uuid NOT NULL REFERENCES video_queue.job(job_id),
    lease_token uuid NOT NULL,
    worker_key text NOT NULL,
    attempt_count integer NOT NULL CHECK (attempt_count BETWEEN 1 AND 3),
    source_fingerprint text NOT NULL CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    approval_reference text NOT NULL CHECK (length(btrim(approval_reference)) BETWEEN 1 AND 500),
    approved_by name NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > approved_at),
    UNIQUE (admission_id,attempt_count),
    UNIQUE (admission_id,lease_token)
);
-- Append-only attempt approvals. A new lease after retry needs a new approval;
-- a consumed or revoked approval is never recycled.

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

CREATE FUNCTION video_queue.approve_one_job_staging_attempt_v1(
    p_admission_id uuid, p_lease_approval_id uuid, p_job_id uuid,
    p_lease_token uuid, p_worker_key text, p_attempt_count integer,
    p_source_fingerprint text, p_expires_at timestamptz,
    p_approval_reference text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
    v_subset video_queue.subset_admission%ROWTYPE;
    v_approval video_queue.subset_approval%ROWTYPE;
    v_reservation video_queue.source_reservation%ROWTYPE;
    v_scope video_queue.one_job_staging_admission_v1%ROWTYPE;
    v_bound_job jsonb;
    v_last_attempt integer;
BEGIN
    -- Reuse 0060's authenticated approver capability; never inherit the
    -- queue or public writer role in this approval session.
    PERFORM video_queue.subset_capability('video_subset_approver');
    IF pg_has_role(session_user,'video_subset_worker_v2','MEMBER')
       OR pg_has_role(session_user,'bridge_school_worker','MEMBER') THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVER_WORKER_ROLE_CONFLICT';
    END IF;
    IF p_admission_id IS NULL OR p_lease_approval_id IS NULL
       OR p_job_id IS NULL OR p_lease_token IS NULL
       OR p_worker_key IS NULL OR p_worker_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
       OR p_attempt_count IS NULL OR p_attempt_count NOT BETWEEN 1 AND 3
       OR p_source_fingerprint IS NULL OR p_source_fingerprint !~ '^[0-9a-f]{64}$'
       OR p_expires_at IS NULL OR p_expires_at <= clock_timestamp()
       OR p_approval_reference IS NULL
       OR length(btrim(p_approval_reference)) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_ARGUMENT_INVALID';
    END IF;
    -- Claim/retry and approval serialize on the same job row. No approval can
    -- silently follow a changed token, owner, attempt, or expired lease.
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job
        WHERE q_job.job_id=p_job_id FOR UPDATE;
    IF NOT FOUND OR v_job.status IS DISTINCT FROM 'LEASED'
       OR v_job.lease_token IS DISTINCT FROM p_lease_token
       OR v_job.lease_owner IS DISTINCT FROM p_worker_key
       OR v_job.attempt_count IS DISTINCT FROM p_attempt_count
       OR v_job.lease_expires_at <= clock_timestamp()
       OR p_expires_at > v_job.lease_expires_at THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_LEASE_LOST';
    END IF;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_job.batch_id;
    SELECT q_subset.* INTO v_subset FROM video_queue.subset_admission AS q_subset
        WHERE q_subset.batch_id=v_job.batch_id AND q_subset.state='FINALIZED';
    SELECT q_approval.* INTO v_approval FROM video_queue.subset_approval AS q_approval
        WHERE q_approval.approval_id=v_subset.approval_id FOR SHARE;
    IF v_subset.approval_id IS NULL OR v_approval.approval_id IS NULL
       OR NOT v_approval.runtime_enabled OR v_approval.revoked_at IS NOT NULL
       OR v_approval.expires_at <= clock_timestamp()
       OR p_expires_at > v_approval.expires_at
       OR v_batch.result_mode IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR v_batch.database_persistence_allowed
       OR v_batch.canonical_promotion_allowed
       OR v_subset.contract_sha256 IS DISTINCT FROM v_approval.contract_sha256
       OR v_subset.queue_manifest_sha256 IS DISTINCT FROM v_approval.queue_manifest_sha256 THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_SUBSET_HOLD';
    END IF;
    PERFORM video_queue.subset_assert_final(v_subset.request_key);
    v_bound_job := (v_subset.binding->'jobs')->(v_job.sequence-1);
    SELECT q_reservation.* INTO v_reservation
      FROM video_queue.source_reservation AS q_reservation
     WHERE q_reservation.job_id=p_job_id;
    IF v_bound_job->>'job_id' IS DISTINCT FROM p_job_id::text
       OR v_bound_job->>'file_id' IS DISTINCT FROM v_job.source_file_id
       OR v_bound_job->>'source_fingerprint' IS DISTINCT FROM p_source_fingerprint
       OR v_reservation.source_fingerprint IS DISTINCT FROM p_source_fingerprint
       OR v_reservation.batch_id IS DISTINCT FROM v_job.batch_id
       OR v_reservation.admission_request_key IS DISTINCT FROM v_subset.request_key THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_SOURCE_HOLD';
    END IF;
    SELECT q_scope.* INTO v_scope FROM video_queue.one_job_staging_admission_v1 AS q_scope
        WHERE q_scope.job_id=p_job_id FOR UPDATE;
    IF FOUND THEN
        IF v_scope.admission_id IS DISTINCT FROM p_admission_id
           OR v_scope.revoked_at IS NOT NULL OR NOT v_scope.enabled
           OR v_scope.batch_id IS DISTINCT FROM v_job.batch_id
           OR v_scope.subset_approval_id IS DISTINCT FROM v_subset.approval_id
           OR v_scope.contract_sha256 IS DISTINCT FROM v_subset.contract_sha256
           OR v_scope.queue_manifest_sha256 IS DISTINCT FROM v_subset.queue_manifest_sha256
           OR v_scope.source_fingerprint IS DISTINCT FROM p_source_fingerprint
           OR v_scope.source_file_id IS DISTINCT FROM v_job.source_file_id
           OR v_scope.stable_job_key IS DISTINCT FROM v_job.stable_job_key
           OR v_scope.algorithm_revision IS DISTINCT FROM v_batch.algorithm_revision THEN
            RAISE EXCEPTION 'ONE_JOB_APPROVAL_REVOKED_OR_CHANGED';
        END IF;
        SELECT max(q_lease_approval.attempt_count) INTO v_last_attempt
          FROM video_queue.one_job_staging_lease_approval_v1 AS q_lease_approval
         WHERE q_lease_approval.admission_id=p_admission_id;
        IF p_attempt_count <= coalesce(v_last_attempt,0) THEN
            RAISE EXCEPTION 'ONE_JOB_APPROVAL_REPLAY';
        END IF;
        UPDATE video_queue.one_job_staging_admission_v1 AS q_scope
           SET expires_at=p_expires_at
         WHERE q_scope.admission_id=p_admission_id;
    ELSE
        IF EXISTS (SELECT 1 FROM video_queue.one_job_staging_admission_v1 AS q_scope
                   WHERE q_scope.admission_id=p_admission_id) THEN
            RAISE EXCEPTION 'ONE_JOB_APPROVAL_REPLAY';
        END IF;
        INSERT INTO video_queue.one_job_staging_admission_v1
            (admission_id,job_id,batch_id,subset_approval_id,contract_sha256,
             queue_manifest_sha256,source_fingerprint,source_file_id,
             stable_job_key,algorithm_revision,enabled,approved_by,expires_at)
        VALUES (p_admission_id,p_job_id,v_job.batch_id,v_subset.approval_id,
                v_subset.contract_sha256,v_subset.queue_manifest_sha256,
                p_source_fingerprint,v_job.source_file_id,v_job.stable_job_key,
                v_batch.algorithm_revision,true,session_user,p_expires_at);
    END IF;
    IF EXISTS (SELECT 1 FROM video_queue.one_job_staging_lease_approval_v1 AS q_lease_approval
               WHERE q_lease_approval.lease_approval_id=p_lease_approval_id) THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_REPLAY';
    END IF;
    IF p_expires_at <= clock_timestamp() OR v_job.lease_expires_at <= clock_timestamp()
       OR v_approval.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_EXPIRED';
    END IF;
    INSERT INTO video_queue.one_job_staging_lease_approval_v1
        (lease_approval_id,admission_id,job_id,lease_token,worker_key,
         attempt_count,source_fingerprint,approval_reference,approved_by,expires_at)
    VALUES (p_lease_approval_id,p_admission_id,p_job_id,p_lease_token,p_worker_key,
            p_attempt_count,p_source_fingerprint,p_approval_reference,session_user,
            p_expires_at);
    RETURN p_admission_id;
END $$;

CREATE FUNCTION video_queue.revoke_one_job_staging_v1(p_admission_id uuid)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE v_scope video_queue.one_job_staging_admission_v1%ROWTYPE;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    IF pg_has_role(session_user,'video_subset_worker_v2','MEMBER')
       OR pg_has_role(session_user,'bridge_school_worker','MEMBER') THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVER_WORKER_ROLE_CONFLICT';
    END IF;
    SELECT q_scope.* INTO v_scope FROM video_queue.one_job_staging_admission_v1 AS q_scope
        WHERE q_scope.admission_id=p_admission_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'ONE_JOB_APPROVAL_NOT_FOUND'; END IF;
    IF v_scope.revoked_at IS NOT NULL OR NOT v_scope.enabled THEN
        RAISE EXCEPTION 'ONE_JOB_APPROVAL_REVOKED';
    END IF;
    UPDATE video_queue.one_job_staging_admission_v1 AS q_scope
       SET revoked_at=clock_timestamp(), enabled=false
     WHERE q_scope.admission_id=p_admission_id;
END $$;

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
    v_lease_approval video_queue.one_job_staging_lease_approval_v1%ROWTYPE;
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
    SELECT q_lease_approval.* INTO v_lease_approval
      FROM video_queue.one_job_staging_lease_approval_v1 AS q_lease_approval
     WHERE q_lease_approval.admission_id=v_scope.admission_id
       AND q_lease_approval.attempt_count=p_attempt_count FOR SHARE;
    IF NOT FOUND OR v_lease_approval.job_id IS DISTINCT FROM p_job_id
       OR v_lease_approval.lease_token IS DISTINCT FROM p_lease_token
       OR v_lease_approval.worker_key IS DISTINCT FROM p_worker_key
       OR v_lease_approval.source_fingerprint IS DISTINCT FROM p_source_fingerprint
       OR v_lease_approval.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'ONE_JOB_LEASE_APPROVAL_HOLD';
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

-- Reuse the complete 0058 hash-bound terminal gate. Artifact manifest and
-- terminal receipt describe the shadow video artifacts; the separate one-job
-- admission is the only source of the staging database exception.
CREATE FUNCTION video_queue.assert_one_job_terminal_evidence_v1(
    p_job_id uuid, p_output jsonb
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, video_queue AS $$
DECLARE
    v_job video_queue.job%ROWTYPE;
    v_batch video_queue.batch%ROWTYPE;
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
    SELECT q_job.* INTO v_job FROM video_queue.job AS q_job WHERE q_job.job_id=p_job_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'ONE_JOB_TERMINAL_JOB_MISSING'; END IF;
    SELECT q_batch.* INTO v_batch FROM video_queue.batch AS q_batch
        WHERE q_batch.batch_id=v_job.batch_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'ONE_JOB_TERMINAL_BATCH_MISSING'; END IF;
    IF p_output IS NULL OR jsonb_typeof(p_output) IS DISTINCT FROM 'object'
       OR p_output->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
       OR p_output->>'stable_job_key' IS DISTINCT FROM v_job.stable_job_key
       OR p_output->>'algorithm_revision' IS DISTINCT FROM v_batch.algorithm_revision THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_IDENTITY_MISMATCH';
    END IF;
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
       OR (CASE
            WHEN jsonb_typeof(v_source_identity->'size_bytes') = 'number'
            THEN (v_source_identity->>'size_bytes')::numeric IS DISTINCT FROM v_job.source_size_bytes::numeric
            ELSE true
          END)
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
       OR (CASE
            WHEN jsonb_typeof(v_artifacts->0->'size_bytes') = 'number'
            THEN (v_artifacts->0->>'size_bytes')::numeric <= 0
            ELSE true
          END)

       OR jsonb_typeof(v_artifacts->1) IS DISTINCT FROM 'object'
       OR v_artifacts->1->>'kind' IS DISTINCT FROM 'ai_done'
       OR v_artifacts->1->>'drive_id' IS DISTINCT FROM v_ai_done_id
       OR v_artifacts->1->>'locator' IS DISTINCT FROM ('gdrive:file:' || v_ai_done_id)
       OR v_artifacts->1->>'mime_type' IS DISTINCT FROM 'application/json'
       OR v_artifacts->1->>'parent_id' IS DISTINCT FROM v_batch.output_folder_id
       OR v_artifacts->1->>'sha256' IS DISTINCT FROM v_ai_done_sha
       OR jsonb_typeof(v_artifacts->1->'size_bytes') IS DISTINCT FROM 'number'
       OR (CASE
            WHEN jsonb_typeof(v_artifacts->1->'size_bytes') = 'number'
            THEN (v_artifacts->1->>'size_bytes')::numeric <= 0
            ELSE true
          END)

       OR v_receipt->>'schema_version' IS DISTINCT FROM 'universal-video-terminal-receipt/v1'
       OR v_receipt->>'status' IS DISTINCT FROM 'PASS'
       OR v_receipt->>'job_id' IS DISTINCT FROM v_job.stable_job_key
       OR v_receipt->>'source_file_id' IS DISTINCT FROM v_job.source_file_id
       OR v_receipt->'source_identity_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->'drive_readback_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->'result_readback_verified' IS DISTINCT FROM 'true'::jsonb
       OR v_receipt->'checksum_verified' IS DISTINCT FROM 'true'::jsonb
       OR jsonb_typeof(v_receipt->'artifact_count') IS DISTINCT FROM 'number'
       OR (CASE
            WHEN jsonb_typeof(v_receipt->'artifact_count') = 'number'
            THEN (v_receipt->>'artifact_count')::numeric IS DISTINCT FROM 2::numeric
            ELSE true
          END)
       OR v_receipt->>'artifact_manifest_sha256' IS DISTINCT FROM v_manifest_sha
       OR v_receipt->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_receipt->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_receipt->>'publication_state' IS DISTINCT FROM 'NOT_PUBLISHED' THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_RESULT_CONTRACT_INVALID';
    END IF;
    -- 0058's terminal revision CHECK is also asserted before transition.
    IF NULLIF(p_output #>> '{artifact_manifest,artifacts,0,modified_time}', '') IS NULL
       OR NULLIF(p_output #>> '{artifact_manifest,artifacts,0,version}', '') IS NULL
       OR NOT coalesce((p_output #>> '{artifact_manifest,artifacts,0,version}') ~ '^[0-9]+$',false)
       OR NULLIF(p_output #>> '{artifact_manifest,artifacts,1,modified_time}', '') IS NULL
       OR NULLIF(p_output #>> '{artifact_manifest,artifacts,1,version}', '') IS NULL
       OR NOT coalesce((p_output #>> '{artifact_manifest,artifacts,1,version}') ~ '^[0-9]+$',false) THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_TERMINAL_REVISION_INVALID';
    END IF;
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
       OR length(p_output::text) > 131072
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
    PERFORM video_queue.assert_one_job_terminal_evidence_v1(p_job_id,p_output);
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
                           'result_mode','REPLAY_CANDIDATE_STAGING_ONLY',
                           'artifact_manifest_sha256',p_output->>'artifact_manifest_sha256',
                           'terminal_evidence_sha256',p_output->>'terminal_evidence_sha256',
                           'released_jobs',0));
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
    video_queue.one_job_staging_lease_approval_v1,
    video_queue.one_job_staging_receipt_v1,
    video_queue.one_job_staging_receipt_attempt_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text),
    video_queue.revoke_one_job_staging_v1(uuid),
    video_queue.check_one_job_staging_scope_v1(uuid,uuid,uuid,text,integer,text,text),
    video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text),
    video_queue.attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text),
    video_queue.assert_one_job_terminal_evidence_v1(uuid,jsonb),
    video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
    FROM PUBLIC;
COMMIT;
