\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    previous_definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0333_autopilot_dispatch_assignment_coherence'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_COHERENCE_NOT_APPLIED';
    END IF;
    SELECT function_definition INTO previous_definition
      FROM autopilot.migration_0333_function_backup
     WHERE function_key='get_dispatch_assignment';
    IF previous_definition IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_COHERENCE_BACKUP_MISSING';
    END IF;
    EXECUTE previous_definition;
END $$;

REVOKE ALL ON FUNCTION autopilot.get_dispatch_assignment(uuid)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
DROP TABLE autopilot.migration_0333_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0333_autopilot_dispatch_assignment_coherence';
COMMIT;
