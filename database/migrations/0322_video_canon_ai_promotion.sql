\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_canon_verifier') THEN
        CREATE ROLE bridge_school_canon_verifier NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_canon_promoter') THEN
        CREATE ROLE bridge_school_canon_promoter NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    FOR r IN SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication
               FROM pg_roles WHERE rolname IN ('bridge_school_canon_verifier','bridge_school_canon_promoter') LOOP
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
            RAISE EXCEPTION 'unsafe Video-to-Canon role: %',r.rolname;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_canon_verifier IS
  'NOLOGIN capability that records content-bound AI verification receipts but cannot activate Canon';
COMMENT ON ROLE bridge_school_canon_promoter IS
  'NOLOGIN capability for the guarded AI-verified teacher-video Canon activation RPC';
GRANT bridge_school_reader TO bridge_school_canon_verifier;
GRANT bridge_school_reader TO bridge_school_canon_promoter;
REVOKE CREATE ON SCHEMA public,bidding FROM bridge_school_canon_verifier,bridge_school_canon_promoter;

CREATE TABLE bidding.video_canon_source_policy (
    video_canon_source_policy_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    source_id uuid NOT NULL REFERENCES public.source(source_id) ON DELETE RESTRICT,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    video_file_id text NOT NULL CHECK (btrim(video_file_id)<>''),
    teacher_ids text[] NOT NULL CHECK (cardinality(teacher_ids)>0),
    semantic_scopes text[] NOT NULL CHECK (cardinality(semantic_scopes)>0),
    system_profile text NOT NULL CHECK (btrim(system_profile)<>''),
    learner_level text NOT NULL CHECK (btrim(learner_level)<>''),
    policy_version text NOT NULL CHECK (policy_version='school-video-auto-canon-v1'),
    authorization_evidence_sha256 text NOT NULL CHECK (authorization_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','superseded')),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to>valid_from),
    UNIQUE (school_id,source_id,source_sha256,video_file_id,policy_version)
);

CREATE TABLE bidding.video_canon_ai_verification (
    video_canon_ai_verification_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    check_id text NOT NULL CHECK (check_id IN (
        'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
        'SEMANTIC_PARSE','EXPLANATION_COMPLETENESS','BRIDGE_LOGIC',
        'HIDDEN_INFORMATION_FIREWALL','POSITIVE_TESTS','NEGATIVE_TESTS',
        'BOUNDARY_TESTS','INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY',
        'CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'
    )),
    result text NOT NULL CHECK (result IN ('PASS','FAIL','ERROR')),
    verifier_family text NOT NULL CHECK (btrim(verifier_family)<>''),
    verifier_version text NOT NULL CHECK (btrim(verifier_version)<>''),
    assurance_level text NOT NULL CHECK (assurance_level IN ('I0','I1','I2','I3')),
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_candidate_id,candidate_payload_hash,check_id,verifier_family,verifier_version,evidence_sha256)
);

CREATE TABLE bidding.video_canon_ai_promotion_receipt (
    video_canon_ai_promotion_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL UNIQUE REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    policy_version text NOT NULL CHECK (policy_version='school-video-auto-canon-v1'),
    rule_id uuid NOT NULL UNIQUE REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    canon_activation_id uuid NOT NULL UNIQUE REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    runtime_activation_id uuid NOT NULL UNIQUE REFERENCES bidding.runtime_activation(runtime_activation_id) ON DELETE RESTRICT,
    promotion_mode text NOT NULL CHECK (promotion_mode='AI_VERIFIED_TEACHER_VIDEO'),
    human_approval_required boolean NOT NULL CHECK (human_approval_required=false),
    promoted_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_school uuid; v_hash text;
BEGIN
    SELECT school_id,payload_hash INTO v_school,v_hash
      FROM public.analysis_candidate WHERE analysis_candidate_id=NEW.analysis_candidate_id;
    IF v_school IS NULL OR v_school<>NEW.school_id THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF v_hash<>NEW.candidate_payload_hash THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_PAYLOAD_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_verification_guard
BEFORE INSERT ON bidding.video_canon_ai_verification
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_canon_verification();

CREATE TRIGGER video_canon_source_policy_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_source_policy
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE TRIGGER video_canon_verification_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_verification
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE TRIGGER video_canon_promotion_receipt_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_promotion_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION bidding.activate_ai_verified_video_canon(
    p_analysis_candidate_id uuid,
    p_rule_id uuid,
    p_scope_key text,
    p_verification_bundle_sha256 text,
    p_policy_version text,
    p_valid_from timestamptz DEFAULT now(),
    p_valid_to timestamptz DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public,bidding
AS $$
DECLARE
    v_candidate public.analysis_candidate%ROWTYPE;
    v_rule bidding.rule%ROWTYPE;
    v_version public.knowledge_version%ROWTYPE;
    v_policy bidding.video_canon_source_policy%ROWTYPE;
    v_canon_activation uuid;
    v_runtime_activation uuid;
    v_existing uuid;
    v_semantic_family text;
    v_bridge_family text;
BEGIN
    IF p_policy_version<>'school-video-auto-canon-v1'
       OR p_verification_bundle_sha256 !~ '^[0-9a-f]{64}$'
       OR btrim(COALESCE(p_scope_key,''))='' THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTION_ARGUMENT_INVALID' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=p_analysis_candidate_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_NOT_FOUND' USING ERRCODE='23514'; END IF;

    SELECT video_canon_ai_promotion_receipt_id INTO v_existing
      FROM bidding.video_canon_ai_promotion_receipt
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    IF v_existing IS NOT NULL THEN RETURN v_existing; END IF;

    IF v_candidate.candidate_type<>'video_school_canon_candidate'
       OR v_candidate.promotion_status NOT IN ('staging','review_queue')
       OR v_candidate.payload->>'schema'<>'video-canon-evidence-v2'
       OR v_candidate.payload->>'review_eligibility'<>'AI_VERIFICATION_PENDING'
       OR v_candidate.payload->>'source_class'<>'SCHOOL_PRIMARY_EVIDENCE'
       OR v_candidate.payload#>>'{source_authorization,policy_version}'<>p_policy_version
       OR v_candidate.payload->>'semantic_scope'<>p_scope_key
       OR jsonb_array_length(COALESCE(v_candidate.payload->'ambiguities','[]'::jsonb))<>0
       OR jsonb_array_length(COALESCE(v_candidate.payload->'contradictions','[]'::jsonb))<>0
       OR COALESCE((v_candidate.payload->>'semantic_confidence')::numeric,0)<0.95
       OR bidding.contains_forbidden_hidden_key(v_candidate.payload) THEN
        RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_NOT_ELIGIBLE' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_rule FROM bidding.rule WHERE rule_id=p_rule_id;
    IF NOT FOUND OR v_rule.school_id<>v_candidate.school_id
       OR v_rule.lifecycle_status<>'validated'
       OR v_rule.compiled_payload->>'video_candidate_payload_hash'<>v_candidate.payload_hash THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_BINDING_INVALID' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_version FROM public.knowledge_version
     WHERE knowledge_version_id=v_rule.knowledge_version_id;
    IF v_version.authority_class<>'school_canon' THEN
        RAISE EXCEPTION 'VIDEO_CANON_AUTHORITY_LANE_INVALID' USING ERRCODE='23514';
    END IF;

    SELECT p.* INTO v_policy
      FROM bidding.video_canon_source_policy p
      JOIN public.knowledge_version_source kvs
        ON kvs.source_id=p.source_id AND kvs.knowledge_version_id=v_version.knowledge_version_id
     WHERE p.school_id=v_candidate.school_id
       AND p.source_id=v_candidate.source_id
       AND p.status='active'
       AND p.valid_from<=p_valid_from AND (p.valid_to IS NULL OR p.valid_to>p_valid_from)
       AND p.source_sha256=v_candidate.payload#>>'{source,source_sha256}'
       AND p.video_file_id=v_candidate.payload#>>'{source,video_file_id}'
       AND (v_candidate.payload#>>'{teacher_assertion,speaker_id}')=ANY(p.teacher_ids)
       AND p_scope_key=ANY(p.semantic_scopes)
       AND p.policy_version=p_policy_version
       AND p.authorization_evidence_sha256=v_candidate.payload#>>'{source_authorization,authorization_evidence_sha256}'
       AND p.system_profile=v_version.bidding_system_key
       AND p.learner_level=v_version.level_scope->>'level_key';
    IF NOT FOUND THEN RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_NOT_FOUND' USING ERRCODE='23514'; END IF;

    IF EXISTS (
        SELECT 1 FROM bidding.video_canon_ai_verification v
         WHERE v.analysis_candidate_id=p_analysis_candidate_id
           AND v.candidate_payload_hash=v_candidate.payload_hash AND v.result<>'PASS'
    ) OR EXISTS (
        SELECT req.check_id FROM (VALUES
          ('SOURCE_AUTHORITY'),('SOURCE_BINDING'),('SPEAKER_IDENTITY'),('TRANSCRIPT_BINDING'),
          ('SEMANTIC_PARSE'),('EXPLANATION_COMPLETENESS'),('BRIDGE_LOGIC'),
          ('HIDDEN_INFORMATION_FIREWALL'),('POSITIVE_TESTS'),('NEGATIVE_TESTS'),
          ('BOUNDARY_TESTS'),('INTERFERENCE_TESTS'),('CANON_REGRESSION'),('CANON_INTEGRITY'),
          ('CANON_CONFLICT_SCAN'),('ROLLBACK_RESTORE')
        ) req(check_id)
        WHERE NOT EXISTS (
          SELECT 1 FROM bidding.video_canon_ai_verification v
           WHERE v.analysis_candidate_id=p_analysis_candidate_id
             AND v.candidate_payload_hash=v_candidate.payload_hash
             AND v.check_id=req.check_id AND v.result='PASS'
        )
    ) THEN RAISE EXCEPTION 'VIDEO_CANON_AI_CHECKS_INCOMPLETE' USING ERRCODE='23514'; END IF;

    SELECT verifier_family INTO v_semantic_family FROM bidding.video_canon_ai_verification
     WHERE analysis_candidate_id=p_analysis_candidate_id AND candidate_payload_hash=v_candidate.payload_hash
       AND check_id='SEMANTIC_PARSE' AND result='PASS' AND assurance_level IN ('I2','I3') LIMIT 1;
    SELECT verifier_family INTO v_bridge_family FROM bidding.video_canon_ai_verification
     WHERE analysis_candidate_id=p_analysis_candidate_id AND candidate_payload_hash=v_candidate.payload_hash
       AND check_id='BRIDGE_LOGIC' AND result='PASS' AND assurance_level IN ('I2','I3') LIMIT 1;
    IF v_semantic_family IS NULL OR v_bridge_family IS NULL OR v_semantic_family=v_bridge_family
       OR NOT EXISTS (
         SELECT 1 FROM bidding.video_canon_ai_verification
          WHERE analysis_candidate_id=p_analysis_candidate_id AND candidate_payload_hash=v_candidate.payload_hash
            AND check_id='HIDDEN_INFORMATION_FIREWALL' AND result='PASS' AND assurance_level IN ('I2','I3')
       ) THEN RAISE EXCEPTION 'VIDEO_CANON_I2_INDEPENDENCE_MISSING' USING ERRCODE='23514'; END IF;

    IF EXISTS (
      SELECT req.test_type FROM (VALUES ('positive'),('negative'),('boundary'),('interference'),('hidden_information'),('regression')) req(test_type)
      WHERE NOT EXISTS (
        SELECT 1 FROM bidding.rule_test t WHERE t.rule_id=p_rule_id AND t.enabled
          AND t.test_type=req.test_type AND bidding.latest_test_result(t.rule_test_id)='pass'
      )
    ) OR EXISTS (
      SELECT 1 FROM bidding.rule_conflict c WHERE c.status='open'
        AND (c.left_rule_id=p_rule_id OR c.right_rule_id=p_rule_id)
    ) THEN RAISE EXCEPTION 'VIDEO_CANON_RULE_GATES_FAILED' USING ERRCODE='23514'; END IF;

    UPDATE public.knowledge_version SET review_status='approved',status='approved'
     WHERE knowledge_version_id=v_version.knowledge_version_id;
    INSERT INTO public.canon_activation(
      knowledge_version_id,scope_key,valid_from,valid_to,approved_by_person_id,approval_provenance,status
    ) VALUES (
      v_version.knowledge_version_id,p_scope_key,p_valid_from,p_valid_to,NULL,
      jsonb_build_object('promotion_mode','AI_VERIFIED_TEACHER_VIDEO','policy_version',p_policy_version,
        'candidate_id',p_analysis_candidate_id,'candidate_payload_hash',v_candidate.payload_hash,
        'verification_bundle_sha256',p_verification_bundle_sha256,'human_approval_required',false),'active'
    ) RETURNING canon_activation_id INTO v_canon_activation;

    INSERT INTO bidding.runtime_activation(
      school_id,rule_id,authority_lane,canon_activation_id,scope_key,valid_from,valid_to,status,
      activation_provenance,activated_by_person_id
    ) VALUES (
      v_candidate.school_id,p_rule_id,'school_canon',v_canon_activation,p_scope_key,p_valid_from,p_valid_to,'active',
      jsonb_build_object('promotion_mode','AI_VERIFIED_TEACHER_VIDEO','candidate_payload_hash',v_candidate.payload_hash),NULL
    ) RETURNING runtime_activation_id INTO v_runtime_activation;

    INSERT INTO bidding.video_canon_ai_promotion_receipt(
      school_id,analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,
      policy_version,rule_id,canon_activation_id,runtime_activation_id,promotion_mode,human_approval_required
    ) VALUES (
      v_candidate.school_id,p_analysis_candidate_id,v_candidate.payload_hash,p_verification_bundle_sha256,
      p_policy_version,p_rule_id,v_canon_activation,v_runtime_activation,'AI_VERIFIED_TEACHER_VIDEO',false
    ) RETURNING video_canon_ai_promotion_receipt_id INTO v_existing;
    UPDATE public.analysis_candidate SET quality_status='AI_VERIFIED',promotion_status='promoted'
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    RETURN v_existing;
END $$;

REVOKE ALL ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text,text,text,timestamptz,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text,text,text,timestamptz,timestamptz)
  TO bridge_school_canon_promoter;

GRANT SELECT ON bidding.video_canon_source_policy,bidding.video_canon_ai_verification,
  bidding.video_canon_ai_promotion_receipt TO bridge_school_reader;
GRANT INSERT ON bidding.video_canon_ai_verification TO bridge_school_canon_verifier;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON bidding.video_canon_source_policy,
  bidding.video_canon_ai_promotion_receipt FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_promoter;
REVOKE UPDATE,DELETE,TRUNCATE ON bidding.video_canon_ai_verification
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,bridge_school_canon_promoter;
REVOKE ALL ON FUNCTION bidding.validate_video_canon_verification() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION bidding.validate_video_canon_verification()
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,bridge_school_canon_promoter;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0322_video_canon_ai_promotion') ON CONFLICT DO NOTHING;
COMMIT;
