from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = (ROOT / "database/migrations/0322_workflow_video_canon_ai_promotion.sql").read_text()
ROLLBACK = (ROOT / "database/rollbacks/0322_workflow_video_canon_ai_promotion.sql").read_text()


def test_ai_promotion_is_narrow_guarded_and_not_granted_to_general_workers():
    assert "bridge_school_canon_verifier','bridge_school_canon_semantic_verifier" in MIGRATION
    assert "bridge_school_canon_bridge_verifier','bridge_school_canon_firewall_verifier" in MIGRATION
    assert "bridge_school_canon_control_verifier','bridge_school_canon_promoter" in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in MIGRATION
    assert "TO bridge_school_canon_promoter" in MIGRATION
    assert "GRANT INSERT ON bidding.video_canon_ai_verification_bundle TO bridge_school_canon_verifier" in MIGRATION
    assert "GRANT INSERT ON bidding.video_canon_ai_verification TO" in MIGRATION
    assert "FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_canon_verifier" in MIGRATION
    assert "promotion_mode','AI_VERIFIED_TEACHER_VIDEO'" in MIGRATION
    assert "'human_approval_required',false" in MIGRATION
    assert "CREATE TABLE bidding.video_correction_review_receipt" in MIGRATION
    assert "'CORRECTION_REVIEW'=ANY(v_principal.allowed_check_ids)" in MIGRATION
    assert "GRANT INSERT ON bidding.video_correction_review_receipt TO bridge_school_canon_control_verifier" in MIGRATION
    assert "GRANT SELECT ON bidding.video_correction_review_receipt TO bridge_school_worker" in MIGRATION
    assert "VIDEO_CANON_ROLE_COLLISION" in MIGRATION
    assert "CREATE VIEW bidding.video_canon_bound_candidate" in MIGRATION
    assert "check_row.value->>'execution_principal'=session_user::text" in MIGRATION
    assert "REVOKE SELECT ON public.analysis_candidate,bidding.video_canon_ai_verification_bundle" in MIGRATION
    assert "bridge_school_canon_restorer" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION bidding.restore_ai_verified_video_canon" in MIGRATION
    assert "TO bridge_school_canon_restorer" in MIGRATION


def test_gate_requires_source_binding_all_ai_checks_independence_and_rule_tests():
    for marker in (
        "source_sha256=v_candidate.payload#>>'{source,source_sha256}'",
        "video_file_id=v_candidate.payload#>>'{source,video_file_id}'",
        "SEMANTIC_PARSE",
        "BRIDGE_LOGIC",
        "HIDDEN_INFORMATION_FIREWALL",
        "v_semantic_family=v_bridge_family",
        "('positive'),('negative'),('boundary'),('interference'),('hidden_information'),('regression')",
        "c.status='open'",
        "ROLLBACK_RESTORE",
        "public.source s ON s.source_id=p.source_id AND s.school_id=p.school_id",
        "VIDEO_CANON_SOURCE_POLICY_SCHOOL_MISMATCH",
        "guard_video_canon_source_policy_lifecycle",
        "guard_promoted_video_canon_source_binding",
        "promoted_video_canon_source_binding_guard",
        "VIDEO_CANON_PROMOTED_SOURCE_BINDING_IMMUTABLE",
        "guard_promoted_video_canon_source_identity",
        "promoted_video_canon_source_identity_guard",
        "VIDEO_CANON_PROMOTED_SOURCE_IDENTITY_IMMUTABLE",
        "guard_promoted_video_canon_knowledge_version",
        "promoted_video_canon_knowledge_version_guard",
        "VIDEO_CANON_PROMOTED_KNOWLEDGE_VERSION_IMMUTABLE",
        "guard_promoted_video_canon_knowledge_item",
        "promoted_video_canon_knowledge_item_guard",
        "VIDEO_CANON_PROMOTED_KNOWLEDGE_ITEM_IMMUTABLE",
        "guard_promoted_video_canon_rule",
        "promoted_video_canon_rule_guard",
        "VIDEO_CANON_PROMOTED_RULE_IMMUTABLE",
        "guard_promoted_video_canon_rule_test",
        "promoted_video_canon_rule_test_guard",
        "VIDEO_CANON_PROMOTED_RULE_TEST_IMMUTABLE",
        "guard_superseded_video_canon_rule_test_run",
        "superseded_video_canon_rule_test_run_guard",
        "VIDEO_CANON_SUPERSEDED_RULE_TEST_RUN_IMMUTABLE",
        "p.superseded_canon_activation_id=ca.canon_activation_id",
        "JOIN public.canon_activation ca",
        "ca.canon_activation_id=p.canon_activation_id",
        "status='superseded',valid_to=v_valid_from",
        "p.valid_from<=clock_timestamp()",
        "v_valid_from>statement_timestamp()",
        "p.system_profile=v_bundle.bundle_payload->>'system_profile'",
        "p.learner_level=v_bundle.bundle_payload->>'learner_level'",
        "VIDEO_CANON_VERIFIER_PRINCIPAL_MISMATCH",
        "database_role=current_user",
        "JOIN bidding.video_canon_verifier_registry vr",
        "vr.status='active'",
        "v.check_id=ANY(vr.allowed_check_ids)",
        "NEW.execution_principal<>session_user",
        "v_semantic_principal=v_bridge_principal",
        "current_school_canon_snapshot_sha256",
        "VIDEO_CANON_STATE_CHECKS_STALE",
        "v.canon_snapshot_sha256=v_canon_snapshot_sha256",
        "contains_forbidden_hidden_value(v_candidate.payload)",
        "'active_rule_tests',active_rule_tests.rows",
        "'active_rule_sources',active_rule_sources.rows",
        "'active_canon_rules',active_canon_rules.rows",
        "SELECT 1 FROM public.canon_activation ca",
        "bidding.rule_conflict,bidding.video_canon_verifier_registry,",
        "LOCK TABLE public.analysis_candidate,public.canon_activation",
        "bidding.runtime_activation,bidding.rule,bidding.rule_test,bidding.rule_test_run,",
        "bidding.video_canon_ai_promotion_receipt IN SHARE ROW EXCLUSIVE MODE",
        "bidding.video_canon_ai_restore_receipt IN SHARE ROW EXCLUSIVE MODE",
        "ORDER BY runtime_activation_id",
        "Lock every restoration target before any final authority check",
        "Phase 1: validate every prelocked runtime target without mutation",
        "Recheck every finite authority boundary",
        "Phase 2: all targets are locked and validated",
        "Capability membership is external to these tables",
        "VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_EXPIRED",
        "v_new_canon.valid_to<=clock_timestamp()",
        "v_new_runtime.valid_to<=clock_timestamp()",
        "UPDATE bidding.runtime_activation target",
        "v_retired_at := clock_timestamp()",
        "NEW.valid_to := v_retired_at",
        "VIDEO_CANON_PROMOTED_VERIFICATION_SET_CLOSED",
        "jsonb_array_length(NEW.bundle_payload->'checks')<>16",
        "VIDEO_CANON_RESTORE_VALIDATION_GATES_FAILED",
        "VIDEO_CANON_RESTORE_SOURCE_POLICY_INACTIVE",
        "VIDEO_CANON_RESTORE_CANON_VERSION_GATES_FAILED",
        "VIDEO_CANON_RESTORE_TARGET_BINDING_MISMATCH",
        "VIDEO_CANON_RESTORE_BUNDLE_NOT_FOUND",
        "VIDEO_CANON_RESTORE_RULE_CONTENT_MISMATCH",
        "superseded_rule_state",
        "superseded_rule_test_state",
        "v_prior_rule_test_state",
        "v_current_prior_rule_test_state",
        "VIDEO_CANON_RESTORE_PREDECESSOR_TEST_STATE_MISMATCH",
        "superseded_source_state",
        "superseded_knowledge_version_content_sha256",
        "superseded_knowledge_item_content_sha256",
        "v_prior_version_content_sha256",
        "v_prior_item_content_sha256",
        "to_jsonb(v_prior_version)-ARRAY['created_at']",
        "VIDEO_CANON_RESTORE_PREDECESSOR_VERSION_MISMATCH",
        "VIDEO_CANON_RESTORE_PREDECESSOR_ITEM_MISMATCH",
        "v_prior_source_state",
        "v_current_prior_source_state",
        "jsonb_agg(to_jsonb(kvs)",
        "ca.canon_activation_id=p.superseded_canon_activation_id",
        "VIDEO_CANON_RESTORE_SOURCE_SET_MISMATCH",
        "video_canon_rule_restore_sha256",
        "to_jsonb(r)-ARRAY['created_at','updated_at']",
        "v_valid_to<=statement_timestamp()",
        "SET TimeZone='UTC'",
        "WITH effective_canon AS",
        "LANGUAGE sql\nVOLATILE\nSECURITY DEFINER",
        "ca.valid_from<=clock_timestamp()",
        "v_valid_to<=clock_timestamp()",
        "v_new_canon.valid_to<=statement_timestamp()",
        "is_complete_bridge_hand",
        "matched.parts[4]",
        "recorded_by_principal text NOT NULL DEFAULT session_user",
        "NEW.recorded_by_principal<>session_user",
        "pg_has_role(login_role.oid,v_principal.database_role,'MEMBER')",
        "VIDEO_CANON_VERIFIER_PRINCIPAL_REVOKED",
        "attestor.rolname=v.execution_principal",
        "VIDEO_CANON_KNOWLEDGE_VERSION_BINDING_INVALID",
        "v_version.content<>v_candidate.payload",
        "v_version.provenance<>v_expected_version_provenance",
        "knowledge_version_content_sha256",
        "rule_test_state_sha256",
        "video_canon_rule_test_state_sha256",
        "VIDEO_CANON_RULE_TEST_BINDING_INVALID",
        "VIDEO_CANON_RULE_TEST_STATE_MISMATCH",
        "VIDEO_CANON_RESTORE_RULE_TEST_STATE_MISMATCH",
        "'video-canon:'||v_candidate.payload_hash||':'",
        "expected.case_payload-'expect'",
        "VIDEO_CANON_RESTORE_SOURCE_BINDING_MISMATCH",
        "kvs.source_locator=jsonb_build_object(",
        "ki.stable_key='video-canon:'||v_candidate.payload_hash",
        "VIDEO_CANON_SOURCE_POLICY_EXPIRED",
        "VIDEO_CANON_RESTORE_ATTESTOR_REVOKED",
        "VIDEO_CANON_RESTORE_VERSION_CONTENT_MISMATCH",
        "v_prior_version_content_sha256",
        "v_revoked_at := clock_timestamp()",
        "SET status='revoked',valid_to=v_revoked_at",
        "replace(upper(COALESCE(p_hand,'')),'10','T')",
        "(?:10)",
        "|(?:^|[^[:alnum:]])[NESW][[:space:]]*:)[^;]*?S",
        "{1,13})[[:space:]/,]+",
        "'♠','S:'",
        "'♥','H:'",
        "'♦','D:'",
        "'♣','C:'",
        "([^;]*)",
        "matched.parts[1] ~*",
        "[SHDC][[:space:]]*:",
        "{1,3}($|[^[:alnum:]])",
        "{2,13}($|[^[:alnum:]])",
        "(?:10|[AKQJT]|",
        "(?:^|[^[:alnum:]_])(?:partner|opponent|north|east|south|west)",
        "(?:^|[^[:alnum:]_])(?:рука|карты)",
        "?[2-9]($|[[:space:],./;])",
        "hearts?|spades?|diamonds?|clubs?",
        "trumps?|losers?|points?|hcp|controls?",
        "карт[[:alnum:]_]*|черв[[:alnum:]_]*|пик[[:alnum:]_]*",
        "(?:held|holds?|has|had)",
        "(?:is|was)[[:space:]]+(?:(?:currently|still|now|already",
        "[''’]s[[:space:]]+(?:(?:currently|still|now|already",
        "(?:[-–—]|to)[[:space:]]*[0-9]{1,2}|[+]",
    ):
        assert marker in MIGRATION


def test_promotion_is_content_bound_idempotent_and_has_fail_closed_rollback():
    assert "video_candidate_payload_hash" in MIGRATION
    assert "candidate_payload_hash=v_candidate.payload_hash" in MIGRATION
    assert "verification_bundle_sha256" in MIGRATION
    assert "bundle_canonical_json" in MIGRATION
    assert "NEW.bundle_payload->'candidate_payload'<>v_candidate.payload" in MIGRATION
    assert "VIDEO_CANON_BOUND_CANDIDATE_MUTATION_FORBIDDEN" in MIGRATION
    assert "v.verification_bundle_sha256=p_verification_bundle_sha256" in MIGRATION
    assert "VIDEO_CANON_IDEMPOTENCY_MISMATCH" in MIGRATION
    assert "v_existing.scope_key<>v_scope_key" in MIGRATION
    assert "v_existing.rule_id<>p_rule_id" in MIGRATION
    assert "WHERE rule_id=p_rule_id FOR UPDATE" in MIGRATION
    assert "v_rule_content_sha256<>v_expected_rule_content_sha256" in MIGRATION
    assert "superseded_canon_activation_id" in MIGRATION
    assert "WHERE analysis_candidate_id=p_analysis_candidate_id FOR UPDATE" in MIGRATION
    assert "RETURN v_existing.video_canon_ai_promotion_receipt_id" in MIGRATION
    assert "rollback refused: Video-to-Canon state exists" in ROLLBACK
    assert "EXISTS (SELECT 1 FROM bidding.video_correction_review_receipt)" in ROLLBACK
    assert "CREATE TABLE bidding.video_canon_ai_restore_receipt" in MIGRATION
    assert "superseded_runtime_state" in MIGRATION
    assert "VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_MISMATCH" in MIGRATION
    assert "DROP FUNCTION bidding.restore_ai_verified_video_canon" in ROLLBACK
    assert "DROP VIEW bidding.video_canon_bound_candidate" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_source_binding_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_source_identity_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_knowledge_version_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_knowledge_item_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_rule_guard" in ROLLBACK
    assert "DROP TRIGGER promoted_video_canon_rule_test_guard" in ROLLBACK
    assert "DROP TRIGGER superseded_video_canon_rule_test_run_guard" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_source_binding" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_source_identity" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_version" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_item" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_rule" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_promoted_video_canon_rule_test" in ROLLBACK
    assert "DROP FUNCTION bidding.guard_superseded_video_canon_rule_test_run" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_rule_test_state_sha256" in ROLLBACK
    assert "DROP FUNCTION bidding.video_canon_rule_restore_sha256" in ROLLBACK
    assert "DROP FUNCTION bidding.is_complete_bridge_hand" in ROLLBACK
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public,bidding" in ROLLBACK
    assert "DROP ROLE bridge_school_canon_verifier" in ROLLBACK
    assert MIGRATION.count(
        "IF v_valid_to IS NOT NULL AND v_valid_to<=clock_timestamp() THEN"
    ) >= 3
    assert MIGRATION.count(
        "v_promotion.superseded_canon_valid_to<=clock_timestamp()"
    ) >= 2
    assert MIGRATION.count(
        "VIDEO_CANON_RESTORE_CURRENT_ACTIVATION_EXPIRED"
    ) >= 2
