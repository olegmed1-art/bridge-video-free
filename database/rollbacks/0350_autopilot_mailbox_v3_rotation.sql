\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM autopilot.role_dispatch_outbox WHERE mailbox_pr=1685) THEN RAISE EXCEPTION 'AUTOPILOT_0350_ROLLBACK_NEW_MAILBOX_HAS_HISTORY'; END IF;
END $$;
DELETE FROM autopilot.role_dispatch_mailbox_registry WHERE mailbox_pr=1685;
UPDATE autopilot.role_dispatch_mailbox_registry SET lifecycle='ACTIVE',activated_at=COALESCE(activated_at,clock_timestamp()),retained_at=NULL WHERE mailbox_pr=1637;
ALTER TABLE autopilot.project_work_item DROP CONSTRAINT project_work_item_mailbox_pr_check;
ALTER TABLE autopilot.project_work_item ADD CONSTRAINT project_work_item_mailbox_pr_check CHECK(mailbox_pr IN (1150,1637));
ALTER TABLE autopilot.project_work_item ALTER COLUMN mailbox_pr SET DEFAULT 1637;
ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_outbox_mailbox_pr_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_outbox_mailbox_pr_check CHECK(mailbox_pr IN (1150,1637));
DELETE FROM public.schema_migration WHERE migration_key='0350_autopilot_mailbox_v3_rotation';
COMMIT;
