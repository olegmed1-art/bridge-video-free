-- LOCAL SYNCHRONOUS RESIDENT CANDIDATE ONLY. Requires pinned 0056-0060.
-- This does not authenticate a human review or activate production.
-- The trusted approver LOGIN must have verified the review reference upstream.
BEGIN;
SET LOCAL search_path = pg_catalog, video_queue, pg_temp;

CREATE TABLE video_queue.subset_reviewed_release (
    batch_id uuid PRIMARY KEY REFERENCES video_queue.batch,
    approval_id uuid NOT NULL REFERENCES video_queue.subset_approval,
    canary_job_id uuid NOT NULL UNIQUE REFERENCES video_queue.job,
    observation_id uuid NOT NULL UNIQUE REFERENCES video_queue.source_observation,
    terminal_evidence_sha256 text NOT NULL CHECK (terminal_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    review_reference text NOT NULL UNIQUE CHECK (review_reference ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{9,199}$'),
    review_bytes text NOT NULL CHECK (octet_length(review_bytes) BETWEEN 1 AND 4096),
    review_sha256 text NOT NULL UNIQUE CHECK (review_sha256 ~ '^[0-9a-f]{64}$'),
    reviewed_by name NOT NULL,
    release_xid bigint NOT NULL,
    released_jobs integer NOT NULL DEFAULT 0 CHECK (released_jobs >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    released_at timestamptz,
    CHECK ((released_at IS NULL AND released_jobs = 0)
        OR (released_at IS NOT NULL AND released_jobs > 0))
);
ALTER TABLE video_queue.subset_reviewed_release OWNER TO video_subset_owner;
REVOKE ALL ON TABLE video_queue.subset_reviewed_release FROM PUBLIC;

-- The installed 0060 fence remains the default. This replacement adds only
-- exact CANARY_REVIEW->RUNNING and PENDING_CANARY->QUEUED transitions while an
-- owner-only receipt for this batch is open in the caller's transaction.
\ir reviewed_release_guard.sql

CREATE FUNCTION video_queue.release_reviewed_subset_batch(p_review_bytes text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, video_queue, pg_temp SET timezone = 'UTC' AS $$
DECLARE
    v_review jsonb;
    v_sha text;
    v_batch_id uuid;
    v_batch video_queue.batch%ROWTYPE;
    v_admission video_queue.subset_admission%ROWTYPE;
    v_approval video_queue.subset_approval%ROWTYPE;
    v_canary video_queue.job%ROWTYPE;
    v_observation video_queue.source_observation%ROWTYPE;
    v_existing video_queue.subset_reviewed_release%ROWTYPE;
    v_pending integer;
    v_released integer;
    v_now timestamptz;
BEGIN
    PERFORM video_queue.subset_capability('video_subset_approver');
    IF p_review_bytes IS NULL OR octet_length(p_review_bytes) NOT BETWEEN 1 AND 4096 THEN
        RAISE EXCEPTION 'REVIEW_RECEIPT_REQUIRED';
    END IF;
    v_review := p_review_bytes::jsonb;
    IF video_queue.subset_json(v_review) IS DISTINCT FROM p_review_bytes
       OR NOT video_queue.subset_shape(v_review, string_to_array(
         'schema decision scope batch_id approval_id canary_job_id observation_id terminal_evidence_sha256 artifact_manifest_sha256 review_reference reviewed_by student_delivery_allowed database_persistence_allowed canonical_promotion_allowed review_sha256',' '))
       OR v_review->>'schema' IS DISTINCT FROM 'universal-video-reviewed-release-sync-v1'
       OR v_review->>'decision' IS DISTINCT FROM 'APPROVE_QUEUE_RELEASE'
       OR v_review->>'scope' IS DISTINCT FROM 'THIS_BATCH_PENDING_JOBS_ONLY'
       OR v_review->>'reviewed_by' IS DISTINCT FROM session_user::text
       OR v_review->'student_delivery_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_review->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_review->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_review->>'review_reference' !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{9,199}$'
       OR v_review->>'terminal_evidence_sha256' !~ '^[0-9a-f]{64}$'
       OR v_review->>'artifact_manifest_sha256' !~ '^[0-9a-f]{64}$'
       OR v_review->>'review_sha256' !~ '^[0-9a-f]{64}$'
       OR v_review->>'batch_id' !~ '^[0-9a-f-]{36}$'
       OR v_review->>'approval_id' !~ '^[0-9a-f-]{36}$'
       OR v_review->>'canary_job_id' !~ '^[0-9a-f-]{36}$'
       OR v_review->>'observation_id' !~ '^[0-9a-f-]{36}$'
    THEN RAISE EXCEPTION 'REVIEW_RECEIPT_INVALID'; END IF;
    v_sha := video_queue.subset_sha(v_review - 'review_sha256');
    IF v_sha IS DISTINCT FROM v_review->>'review_sha256' THEN
        RAISE EXCEPTION 'REVIEW_RECEIPT_HASH_MISMATCH';
    END IF;
    v_batch_id := (v_review->>'batch_id')::uuid;
    SELECT q.* INTO v_batch FROM video_queue.batch q WHERE q.batch_id=v_batch_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'REVIEW_BATCH_MISMATCH'; END IF;
    SELECT q.* INTO v_existing FROM video_queue.subset_reviewed_release q
      WHERE q.batch_id=v_batch_id FOR UPDATE;
    IF FOUND THEN
        IF v_existing.review_bytes IS DISTINCT FROM p_review_bytes
           OR v_existing.released_at IS NULL THEN RAISE EXCEPTION 'REVIEW_RECEIPT_CONFLICT'; END IF;
        RETURN jsonb_build_object('status','ALREADY_RELEASED','batch_id',v_batch_id,
            'review_sha256',v_sha,'released_jobs',v_existing.released_jobs,
            'released_at',v_existing.released_at,'queue_release_only',true,
            'student_delivery_allowed',false,'database_persistence_allowed',false,
            'canonical_promotion_allowed',false);
    END IF;
    IF v_batch.status IS DISTINCT FROM 'CANARY_REVIEW'
       OR v_batch.completed_at IS NOT NULL OR v_batch.expected_count < 2
       OR v_batch.canonical_promotion_allowed OR v_batch.database_persistence_allowed
    THEN RAISE EXCEPTION 'REVIEW_BATCH_NOT_READY'; END IF;
    SELECT q.* INTO v_admission FROM video_queue.subset_admission q
      WHERE q.batch_id=v_batch_id;
    IF NOT FOUND OR v_admission.state IS DISTINCT FROM 'FINALIZED'
       OR v_admission.approval_id::text IS DISTINCT FROM v_review->>'approval_id'
    THEN RAISE EXCEPTION 'REVIEW_ADMISSION_MISMATCH'; END IF;
    PERFORM video_queue.subset_assert_final(v_admission.request_key);
    SELECT q.* INTO v_approval FROM video_queue.subset_approval q
      WHERE q.approval_id=v_admission.approval_id FOR SHARE;
    v_now := clock_timestamp();
    IF NOT FOUND OR NOT v_approval.runtime_enabled OR v_approval.revoked_at IS NOT NULL
       OR v_approval.expires_at <= v_now OR NOT v_approval.contract @> video_queue.subset_disabled()
    THEN RAISE EXCEPTION 'REVIEW_APPROVAL_INACTIVE'; END IF;
    SELECT q.* INTO v_canary FROM video_queue.job q
      WHERE q.job_id=(v_review->>'canary_job_id')::uuid FOR UPDATE;
    IF NOT FOUND OR v_canary.batch_id IS DISTINCT FROM v_batch_id
       OR NOT v_canary.is_canary OR v_canary.source_file_id IS DISTINCT FROM v_batch.canary_source_file_id
       OR v_canary.status IS DISTINCT FROM 'REVIEW_READY' OR v_canary.completed_at IS NULL
       OR v_canary.attempt_count NOT BETWEEN 1 AND 3
       OR v_canary.output->>'terminal_evidence_sha256' IS DISTINCT FROM v_review->>'terminal_evidence_sha256'
       OR v_canary.output->>'artifact_manifest_sha256' IS DISTINCT FROM v_review->>'artifact_manifest_sha256'
       OR v_canary.output#>>'{terminal_receipt,status}' IS DISTINCT FROM 'PASS'
       OR v_canary.output->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'
       OR v_canary.output->'canonical_promotion_allowed' IS DISTINCT FROM 'false'::jsonb
       OR v_canary.output->'database_persistence_allowed' IS DISTINCT FROM 'false'::jsonb
    THEN RAISE EXCEPTION 'REVIEW_CANARY_RECEIPT_INVALID'; END IF;
    SELECT q.* INTO v_observation FROM video_queue.source_observation q
      WHERE q.observation_id=(v_review->>'observation_id')::uuid FOR SHARE;
    IF NOT FOUND OR v_observation.job_id IS DISTINCT FROM v_canary.job_id
       OR v_observation.approval_id IS DISTINCT FROM v_admission.approval_id
       OR v_observation.phase IS DISTINCT FROM 'ATTEMPT'
       OR v_observation.contract_sha256 IS DISTINCT FROM v_approval.contract_sha256
       OR v_observation.request_key IS DISTINCT FROM v_admission.request_key
       OR v_observation.attempt_count IS DISTINCT FROM v_canary.attempt_count
       OR v_observation.consumed_at IS NULL
    THEN RAISE EXCEPTION 'REVIEW_OBSERVATION_NOT_DURABLE'; END IF;
    IF NOT EXISTS (SELECT FROM video_queue.source_observation q
        WHERE q.job_id=v_canary.job_id AND q.approval_id=v_admission.approval_id
          AND q.phase='ATTEMPT' AND q.observation_id<>v_observation.observation_id
          AND q.lease_token=v_observation.lease_token
          AND q.attempt_count=v_observation.attempt_count
          AND q.worker_key=v_observation.worker_key
          AND q.consumed_at IS NOT NULL AND q.consumed_at<v_observation.consumed_at)
    THEN RAISE EXCEPTION 'REVIEW_PREWORK_OBSERVATION_MISSING'; END IF;
    IF NOT EXISTS (SELECT FROM video_queue.job_event q WHERE q.job_id=v_canary.job_id
        AND q.event_type='REVIEW_READY' AND q.lease_token=v_observation.lease_token
        AND q.worker_key=v_observation.worker_key
        AND q.details->>'terminal_evidence_sha256'=v_review->>'terminal_evidence_sha256'
        AND q.details->>'artifact_manifest_sha256'=v_review->>'artifact_manifest_sha256')
    THEN RAISE EXCEPTION 'REVIEW_TERMINAL_EVENT_MISSING'; END IF;
    SELECT count(*) INTO v_pending FROM video_queue.job q
      WHERE q.batch_id=v_batch_id AND NOT q.is_canary AND q.status='PENDING_CANARY'
        AND q.attempt_count=0;
    IF v_pending <> v_batch.expected_count-1 OR v_pending < 1
       OR (SELECT count(*) FROM video_queue.job q WHERE q.batch_id=v_batch_id) <> v_batch.expected_count
    THEN RAISE EXCEPTION 'REVIEW_DEPENDENT_SET_CHANGED'; END IF;

    INSERT INTO video_queue.subset_reviewed_release(batch_id,approval_id,canary_job_id,
        observation_id,terminal_evidence_sha256,review_reference,
        review_bytes,review_sha256,reviewed_by,release_xid)
    VALUES (v_batch_id,v_admission.approval_id,v_canary.job_id,
        v_observation.observation_id,v_review->>'terminal_evidence_sha256',
        v_review->>'review_reference',p_review_bytes,v_sha,session_user,txid_current());
    UPDATE video_queue.job q SET status='QUEUED',updated_at=clock_timestamp()
      WHERE q.batch_id=v_batch_id AND NOT q.is_canary AND q.status='PENDING_CANARY';
    GET DIAGNOSTICS v_released = ROW_COUNT;
    IF v_released <> v_pending THEN RAISE EXCEPTION 'REVIEW_RELEASE_COUNT_MISMATCH'; END IF;
    UPDATE video_queue.batch q SET status='RUNNING',updated_at=clock_timestamp()
      WHERE q.batch_id=v_batch_id AND q.status='CANARY_REVIEW';
    IF NOT FOUND THEN RAISE EXCEPTION 'REVIEW_BATCH_TRANSITION_MISSING'; END IF;
    UPDATE video_queue.subset_reviewed_release q
      SET released_jobs=v_released,released_at=clock_timestamp() WHERE q.batch_id=v_batch_id;
    INSERT INTO video_queue.job_event(batch_id,job_id,event_type,details)
      VALUES(v_batch_id,NULL,'REVIEWED_RELEASE',jsonb_build_object(
        'review_sha256',v_sha,'review_reference',v_review->>'review_reference',
        'canary_job_id',v_canary.job_id,'released_jobs',v_released));
    -- Row and index locks above can outlive the earlier approval check. Use wall
    -- clock time: transaction_timestamp()/now() would remain frozen while waiting.
    IF v_approval.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'REVIEW_APPROVAL_INACTIVE';
    END IF;
    RETURN jsonb_build_object('status','RELEASED','batch_id',v_batch_id,
        'review_sha256',v_sha,'released_jobs',v_released,'queue_release_only',true,
        'student_delivery_allowed',false,'database_persistence_allowed',false,
        'canonical_promotion_allowed',false);
END $$;
ALTER FUNCTION video_queue.release_reviewed_subset_batch(text) OWNER TO video_subset_owner;
REVOKE ALL ON FUNCTION video_queue.release_reviewed_subset_batch(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.release_reviewed_subset_batch(text) TO video_subset_approver;
COMMIT;
