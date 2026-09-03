\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE r record; v_failed boolean;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.schema_migration WHERE migration_key='0322_workflow_video_canon_ai_promotion') THEN
    RAISE EXCEPTION 'VIDEO_CANON_MIGRATION_MISSING';
  END IF;
  FOR r IN SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication
             FROM pg_roles WHERE rolname IN ('bridge_school_canon_verifier','bridge_school_canon_promoter') LOOP
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
      RAISE EXCEPTION 'VIDEO_CANON_UNSAFE_ROLE_%',r.rolname;
    END IF;
  END LOOP;
  IF has_function_privilege('bridge_school_worker',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR has_function_privilege('bridge_school_canon_verifier',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE')
     OR NOT has_function_privilege('bridge_school_canon_promoter',
       'bidding.activate_ai_verified_video_canon(uuid,uuid,text)','EXECUTE') THEN
    RAISE EXCEPTION 'VIDEO_CANON_PROMOTION_RPC_ACL_INVALID';
  END IF;
  IF has_table_privilege('bridge_school_worker','bidding.video_canon_ai_verification','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.video_canon_ai_verification','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_verifier','bidding.video_canon_ai_verification','INSERT')
     OR has_table_privilege('bridge_school_worker','bidding.video_canon_ai_verification_bundle','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.video_canon_ai_verification_bundle','INSERT')
     OR NOT has_table_privilege('bridge_school_canon_verifier','bidding.video_canon_ai_verification_bundle','INSERT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_ACL_INVALID';
  END IF;
  IF has_table_privilege('bridge_school_canon_promoter','public.canon_activation','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.runtime_activation','INSERT')
     OR has_table_privilege('bridge_school_canon_promoter','bidding.video_canon_ai_promotion_receipt','INSERT') THEN
    RAISE EXCEPTION 'VIDEO_CANON_DIRECT_WRITE_NOT_BLOCKED';
  END IF;

  v_failed:=false;
  BEGIN
    PERFORM bidding.activate_ai_verified_video_canon(
      uuidv7(),uuidv7(),repeat('a',64)
    );
  EXCEPTION WHEN check_violation THEN v_failed:=true;
  END;
  IF NOT v_failed THEN RAISE EXCEPTION 'VIDEO_CANON_MISSING_CANDIDATE_NOT_BLOCKED'; END IF;
END $$;

ROLLBACK;
