\set ON_ERROR_STOP on
BEGIN;
DO $rollback$
DECLARE source text;
BEGIN
 IF EXISTS(SELECT FROM autopilot.project_work_item w
   JOIN autopilot.project_progress_receipt r USING(work_item_id)
   WHERE w.state IN ('READY','ACTIVE','BLOCKED','WAITING_DEPENDENCY')) THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_ROLLBACK_WORK_IN_PROGRESS';
 END IF;
 FOR source IN SELECT definition FROM autopilot.migration_0368_function_backup
 LOOP EXECUTE source; END LOOP;
END $rollback$;
DROP FUNCTION autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz);
DROP FUNCTION autopilot.project_progress_candidates(integer);
DROP TABLE autopilot.migration_0368_function_backup;
-- Preserve receipt budgets and all task/work history across rollback/reapply.
DELETE FROM public.schema_migration WHERE migration_key='0368_autopilot_next_step';
COMMIT;
