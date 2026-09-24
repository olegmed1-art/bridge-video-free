\set ON_ERROR_STOP on
-- Operator prerequisite: pause/quiesce the paused evidence reconciler first.
-- Restoring the legacy function can reintroduce unbudgeted retries.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
DO $rollback$
DECLARE saved record;
BEGIN
 IF (SELECT count(*) FROM autopilot.migration_0371_function_backup)<>3
   OR EXISTS(SELECT FROM (VALUES
    ('autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'),
    ('autopilot.project_progress_candidates(integer)'),
    ('autopilot.reconcile_project_progress(uuid,timestamp with time zone,text,timestamp with time zone)')
   ) expected(function_key) WHERE NOT EXISTS(SELECT FROM autopilot.migration_0371_function_backup b
      WHERE b.function_key=expected.function_key)) THEN
  RAISE EXCEPTION 'AUTOPILOT_PROVIDER_BUDGET_ROLLBACK_BACKUP_INCOMPLETE';
 END IF;
 FOR saved IN SELECT definition FROM autopilot.migration_0371_function_backup LOOP
  EXECUTE saved.definition;
 END LOOP;
END $rollback$;
DROP TABLE autopilot.migration_0371_function_backup;
-- Never erase/reset progress receipts or task/dispatch/send-intent history.
DELETE FROM public.schema_migration WHERE migration_key='0371_autopilot_provider_retry_budget';
COMMIT;
