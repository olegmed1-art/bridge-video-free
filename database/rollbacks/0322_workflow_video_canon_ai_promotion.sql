\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM bidding.video_correction_review_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_promotion_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification_bundle)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_source_policy) THEN
    RAISE EXCEPTION '0322 rollback refused: Video-to-Canon state exists';
  END IF;
END $$;
DROP FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text);
DROP TRIGGER video_correction_review_receipt_append_only ON bidding.video_correction_review_receipt;
DROP TRIGGER video_correction_review_receipt_guard ON bidding.video_correction_review_receipt;
DROP TRIGGER bound_video_canon_candidate_guard ON public.analysis_candidate;
DROP TRIGGER video_canon_verification_guard ON bidding.video_canon_ai_verification;
DROP TRIGGER video_canon_verification_bundle_guard ON bidding.video_canon_ai_verification_bundle;
DROP TRIGGER video_canon_source_policy_lifecycle_guard ON bidding.video_canon_source_policy;
DROP TRIGGER video_canon_verifier_registry_lifecycle_guard ON bidding.video_canon_verifier_registry;
DROP FUNCTION bidding.guard_video_canon_source_policy_lifecycle();
DROP FUNCTION bidding.guard_video_canon_verifier_registry_lifecycle();
DROP FUNCTION bidding.guard_bound_video_canon_candidate();
DROP FUNCTION bidding.validate_video_canon_verification();
DROP FUNCTION bidding.validate_video_canon_verification_bundle();
DROP FUNCTION bidding.validate_video_correction_review_receipt();
DROP TABLE bidding.video_correction_review_receipt;
DROP TABLE bidding.video_canon_ai_promotion_receipt;
DROP TABLE bidding.video_canon_ai_verification;
DROP TABLE bidding.video_canon_ai_verification_bundle;
DROP TABLE bidding.video_canon_verifier_registry;
DROP TABLE bidding.video_canon_source_policy;
DELETE FROM public.schema_migration WHERE migration_key='0322_workflow_video_canon_ai_promotion';
COMMIT;
