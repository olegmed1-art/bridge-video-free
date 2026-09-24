\set ON_ERROR_STOP on
BEGIN;

DO $test$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0360_autopilot_health_e2e_acl'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0360_MIGRATION_MISSING';
 END IF;
 IF NOT has_function_privilege(
      'bridge_school_health',
      'autopilot.mailbox_e2e_acceptance()','EXECUTE'
    )
    OR NOT has_function_privilege(
      'bridge_school_health_principal',
      'autopilot.mailbox_e2e_acceptance()','EXECUTE'
    )
    OR has_function_privilege(
      'public','autopilot.mailbox_e2e_acceptance()','EXECUTE'
    ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0360_HEALTH_E2E_ACL_INVALID';
 END IF;
 IF strpos(
      pg_get_viewdef('public.autopilot_operational_health_signal'::regclass,true),
      'mailbox_e2e_acceptance'
    )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0360_HEALTH_VIEW_DEPENDENCY_MISSING';
 END IF;
END $test$;

ROLLBACK;
