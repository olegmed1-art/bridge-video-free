\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    function_body text;
    runtime_row record;
    rejected_key text;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0309_autopilot_uv_p1_bounded_ingress'
    ) OR NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0313_autopilot_uv_p1_allowlist_upgrade'
    ) OR NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0314_autopilot_uv_p1_allowlist_upgrade'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_UPGRADE_HISTORY_MISSING';
    END IF;

    SELECT pg_get_functiondef(
        'autopilot.register_approved_uv_p1_ci(text)'::regprocedure
    ) INTO function_body;
    IF position('uv-p1-runtime-pr997-c1515c5af4a4-20260902' in function_body) = 0
       OR position('uv-p1-canary-pr1062-8aa4f80b8d20-20260902' in function_body) = 0
       OR position('uv-p1-idle-pr1061-8ab8d74c2a0f-20260902' in function_body) = 0
       OR position('uv-p1-runtime-pr997-17b74b86b309-20260902' in function_body) <> 0
       OR position('uv-p1-canary-pr1062-164d0d509fa3-20260902' in function_body) <> 0
       OR position('uv-p1-canary-pr1062-f2a6c0ff3f58-20260902' in function_body) <> 0
       OR position('uv-p1-idle-pr1047-e8e71b569f81-20260902' in function_body) <> 0
       OR position('uv-p1-runtime-pr997-545ef013-20260901' in function_body) <> 0
       OR position('uv-p1-intake-pr1000-5af0675a-20260901' in function_body) <> 0
       OR position('uv-p1-idle-pr1047-621ab073-20260901' in function_body) <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_FORWARD_ALLOWLIST_INVALID';
    END IF;

    IF NOT has_function_privilege(
        'autopilot_runtime_principal',
        'autopilot.register_approved_uv_p1_ci(text)',
        'EXECUTE'
    ) OR NOT has_table_privilege(
        'autopilot_runtime_principal',
        'autopilot.task_status',
        'SELECT'
    ) OR has_table_privilege(
        'autopilot_runtime_principal',
        'autopilot.task',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_UPGRADE_PRIVILEGE_BOUNDARY_INVALID';
    END IF;

    FOREACH rejected_key IN ARRAY ARRAY[
        'uv-p1-runtime-pr997-545ef013-20260901',
        'uv-p1-intake-pr1000-5af0675a-20260901',
        'uv-p1-idle-pr1047-621ab073-20260901',
        'uv-p1-runtime-pr997-17b74b86b309-20260902',
        'uv-p1-canary-pr1062-164d0d509fa3-20260902',
        'uv-p1-canary-pr1062-f2a6c0ff3f58-20260902',
        'uv-p1-canary-pr1062-f7685ac91c90-20260902',
        'uv-p1-idle-pr1047-e8e71b569f81-20260902'
    ] LOOP
        BEGIN
            PERFORM * FROM autopilot.register_approved_uv_p1_ci(rejected_key);
            RAISE EXCEPTION 'AUTOPILOT_UV_P1_RETIRED_KEY_ACCEPTED:%', rejected_key;
        EXCEPTION WHEN OTHERS THEN
            IF SQLERRM NOT LIKE '%AUTOPILOT_UV_P1_TASK_NOT_APPROVED%' THEN
                RAISE;
            END IF;
        END;
    END LOOP;

    SELECT * INTO runtime_row
      FROM autopilot.register_approved_uv_p1_ci(
          'uv-p1-runtime-pr997-c1515c5af4a4-20260902'
      );
    IF NOT runtime_row.created OR runtime_row.status <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_UPGRADED_KEY_CREATE_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM autopilot.task_status
         WHERE task_id = runtime_row.task_id
           AND task_key = 'uv-p1-runtime-pr997-c1515c5af4a4-20260902'
           AND status = 'READY'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_STATUS_VIEW_OBSERVATION_INVALID';
    END IF;
END $$;

ROLLBACK;
