\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    current_source text;
    previous_source text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0335_autopilot_codex_delivery_window'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DELIVERY_WINDOW_MIGRATION_MISSING';
    END IF;

    SELECT prosrc INTO current_source
      FROM pg_proc
     WHERE oid='autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text)'::regprocedure;
    SELECT function_definition INTO previous_source
      FROM autopilot.migration_0335_function_backup
     WHERE function_key='mark_role_dispatch_published';

    IF current_source NOT LIKE '%THEN interval ''30 minutes''%'
       OR current_source LIKE '%THEN interval ''10 minutes''%'
       OR previous_source NOT LIKE '%10 minutes%' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DELIVERY_WINDOW_DEFINITION_INVALID';
    END IF;
    IF NOT has_function_privilege(
        'autopilot_runtime',
        'autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text)',
        'EXECUTE'
    ) OR has_table_privilege(
        'autopilot_runtime','autopilot.migration_0335_function_backup','SELECT'
    ) OR has_table_privilege(
        'autopilot_callback','autopilot.migration_0335_deadline_backup','SELECT'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DELIVERY_WINDOW_PRIVILEGE_INVALID';
    END IF;
END $$;

ROLLBACK;
