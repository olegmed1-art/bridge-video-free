\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_followup IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0359_autopilot_historical_repair_reopen'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_ROLLBACK_MIGRATION_MISSING';
 END IF;
 IF (SELECT count(*) FROM autopilot.migration_0359_function_backup)<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_ROLLBACK_BACKUP_INCOMPLETE';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
 ) OR EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN (
       'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
     )
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_ROLLBACK_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

DO $restore$
DECLARE saved_definition text;
BEGIN
 SELECT definition INTO STRICT saved_definition
 FROM autopilot.migration_0359_function_backup
 WHERE function_key='autopilot.on_project_work_followup()';
 EXECUTE saved_definition;
END $restore$;

DELETE FROM public.schema_migration
WHERE migration_key='0359_autopilot_historical_repair_reopen';
DROP TABLE autopilot.migration_0359_function_backup;

COMMIT;
