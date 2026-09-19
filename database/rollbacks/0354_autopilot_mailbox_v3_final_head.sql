\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_mailbox_registry IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
BEGIN
 IF (SELECT enabled FROM autopilot.project_planner_state WHERE singleton) THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_ROLLBACK_REQUIRES_PLANNER_PAUSED';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE mailbox_pr=1685
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_ROLLBACK_MAILBOX_HAS_HISTORY';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1685
     AND lifecycle='ACTIVE'
     AND expected_head_sha='5ff5d9abe497a50cac6564d856297b68c7b4a6c0'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_ROLLBACK_SOURCE_DRIFT';
 END IF;
END $guard$;

UPDATE autopilot.role_dispatch_mailbox_registry
SET expected_head_sha='7bfae72289f12ca5c28ce6a3754ccab5383b7f75'
WHERE mailbox_pr=1685
  AND lifecycle='ACTIVE'
  AND expected_head_sha='5ff5d9abe497a50cac6564d856297b68c7b4a6c0';

DELETE FROM public.schema_migration
WHERE migration_key='0354_autopilot_mailbox_v3_final_head';

COMMIT;
