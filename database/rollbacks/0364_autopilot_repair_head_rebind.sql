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
   WHERE migration_key='0364_autopilot_repair_head_rebind'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_ROLLBACK_MIGRATION_MISSING';
 END IF;
 IF to_regprocedure(
      'autopilot.materialize_role_repair_rebound(uuid,text,text,text)'
    ) IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_ROLLBACK_FUNCTION_MISSING';
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
   RAISE EXCEPTION 'AUTOPILOT_0364_ROLLBACK_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

DROP FUNCTION autopilot.materialize_role_repair_rebound(uuid,text,text,text);
DELETE FROM public.schema_migration
WHERE migration_key='0364_autopilot_repair_head_rebind';

COMMIT;
