\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE r record; v_role text;
BEGIN
    FOREACH v_role IN ARRAY ARRAY[
      'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
      'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
      'bridge_school_canon_control_verifier','bridge_school_canon_promoter'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=v_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION',v_role);
        END IF;
    END LOOP;
    FOR r IN SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication
               FROM pg_roles WHERE rolname=ANY(ARRAY[
                 'bridge_school_canon_verifier','bridge_school_canon_semantic_verifier',
                 'bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier',
                 'bridge_school_canon_control_verifier','bridge_school_canon_promoter'
               ]) LOOP
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
            RAISE EXCEPTION 'unsafe Video-to-Canon role: %',r.rolname;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_canon_verifier IS
  'NOLOGIN capability that records sealed verification bundles but cannot attest checks or activate Canon';
COMMENT ON ROLE bridge_school_canon_semantic_verifier IS
  'NOLOGIN capability for authenticated semantic-parser attestations only';
COMMENT ON ROLE bridge_school_canon_bridge_verifier IS
  'NOLOGIN capability for authenticated bridge-logic attestations only';
COMMENT ON ROLE bridge_school_canon_firewall_verifier IS
  'NOLOGIN capability for authenticated hidden-information firewall attestations only';
COMMENT ON ROLE bridge_school_canon_control_verifier IS
  'NOLOGIN capability for non-independent control attestations only';
COMMENT ON ROLE bridge_school_canon_promoter IS
  'NOLOGIN capability for the guarded AI-verified teacher-video Canon activation RPC';
GRANT bridge_school_reader TO bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter;
REVOKE CREATE ON SCHEMA public,bidding FROM bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter;

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

CREATE TABLE bidding.video_canon_ai_verification_bundle (
    video_canon_ai_verification_bundle_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    bundle_canonical_json text NOT NULL CHECK (btrim(bundle_canonical_json)<>''),
    bundle_payload jsonb NOT NULL CHECK (jsonb_typeof(bundle_payload)='object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256)
);

CREATE TABLE bidding.video_canon_verifier_registry (
    database_role text PRIMARY KEY,
    verifier_family text NOT NULL UNIQUE CHECK (btrim(verifier_family)<>''),
    allowed_check_ids text[] NOT NULL CHECK (cardinality(allowed_check_ids)>0),
    max_assurance_level text NOT NULL CHECK (max_assurance_level IN ('I1','I2','I3')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    registered_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO bidding.video_canon_verifier_registry(
  database_role,verifier_family,allowed_check_ids,max_assurance_level
) VALUES
  ('bridge_school_canon_semantic_verifier','semantic-model-a',ARRAY['SEMANTIC_PARSE'],'I3'),
  ('bridge_school_canon_bridge_verifier','bridge-engine-b',ARRAY['BRIDGE_LOGIC'],'I3'),
  ('bridge_school_canon_firewall_verifier','taint-analyzer',ARRAY['HIDDEN_INFORMATION_FIREWALL'],'I3'),
  ('bridge_school_canon_control_verifier','formal-checker',ARRAY[
    'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
    'EXPLANATION_COMPLETENESS','POSITIVE_TESTS','NEGATIVE_TESTS','BOUNDARY_TESTS',
    'INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN',
    'ROLLBACK_RESTORE'
  ],'I1');

CREATE TABLE bidding.video_canon_ai_verification (
    video_canon_ai_verification_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    video_canon_ai_verification_bundle_id uuid NOT NULL
        REFERENCES bidding.video_canon_ai_verification_bundle(video_canon_ai_verification_bundle_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
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
    UNIQUE (analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,check_id,verifier_family,verifier_version,evidence_sha256)
);

CREATE TABLE bidding.video_canon_ai_promotion_receipt (
    video_canon_ai_promotion_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    analysis_candidate_id uuid NOT NULL UNIQUE REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
    candidate_payload_hash text NOT NULL CHECK (candidate_payload_hash ~ '^[0-9a-f]{64}$'),
    verification_bundle_sha256 text NOT NULL CHECK (verification_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    policy_version text NOT NULL CHECK (policy_version='school-video-auto-canon-v1'),
    scope_key text NOT NULL CHECK (btrim(scope_key)<>''),
    rule_content_sha256 text NOT NULL CHECK (rule_content_sha256 ~ '^[0-9a-f]{64}$'),
    rule_id uuid NOT NULL UNIQUE REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    canon_activation_id uuid NOT NULL UNIQUE REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    runtime_activation_id uuid NOT NULL UNIQUE REFERENCES bidding.runtime_activation(runtime_activation_id) ON DELETE RESTRICT,
    superseded_canon_activation_id uuid REFERENCES public.canon_activation(canon_activation_id) ON DELETE RESTRICT,
    superseded_runtime_activation_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    promotion_mode text NOT NULL CHECK (promotion_mode='AI_VERIFIED_TEACHER_VIDEO'),
    human_approval_required boolean NOT NULL CHECK (human_approval_required=false),
    promoted_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification_bundle()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_candidate public.analysis_candidate%ROWTYPE; v_decoded jsonb; v_computed text;
BEGIN
    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id;
    IF NOT FOUND OR v_candidate.school_id<>NEW.school_id
       OR v_candidate.payload_hash<>NEW.candidate_payload_hash THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CANDIDATE_MISMATCH' USING ERRCODE='23514';
    END IF;
    BEGIN
        v_decoded := NEW.bundle_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CANONICAL_JSON_INVALID' USING ERRCODE='23514';
    END;
    v_computed := encode(public.digest(convert_to(NEW.bundle_canonical_json,'UTF8'),'sha256'),'hex');
    IF v_decoded<>NEW.bundle_payload OR v_computed<>NEW.verification_bundle_sha256
       OR NEW.bundle_payload->>'schema'<>'video-canon-ai-promotion-v1'
       OR NEW.bundle_payload->>'policy_version'<>'school-video-auto-canon-v1'
       OR NEW.bundle_payload->>'candidate_payload_hash'<>NEW.candidate_payload_hash
       OR NEW.bundle_payload->'candidate_payload'<>v_candidate.payload
       OR jsonb_typeof(NEW.bundle_payload->'checks')<>'array'
       OR jsonb_typeof(NEW.bundle_payload->'effective_period')<>'object'
       OR jsonb_typeof(NEW.bundle_payload->'rollback')<>'object' THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CONTENT_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_verification_bundle_guard
BEFORE INSERT ON bidding.video_canon_ai_verification_bundle
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_canon_verification_bundle();

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_school uuid;
    v_hash text;
    v_bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
    v_principal bidding.video_canon_verifier_registry%ROWTYPE;
BEGIN
    SELECT school_id,payload_hash INTO v_school,v_hash
      FROM public.analysis_candidate WHERE analysis_candidate_id=NEW.analysis_candidate_id;
    IF v_school IS NULL OR v_school<>NEW.school_id THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF v_hash<>NEW.candidate_payload_hash THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_PAYLOAD_MISMATCH' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_principal FROM bidding.video_canon_verifier_registry
     WHERE database_role=current_user AND status='active';
    IF NOT FOUND OR NEW.verifier_family<>v_principal.verifier_family
       OR NOT (NEW.check_id=ANY(v_principal.allowed_check_ids))
       OR (v_principal.max_assurance_level='I1' AND NEW.assurance_level NOT IN ('I0','I1'))
       OR (v_principal.max_assurance_level='I2' AND NEW.assurance_level NOT IN ('I0','I1','I2'))
       OR v_principal.max_assurance_level NOT IN ('I1','I2','I3') THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_bundle FROM bidding.video_canon_ai_verification_bundle
     WHERE video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id;
    IF NOT FOUND OR v_bundle.school_id<>NEW.school_id
       OR v_bundle.analysis_candidate_id<>NEW.analysis_candidate_id
       OR v_bundle.candidate_payload_hash<>NEW.candidate_payload_hash
       OR v_bundle.verification_bundle_sha256<>NEW.verification_bundle_sha256
       OR NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_bundle.bundle_payload->'checks') AS c(value)
          WHERE c.value->>'check_id'=NEW.check_id
            AND c.value->>'result'=NEW.result
            AND c.value->>'verifier_family'=NEW.verifier_family
            AND c.value->>'verifier_version'=NEW.verifier_version
            AND c.value->>'assurance_level'=NEW.assurance_level
            AND c.value->>'evidence_sha256'=NEW.evidence_sha256
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BUNDLE_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_verification_guard
BEFORE INSERT ON bidding.video_canon_ai_verification
FOR EACH ROW EXECUTE FUNCTION bidding.validate_video_canon_verification();

CREATE OR REPLACE FUNCTION bidding.guard_bound_video_canon_candidate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM bidding.video_canon_ai_verification_bundle
         WHERE analysis_candidate_id=OLD.analysis_candidate_id
    ) THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'VIDEO_CANON_BOUND_CANDIDATE_DELETE_FORBIDDEN' USING ERRCODE='55000';
        END IF;
        IF NEW.school_id IS DISTINCT FROM OLD.school_id
           OR NEW.analysis_run_id IS DISTINCT FROM OLD.analysis_run_id
           OR NEW.source_id IS DISTINCT FROM OLD.source_id
           OR NEW.candidate_type IS DISTINCT FROM OLD.candidate_type
           OR NEW.stable_key IS DISTINCT FROM OLD.stable_key
           OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
           OR NEW.payload IS DISTINCT FROM OLD.payload
           OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
           OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
           OR NEW.method_version IS DISTINCT FROM OLD.method_version
           OR NEW.supersedes_candidate_id IS DISTINCT FROM OLD.supersedes_candidate_id
           OR NEW.status IS DISTINCT FROM OLD.status
           OR (OLD.promotion_status='promoted' AND (
               NEW.promotion_status<>'promoted' OR NEW.quality_status<>'AI_VERIFIED'
           )) THEN
            RAISE EXCEPTION 'VIDEO_CANON_BOUND_CANDIDATE_MUTATION_FORBIDDEN' USING ERRCODE='55000';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER bound_video_canon_candidate_guard
BEFORE UPDATE OR DELETE ON public.analysis_candidate
FOR EACH ROW EXECUTE FUNCTION bidding.guard_bound_video_canon_candidate();

CREATE OR REPLACE FUNCTION bidding.guard_video_canon_source_policy_lifecycle()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_DELETE_FORBIDDEN' USING ERRCODE='55000';
    END IF;
    IF OLD.status<>'active' OR NEW.status NOT IN ('revoked','superseded')
       OR NEW.valid_to IS NULL OR NEW.valid_to>now()
       OR (to_jsonb(NEW)-ARRAY['status','valid_to'])<>(to_jsonb(OLD)-ARRAY['status','valid_to']) THEN
        RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_MUTATION_FORBIDDEN' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_source_policy_lifecycle_guard
BEFORE UPDATE OR DELETE ON bidding.video_canon_source_policy
FOR EACH ROW EXECUTE FUNCTION bidding.guard_video_canon_source_policy_lifecycle();
CREATE TRIGGER video_canon_verification_bundle_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_verification_bundle
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE OR REPLACE FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.status<>'active' OR NEW.status<>'revoked'
       OR (to_jsonb(NEW)-'status')<>(to_jsonb(OLD)-'status') THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_REGISTRY_MUTATION_FORBIDDEN' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER video_canon_verifier_registry_lifecycle_guard
BEFORE UPDATE OR DELETE ON bidding.video_canon_verifier_registry
FOR EACH ROW EXECUTE FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle();
CREATE TRIGGER video_canon_verification_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_verification
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();
CREATE TRIGGER video_canon_promotion_receipt_append_only
BEFORE UPDATE OR DELETE ON bidding.video_canon_ai_promotion_receipt
FOR EACH ROW EXECUTE FUNCTION bidding.reject_append_only_mutation();

CREATE OR REPLACE FUNCTION bidding.activate_ai_verified_video_canon(
    p_analysis_candidate_id uuid,
    p_rule_id uuid,
    p_verification_bundle_sha256 text
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
    v_bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
    v_canon_activation uuid;
    v_runtime_activation uuid;
    v_existing bidding.video_canon_ai_promotion_receipt%ROWTYPE;
    v_prior_canon public.canon_activation%ROWTYPE;
    v_prior_runtime_ids uuid[] := '{}'::uuid[];
    v_semantic_family text;
    v_bridge_family text;
    v_scope_key text;
    v_policy_version text;
    v_valid_from timestamptz;
    v_valid_to timestamptz;
    v_rule_content jsonb;
    v_rule_content_sha256 text;
    v_expected_rule_content_sha256 text;
BEGIN
    IF p_verification_bundle_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTION_ARGUMENT_INVALID' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=p_analysis_candidate_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_NOT_FOUND' USING ERRCODE='23514'; END IF;

    SELECT * INTO v_bundle FROM bidding.video_canon_ai_verification_bundle
     WHERE analysis_candidate_id=p_analysis_candidate_id
       AND candidate_payload_hash=v_candidate.payload_hash
       AND verification_bundle_sha256=p_verification_bundle_sha256;
    IF NOT FOUND OR v_bundle.bundle_payload->'candidate_payload'<>v_candidate.payload THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BUNDLE_NOT_FOUND' USING ERRCODE='23514';
    END IF;
    v_scope_key := v_bundle.bundle_payload->>'activation_scope';
    v_policy_version := v_bundle.bundle_payload->>'policy_version';
    BEGIN
        v_valid_from := (v_bundle.bundle_payload#>>'{effective_period,valid_from}')::timestamptz;
        v_valid_to := NULLIF(v_bundle.bundle_payload#>>'{effective_period,valid_to}','')::timestamptz;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CANON_EFFECTIVE_PERIOD_INVALID' USING ERRCODE='23514';
    END;
    IF v_policy_version<>'school-video-auto-canon-v1'
       OR btrim(COALESCE(v_scope_key,''))=''
       OR v_valid_from IS NULL OR v_valid_from>statement_timestamp()
       OR (v_valid_to IS NOT NULL AND v_valid_to<=v_valid_from) THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_ARGUMENT_INVALID' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_existing
      FROM bidding.video_canon_ai_promotion_receipt
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    IF FOUND THEN
        IF v_existing.candidate_payload_hash<>v_candidate.payload_hash
           OR v_existing.verification_bundle_sha256<>p_verification_bundle_sha256
           OR v_existing.policy_version<>v_policy_version
           OR v_existing.scope_key<>v_scope_key
           OR v_existing.rule_id<>p_rule_id THEN
            RAISE EXCEPTION 'VIDEO_CANON_IDEMPOTENCY_MISMATCH' USING ERRCODE='23514';
        END IF;
        RETURN v_existing.video_canon_ai_promotion_receipt_id;
    END IF;

    IF v_candidate.candidate_type<>'video_school_canon_candidate'
       OR v_candidate.promotion_status NOT IN ('staging','review_queue')
       OR v_candidate.payload->>'schema'<>'video-canon-evidence-v2'
       OR v_candidate.payload->>'review_eligibility'<>'AI_VERIFICATION_PENDING'
       OR v_candidate.payload->>'source_class'<>'SCHOOL_PRIMARY_EVIDENCE'
       OR v_candidate.payload#>>'{source_authorization,policy_version}'<>v_policy_version
       OR v_candidate.payload->>'semantic_scope'<>v_scope_key
       OR jsonb_array_length(COALESCE(v_candidate.payload->'ambiguities','[]'::jsonb))<>0
       OR jsonb_array_length(COALESCE(v_candidate.payload->'contradictions','[]'::jsonb))<>0
       OR COALESCE((v_candidate.payload->>'semantic_confidence')::numeric,0)<0.95
       OR bidding.contains_forbidden_hidden_key(v_candidate.payload) THEN
        RAISE EXCEPTION 'VIDEO_CANON_CANDIDATE_NOT_ELIGIBLE' USING ERRCODE='23514';
    END IF;

    SELECT * INTO v_rule FROM bidding.rule WHERE rule_id=p_rule_id FOR UPDATE;
    IF NOT FOUND OR v_rule.school_id<>v_candidate.school_id
       OR v_rule.lifecycle_status<>'validated'
       OR v_rule.compiled_payload->>'video_candidate_payload_hash'<>v_candidate.payload_hash THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_BINDING_INVALID' USING ERRCODE='23514';
    END IF;
    v_rule_content := jsonb_build_object(
      'rule_key',v_rule.rule_key,'rule_kind',v_rule.rule_kind,
      'auction_pattern',v_rule.auction_pattern,'hand_constraints',v_rule.hand_constraints,
      'public_context_constraints',v_rule.public_context_constraints,'action',v_rule.action,
      'meaning',v_rule.meaning,'public_inference',v_rule.public_inference,
      'alert_semantics',v_rule.alert_semantics,'forcing_semantics',v_rule.forcing_semantics,
      'priority',v_rule.priority,'specificity',v_rule.specificity,
      'condition_schema_version',v_rule.condition_schema_version,
      'compiled_payload',v_rule.compiled_payload-ARRAY['video_candidate_payload_hash','video_rule_content_sha256'],
      'method_version',v_rule.method_version
    );
    v_rule_content_sha256 := encode(public.digest(convert_to(
      jsonb_build_object('normalized_rule',v_rule_content,'explanation',v_rule.explanation)::text,
      'UTF8'),'sha256'),'hex');
    v_expected_rule_content_sha256 := encode(public.digest(convert_to(
      jsonb_build_object('normalized_rule',v_candidate.payload->'normalized_rule',
        'explanation',v_candidate.payload->'explanation')::text,'UTF8'),'sha256'),'hex');
    IF v_rule_content<>v_candidate.payload->'normalized_rule'
       OR v_rule.explanation<>v_candidate.payload->'explanation'
       OR v_rule_content_sha256<>v_expected_rule_content_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_RULE_CONTENT_MISMATCH' USING ERRCODE='23514';
    END IF;
    SELECT * INTO v_version FROM public.knowledge_version
     WHERE knowledge_version_id=v_rule.knowledge_version_id;
    IF v_version.authority_class<>'school_canon' THEN
        RAISE EXCEPTION 'VIDEO_CANON_AUTHORITY_LANE_INVALID' USING ERRCODE='23514';
    END IF;

    SELECT p.* INTO v_policy
      FROM bidding.video_canon_source_policy p
      JOIN public.source s ON s.source_id=p.source_id AND s.status='active'
      JOIN public.knowledge_version_source kvs
        ON kvs.source_id=p.source_id AND kvs.knowledge_version_id=v_version.knowledge_version_id
     WHERE p.school_id=v_candidate.school_id
       AND p.source_id=v_candidate.source_id
       AND p.status='active'
       AND p.valid_from<=statement_timestamp()
       AND (p.valid_to IS NULL OR p.valid_to>statement_timestamp())
       AND p.valid_from<=v_valid_from AND (p.valid_to IS NULL OR p.valid_to>v_valid_from)
       AND (p.valid_to IS NULL OR (v_valid_to IS NOT NULL AND v_valid_to<=p.valid_to))
       AND p.source_sha256=v_candidate.payload#>>'{source,source_sha256}'
       AND p.video_file_id=v_candidate.payload#>>'{source,video_file_id}'
       AND (v_candidate.payload#>>'{teacher_assertion,speaker_id}')=ANY(p.teacher_ids)
       AND v_scope_key=ANY(p.semantic_scopes)
       AND p.policy_version=v_policy_version
       AND p.authorization_evidence_sha256=v_candidate.payload#>>'{source_authorization,authorization_evidence_sha256}'
       AND p.system_profile=v_bundle.bundle_payload->>'system_profile'
       AND p.learner_level=v_bundle.bundle_payload->>'learner_level'
       AND p.system_profile=v_version.bidding_system_key
       AND p.learner_level=v_version.level_scope->>'level_key'
     FOR SHARE OF p,s;
    IF NOT FOUND THEN RAISE EXCEPTION 'VIDEO_CANON_SOURCE_POLICY_NOT_FOUND' USING ERRCODE='23514'; END IF;

    IF EXISTS (
        SELECT 1 FROM bidding.video_canon_ai_verification v
         WHERE v.analysis_candidate_id=p_analysis_candidate_id
           AND v.candidate_payload_hash=v_candidate.payload_hash
           AND v.verification_bundle_sha256=p_verification_bundle_sha256
           AND v.result<>'PASS'
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
          JOIN bidding.video_canon_verifier_registry vr
            ON vr.verifier_family=v.verifier_family
           AND vr.status='active'
           AND v.check_id=ANY(vr.allowed_check_ids)
           AND (vr.max_assurance_level='I3'
                OR (vr.max_assurance_level='I2' AND v.assurance_level IN ('I0','I1','I2'))
                OR (vr.max_assurance_level='I1' AND v.assurance_level IN ('I0','I1')))
           WHERE v.analysis_candidate_id=p_analysis_candidate_id
             AND v.candidate_payload_hash=v_candidate.payload_hash
             AND v.verification_bundle_sha256=p_verification_bundle_sha256
             AND v.check_id=req.check_id AND v.result='PASS'
        )
    ) THEN RAISE EXCEPTION 'VIDEO_CANON_AI_CHECKS_INCOMPLETE' USING ERRCODE='23514'; END IF;

    SELECT v.verifier_family INTO v_semantic_family
      FROM bidding.video_canon_ai_verification v
      JOIN bidding.video_canon_verifier_registry vr
       ON vr.verifier_family=v.verifier_family AND vr.status='active'
       AND v.check_id=ANY(vr.allowed_check_ids)
       AND vr.max_assurance_level IN ('I2','I3')
       AND (vr.max_assurance_level='I3' OR v.assurance_level='I2')
     WHERE v.analysis_candidate_id=p_analysis_candidate_id
       AND v.candidate_payload_hash=v_candidate.payload_hash
       AND v.verification_bundle_sha256=p_verification_bundle_sha256
       AND v.check_id='SEMANTIC_PARSE' AND v.result='PASS'
       AND v.assurance_level IN ('I2','I3') LIMIT 1;
    SELECT v.verifier_family INTO v_bridge_family
      FROM bidding.video_canon_ai_verification v
      JOIN bidding.video_canon_verifier_registry vr
       ON vr.verifier_family=v.verifier_family AND vr.status='active'
       AND v.check_id=ANY(vr.allowed_check_ids)
       AND vr.max_assurance_level IN ('I2','I3')
       AND (vr.max_assurance_level='I3' OR v.assurance_level='I2')
     WHERE v.analysis_candidate_id=p_analysis_candidate_id
       AND v.candidate_payload_hash=v_candidate.payload_hash
       AND v.verification_bundle_sha256=p_verification_bundle_sha256
       AND v.check_id='BRIDGE_LOGIC' AND v.result='PASS'
       AND v.assurance_level IN ('I2','I3') LIMIT 1;
    IF v_semantic_family IS NULL OR v_bridge_family IS NULL OR v_semantic_family=v_bridge_family
       OR NOT EXISTS (
         SELECT 1 FROM bidding.video_canon_ai_verification v
         JOIN bidding.video_canon_verifier_registry vr
           ON vr.verifier_family=v.verifier_family AND vr.status='active'
          AND v.check_id=ANY(vr.allowed_check_ids)
          AND vr.max_assurance_level IN ('I2','I3')
          AND (vr.max_assurance_level='I3' OR v.assurance_level='I2')
          WHERE v.analysis_candidate_id=p_analysis_candidate_id
            AND v.candidate_payload_hash=v_candidate.payload_hash
            AND v.verification_bundle_sha256=p_verification_bundle_sha256
            AND v.check_id='HIDDEN_INFORMATION_FIREWALL' AND v.result='PASS'
            AND v.assurance_level IN ('I2','I3')
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

    SELECT ca.* INTO v_prior_canon
      FROM public.canon_activation ca
      JOIN public.knowledge_version prior_kv
        ON prior_kv.knowledge_version_id=ca.knowledge_version_id
     WHERE prior_kv.knowledge_item_id=v_version.knowledge_item_id
       AND ca.scope_key=v_scope_key AND ca.status='active'
       AND tstzrange(ca.valid_from,ca.valid_to,'[)') && tstzrange(v_valid_from,v_valid_to,'[)')
     FOR UPDATE OF ca;
    IF FOUND THEN
        IF v_prior_canon.knowledge_version_id=v_version.knowledge_version_id
           OR v_prior_canon.valid_from>=v_valid_from
           OR v_bundle.bundle_payload#>>'{rollback,target_knowledge_version_id}'
                IS DISTINCT FROM v_prior_canon.knowledge_version_id::text
           OR v_bundle.bundle_payload#>>'{rollback,target_canon_activation_id}'
                IS DISTINCT FROM v_prior_canon.canon_activation_id::text THEN
            RAISE EXCEPTION 'VIDEO_CANON_ROLLBACK_TARGET_MISMATCH' USING ERRCODE='23514';
        END IF;
        PERFORM 1 FROM bidding.runtime_activation
         WHERE canon_activation_id=v_prior_canon.canon_activation_id AND status='active' FOR UPDATE;
        SELECT COALESCE(array_agg(runtime_activation_id ORDER BY runtime_activation_id),'{}'::uuid[])
          INTO v_prior_runtime_ids FROM bidding.runtime_activation
         WHERE canon_activation_id=v_prior_canon.canon_activation_id AND status='active';
        UPDATE bidding.runtime_activation SET status='superseded',valid_to=v_valid_from
         WHERE canon_activation_id=v_prior_canon.canon_activation_id AND status='active';
        UPDATE public.canon_activation SET status='superseded',valid_to=v_valid_from
         WHERE canon_activation_id=v_prior_canon.canon_activation_id;
    ELSIF v_bundle.bundle_payload#>>'{rollback,target_knowledge_version_id}' IS NOT NULL
       OR v_bundle.bundle_payload#>>'{rollback,target_canon_activation_id}' IS NOT NULL THEN
        RAISE EXCEPTION 'VIDEO_CANON_ROLLBACK_TARGET_UNEXPECTED' USING ERRCODE='23514';
    END IF;

    UPDATE public.knowledge_version SET review_status='approved',status='approved'
     WHERE knowledge_version_id=v_version.knowledge_version_id;
    INSERT INTO public.canon_activation(
      knowledge_version_id,scope_key,valid_from,valid_to,approved_by_person_id,approval_provenance,status
    ) VALUES (
      v_version.knowledge_version_id,v_scope_key,v_valid_from,v_valid_to,NULL,
      jsonb_build_object('promotion_mode','AI_VERIFIED_TEACHER_VIDEO','policy_version',v_policy_version,
        'candidate_id',p_analysis_candidate_id,'candidate_payload_hash',v_candidate.payload_hash,
        'verification_bundle_sha256',p_verification_bundle_sha256,
        'rule_content_sha256',v_rule_content_sha256,'human_approval_required',false),'active'
    ) RETURNING canon_activation_id INTO v_canon_activation;

    INSERT INTO bidding.runtime_activation(
      school_id,rule_id,authority_lane,canon_activation_id,scope_key,valid_from,valid_to,status,
      activation_provenance,activated_by_person_id
    ) VALUES (
      v_candidate.school_id,p_rule_id,'school_canon',v_canon_activation,v_scope_key,v_valid_from,v_valid_to,'active',
      jsonb_build_object('promotion_mode','AI_VERIFIED_TEACHER_VIDEO',
        'candidate_payload_hash',v_candidate.payload_hash,'rule_content_sha256',v_rule_content_sha256),NULL
    ) RETURNING runtime_activation_id INTO v_runtime_activation;

    INSERT INTO bidding.video_canon_ai_promotion_receipt(
      school_id,analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,
      policy_version,scope_key,rule_content_sha256,rule_id,canon_activation_id,runtime_activation_id,
      superseded_canon_activation_id,superseded_runtime_activation_ids,promotion_mode,human_approval_required
    ) VALUES (
      v_candidate.school_id,p_analysis_candidate_id,v_candidate.payload_hash,p_verification_bundle_sha256,
      v_policy_version,v_scope_key,v_rule_content_sha256,p_rule_id,v_canon_activation,v_runtime_activation,
      v_prior_canon.canon_activation_id,v_prior_runtime_ids,'AI_VERIFIED_TEACHER_VIDEO',false
    ) RETURNING * INTO v_existing;
    UPDATE public.analysis_candidate SET quality_status='AI_VERIFIED',promotion_status='promoted'
     WHERE analysis_candidate_id=p_analysis_candidate_id;
    RETURN v_existing.video_canon_ai_promotion_receipt_id;
END $$;

REVOKE ALL ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text)
  TO bridge_school_canon_promoter;

GRANT SELECT ON bidding.video_canon_source_policy,bidding.video_canon_ai_verification_bundle,
  bidding.video_canon_verifier_registry,
  bidding.video_canon_ai_verification,
  bidding.video_canon_ai_promotion_receipt TO bridge_school_reader;
GRANT INSERT ON bidding.video_canon_ai_verification_bundle TO bridge_school_canon_verifier;
GRANT INSERT ON bidding.video_canon_ai_verification TO
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON bidding.video_canon_source_policy,
  bidding.video_canon_verifier_registry,bidding.video_canon_ai_promotion_receipt
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,bridge_school_canon_promoter;
REVOKE UPDATE,DELETE,TRUNCATE ON bidding.video_canon_ai_verification_bundle,bidding.video_canon_ai_verification
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter;
REVOKE INSERT ON bidding.video_canon_ai_verification FROM bridge_school_canon_verifier,bridge_school_canon_promoter;
REVOKE INSERT ON bidding.video_canon_ai_verification_bundle FROM
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter;
REVOKE ALL ON FUNCTION bidding.validate_video_canon_verification_bundle() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.validate_video_canon_verification() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_bound_video_canon_candidate() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_video_canon_source_policy_lifecycle() FROM PUBLIC;
REVOKE ALL ON FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION bidding.validate_video_canon_verification_bundle(),
  bidding.guard_bound_video_canon_candidate(),bidding.guard_video_canon_source_policy_lifecycle(),
  bidding.guard_video_canon_verifier_registry_lifecycle()
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter;
REVOKE EXECUTE ON FUNCTION bidding.validate_video_canon_verification()
  FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,
  bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,
  bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier,
  bridge_school_canon_promoter;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0322_workflow_video_canon_ai_promotion') ON CONFLICT DO NOTHING;
COMMIT;
