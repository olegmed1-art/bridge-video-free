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
DO $terminal_callback_rollback$
DECLARE
    current_definition text;
    bound_role text := $bound_role$       -- TERMINAL_BOUND_ROLE_V1: authority was bound by the accepted ACK.
       -- Later disablement must not discard terminal evidence or retain resources.
       OR COALESCE(p_body->>'role','') !~ '^[A-Z][A-Z0-9_]{0,63}
    enabled_check text := $enabled_check$       OR NOT autopilot.role_is_enabled(COALESCE(p_body->>'role',''))
$enabled_check$;
BEGIN
    current_definition := pg_get_functiondef(
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,bound_role,'')))
          /length(bound_role) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_BOUND_ROLE_ROLLBACK_DRIFT';
    END IF;
    EXECUTE replace(current_definition,bound_role,enabled_check);
END $terminal_callback_rollback$;
-- Only the activation marker is removed; tasks, receipts and evidence remain.
DELETE FROM public.schema_migration
 WHERE migration_key='0337_autopilot_role_repair_admission';
COMMIT;

$bound_role$;
    enabled_check text := $enabled_check$       OR NOT autopilot.role_is_enabled(COALESCE(p_body->>'role',''))
$enabled_check$;
BEGIN
    current_definition := pg_get_functiondef(
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,bound_role,'')))
          /length(bound_role) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_BOUND_ROLE_ROLLBACK_DRIFT';
    END IF;
    EXECUTE replace(current_definition,bound_role,enabled_check);
END $terminal_callback_rollback$;
-- Only the activation marker is removed; tasks, receipts and evidence remain.
DELETE FROM public.schema_migration
 WHERE migration_key='0337_autopilot_role_repair_admission';
COMMIT;
