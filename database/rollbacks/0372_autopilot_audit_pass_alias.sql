\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
DO $rollback$
DECLARE saved record;
BEGIN
 SELECT * INTO STRICT saved FROM autopilot.migration_0372_function_backup
 WHERE function_key='autopilot.on_project_work_task_terminal()';
 IF pg_get_functiondef('autopilot.on_project_work_task_terminal()'::regprocedure)
       IS DISTINCT FROM saved.patched_definition THEN
   RAISE EXCEPTION 'AUDIT_PASS_ALIAS_ROLLBACK_SOURCE_DRIFT';
 END IF;
 EXECUTE saved.definition;
END $rollback$;
DROP TABLE autopilot.migration_0372_function_backup;
DELETE FROM public.schema_migration WHERE migration_key='0372_autopilot_audit_pass_alias';
COMMIT;
