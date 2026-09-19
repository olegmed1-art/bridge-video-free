\set ON_ERROR_STOP on
BEGIN;

DO $t$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0354_autopilot_mailbox_v3_final_head'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_MIGRATION_MISSING';
 END IF;
 IF (SELECT count(*) FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE')<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_ACTIVE_MAILBOX_COUNT_INVALID';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1685
     AND lifecycle='ACTIVE'
     AND expected_head_sha='5ff5d9abe497a50cac6564d856297b68c7b4a6c0'
     AND max_dispatches=40
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_FINAL_HEAD_NOT_PINNED';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_mailbox_registry
   WHERE mailbox_pr=1685
     AND expected_head_sha='7bfae72289f12ca5c28ce6a3754ccab5383b7f75'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0354_BOOTSTRAP_HEAD_RETAINED';
 END IF;
END $t$;

ROLLBACK;
