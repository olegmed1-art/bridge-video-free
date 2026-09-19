\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_mailbox_registry IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;

UPDATE autopilot.project_planner_state
SET enabled=false
WHERE singleton;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0353_autopilot_paused_reconcile_runner'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_FINAL_HEAD_REQUIRES_0353';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1685
     AND lifecycle='ACTIVE'
     AND expected_head_sha='7bfae72289f12ca5c28ce6a3754ccab5383b7f75'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_FINAL_HEAD_SOURCE_DRIFT';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE mailbox_pr=1685
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_FINAL_HEAD_HISTORY_PRESENT';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V3_FINAL_HEAD_ACTIVE_ROLE_TASK_PRESENT';
 END IF;
END $pre$;

UPDATE autopilot.role_dispatch_mailbox_registry
SET expected_head_sha='5ff5d9abe497a50cac6564d856297b68c7b4a6c0'
WHERE mailbox_pr=1685
  AND lifecycle='ACTIVE'
  AND expected_head_sha='7bfae72289f12ca5c28ce6a3754ccab5383b7f75';

INSERT INTO public.schema_migration(migration_key)
VALUES('0354_autopilot_mailbox_v3_final_head');

COMMIT;
