\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM bidding.video_correction_review_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_restore_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_promotion_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification_bundle)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_source_policy) THEN
    RAISE EXCEPTION '0322 rollback refused: Video-to-Canon state exists';
  END IF;
END $$;
DROP FUNCTION bidding.restore_ai_verified_video_canon(uuid,text,text);
DROP FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text);
DROP VIEW bidding.video_canon_bound_candidate;
DROP TRIGGER video_canon_restore_receipt_append_only ON bidding.video_canon_ai_restore_receipt;
DROP TRIGGER video_correction_review_receipt_append_only ON bidding.video_correction_review_receipt;
DROP TRIGGER video_correction_review_receipt_guard ON bidding.video_correction_review_receipt;
DROP TRIGGER promoted_video_canon_source_identity_guard ON public.source;
DROP TRIGGER promoted_video_canon_knowledge_version_guard ON public.knowledge_version;
DROP TRIGGER promoted_video_canon_knowledge_item_guard ON public.knowledge_item;
DROP TRIGGER promoted_video_canon_rule_test_guard ON bidding.rule_test;
DROP TRIGGER promoted_video_canon_rule_guard ON bidding.rule;
DROP TRIGGER promoted_video_canon_source_binding_guard ON public.knowledge_version_source;
DROP TRIGGER bound_video_canon_candidate_guard ON public.analysis_candidate;
DROP TRIGGER video_canon_verification_guard ON bidding.video_canon_ai_verification;
DROP TRIGGER video_canon_verification_bundle_guard ON bidding.video_canon_ai_verification_bundle;
DROP TRIGGER video_canon_source_policy_lifecycle_guard ON bidding.video_canon_source_policy;
DROP TRIGGER video_canon_verifier_registry_lifecycle_guard ON bidding.video_canon_verifier_registry;
DROP FUNCTION bidding.guard_video_canon_source_policy_lifecycle();
DROP FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle();
DROP FUNCTION bidding.guard_promoted_video_canon_rule_test();
DROP FUNCTION bidding.guard_promoted_video_canon_rule();
DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_item();
DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_version();
DROP FUNCTION bidding.guard_promoted_video_canon_source_identity();
DROP FUNCTION bidding.guard_promoted_video_canon_source_binding();
DROP FUNCTION bidding.guard_bound_video_canon_candidate();
DROP FUNCTION bidding.validate_video_canon_verification();
DROP FUNCTION bidding.validate_video_canon_verification_bundle();
DROP FUNCTION bidding.validate_video_correction_review_receipt();
DROP FUNCTION bidding.current_school_canon_snapshot_sha256(uuid);
DROP FUNCTION bidding.video_canon_rule_test_state_sha256(uuid);
DROP FUNCTION bidding.video_canon_rule_restore_sha256(uuid);
DROP FUNCTION bidding.contains_forbidden_hidden_value(jsonb);
DROP FUNCTION bidding.is_complete_bridge_hand(text);
DROP TABLE bidding.video_correction_review_receipt;
DROP TABLE bidding.video_canon_ai_restore_receipt;
DROP TABLE bidding.video_canon_ai_promotion_receipt;
DROP TABLE bidding.video_canon_ai_verification;
DROP TABLE bidding.video_canon_ai_verification_bundle;
DROP TABLE bidding.video_canon_verifier_registry;
DROP TABLE bidding.video_canon_source_policy;
REVOKE ALL PRIVILEGES ON SCHEMA public,bidding FROM
  bridge_school_canon_verifier,bridge_school_canon_semantic_verifier,
  bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,
  bridge_school_canon_control_verifier,bridge_school_canon_promoter,
  bridge_school_canon_restorer;
DROP ROLE bridge_school_canon_verifier,bridge_school_canon_semantic_verifier,
  bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,
  bridge_school_canon_control_verifier,bridge_school_canon_promoter,
  bridge_school_canon_restorer;
DELETE FROM public.schema_migration WHERE migration_key='0322_workflow_video_canon_ai_promotion';
COMMIT;
