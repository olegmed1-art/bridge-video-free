\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE r record;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.schema_migration
    WHERE migration_key='0323_workflow_video_canon_promotion_consumer'
  ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_CONSUMER_MIGRATION_MISSING';
  END IF;

  FOR r IN SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication
             FROM pg_roles WHERE rolname=ANY(ARRAY[
               'bridge_school_canon_i2_verifier','bridge_school_canon_i3_verifier',
               'bridge_school_canon_consumer'
             ]) LOOP
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
      RAISE EXCEPTION 'VIDEO_CANON_CONSUMER_UNSAFE_ROLE_%',r.rolname;
    END IF;
  END LOOP;

  IF (SELECT count(*) FROM pg_roles WHERE rolname=ANY(ARRAY[
       'bridge_school_canon_i2_verifier','bridge_school_canon_i3_verifier',
       'bridge_school_canon_consumer'
     ]))<>3 THEN
    RAISE EXCEPTION 'VIDEO_CANON_CONSUMER_ROLE_MISSING';
  END IF;

  IF has_function_privilege('bridge_school_canon_promoter',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR has_function_privilege('bridge_school_canon_consumer',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_consumer',
       'bidding.claim_video_canon_promotion(integer)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_consumer',
       'bidding.heartbeat_video_canon_promotion(uuid,uuid,bigint,integer)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_consumer',
       'bidding.consume_video_canon_promotion(uuid,uuid,bigint)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_consumer',
       'bidding.fail_video_canon_promotion(uuid,uuid,bigint,text)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_verifier',
       'bidding.enqueue_video_canon_promotion(uuid,uuid,text,text)','EXECUTE')
     OR has_function_privilege('bridge_school_canon_verifier',
       'bidding.consume_video_canon_promotion(uuid,uuid,bigint)','EXECUTE')
     OR has_function_privilege('bridge_school_canon_verifier',
       'bidding.heartbeat_video_canon_promotion(uuid,uuid,bigint,integer)','EXECUTE') THEN
    RAISE EXCEPTION 'VIDEO_CANON_CONSUMER_RPC_ACL_INVALID';
  END IF;

  IF NOT has_table_privilege('bridge_school_canon_i2_verifier',
       'bidding.video_canon_assurance_verdict','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_i3_verifier',
       'bidding.video_canon_assurance_verdict','INSERT')
     OR has_table_privilege('bridge_school_canon_i2_verifier',
       'bidding.video_canon_assurance_verdict','UPDATE')
     OR has_table_privilege('bridge_school_canon_i3_verifier',
       'bidding.video_canon_assurance_verdict','DELETE')
     OR NOT has_table_privilege('bridge_school_canon_i2_verifier',
       'bidding.video_canon_bound_candidate','SELECT')
     OR NOT has_table_privilege('bridge_school_canon_i3_verifier',
       'bidding.video_canon_assurance_bound_bundle','SELECT')
     OR has_table_privilege('bridge_school_canon_i2_verifier',
       'bidding.video_canon_ai_verification_bundle','SELECT')
     OR has_table_privilege('bridge_school_canon_i2_verifier',
       'public.analysis_candidate','SELECT')
     OR has_table_privilege('bridge_school_canon_i3_verifier',
       'public.person','SELECT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_ASSURANCE_ACL_INVALID';
  END IF;

  IF pg_has_role('bridge_school_canon_i2_verifier','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_i3_verifier','bridge_school_reader','member')
     OR pg_has_role('bridge_school_canon_consumer','bridge_school_reader','member')
     OR has_table_privilege('bridge_school_canon_consumer',
       'bidding.video_canon_promotion_job','UPDATE')
     OR has_table_privilege('bridge_school_canon_consumer',
       'bidding.video_canon_promotion_delivery_receipt','INSERT')
     OR has_table_privilege('bridge_school_canon_consumer',
       'public.canon_activation','INSERT')
     OR has_table_privilege('bridge_school_canon_consumer',
       'bidding.runtime_activation','INSERT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_CONSUMER_OVERBROAD_ACCESS';
  END IF;

  IF to_regclass('bidding.video_canon_promotion_job') IS NULL
     OR to_regclass('bidding.video_canon_promotion_delivery_receipt') IS NULL
     OR to_regclass('bidding.video_canon_assurance_verdict') IS NULL
     OR to_regclass('bidding.video_canon_assurance_assignment') IS NULL
     OR to_regclass('bidding.video_canon_assurance_bound_bundle') IS NULL
     OR to_regclass('bidding.video_canon_assurance_verifier_registry') IS NULL
     OR (SELECT count(*) FROM bidding.video_canon_assurance_verifier_registry)<>2 THEN
    RAISE EXCEPTION 'VIDEO_CANON_CONSUMER_SCHEMA_MISSING';
  END IF;

  IF to_regclass('bidding.video_canon_promotion_delivery_assurance_uq') IS NULL
     OR EXISTS (
       SELECT 1
         FROM pg_index i
         JOIN pg_class idx ON idx.oid=i.indexrelid
        WHERE i.indrelid='bidding.video_canon_promotion_delivery_receipt'::regclass
          AND i.indisunique
          AND idx.relname<>'video_canon_promotion_delivery_assurance_uq'
          AND pg_get_indexdef(i.indexrelid)~'\((analysis_candidate_id|promotion_receipt_id)\)'
     ) THEN
    RAISE EXCEPTION 'VIDEO_CANON_DELIVERY_ASSURANCE_IDENTITY_INVALID';
  END IF;
END $$;

ROLLBACK;
