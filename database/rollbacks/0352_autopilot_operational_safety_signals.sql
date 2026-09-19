\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
 IF EXISTS(SELECT 1 FROM autopilot.mailbox_rotation_signal) THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_ROLLBACK_HAS_ROTATION_SIGNAL';
 END IF;
END $guard$;

DO $restore$
DECLARE d text;
BEGIN
 SELECT function_definition INTO d FROM autopilot.migration_0352_function_backup WHERE function_key='claim_project_work_probe';
 IF d IS NULL THEN RAISE EXCEPTION 'AUTOPILOT_0352_ROLLBACK_PLANNER_BACKUP_MISSING'; END IF;
 EXECUTE d;
 SELECT function_definition INTO d FROM autopilot.migration_0352_function_backup WHERE function_key='blocker_remediation_action';
 IF d IS NULL THEN RAISE EXCEPTION 'AUTOPILOT_0352_ROLLBACK_BLOCKER_BACKUP_MISSING'; END IF;
 EXECUTE d;
 SELECT function_definition INTO d FROM autopilot.migration_0352_function_backup WHERE function_key='enforce_role_dispatch_mailbox_capacity';
 IF d IS NULL THEN RAISE EXCEPTION 'AUTOPILOT_0352_ROLLBACK_CAPACITY_BACKUP_MISSING'; END IF;
 EXECUTE d;
END $restore$;

DROP VIEW public.autopilot_operational_health_signal;
DROP FUNCTION autopilot.mailbox_e2e_acceptance();
DROP FUNCTION autopilot.mailbox_rotation_readiness();
DROP TABLE autopilot.mailbox_rotation_signal;
DROP TABLE autopilot.migration_0352_function_backup;
DELETE FROM public.schema_migration WHERE migration_key='0352_autopilot_operational_safety_signals';
COMMIT;
