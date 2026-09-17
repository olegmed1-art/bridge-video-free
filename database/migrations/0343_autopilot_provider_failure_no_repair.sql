\set ON_ERROR_STOP on
BEGIN;

DO $prerequisite$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0340_autopilot_publication_permit_issuer'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROVIDER_FAILURE_NO_REPAIR_REQUIRES_0340';
    END IF;
END $prerequisite$;

-- A pinned provider execution failure is terminal transport evidence, not a
-- repository defect. Retain the BLOCKED receipt but never synthesize REPAIR.
DO $migration$
DECLARE
    original text;
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
    original := pg_get_functiondef(
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure);
    IF original IS NULL
       OR strpos(original,'REPAIR_ADMISSION_V1') = 0
       OR strpos(original,'REPAIR_ADMISSION_PROVIDER_TERMINAL_V1') > 0
       OR (length(original)-length(replace(original,old_guard,'')))
          /length(old_guard) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROVIDER_FAILURE_NO_REPAIR_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(original,old_guard,new_guard);
END $migration$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0343_autopilot_provider_failure_no_repair');
COMMIT;
