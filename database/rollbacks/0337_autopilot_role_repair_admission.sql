\set ON_ERROR_STOP on
BEGIN;
DO $rollback$
DECLARE
    current_definition text;
    admission text := $admission$    -- REPAIR_ADMISSION_V1: authority and target disposition before side effects.
    IF p_result_code IS NULL
       OR p_result_code ~ '(^|_)(SUPERSEDED|OBSOLETE)(_|$)' THEN
        RETURN NULL;
    END IF;
    PERFORM 1 FROM autopilot.role_registry AS permitted_role
     WHERE permitted_role.role_id = origin_row.goal_json->>'role'
       AND permitted_role.enabled
       AND permitted_role.execution_scope = 'REPOSITORY'
       AND permitted_role.can_repair
     FOR SHARE;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

$admission$;
BEGIN
    current_definition := pg_get_functiondef(
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,admission,'')))
          /length(admission) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_ADMISSION_ROLLBACK_DRIFT';
    END IF;
    EXECUTE replace(current_definition,admission,'');
END $rollback$;
-- Only the activation marker is removed; tasks, receipts and evidence remain.
DELETE FROM public.schema_migration
 WHERE migration_key='0337_autopilot_role_repair_admission';
COMMIT;
