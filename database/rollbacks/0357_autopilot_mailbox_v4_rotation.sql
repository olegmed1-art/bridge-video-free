\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_mailbox_registry IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox WHERE mailbox_pr=1703
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_ROLLBACK_NEW_MAILBOX_HAS_HISTORY';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_ROLLBACK_ACTIVE_ROLE_TASK_PRESENT';
 END IF;
 IF (SELECT count(*) FROM autopilot.migration_0357_function_backup)<>10 THEN
   RAISE EXCEPTION 'AUTOPILOT_0357_ROLLBACK_BACKUP_INCOMPLETE';
 END IF;
END $pre$;

DO $restore$
DECLARE saved record;
BEGIN
 FOR saved IN
  SELECT definition FROM autopilot.migration_0357_function_backup ORDER BY function_key
 LOOP
  EXECUTE saved.definition;
 END LOOP;
END $restore$;

UPDATE autopilot.project_work_item
SET mailbox_pr=1685,updated_at=clock_timestamp()
WHERE mailbox_pr=1703 AND state<>'DONE';

ALTER TABLE autopilot.project_work_item
 DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item
 ADD CONSTRAINT project_work_item_mailbox_pr_check
 CHECK(mailbox_pr IN (1150,1637,1685));
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1685;

ALTER TABLE autopilot.role_dispatch_outbox
 DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox
 ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check
 CHECK(mailbox_pr IN (1150,1637,1685));

DELETE FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1703;
UPDATE autopilot.role_dispatch_mailbox_registry
SET lifecycle='ACTIVE',retained_at=NULL
WHERE mailbox_pr=1685 AND lifecycle='RETAINED';

DELETE FROM public.schema_migration
WHERE migration_key='0357_autopilot_mailbox_v4_rotation';
DROP TABLE autopilot.migration_0357_function_backup;

COMMIT;
