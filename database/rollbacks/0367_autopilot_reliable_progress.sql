\set ON_ERROR_STOP on
BEGIN;
DO $rollback$
DECLARE source text;
BEGIN
 FOR source IN SELECT definition FROM autopilot.migration_0367_function_backup
 LOOP EXECUTE source; END LOOP;
END $rollback$;
DROP FUNCTION autopilot.reconcile_paused_project_work_cas(uuid,timestamptz,text,text,text);
DROP FUNCTION autopilot.parallel_work_manifest_status(text);
DROP TABLE autopilot.migration_0367_function_backup;
DELETE FROM public.schema_migration WHERE migration_key='0367_autopilot_reliable_progress';
-- Work, dispatches and receipts are durable history, never rollback targets.
COMMIT;
