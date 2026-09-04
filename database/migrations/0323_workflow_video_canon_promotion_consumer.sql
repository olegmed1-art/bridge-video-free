\set ON_ERROR_STOP on
BEGIN;

-- Durable, fenced consumer boundary for the already guarded 0322 activation.
-- This migration is intentionally not applied by this PR.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname=ANY(ARRAY[
      'bridge_school_canon_i2_verifier','bridge_school_canon_i3_verifier',
      'bridge_school_canon_consumer'
    ])
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_RUNTIME_ROLE_COLLISION' USING ERRCODE='55000';
  END IF;
END $$;

DO $$
DECLARE v_role text;
BEGIN
  FOREACH v_role IN ARRAY ARRAY[
    'bridge_school_canon_i2_verifier','bridge_school_canon_i3_verifier',
    'bridge_school_canon_consumer'
  ] LOOP
    EXECUTE format(
      'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',
      v_role
    );
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA public,bidding TO
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier,
  bridge_school_canon_consumer;
REVOKE CREATE ON SCHEMA public,bidding FROM
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier,
  bridge_school_canon_consumer;

CREATE TABLE bidding.video_canon_assurance_verifier_registry (
  assurance_level text PRIMARY KEY CHECK (assurance_level IN ('I2','I3')),
  capability_role name NOT NULL UNIQUE,
  verifier_family text NOT NULL UNIQUE CHECK (btrim(verifier_family)<>''),
  verifier_version text NOT NULL CHECK (btrim(verifier_version)<>''),
  registered_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (assurance_level='I2' AND capability_role='bridge_school_canon_i2_verifier')
    OR (assurance_level='I3' AND capability_role='bridge_school_canon_i3_verifier')
  )
);
INSERT INTO bidding.video_canon_assurance_verifier_registry(
  assurance_level,capability_role,verifier_family,verifier_version
) VALUES
  ('I2','bridge_school_canon_i2_verifier','independent-i2','pinned-v1'),
  ('I3','bridge_school_canon_i3_verifier','independent-i3','pinned-v1');
CREATE TRIGGER video_canon_assurance_verifier_registry_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_assurance_verifier_registry
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE TABLE bidding.video_canon_assurance_verdict (
  video_canon_assurance_verdict_id uuid PRIMARY KEY DEFAULT uuidv7(),
  school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
  analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
  video_canon_ai_verification_bundle_id uuid NOT NULL
    REFERENCES bidding.video_canon_ai_verification_bundle(video_canon_ai_verification_bundle_id) ON DELETE RESTRICT,
  candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash~'^[0-9a-f]{64}$'),
  verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256~'^[0-9a-f]{64}$'),
  assurance_set_sha256 text NOT NULL CHECK (assurance_set_sha256~'^[0-9a-f]{64}$'),
  assurance_level text NOT NULL CHECK (assurance_level IN ('I2','I3')),
  verdict text NOT NULL CHECK (verdict IN (
    'VERIFIED_FOR_PROMOTION','REJECTED','NEEDS_EVIDENCE',
    'CANON_CONFLICT','PROFILE_AMBIGUITY'
  )),
  verifier_family text NOT NULL CHECK (btrim(verifier_family)<>''),
  verifier_version text NOT NULL CHECK (btrim(verifier_version)<>''),
  execution_principal text NOT NULL DEFAULT session_user CHECK (btrim(execution_principal)<>''),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256~'^[0-9a-f]{64}$'),
  canon_snapshot_sha256 text NOT NULL CHECK (canon_snapshot_sha256~'^[0-9a-f]{64}$'),
  system_profile text NOT NULL CHECK (btrim(system_profile)<>''),
  learner_level text NOT NULL CHECK (btrim(learner_level)<>''),
  provenance_verified boolean NOT NULL,
  hidden_information_clear boolean NOT NULL,
  profile_unambiguous boolean NOT NULL,
  canon_conflict boolean NOT NULL,
  deterministic boolean NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (
    analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,
    assurance_level
  )
);

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_assurance_verdict()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,bidding AS $$
DECLARE
  v_candidate public.analysis_candidate%ROWTYPE;
  v_bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
  v_registry bidding.video_canon_assurance_verifier_registry%ROWTYPE;
BEGIN
  IF NEW.execution_principal<>session_user THEN
    RAISE EXCEPTION 'VIDEO_CANON_ASSURANCE_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
  END IF;
  SELECT * INTO v_registry FROM bidding.video_canon_assurance_verifier_registry
   WHERE assurance_level=NEW.assurance_level;
  IF NOT FOUND OR NOT pg_has_role(session_user,v_registry.capability_role,'member')
     OR NEW.verifier_family IS DISTINCT FROM v_registry.verifier_family
     OR NEW.verifier_version IS DISTINCT FROM v_registry.verifier_version THEN
    RAISE EXCEPTION 'VIDEO_CANON_ASSURANCE_ROLE_MISMATCH' USING ERRCODE='42501';
  END IF;
  SELECT * INTO v_candidate FROM public.analysis_candidate
   WHERE analysis_candidate_id=NEW.analysis_candidate_id;
  SELECT * INTO v_bundle FROM bidding.video_canon_ai_verification_bundle
   WHERE video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id;
  IF NOT FOUND OR v_candidate.analysis_candidate_id IS NULL
     OR v_candidate.school_id<>NEW.school_id
     OR v_candidate.candidate_type<>'video_school_canon_candidate'
     OR v_candidate.payload->>'authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'
     OR v_candidate.payload->>'source_class' IS DISTINCT FROM 'SCHOOL_PRIMARY_EVIDENCE'
     OR v_candidate.payload_hash<>NEW.candidate_payload_hash
     OR v_bundle.school_id<>NEW.school_id
     OR v_bundle.analysis_candidate_id<>NEW.analysis_candidate_id
     OR v_bundle.candidate_payload_hash<>NEW.candidate_payload_hash
     OR v_bundle.verification_bundle_sha256<>NEW.verification_bundle_sha256
     OR v_bundle.bundle_payload->>'canon_snapshot_sha256' IS DISTINCT FROM NEW.canon_snapshot_sha256
     OR v_bundle.bundle_payload->>'system_profile' IS DISTINCT FROM NEW.system_profile
     OR v_bundle.bundle_payload->>'learner_level' IS DISTINCT FROM NEW.learner_level THEN
    RAISE EXCEPTION 'VIDEO_CANON_ASSURANCE_BINDING_INVALID' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER video_canon_assurance_verdict_guard
BEFORE INSERT ON bidding.video_canon_assurance_verdict
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_canon_assurance_verdict();
CREATE TRIGGER video_canon_assurance_verdict_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_assurance_verdict
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE TABLE bidding.video_canon_promotion_job (
  video_canon_promotion_job_id uuid PRIMARY KEY DEFAULT uuidv7(),
  school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
  analysis_candidate_id uuid NOT NULL UNIQUE REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
  rule_id uuid NOT NULL UNIQUE REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
  video_canon_ai_verification_bundle_id uuid NOT NULL
    REFERENCES bidding.video_canon_ai_verification_bundle(video_canon_ai_verification_bundle_id) ON DELETE RESTRICT,
  candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash~'^[0-9a-f]{64}$'),
  verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256~'^[0-9a-f]{64}$'),
  assurance_set_sha256 text NOT NULL CHECK (assurance_set_sha256~'^[0-9a-f]{64}$'),
  idempotency_key text NOT NULL UNIQUE CHECK (btrim(idempotency_key)<>''),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','leased','promoted','blocked')),
  attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
  lease_owner text,
  lease_token uuid,
  lease_expires_at timestamptz,
  fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token>=0),
  terminal_error_code text CHECK (terminal_error_code IS NULL OR terminal_error_code IN (
    'CANON_CONFLICT','PROFILE_AMBIGUITY','HIDDEN_INFORMATION','PROVENANCE_INVALID',
    'I2_I3_MISMATCH','CANDIDATE_CHANGED','STATE_STALE','INTEGRITY_FAILED',
    'RETRYABLE_DATABASE_ERROR','ATTEMPTS_EXHAUSTED'
  )),
  promotion_receipt_id uuid UNIQUE
    REFERENCES bidding.video_canon_ai_promotion_receipt(video_canon_ai_promotion_receipt_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (updated_at>=created_at),
  CHECK (
    (status='leased')=(lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
  ),
  CHECK ((status='promoted')=(promotion_receipt_id IS NOT NULL)),
  CHECK ((status='blocked')=(terminal_error_code IS NOT NULL))
);

CREATE INDEX video_canon_promotion_job_claim_idx
ON bidding.video_canon_promotion_job(status,lease_expires_at,created_at)
WHERE status IN ('queued','leased');

CREATE TABLE bidding.video_canon_promotion_delivery_receipt (
  video_canon_promotion_delivery_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
  video_canon_promotion_job_id uuid NOT NULL UNIQUE
    REFERENCES bidding.video_canon_promotion_job(video_canon_promotion_job_id) ON DELETE RESTRICT,
  school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
  analysis_candidate_id uuid NOT NULL UNIQUE REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
  candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash~'^[0-9a-f]{64}$'),
  verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256~'^[0-9a-f]{64}$'),
  assurance_set_sha256 text NOT NULL CHECK (assurance_set_sha256~'^[0-9a-f]{64}$'),
  fencing_token bigint NOT NULL CHECK (fencing_token>0),
  promotion_receipt_id uuid NOT NULL UNIQUE
    REFERENCES bidding.video_canon_ai_promotion_receipt(video_canon_ai_promotion_receipt_id) ON DELETE RESTRICT,
  post_write_integrity_sha256 text NOT NULL CHECK (post_write_integrity_sha256~'^[0-9a-f]{64}$'),
  completed_by_principal text NOT NULL CHECK (btrim(completed_by_principal)<>''),
  completed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER video_canon_promotion_delivery_receipt_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_promotion_delivery_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION bidding.enqueue_video_canon_promotion(
  p_analysis_candidate_id uuid,p_rule_id uuid,p_verification_bundle_sha256 text,
  p_assurance_set_sha256 text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE
  v_candidate public.analysis_candidate%ROWTYPE;
  v_bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
  v_rule bidding.rule%ROWTYPE;
  v_existing bidding.video_canon_promotion_job%ROWTYPE;
  v_job_id uuid;
BEGIN
  SELECT * INTO v_candidate FROM public.analysis_candidate
   WHERE analysis_candidate_id=p_analysis_candidate_id FOR SHARE;
  SELECT * INTO v_bundle FROM bidding.video_canon_ai_verification_bundle
   WHERE analysis_candidate_id=p_analysis_candidate_id
     AND verification_bundle_sha256=p_verification_bundle_sha256 FOR SHARE;
  SELECT * INTO v_rule FROM bidding.rule WHERE rule_id=p_rule_id FOR SHARE;
  IF v_candidate.analysis_candidate_id IS NULL OR v_bundle.video_canon_ai_verification_bundle_id IS NULL
     OR v_rule.rule_id IS NULL OR v_candidate.school_id<>v_bundle.school_id
     OR v_candidate.school_id<>v_rule.school_id
     OR v_candidate.payload_hash<>v_bundle.candidate_payload_hash
     OR v_candidate.payload->>'authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'
     OR v_candidate.payload->>'source_class' IS DISTINCT FROM 'SCHOOL_PRIMARY_EVIDENCE'
     OR v_rule.lifecycle_status<>'validated'
     OR v_rule.compiled_payload->>'video_candidate_payload_hash' IS DISTINCT FROM v_candidate.payload_hash THEN
    RAISE EXCEPTION 'VIDEO_CANON_ENQUEUE_BINDING_INVALID' USING ERRCODE='23514';
  END IF;
  IF p_assurance_set_sha256!~'^[0-9a-f]{64}$' OR NOT EXISTS (
    SELECT 1 FROM bidding.video_canon_assurance_verdict i2
    JOIN bidding.video_canon_assurance_verdict i3
      ON i3.analysis_candidate_id=i2.analysis_candidate_id
     AND i3.candidate_payload_hash=i2.candidate_payload_hash
     AND i3.verification_bundle_sha256=i2.verification_bundle_sha256
     AND i3.assurance_set_sha256=i2.assurance_set_sha256
     AND i3.assurance_level='I3'
    WHERE i2.analysis_candidate_id=p_analysis_candidate_id
      AND i2.candidate_payload_hash=v_candidate.payload_hash
      AND i2.verification_bundle_sha256=p_verification_bundle_sha256
      AND i2.assurance_set_sha256=p_assurance_set_sha256
      AND i2.assurance_level='I2'
      AND i2.verdict='VERIFIED_FOR_PROMOTION'
      AND i3.verdict='VERIFIED_FOR_PROMOTION'
      AND i2.provenance_verified AND i3.provenance_verified
      AND i2.hidden_information_clear AND i3.hidden_information_clear
      AND i2.profile_unambiguous AND i3.profile_unambiguous
      AND NOT i2.canon_conflict AND NOT i3.canon_conflict
      AND i2.deterministic AND i3.deterministic
      AND i2.verifier_family<>i3.verifier_family
      AND i2.execution_principal<>i3.execution_principal
      AND i2.canon_snapshot_sha256=i3.canon_snapshot_sha256
      AND i2.system_profile=i3.system_profile
      AND i2.learner_level=i3.learner_level
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_I2_I3_NOT_VERIFIED' USING ERRCODE='23514';
  END IF;
  SELECT * INTO v_existing FROM bidding.video_canon_promotion_job
   WHERE analysis_candidate_id=p_analysis_candidate_id;
  IF FOUND THEN
    IF v_existing.rule_id<>p_rule_id
       OR v_existing.verification_bundle_sha256<>p_verification_bundle_sha256
       OR v_existing.assurance_set_sha256<>p_assurance_set_sha256 THEN
      RAISE EXCEPTION 'VIDEO_CANON_ENQUEUE_IDEMPOTENCY_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN v_existing.video_canon_promotion_job_id;
  END IF;
  INSERT INTO bidding.video_canon_promotion_job(
    school_id,analysis_candidate_id,rule_id,video_canon_ai_verification_bundle_id,
    candidate_payload_hash,verification_bundle_sha256,assurance_set_sha256,idempotency_key
  ) VALUES (
    v_candidate.school_id,p_analysis_candidate_id,p_rule_id,
    v_bundle.video_canon_ai_verification_bundle_id,v_candidate.payload_hash,
    p_verification_bundle_sha256,p_assurance_set_sha256,
    'video-canon:'||v_candidate.payload_hash||':'||p_verification_bundle_sha256
  ) ON CONFLICT (analysis_candidate_id) DO NOTHING
    RETURNING video_canon_promotion_job_id INTO v_job_id;
  IF v_job_id IS NULL THEN
    SELECT * INTO v_existing FROM bidding.video_canon_promotion_job
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    IF NOT FOUND OR v_existing.rule_id<>p_rule_id
       OR v_existing.verification_bundle_sha256<>p_verification_bundle_sha256
       OR v_existing.assurance_set_sha256<>p_assurance_set_sha256 THEN
      RAISE EXCEPTION 'VIDEO_CANON_ENQUEUE_IDEMPOTENCY_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN v_existing.video_canon_promotion_job_id;
  END IF;
  RETURN v_job_id;
END $$;

CREATE OR REPLACE FUNCTION bidding.claim_video_canon_promotion(p_lease_seconds integer DEFAULT 120)
RETURNS TABLE(
  video_canon_promotion_job_id uuid,analysis_candidate_id uuid,rule_id uuid,
  candidate_payload_hash text,verification_bundle_sha256 text,
  assurance_set_sha256 text,lease_token uuid,fencing_token bigint,lease_expires_at timestamptz
) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE v_id uuid;
BEGIN
  IF p_lease_seconds<30 OR p_lease_seconds>900 THEN
    RAISE EXCEPTION 'VIDEO_CANON_LEASE_DURATION_INVALID' USING ERRCODE='23514';
  END IF;
  UPDATE bidding.video_canon_promotion_job SET
    status='blocked',terminal_error_code='ATTEMPTS_EXHAUSTED',lease_owner=NULL,
    lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
  WHERE status='leased' AND lease_expires_at<=clock_timestamp() AND attempt_count>=5;
  SELECT j.video_canon_promotion_job_id INTO v_id
    FROM bidding.video_canon_promotion_job j
   WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<=clock_timestamp()))
     AND j.attempt_count<5
   ORDER BY j.created_at,j.video_canon_promotion_job_id
   FOR UPDATE SKIP LOCKED LIMIT 1;
  IF v_id IS NULL THEN RETURN; END IF;
  RETURN QUERY
  UPDATE bidding.video_canon_promotion_job j SET
    status='leased',attempt_count=j.attempt_count+1,lease_owner=session_user,
    lease_token=uuidv7(),lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
    fencing_token=j.fencing_token+1,terminal_error_code=NULL,updated_at=clock_timestamp()
  WHERE j.video_canon_promotion_job_id=v_id
  RETURNING j.video_canon_promotion_job_id,j.analysis_candidate_id,j.rule_id,
    j.candidate_payload_hash,j.verification_bundle_sha256,j.assurance_set_sha256,
    j.lease_token,j.fencing_token,j.lease_expires_at;
END $$;

CREATE OR REPLACE FUNCTION bidding.consume_video_canon_promotion(
  p_job_id uuid,p_lease_token uuid,p_fencing_token bigint
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE
  v_job bidding.video_canon_promotion_job%ROWTYPE;
  v_existing bidding.video_canon_promotion_delivery_receipt%ROWTYPE;
  v_promotion uuid;
  v_delivery uuid;
  v_integrity text;
BEGIN
  SELECT * INTO v_existing FROM bidding.video_canon_promotion_delivery_receipt
   WHERE video_canon_promotion_job_id=p_job_id;
  IF FOUND THEN RETURN v_existing.video_canon_promotion_delivery_receipt_id; END IF;
  SELECT * INTO v_job FROM bidding.video_canon_promotion_job
   WHERE video_canon_promotion_job_id=p_job_id FOR UPDATE;
  IF NOT FOUND OR v_job.status<>'leased' OR v_job.lease_owner<>session_user
     OR v_job.lease_token<>p_lease_token OR v_job.fencing_token<>p_fencing_token
     OR v_job.lease_expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'VIDEO_CANON_STALE_LEASE_OR_FENCE' USING ERRCODE='55000';
  END IF;
  -- Recheck both assurance rows at the mutation boundary; a non-PASS or stale
  -- candidate cannot be hidden behind an earlier enqueue decision.
  IF NOT EXISTS (
    SELECT 1 FROM bidding.video_canon_assurance_verdict i2
    JOIN bidding.video_canon_assurance_verdict i3
      ON i3.analysis_candidate_id=i2.analysis_candidate_id
     AND i3.candidate_payload_hash=i2.candidate_payload_hash
     AND i3.verification_bundle_sha256=i2.verification_bundle_sha256
     AND i3.assurance_set_sha256=i2.assurance_set_sha256
     AND i3.assurance_level='I3'
    WHERE i2.analysis_candidate_id=v_job.analysis_candidate_id
      AND i2.candidate_payload_hash=v_job.candidate_payload_hash
      AND i2.verification_bundle_sha256=v_job.verification_bundle_sha256
      AND i2.assurance_set_sha256=v_job.assurance_set_sha256
      AND i2.assurance_level='I2'
      AND i2.verdict='VERIFIED_FOR_PROMOTION' AND i3.verdict='VERIFIED_FOR_PROMOTION'
      AND i2.provenance_verified AND i3.provenance_verified
      AND i2.hidden_information_clear AND i3.hidden_information_clear
      AND i2.profile_unambiguous AND i3.profile_unambiguous
      AND NOT i2.canon_conflict AND NOT i3.canon_conflict
      AND i2.deterministic AND i3.deterministic
      AND i2.verifier_family<>i3.verifier_family
      AND i2.execution_principal<>i3.execution_principal
      AND pg_has_role(i2.execution_principal,'bridge_school_canon_i2_verifier','member')
      AND pg_has_role(i3.execution_principal,'bridge_school_canon_i3_verifier','member')
      AND EXISTS (
        SELECT 1 FROM bidding.video_canon_assurance_verifier_registry r2
         WHERE r2.assurance_level='I2' AND r2.capability_role='bridge_school_canon_i2_verifier'
           AND r2.verifier_family=i2.verifier_family AND r2.verifier_version=i2.verifier_version
      )
      AND EXISTS (
        SELECT 1 FROM bidding.video_canon_assurance_verifier_registry r3
         WHERE r3.assurance_level='I3' AND r3.capability_role='bridge_school_canon_i3_verifier'
           AND r3.verifier_family=i3.verifier_family AND r3.verifier_version=i3.verifier_version
      )
      AND i2.canon_snapshot_sha256=i3.canon_snapshot_sha256
      AND i2.system_profile=i3.system_profile
      AND i2.learner_level=i3.learner_level
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_I2_I3_STALE' USING ERRCODE='23514';
  END IF;
  v_promotion:=bidding.activate_ai_verified_video_canon(
    v_job.analysis_candidate_id,v_job.rule_id,v_job.verification_bundle_sha256
  );
  IF NOT EXISTS (
    SELECT 1 FROM bidding.video_canon_ai_promotion_receipt pr
    JOIN public.canon_activation ca ON ca.canon_activation_id=pr.canon_activation_id
    JOIN bidding.runtime_activation ra ON ra.runtime_activation_id=pr.runtime_activation_id
    JOIN public.analysis_candidate c ON c.analysis_candidate_id=pr.analysis_candidate_id
    WHERE pr.video_canon_ai_promotion_receipt_id=v_promotion
      AND pr.analysis_candidate_id=v_job.analysis_candidate_id
      AND pr.candidate_payload_hash=v_job.candidate_payload_hash
      AND pr.verification_bundle_sha256=v_job.verification_bundle_sha256
      AND ca.status='active' AND ra.status='active'
      AND c.quality_status='AI_VERIFIED' AND c.promotion_status='promoted'
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_POST_WRITE_INTEGRITY_FAILED' USING ERRCODE='23514';
  END IF;
  v_integrity:=encode(public.digest(convert_to(jsonb_build_object(
    'job_id',p_job_id,'candidate_payload_hash',v_job.candidate_payload_hash,
    'verification_bundle_sha256',v_job.verification_bundle_sha256,
    'assurance_set_sha256',v_job.assurance_set_sha256,
    'promotion_receipt_id',v_promotion,'fencing_token',p_fencing_token,
    'status','POST_WRITE_INTEGRITY_PASS'
  )::text,'UTF8'),'sha256'),'hex');
  INSERT INTO bidding.video_canon_promotion_delivery_receipt(
    video_canon_promotion_job_id,school_id,analysis_candidate_id,
    candidate_payload_hash,verification_bundle_sha256,assurance_set_sha256,
    fencing_token,promotion_receipt_id,post_write_integrity_sha256,
    completed_by_principal
  ) VALUES (
    p_job_id,v_job.school_id,v_job.analysis_candidate_id,
    v_job.candidate_payload_hash,v_job.verification_bundle_sha256,
    v_job.assurance_set_sha256,p_fencing_token,v_promotion,v_integrity,session_user
  ) RETURNING video_canon_promotion_delivery_receipt_id INTO v_delivery;
  UPDATE bidding.video_canon_promotion_job SET
    status='promoted',promotion_receipt_id=v_promotion,lease_owner=NULL,
    lease_token=NULL,lease_expires_at=NULL,terminal_error_code=NULL,
    updated_at=clock_timestamp()
  WHERE video_canon_promotion_job_id=p_job_id;
  RETURN v_delivery;
END $$;

CREATE OR REPLACE FUNCTION bidding.fail_video_canon_promotion(
  p_job_id uuid,p_lease_token uuid,p_fencing_token bigint,p_error_code text
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE v_job bidding.video_canon_promotion_job%ROWTYPE; v_status text;
BEGIN
  IF p_error_code NOT IN (
    'CANON_CONFLICT','PROFILE_AMBIGUITY','HIDDEN_INFORMATION','PROVENANCE_INVALID',
    'I2_I3_MISMATCH','CANDIDATE_CHANGED','STATE_STALE','INTEGRITY_FAILED',
    'RETRYABLE_DATABASE_ERROR'
  ) THEN RAISE EXCEPTION 'VIDEO_CANON_ERROR_CODE_INVALID' USING ERRCODE='23514'; END IF;
  SELECT * INTO v_job FROM bidding.video_canon_promotion_job
   WHERE video_canon_promotion_job_id=p_job_id FOR UPDATE;
  IF NOT FOUND OR v_job.status<>'leased' OR v_job.lease_owner<>session_user
     OR v_job.lease_token<>p_lease_token OR v_job.fencing_token<>p_fencing_token
     OR v_job.lease_expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'VIDEO_CANON_STALE_LEASE_OR_FENCE' USING ERRCODE='55000';
  END IF;
  v_status:=CASE WHEN p_error_code='RETRYABLE_DATABASE_ERROR' AND v_job.attempt_count<5
                 THEN 'queued' ELSE 'blocked' END;
  UPDATE bidding.video_canon_promotion_job SET status=v_status,
    terminal_error_code=CASE WHEN v_status='blocked' THEN
      CASE WHEN attempt_count>=5 AND p_error_code='RETRYABLE_DATABASE_ERROR'
           THEN 'ATTEMPTS_EXHAUSTED' ELSE p_error_code END
      ELSE NULL END,
    lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
   WHERE video_canon_promotion_job_id=p_job_id;
  RETURN upper(v_status);
END $$;

REVOKE EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text)
  FROM bridge_school_canon_promoter;
REVOKE ALL ON FUNCTION bidding.validate_video_canon_assurance_verdict() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.enqueue_video_canon_promotion(uuid,uuid,text,text),
  bidding.claim_video_canon_promotion(integer),
  bidding.consume_video_canon_promotion(uuid,uuid,bigint),
  bidding.fail_video_canon_promotion(uuid,uuid,bigint,text) FROM PUBLIC;

GRANT INSERT ON bidding.video_canon_assurance_verdict TO
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier;
GRANT SELECT ON bidding.video_canon_ai_verification_bundle,
  bidding.video_canon_bound_candidate TO
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier;
GRANT EXECUTE ON FUNCTION bidding.enqueue_video_canon_promotion(uuid,uuid,text,text)
  TO bridge_school_canon_verifier;
GRANT EXECUTE ON FUNCTION bidding.claim_video_canon_promotion(integer),
  bidding.consume_video_canon_promotion(uuid,uuid,bigint),
  bidding.fail_video_canon_promotion(uuid,uuid,bigint,text)
  TO bridge_school_canon_consumer;

REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON
  bidding.video_canon_promotion_job,
  bidding.video_canon_promotion_delivery_receipt
  FROM bridge_school_canon_consumer,bridge_school_canon_verifier,
       bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier;
REVOKE UPDATE,DELETE,TRUNCATE ON bidding.video_canon_assurance_verdict FROM
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier;
REVOKE ALL ON bidding.video_canon_assurance_verifier_registry FROM
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier,
  bridge_school_canon_consumer,bridge_school_canon_verifier;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0323_workflow_video_canon_promotion_consumer') ON CONFLICT DO NOTHING;

COMMIT;
