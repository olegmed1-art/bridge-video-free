\set ON_ERROR_STOP on
BEGIN;
DO $rollback$
DECLARE original text; patched text;
BEGIN
    SELECT function_definition INTO original
      FROM autopilot.migration_0336_function_backup WHERE function_key='before';
    SELECT function_definition INTO patched
      FROM autopilot.migration_0336_function_backup WHERE function_key='after';
    IF original IS NULL OR patched IS NULL OR
       pg_get_functiondef('autopilot.get_dispatch_assignment(uuid)'::regprocedure)
       IS DISTINCT FROM patched THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_ROLLBACK_DRIFT';
    END IF;
    EXECUTE original;
END $rollback$;
-- Remove only the active migration marker. Keep both function backups/evidence.
DELETE FROM public.schema_migration
 WHERE migration_key='0336_autopilot_terminal_summary_contract';
COMMIT;
