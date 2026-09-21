\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0359_autopilot_historical_repair_reopen'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_E2E_ACL_REQUIRES_0359';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0360_autopilot_health_e2e_acl'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_E2E_ACL_ALREADY_APPLIED';
 END IF;
 IF to_regprocedure('autopilot.mailbox_e2e_acceptance()') IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_E2E_ACL_FUNCTION_MISSING';
 END IF;
 IF NOT pg_has_role(
   'bridge_school_health_principal','bridge_school_health','MEMBER'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_E2E_ACL_ROLE_MEMBERSHIP_MISSING';
 END IF;
 IF has_function_privilege(
      'bridge_school_health',
      'autopilot.mailbox_e2e_acceptance()','EXECUTE'
    )
    OR has_function_privilege(
      'bridge_school_health_principal',
      'autopilot.mailbox_e2e_acceptance()','EXECUTE'
    ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_E2E_ACL_ALREADY_PRESENT';
 END IF;
 IF has_function_privilege(
      'public','autopilot.mailbox_e2e_acceptance()','EXECUTE'
    ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_E2E_ACL_PUBLIC_EXECUTE_PRESENT';
 END IF;
END $pre$;

REVOKE ALL ON FUNCTION autopilot.mailbox_e2e_acceptance() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mailbox_e2e_acceptance()
 TO bridge_school_health;

INSERT INTO public.schema_migration(migration_key)
VALUES('0360_autopilot_health_e2e_acl');

COMMIT;
