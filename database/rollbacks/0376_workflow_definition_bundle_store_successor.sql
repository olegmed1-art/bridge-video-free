-- SOURCE ONLY rollback: disable Definition, retain every stored row and denial guard.
-- Not a deletion/uninstall. NOT authorized to run by generating this file.
BEGIN;
SET LOCAL search_path=pg_catalog,public,bidding,pg_temp;
SET LOCAL TimeZone='UTC';
DO $rollback_preflight$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid='bidding.validate_video_canon_verification_bundle()'::regprocedure
 AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\n',E'\n'),'UTF8'),'sha256'),'hex')='6967c3b85e20d0149d54f790e69cd3323507f4dd49c39a8df813411620849fa6') THEN
 RAISE EXCEPTION 'DEFINITION_ROLLBACK_SOURCE_PIN_MISMATCH:bidding.validate_video_canon_verification_bundle'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p WHERE p.oid='bidding.validate_video_canon_verification()'::regprocedure
 AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\n',E'\n'),'UTF8'),'sha256'),'hex')='f184ccb839e0400ab3c03b735ea07ede97abac6b7172ff0fa6e1143d5f40dbc1') THEN
 RAISE EXCEPTION 'DEFINITION_ROLLBACK_SOURCE_PIN_MISMATCH:bidding.validate_video_canon_verification'; END IF;
 IF (SELECT count(*) FROM pg_catalog.pg_trigger WHERE tgname IN ('video_definition_bundle_denial','video_definition_check_denial')
 AND tgfoid='bidding.video_definition_denial_guard()'::regprocedure AND tgenabled IN ('O','A'))<>2 THEN
 RAISE EXCEPTION 'DEFINITION_ROLLBACK_DENIAL_GUARD_REQUIRED'; END IF;
END $rollback_preflight$;
UPDATE bidding.video_definition_store_control SET enabled=false,generation=generation+1 WHERE singleton;
CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification_bundle()
RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE v_candidate public.analysis_candidate%ROWTYPE; v_decoded jsonb; v_computed text;
BEGIN
    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id;
    IF NOT FOUND OR v_candidate.school_id<>NEW.school_id
       OR v_candidate.payload_hash<>NEW.candidate_payload_hash
       OR v_candidate.candidate_type<>'video_school_canon_candidate' THEN
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
       OR NOT ((NEW.bundle_payload->>'canon_snapshot_sha256') ~ '^[0-9a-f]{64}$')
       OR NOT ((NEW.bundle_payload->>'rule_test_state_sha256') ~ '^[0-9a-f]{64}$')
       OR jsonb_typeof(NEW.bundle_payload->'checks')<>'array'
       OR jsonb_array_length(NEW.bundle_payload->'checks')<>16
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(NEW.bundle_payload->'checks') AS c(value)
          WHERE c.value->>'result' IS DISTINCT FROM 'PASS'
             OR c.value->>'check_id' NOT IN (
               'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
               'SEMANTIC_PARSE','EXPLANATION_COMPLETENESS','BRIDGE_LOGIC',
               'HIDDEN_INFORMATION_FIREWALL','POSITIVE_TESTS','NEGATIVE_TESTS',
               'BOUNDARY_TESTS','INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY',
               'CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'
             )
       )
       OR (SELECT count(DISTINCT c.value->>'check_id')
             FROM jsonb_array_elements(NEW.bundle_payload->'checks') AS c(value))<>16
       OR jsonb_typeof(NEW.bundle_payload->'effective_period')<>'object'
       OR jsonb_typeof(NEW.bundle_payload->'rollback')<>'object' THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CONTENT_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_bound record;
    v_principal bidding.video_canon_verifier_registry%ROWTYPE;
BEGIN
    SELECT * INTO v_bound FROM bidding.video_canon_bound_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id
       AND video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id;
    IF NOT FOUND OR v_bound.school_id<>NEW.school_id
       OR v_bound.payload_hash<>NEW.candidate_payload_hash
       OR v_bound.bound_candidate_payload_hash<>NEW.candidate_payload_hash
       OR v_bound.verification_bundle_sha256<>NEW.verification_bundle_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BINDING_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF v_bound.verification_set_closed THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_VERIFICATION_SET_CLOSED' USING ERRCODE='55000';
    END IF;
    SELECT * INTO v_principal FROM bidding.video_canon_verifier_registry
     WHERE database_role=current_user AND status='active';
    IF NOT FOUND OR NEW.verifier_family<>v_principal.verifier_family
       OR NEW.execution_principal<>session_user
       OR NOT (NEW.check_id=ANY(v_principal.allowed_check_ids))
       OR (v_principal.max_assurance_level='I1' AND NEW.assurance_level NOT IN ('I0','I1'))
       OR (v_principal.max_assurance_level='I2' AND NEW.assurance_level NOT IN ('I0','I1','I2'))
       OR v_principal.max_assurance_level NOT IN ('I1','I2','I3') THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_bound.assigned_checks) AS c(value)
          WHERE c.value->>'check_id'=NEW.check_id
            AND c.value->>'result'=NEW.result
            AND c.value->>'verifier_family'=NEW.verifier_family
            AND c.value->>'verifier_version'=NEW.verifier_version
            AND c.value->>'execution_principal'=NEW.execution_principal
            AND c.value->>'assurance_level'=NEW.assurance_level
            AND c.value->>'evidence_sha256'=NEW.evidence_sha256
            AND (c.value->>'canon_snapshot_sha256') IS NOT DISTINCT FROM NEW.canon_snapshot_sha256
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BUNDLE_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION bidding.video_definition_need(pg_catalog.bool,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_keys(jsonb,text[]) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_int(jsonb,pg_catalog.int8) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_test_context(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_manifest_digest(jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_test_state(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_canonical_int(jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_digest(text,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_epoch_us(timestamptz) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_unique_json(pg_catalog.json,pg_catalog.int4) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_scope(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_bounded_root() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_metadata_fence() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_require_binding(uuid,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_policy(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_descriptor(uuid,jsonb,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_control_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_binding_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_no_truncate() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_binding_for(uuid,text,jsonb,text,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_validate_bundle_row(bidding.video_canon_ai_verification_bundle) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_validate_check_row(bidding.video_canon_ai_verification) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_denial_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.register_video_definition_bundle(uuid,uuid,text,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.record_video_definition_verification(uuid,uuid,text,text,text,text,text,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.read_current_video_definition_bundle(uuid,uuid) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.read_video_definition_test_context(uuid) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_commit_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON bidding.video_definition_store_control,bidding.video_definition_store_binding FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
COMMIT;
