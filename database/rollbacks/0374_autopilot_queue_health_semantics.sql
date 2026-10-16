\set ON_ERROR_STOP on
BEGIN;
DO $restore$
DECLARE original text;
BEGIN
 SELECT definition INTO STRICT original FROM autopilot.migration_0374_health_backup WHERE singleton;
 EXECUTE 'CREATE OR REPLACE VIEW public.autopilot_operational_health_signal AS '||original;
END $restore$;
DROP TABLE autopilot.migration_0374_health_backup;
DELETE FROM public.schema_migration WHERE migration_key='0374_autopilot_queue_health_semantics';
-- Restores the predecessor health behavior. Keep HOLD until forward recovery.
COMMIT;
