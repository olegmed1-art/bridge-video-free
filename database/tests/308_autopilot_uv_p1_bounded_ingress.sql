\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    runtime_row record;
    replay_row record;
    canary_row record;
    idle_row record;
BEGIN
    IF NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.register_approved_uv_p1_ci(text)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_INGRESS_RPC_NOT_GRANTED';
    END IF;
    IF has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.create_shadow_task(text,text,jsonb,integer,bigint,text,text)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GENERIC_TASK_CREATE_EXPOSED';
    END IF;
    IF has_table_privilege(
           'autopilot_runtime_principal', 'autopilot.task', 'INSERT'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DIRECT_TASK_INSERT_EXPOSED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.register_approved_uv_p1_ci('uv-p1-not-approved');
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_ARBITRARY_TASK_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_UV_P1_TASK_NOT_APPROVED%' THEN RAISE; END IF;
    END;

    SELECT * INTO runtime_row
      FROM autopilot.register_approved_uv_p1_ci(
          'uv-p1-runtime-pr997-c1515c5af4a4-20260902'
      );
    IF NOT runtime_row.created OR runtime_row.status <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_CREATE_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = runtime_row.task_id
           AND goal_type = 'GITHUB_CI_READ_ONLY_V1'
           AND goal_json = jsonb_build_object(
               'repository', 'olegmed1-art/bridge-video-free',
               'pr_number', 997,
               'expected_head_sha', 'c1515c5af4a47c7468d7c4769e91082f7afd163c',
               'require_draft', true
           )
           AND current_step_key = 'github.ci.snapshot'
           AND allowed_capabilities_json = '["github.ci.snapshot"]'::jsonb
           AND acceptance_contract_json->'production_mutation' = 'false'::jsonb
           AND acceptance_contract_json->'model_calls' = '0'::jsonb
           AND priority = 10
           AND max_attempts = 1
           AND model_turn_cap = 0
           AND cost_cap_microusd = 0
           AND created_by = 'DIRECTOR_CHAT_GO_20260901'
           AND source = 'BOUNDED_UV_P1_INGRESS_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_TASK_SHAPE_INVALID';
    END IF;

    SELECT * INTO replay_row
      FROM autopilot.register_approved_uv_p1_ci(
          'uv-p1-runtime-pr997-c1515c5af4a4-20260902'
      );
    IF replay_row.created OR replay_row.task_id <> runtime_row.task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_REPLAY_FAILED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.register_approved_uv_p1_ci(
            'uv-p1-canary-pr1062-8aa4f80b8d20-20260902'
        );
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_ACTIVE_LIMIT_BYPASSED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_INGRESS_ACTIVE_LIMIT%' THEN RAISE; END IF;
    END;

    UPDATE autopilot.task
       SET status = 'DONE',
           terminal_reason_code = 'SQL_TEST_TERMINAL',
           completed_at = now()
     WHERE task_id = runtime_row.task_id;

    SELECT * INTO canary_row
      FROM autopilot.register_approved_uv_p1_ci(
          'uv-p1-canary-pr1062-8aa4f80b8d20-20260902'
      );
    IF NOT canary_row.created THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_SECOND_CREATE_FAILED';
    END IF;

    UPDATE autopilot.task
       SET status = 'DONE',
           terminal_reason_code = 'SQL_TEST_TERMINAL',
           completed_at = now()
     WHERE task_id = canary_row.task_id;

    SELECT * INTO idle_row
      FROM autopilot.register_approved_uv_p1_ci(
          'uv-p1-idle-pr1061-8ab8d74c2a0f-20260902'
      );
    IF NOT idle_row.created THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_THIRD_CREATE_FAILED';
    END IF;

    IF (
        SELECT count(*)
          FROM autopilot.task
         WHERE source = 'BOUNDED_UV_P1_INGRESS_V1'
           AND goal_type = 'GITHUB_CI_READ_ONLY_V1'
           AND cost_cap_microusd = 0
           AND model_turn_cap = 0
           AND max_attempts = 1
       ) <> 3 THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_BOUNDED_SET_INVALID';
    END IF;
END $$;

ROLLBACK;
