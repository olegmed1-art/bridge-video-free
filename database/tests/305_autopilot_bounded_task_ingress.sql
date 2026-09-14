\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    first_row record;
    replay_row record;
    second_row record;
    third_row record;
    claimed record;
BEGIN
    IF NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.register_approved_ibf_analysis(text,text,text,integer)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_RPC_NOT_GRANTED';
    END IF;
    IF has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.create_shadow_task(text,text,jsonb,integer,bigint,text,text)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GENERIC_TASK_CREATE_EXPOSED';
    END IF;
    IF has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'INSERT')
       OR has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'SELECT') THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_DIRECT_TABLE_ACCESS_EXPOSED';
    END IF;

    SELECT * INTO first_row
      FROM autopilot.register_approved_ibf_analysis(
          'ibf-readonly-1013-15031',
          '15031',
          'github:issue:1013#comment-5491004605',
          10
      );
    IF NOT first_row.created OR first_row.status <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_CREATE_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = first_row.task_id
           AND goal_type = 'IBF_READ_ONLY_ANALYSIS'
           AND current_step_key = 'ibf.read_only_analysis'
           AND goal_json = jsonb_build_object(
               'approval_ref', 'github:issue:1013#comment-5491004605',
               'ibf_player_id', '15031',
               'source_authority', 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS'
           )
           AND acceptance_contract_json->'production_mutation' = 'false'::jsonb
           AND acceptance_contract_json->'manual_director_url_forbidden' = 'true'::jsonb
           AND allowed_capabilities_json = '["ibf.read_only_analysis"]'::jsonb
           AND cost_cap_microusd = 0
           AND created_by = 'DIRECTOR_APPROVED_INGRESS'
           AND source = 'BOUNDED_TASK_INGRESS_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_TASK_SHAPE_INVALID';
    END IF;
    IF (SELECT count(*) FROM autopilot.task_event
         WHERE task_id = first_row.task_id
           AND event_type = 'TASK_READY'
           AND state_from = 'NEW'
           AND state_to = 'READY'
           AND idempotency_key = 'ingress:ibf-readonly-1013-15031') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_EVENT_INVALID';
    END IF;

    SELECT * INTO replay_row
      FROM autopilot.register_approved_ibf_analysis(
          'ibf-readonly-1013-15031',
          '15031',
          'github:issue:1013#comment-5491004605',
          10
      );
    IF replay_row.created OR replay_row.task_id <> first_row.task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_IDEMPOTENT_REPLAY_FAILED';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.register_approved_ibf_analysis(
            'ibf-readonly-1013-15031',
            '15032',
            'github:issue:1013#comment-5491004605',
            10
        );
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_CONFLICT_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IDEMPOTENCY_CONFLICT%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM * FROM autopilot.register_approved_ibf_analysis(
            'ibf-invalid-player', '15A31',
            'github:issue:1013#comment-5491004605', 10
        );
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_INVALID_PLAYER_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IBF_PLAYER_ID_INVALID%' THEN RAISE; END IF;
    END;

    BEGIN
        PERFORM * FROM autopilot.register_approved_ibf_analysis(
            'ibf-invalid-approval', '15031', 'https://evil.example/?x=1&y=2', 10
        );
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_INVALID_APPROVAL_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_APPROVAL_REF_INVALID%' THEN RAISE; END IF;
    END;

    SELECT * INTO second_row
      FROM autopilot.register_approved_ibf_analysis(
          'ibf-readonly-limit-2', '15032',
          'github:issue:1015#director-go', 20
      );
    SELECT * INTO third_row
      FROM autopilot.register_approved_ibf_analysis(
          'ibf-readonly-limit-3', '15033',
          'github:issue:1015#director-go', 20
      );
    IF NOT second_row.created OR NOT third_row.created THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_LIMIT_SETUP_FAILED';
    END IF;
    BEGIN
        PERFORM * FROM autopilot.register_approved_ibf_analysis(
            'ibf-readonly-limit-4', '15034',
            'github:issue:1015#director-go', 20
        );
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_ACTIVE_LIMIT_BYPASSED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_INGRESS_ACTIVE_LIMIT%' THEN RAISE; END IF;
    END;

    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-ibf-worker', 60);
    IF claimed.task_id <> first_row.task_id
       OR claimed.goal_type <> 'IBF_READ_ONLY_ANALYSIS'
       OR claimed.current_step_key <> 'ibf.read_only_analysis'
       OR claimed.cost_cap_microusd <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_CLAIM_INVALID';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM autopilot.step_attempt
         WHERE task_id = first_row.task_id
           AND capability_name = 'ibf.read_only_analysis'
           AND status = 'RUNNING'
           AND lease_epoch = claimed.lease_epoch
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_STEP_ATTEMPT_INVALID';
    END IF;
END $$;

ROLLBACK;
