\set ON_ERROR_STOP on
BEGIN;
DO $rollback$
DECLARE saved text;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.schema_migration WHERE migration_key='0348_autopilot_blocker_remediation') THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_ROLLBACK_MIGRATION_MISSING';
 END IF;
 SELECT function_definition INTO saved
 FROM autopilot.migration_0348_function_backup
 WHERE function_key='materialize_role_repair';
 IF saved IS NULL OR strpos(saved,'BLOCKER_REMEDIATION_ADMISSION_V1')>0 THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_ROLLBACK_BACKUP_INVALID';
 END IF;
 EXECUTE saved;
END $rollback$;
DROP FUNCTION autopilot.blocker_repository_repair_allowed(text);
DROP FUNCTION autopilot.blocker_remediation_action(text);
DROP TABLE autopilot.migration_0348_function_backup;
DELETE FROM public.schema_migration WHERE migration_key='0348_autopilot_blocker_remediation';
COMMIT;
