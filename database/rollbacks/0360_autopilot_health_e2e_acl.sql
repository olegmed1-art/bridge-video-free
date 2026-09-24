\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0360_autopilot_health_e2e_acl'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0360_ROLLBACK_MIGRATION_MISSING';
 END IF;
 IF to_regprocedure('autopilot.mailbox_e2e_acceptance()') IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0360_ROLLBACK_FUNCTION_MISSING';
 END IF;
END $pre$;

REVOKE EXECUTE ON FUNCTION autopilot.mailbox_e2e_acceptance()
 FROM bridge_school_health;

DELETE FROM public.schema_migration
WHERE migration_key='0360_autopilot_health_e2e_acl';

COMMIT;
