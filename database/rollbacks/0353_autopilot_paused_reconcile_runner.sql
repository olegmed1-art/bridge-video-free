\set ON_ERROR_STOP on
BEGIN;

DO $guard$
BEGIN
 IF EXISTS (
   SELECT 1
   FROM autopilot.paused_work_reconcile_receipt r
   JOIN public.schema_migration m
     ON m.migration_key='0353_autopilot_paused_reconcile_runner'
   WHERE r.created_at>=m.applied_at
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_ROLLBACK_HAS_RECONCILIATION_EVIDENCE';
 END IF;
END $guard$;

REVOKE EXECUTE ON FUNCTION autopilot.paused_reconcile_candidates(integer)
FROM bridge_school_worker;
REVOKE EXECUTE ON FUNCTION autopilot.reconcile_paused_project_work(uuid,text,text,text,text)
FROM bridge_school_worker;

DROP FUNCTION autopilot.paused_reconcile_candidates(integer);

DO $restore$
DECLARE d text;
BEGIN
 SELECT function_definition INTO d
 FROM autopilot.migration_0353_function_backup
 WHERE function_key='reconcile_paused_project_work';
 IF d IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_ROLLBACK_BACKUP_MISSING';
 END IF;
 EXECUTE d;
END $restore$;

DROP TABLE autopilot.migration_0353_function_backup;
DELETE FROM public.schema_migration
WHERE migration_key='0353_autopilot_paused_reconcile_runner';

COMMIT;
