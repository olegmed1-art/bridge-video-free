\set ON_ERROR_STOP on
BEGIN;

REVOKE ALL ON FUNCTION bidding.enqueue_video_canon_promotion(uuid,uuid,text,text),
  bidding.claim_video_canon_promotion(integer),
  bidding.consume_video_canon_promotion(uuid,uuid,bigint),
  bidding.fail_video_canon_promotion(uuid,uuid,bigint,text)
  FROM bridge_school_canon_consumer,bridge_school_canon_verifier,PUBLIC;
REVOKE ALL ON TABLE bidding.video_canon_assurance_verdict FROM
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier;
REVOKE ALL ON TABLE bidding.video_canon_assurance_bound_bundle,
  bidding.video_canon_bound_candidate FROM
  bridge_school_canon_i2_verifier,bridge_school_canon_i3_verifier;

DROP FUNCTION IF EXISTS bidding.fail_video_canon_promotion(uuid,uuid,bigint,text);
DROP FUNCTION IF EXISTS bidding.consume_video_canon_promotion(uuid,uuid,bigint);
DROP FUNCTION IF EXISTS bidding.claim_video_canon_promotion(integer);
DROP FUNCTION IF EXISTS bidding.enqueue_video_canon_promotion(uuid,uuid,text,text);
DROP FUNCTION IF EXISTS bidding.reassign_video_canon_assurance(uuid,name,text);
DROP FUNCTION IF EXISTS bidding.video_canon_assurance_set_sha256(uuid,text,text);
DROP TRIGGER IF EXISTS video_canon_promotion_delivery_receipt_append_only
  ON bidding.video_canon_promotion_delivery_receipt;
DROP TABLE IF EXISTS bidding.video_canon_promotion_delivery_receipt;
DROP TABLE IF EXISTS bidding.video_canon_promotion_job;
DROP TRIGGER IF EXISTS video_canon_assurance_verdict_append_only
  ON bidding.video_canon_assurance_verdict;
DROP TRIGGER IF EXISTS video_canon_assurance_verdict_guard
  ON bidding.video_canon_assurance_verdict;
DROP FUNCTION IF EXISTS bidding.validate_video_canon_assurance_verdict();
DROP TABLE IF EXISTS bidding.video_canon_assurance_verdict;
DROP VIEW IF EXISTS bidding.video_canon_assurance_bound_bundle;
DROP TRIGGER IF EXISTS video_canon_assurance_assignment_guard
  ON bidding.video_canon_assurance_assignment;
DROP TRIGGER IF EXISTS video_canon_assurance_assignment_insert_guard
  ON bidding.video_canon_assurance_assignment;
DROP FUNCTION IF EXISTS bidding.guard_video_canon_assurance_assignment();
DROP TABLE IF EXISTS bidding.video_canon_assurance_assignment;
DROP TRIGGER IF EXISTS video_canon_assurance_verifier_registry_append_only
  ON bidding.video_canon_assurance_verifier_registry;
DROP TABLE IF EXISTS bidding.video_canon_assurance_verifier_registry;

GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon(uuid,uuid,text)
  TO bridge_school_canon_promoter;
DELETE FROM public.schema_migration
WHERE migration_key='0323_workflow_video_canon_promotion_consumer';

REVOKE USAGE,CREATE ON SCHEMA public,bidding FROM
  bridge_school_canon_consumer,bridge_school_canon_i3_verifier,
  bridge_school_canon_i2_verifier;

DROP ROLE IF EXISTS bridge_school_canon_consumer;
DROP ROLE IF EXISTS bridge_school_canon_i3_verifier;
DROP ROLE IF EXISTS bridge_school_canon_i2_verifier;

COMMIT;
