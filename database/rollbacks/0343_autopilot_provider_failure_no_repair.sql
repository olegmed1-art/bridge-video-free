\set ON_ERROR_STOP on
BEGIN;

DO $rollback$
DECLARE
    current_definition text;
    old_guard text := $old_guard$    IF p_result_code IS NULL
       OR p_result_code ~ '(^|_)(SUPERSEDED|OBSOLETE)(_|$)' THEN
        RETURN NULL;
    END IF;
$old_guard$;
    new_guard text := $new_guard$    -- REPAIR_ADMISSION_PROVIDER_TERMINAL_V1: provider failures are not repairable code.
    IF p_result_code IS NULL
       OR p_result_code = 'CODEX_PROVIDER_GENERIC_FAILURE'
       OR p_result_code ~ '(^|_)(SUPERSEDED|OBSOLETE)(_|$)' THEN
        RETURN NULL;
    END IF;
$new_guard$;
BEGIN
    current_definition := pg_get_functiondef(
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,new_guard,'')))
          /length(new_guard) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROVIDER_FAILURE_NO_REPAIR_ROLLBACK_DRIFT';
    END IF;
    EXECUTE replace(current_definition,new_guard,old_guard);
END $rollback$;

-- Preserve tasks, receipts, evidence and retained BLOCKED outcomes.
DELETE FROM public.schema_migration
 WHERE migration_key='0343_autopilot_provider_failure_no_repair';
COMMIT;
