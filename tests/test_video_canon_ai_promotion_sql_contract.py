from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = (ROOT / "database/migrations/0322_workflow_video_canon_ai_promotion.sql").read_text()
ROLLBACK = (ROOT / "database/rollbacks/0322_workflow_video_canon_ai_promotion.sql").read_text()


def test_ai_promotion_is_narrow_guarded_and_not_granted_to_general_workers():
    assert "CREATE ROLE bridge_school_canon_verifier NOLOGIN" in MIGRATION
    assert "CREATE ROLE bridge_school_canon_promoter NOLOGIN" in MIGRATION
    assert "SECURITY DEFINER" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in MIGRATION
    assert "TO bridge_school_canon_promoter" in MIGRATION
    assert "GRANT INSERT ON bidding.video_canon_ai_verification_bundle,bidding.video_canon_ai_verification" in MIGRATION
    assert "bridge_school_app,bridge_school_worker,bridge_school_canon_verifier,bridge_school_canon_promoter" in MIGRATION
    assert "promotion_mode','AI_VERIFIED_TEACHER_VIDEO'" in MIGRATION
    assert "'human_approval_required',false" in MIGRATION


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
        "public.source s ON s.source_id=p.source_id AND s.status='active'",
        "guard_video_canon_source_policy_lifecycle",
        "status='superseded',valid_to=v_valid_from",
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
