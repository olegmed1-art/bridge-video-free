\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM bidding.video_correction_review_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_restore_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_promotion_receipt)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_ai_verification_bundle)
     OR EXISTS (SELECT 1 FROM bidding.video_canon_source_policy)
     OR EXISTS (
       SELECT 1 FROM bidding.rule GROUP BY school_id,rule_key HAVING count(*)>1
     ) THEN
    RAISE EXCEPTION '0322 rollback refused: Video-to-Canon state exists';
  END IF;
END $$;
DROP FUNCTION bidding.restore_ai_verified_video_canon(uuid,text,text);
DROP FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text);
DROP FUNCTION bidding.video_canon_drive_source_id(text);
DROP FUNCTION bidding.get_school_runtime_rule_catalog(uuid,text,text,text);
DROP VIEW bidding.video_canon_bound_candidate;
DROP TRIGGER video_canon_restore_receipt_append_only ON bidding.video_canon_ai_restore_receipt;
DROP TRIGGER video_correction_review_receipt_append_only ON bidding.video_correction_review_receipt;
DROP TRIGGER video_correction_review_receipt_guard ON bidding.video_correction_review_receipt;
DROP TRIGGER promoted_video_canon_source_identity_guard ON public.source;
DROP TRIGGER promoted_video_canon_provider_identity_guard ON public.source_identity;
DROP TRIGGER promoted_video_canon_knowledge_version_guard ON public.knowledge_version;
DROP TRIGGER promoted_video_canon_knowledge_item_guard ON public.knowledge_item;
DROP TRIGGER superseded_video_canon_rule_test_run_guard ON bidding.rule_test_run;
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
DROP FUNCTION bidding.guard_superseded_video_canon_rule_test_run();
DROP FUNCTION bidding.guard_promoted_video_canon_rule_test();
DROP FUNCTION bidding.guard_promoted_video_canon_rule();
DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_item();
DROP FUNCTION bidding.guard_promoted_video_canon_knowledge_version();
DROP FUNCTION bidding.guard_promoted_video_canon_source_identity();
DROP FUNCTION bidding.guard_promoted_video_canon_provider_identity();
DROP FUNCTION bidding.guard_promoted_video_canon_source_binding();
DROP FUNCTION bidding.guard_bound_video_canon_candidate();
DROP FUNCTION bidding.validate_video_canon_verification();
DROP FUNCTION bidding.validate_video_canon_verification_bundle();
DROP FUNCTION bidding.validate_video_correction_review_receipt();
DROP FUNCTION bidding.current_school_canon_snapshot_sha256(uuid);
DROP FUNCTION bidding.video_canon_rule_test_state_sha256(uuid);
DROP FUNCTION bidding.video_canon_rule_restore_sha256(uuid);
DROP FUNCTION bidding.video_canon_semantic_identity_sha256(jsonb);
DROP FUNCTION bidding.video_canon_runtime_scope_key(text,text,text);
DROP FUNCTION bidding.contains_forbidden_hidden_value(jsonb);
DROP FUNCTION bidding.is_video_canon_semantic_confidence_eligible(jsonb);
DROP FUNCTION bidding.is_complete_bridge_hand(text);
-- Restore the pre-0322 definition retained in immutable migration 0200.
CREATE OR REPLACE FUNCTION bidding.contains_forbidden_hidden_key(payload jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
WITH RECURSIVE walk(value) AS (
    SELECT COALESCE(payload, 'null'::jsonb)
    UNION ALL
    SELECT child.value
      FROM walk AS w
      CROSS JOIN LATERAL (
          SELECT e.value
            FROM jsonb_each(
                CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END
            ) AS e
          UNION ALL
          SELECT a.value
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(w.value)='array' THEN w.value ELSE '[]'::jsonb END
            ) AS a
      ) AS child
), forbidden AS (
    SELECT 1
      FROM walk AS w
      CROSS JOIN LATERAL jsonb_object_keys(
          CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END
      ) AS k(key)
     WHERE lower(k.key) = ANY (ARRAY[
        'partner_hand','opponent_hand','opponent_hands',
        'north_hand','east_hand','south_hand','west_hand',
        'full_deal','hidden_cards','actual_partner_hand',
        'actual_opponent_hand','actual_opponent_hands',
        'partner_cards','opponent_cards','all_hands'
     ])
     LIMIT 1
)
SELECT EXISTS (SELECT 1 FROM forbidden);
$$;
DROP TABLE bidding.video_correction_review_receipt;
DROP TABLE bidding.video_canon_ai_restore_receipt;
DROP TABLE bidding.video_canon_ai_promotion_receipt;
DROP TABLE bidding.video_canon_ai_verification;
DROP TABLE bidding.video_canon_ai_verification_bundle;
DROP TABLE bidding.video_canon_verifier_registry;
DROP TABLE bidding.video_canon_source_policy;
DROP INDEX bidding.bidding_rule_version_identity_idx;
DROP TRIGGER knowledge_version_rule_identity_scope_guard ON public.knowledge_version;
DROP FUNCTION bidding.guard_rule_identity_knowledge_version_scope();
DROP TRIGGER bidding_rule_key_identity_guard ON bidding.rule;
DROP FUNCTION bidding.bind_rule_key_identity();
DROP TABLE bidding.rule_key_identity;
ALTER TABLE bidding.rule ADD CONSTRAINT rule_school_id_rule_key_key
  UNIQUE (school_id,rule_key);
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
