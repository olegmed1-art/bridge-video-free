\set ON_ERROR_STOP on
BEGIN;
DO $dependency$
BEGIN
 IF EXISTS (SELECT FROM public.schema_migration
            WHERE migration_key='0374_autopilot_queue_health_semantics') THEN
   RAISE EXCEPTION 'AUTOPILOT_ACTIVE_QUEUE_ROLLBACK_REQUIRES_0374_REMOVAL';
 END IF;
END $dependency$;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_mailbox_registry IN SHARE ROW EXCLUSIVE MODE;

DO $restore$
DECLARE item record;
BEGIN
 IF (SELECT count(*) FROM autopilot.migration_0373_definition_backup)<>5 THEN
   RAISE EXCEPTION 'AUTOPILOT_ACTIVE_QUEUE_BACKUP_INCOMPLETE';
 END IF;
 FOR item IN SELECT * FROM autopilot.migration_0373_definition_backup
             WHERE object_key<>'health_view' ORDER BY object_key LOOP
   EXECUTE item.definition;
 END LOOP;
 SELECT definition INTO STRICT item FROM autopilot.migration_0373_definition_backup
 WHERE object_key='health_view';
 EXECUTE 'CREATE OR REPLACE VIEW public.autopilot_operational_health_signal AS '||item.definition;
END $restore$;
DROP TRIGGER role_dispatch_mailbox_capacity_guard ON autopilot.role_dispatch_outbox;
CREATE TRIGGER role_dispatch_mailbox_capacity_guard BEFORE INSERT
ON autopilot.role_dispatch_outbox FOR EACH ROW
EXECUTE FUNCTION autopilot.enforce_role_dispatch_mailbox_capacity();
DROP TRIGGER role_dispatch_history_guard ON autopilot.role_dispatch_outbox;
DROP FUNCTION autopilot.guard_role_dispatch_history();
DROP FUNCTION autopilot.role_dispatch_queue_available(integer);
DROP VIEW autopilot.role_dispatch_active,autopilot.role_dispatch_archive;
DROP INDEX autopilot.role_dispatch_active_mailbox_idx;
DROP TABLE autopilot.migration_0373_definition_backup;
DELETE FROM public.schema_migration WHERE migration_key='0373_autopilot_active_dispatch_capacity';
-- No task, dispatch, receipt, comment or evidence is deleted/reopened. The
-- restored lifetime guard may block admission again; retain HOLD on rollback.
COMMIT;
