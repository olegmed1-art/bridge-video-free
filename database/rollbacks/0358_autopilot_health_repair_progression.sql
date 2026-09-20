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
   WHERE migration_key='0358_autopilot_health_repair_progression'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0358_ROLLBACK_MIGRATION_MISSING';
 END IF;
 IF (SELECT count(*) FROM autopilot.migration_0358_function_backup)<>3 THEN
   RAISE EXCEPTION 'AUTOPILOT_0358_ROLLBACK_BACKUP_INCOMPLETE';
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
   RAISE EXCEPTION 'AUTOPILOT_0358_ROLLBACK_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

DO $restore$
DECLARE saved record;
BEGIN
 FOR saved IN
   SELECT definition
   FROM autopilot.migration_0358_function_backup
   ORDER BY function_key
 LOOP
   EXECUTE saved.definition;
 END LOOP;
END $restore$;

REVOKE EXECUTE ON FUNCTION autopilot.mailbox_rotation_readiness()
 FROM bridge_school_health;

DELETE FROM public.schema_migration
WHERE migration_key='0358_autopilot_health_repair_progression';
DROP TABLE autopilot.migration_0358_function_backup;

COMMIT;
