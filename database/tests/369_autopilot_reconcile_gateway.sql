\set ON_ERROR_STOP on
BEGIN;
-- Test-only, transaction-local impersonation. The final ROLLBACK restores the
-- migration owner's original SET FALSE membership; never persist this grant.
DO $test_role$
BEGIN
 EXECUTE format('GRANT bridge_school_worker TO %I WITH SET TRUE',current_user);
END $test_role$;
DO $test$
BEGIN
 IF has_schema_privilege('bridge_school_worker','autopilot','USAGE')
 OR has_schema_privilege('bridge_school_worker','autopilot_reconcile','CREATE')
 OR has_table_privilege('bridge_school_worker','autopilot.project_work_item','UPDATE') THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_EXCESS_PRIVILEGE';
 END IF;
 IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='autopilot_reconcile')<>4 THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_EXACT_RPC_SET_REQUIRED';
 END IF;
 -- Schema access plus PostgreSQL's global default PUBLIC EXECUTE could expose
 -- future owner-created functions. Every future gateway RPC must revoke PUBLIC;
 -- never add a function without updating/reviewing this exact surface contract.
 IF EXISTS(SELECT FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
     WHERE n.nspname='autopilot_reconcile' AND a.grantee=0)
 OR EXISTS(SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
     WHERE n.nspname='autopilot_reconcile' AND a.grantee=0) THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_PUBLIC_ACCESS_FORBIDDEN';
 END IF;
 IF EXISTS(SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='autopilot_reconcile'
       AND (NOT p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog']::text[])) THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_SECURITY_CONFIGURATION_INVALID';
 END IF;
END $test$;

SET LOCAL ROLE bridge_school_worker;
SELECT count(*) FROM autopilot_reconcile.paused_candidates(50);
SELECT count(*) FROM autopilot_reconcile.progress_candidates(50);
DO $test$
BEGIN
 IF autopilot_reconcile.apply_paused('00000000-0000-0000-0000-000000000000',now(),repeat('a',64),NULL,NULL)<>'NO_CHANGE'
 OR autopilot_reconcile.apply_progress('00000000-0000-0000-0000-000000000000',now(),repeat('a',40),NULL)<>'NO_CHANGE' THEN
   RAISE EXCEPTION 'RECONCILE_GATEWAY_UNKNOWN_WORK_NOT_FENCED';
 END IF;
 BEGIN
   PERFORM autopilot.reconcile_paused_project_work('00000000-0000-0000-0000-000000000000',repeat('a',64),NULL,NULL,NULL);
   RAISE EXCEPTION 'RECONCILE_GATEWAY_LEGACY_ACCESS_NOT_FENCED';
 EXCEPTION WHEN insufficient_privilege THEN NULL;
 END;
END $test$;
RESET ROLE;
ROLLBACK;
