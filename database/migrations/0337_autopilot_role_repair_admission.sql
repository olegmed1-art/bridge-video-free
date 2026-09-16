\set ON_ERROR_STOP on
BEGIN;

-- Do not turn a retained BLOCKED result into unauthorized or obsolete work.
-- Return NULL (not an exception): the callback must still retain the result,
-- release the original task, and let independent work continue.
DO $migration$
DECLARE
    original text;
    anchor text := E'    SELECT created.task_id INTO followup_id\n      FROM autopilot.create_chatgpt_role_followup_task(';
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
    IF to_regclass('autopilot.role_registry') IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_ADMISSION_REQUIRES_ROLE_REGISTRY';
    END IF;
    original := pg_get_functiondef(
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure);
    IF original IS NULL
       OR (length(original)-length(replace(original,anchor,'')))/length(anchor) <> 1
       OR strpos(original,'REPAIR_ADMISSION_V1') > 0
       OR strpos(original,'autopilot.role_blocker_requires_owner(p_result_code)') = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_ADMISSION_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(original,anchor,admission || anchor);
END $migration$;

DO $terminal_callback$
DECLARE
    original text;
    enabled_check text := $enabled_check$       OR NOT autopilot.role_is_enabled(COALESCE(p_body->>'role',''))
$enabled_check$;
    bound_role text := $bound_role$       -- TERMINAL_BOUND_ROLE_V1: authority was bound by the accepted ACK.
       -- Later disablement must not discard terminal evidence or retain resources.
$bound_role$;
BEGIN
    original := pg_get_functiondef(
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure);
    IF original IS NULL
       OR (length(original)-length(replace(original,enabled_check,'')))
          /length(enabled_check) <> 1
       OR strpos(original,'TERMINAL_BOUND_ROLE_V1') > 0
       OR strpos(original,'AUTOPILOT_CODEX_TERMINAL_BINDING_INVALID') = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_BOUND_ROLE_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(original,enabled_check,bound_role);
END $terminal_callback$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0337_autopilot_role_repair_admission');
COMMIT;
