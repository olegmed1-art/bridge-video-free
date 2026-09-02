\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    source_created record;
    first_claim record;
    second_claim record;
    finding bigint;
    recovery_registered record;
    recovery_claim record;
    recovery_evaluated record;
    gate_registered record;
    deployment_canary autopilot.task;
    deployment_claim record;
    resumed record;
    replayed record;
    first_online_tick record;
    online_claim record;
    second_online_tick record;
    resume_status record;
BEGIN
    IF has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.resume_online_pilot_after_deployment(text,text,bigint,bigint,uuid)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_OWNER_RPC_EXPOSED';
    END IF;
    IF NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.online_resume_status()',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_STATUS_NOT_EXPOSED';
    END IF;
    IF has_table_privilege(
           'autopilot_runtime_principal',
           'autopilot.online_resume_receipt',
           'SELECT,INSERT,UPDATE,DELETE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_TABLE_EXPOSED';
    END IF;

    SELECT * INTO source_created
      FROM autopilot.create_shadow_task(
          'phase3b-oracle-online-sql-deployment-resume-source',
          'AUTOPILOT_SMOKE_V1',
          '{}'::jsonb,
          20,
          0,
          'DIRECTOR_DELEGATED_PILOT',
          'ORACLE_ONLINE_PILOT_V1'
      );
    SELECT * INTO first_claim
      FROM autopilot.claim_next_task('sql-deployment-resume-source-worker', 60);
    UPDATE autopilot.task
       SET lease_until = now() - interval '1 second'
     WHERE task_id = source_created.task_id;
    PERFORM autopilot.reconcile_stale_tasks();
    SELECT * INTO second_claim
      FROM autopilot.claim_next_task('sql-deployment-resume-source-worker', 60);
    IF first_claim.lease_epoch <> 1 OR second_claim.lease_epoch <> 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_FENCING_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        source_created.task_id,
        'sql-deployment-resume-source-worker',
        second_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('a', 64),
        jsonb_build_object(
            'task_id', source_created.task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_SOURCE_COMPLETE_FAILED';
    END IF;
    -- CI executes this whole fixture in one transaction, so move the incident
    -- task behind the later receipt boundary explicitly.
    UPDATE autopilot.task
       SET created_at = now() - interval '1 hour'
     WHERE task_id = source_created.task_id;

    UPDATE autopilot.online_pilot_state
       SET circuit_open = false,
           circuit_reason_code = NULL,
           last_task_id = source_created.task_id,
           last_task_key =
               'phase3b-oracle-online-sql-deployment-resume-source',
           last_created_at = now(),
           created_count = 1,
           pass_count = 0,
           finding_count = 0,
           updated_at = now()
     WHERE singleton;
    PERFORM autopilot.open_online_pilot_circuit(
        'ONLINE_STALE_RUNNING',
        'online-stale-running',
        NULL,
        NULL,
        '{"stale_running":1}'::jsonb,
        'Reconcile the stale lease and prove fencing before resuming the online pilot.'
    );
    SELECT finding_id INTO finding
      FROM autopilot.online_pilot_finding
     WHERE dedupe_key = 'online-stale-running';

    SELECT * INTO recovery_registered
      FROM autopilot.register_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1',
          finding,
          source_created.task_id
      );
    SELECT * INTO recovery_claim
      FROM autopilot.claim_next_task('sql-deployment-recovery-worker', 60);
    IF recovery_claim.task_id <> recovery_registered.canary_task_id
       OR recovery_claim.lease_epoch <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RECOVERY_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        recovery_registered.canary_task_id,
        'sql-deployment-recovery-worker',
        recovery_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('b', 64),
        jsonb_build_object(
            'task_id', recovery_registered.canary_task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RECOVERY_COMPLETE_FAILED';
    END IF;
    SELECT * INTO recovery_evaluated
      FROM autopilot.evaluate_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1'
      );
    IF recovery_evaluated.evaluation <> 'PASS'
       OR NOT recovery_evaluated.passed
       OR NOT recovery_evaluated.circuit_open THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RECOVERY_EVALUATION_INVALID';
    END IF;

    SELECT * INTO gate_registered
      FROM autopilot.register_online_stale_resume_gate(
          'online-stale-running-pre-resume-20260901-v1',
          'online-stale-running-recovery-20260901-v1'
      );
    IF gate_registered.gate_status <> 'FIX_VALIDATED_NOT_DEPLOYED'
       OR gate_registered.ready
       OR NOT gate_registered.circuit_open THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_PRE_RESUME_GATE_INVALID';
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json,
        priority, max_attempts, model_turn_cap,
        cost_cap_microusd, created_by, source
    ) VALUES (
        'deployment-canary-autopilot-race-fix-sql-resume-v1',
        'AUTOPILOT_SMOKE_V1',
        '{}'::jsonb,
        'READY',
        'shadow.noop',
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'deployed_revision',
                '9064044d4c5b85803c6778060dff4843111ab888',
            'activation_run_id', 33586501046::bigint,
            'circuit_must_remain_open', true,
            'max_attempts', 1,
            'model_calls', 0,
            'cost_actual_microusd', 0
        ),
        '["shadow.noop"]'::jsonb,
        10,
        1,
        0,
        0,
        'DIRECTOR_APPROVED_DEPLOYMENT_CANARY',
        'ORACLE_DEPLOYMENT_CANARY_V1'
    ) RETURNING * INTO deployment_canary;
    PERFORM autopilot.record_event(
        deployment_canary.task_id,
        'TASK_READY',
        'NEW',
        'READY',
        jsonb_build_object(
            'goal_type', 'AUTOPILOT_SMOKE_V1',
            'deployed_revision',
                '9064044d4c5b85803c6778060dff4843111ab888',
            'activation_run_id', 33586501046::bigint,
            'circuit_remains_open', true
        ),
        'SYSTEM',
        'deployment-resume-sql-test',
        'deployment-resume-sql-test-ready'
    );
    SELECT * INTO deployment_claim
      FROM autopilot.claim_next_task('sql-deployment-canary-worker', 60);
    IF deployment_claim.task_id <> deployment_canary.task_id
       OR deployment_claim.lease_epoch <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_CANARY_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        deployment_canary.task_id,
        'sql-deployment-canary-worker',
        deployment_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('c', 64),
        jsonb_build_object(
            'task_id', deployment_canary.task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_CANARY_COMPLETE_FAILED';
    END IF;

    SELECT * INTO resumed
      FROM autopilot.resume_online_pilot_after_deployment(
          'online-stale-running-deployment-resume-20260902-v1',
          '9064044d4c5b85803c6778060dff4843111ab888',
          33586404964,
          33586501046,
          deployment_canary.task_id
      );
    IF NOT resumed.created
       OR resumed.resume_status <> 'RESUMED_SHADOW_ONLY'
       OR resumed.deployment_revision_sha <>
            '9064044d4c5b85803c6778060dff4843111ab888'
       OR resumed.circuit_open
       OR resumed.online_created_count <> 1
       OR resumed.online_pass_count <> 0
       OR resumed.receipt_proof_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_TRANSITION_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1 FROM autopilot.online_pilot_state
         WHERE singleton AND (
             circuit_open
             OR circuit_reason_code IS NOT NULL
             OR last_task_id IS NOT NULL
             OR last_task_key IS NOT NULL
             OR last_created_at IS NOT NULL
         )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_STATE_NOT_RESET';
    END IF;

    SELECT * INTO first_online_tick
      FROM autopilot.online_pilot_tick('sql-deployment-resume-observer', 5, 720);
    IF first_online_tick.action <> 'CREATED'
       OR first_online_tick.circuit_open
       OR first_online_tick.created_count <> 2
       OR first_online_tick.pass_count <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_FIRST_TICK_INVALID';
    END IF;
    SELECT * INTO online_claim
      FROM autopilot.claim_next_task('sql-deployment-online-worker', 60);
    IF online_claim.task_key <> first_online_tick.task_key
       OR online_claim.lease_epoch <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_ONLINE_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        online_claim.task_id,
        'sql-deployment-online-worker',
        online_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('d', 64),
        jsonb_build_object(
            'task_id', online_claim.task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_ONLINE_COMPLETE_FAILED';
    END IF;
    SELECT * INTO second_online_tick
      FROM autopilot.online_pilot_tick('sql-deployment-resume-observer', 5, 720);
    IF second_online_tick.action <> 'INTERVAL_HOLD'
       OR second_online_tick.circuit_open
       OR second_online_tick.created_count <> 2
       OR second_online_tick.pass_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_PASS_TICK_INVALID';
    END IF;

    SELECT * INTO resume_status FROM autopilot.online_resume_status();
    IF resume_status.resume_status <> 'RESUMED_SHADOW_ONLY'
       OR resume_status.circuit_open
       OR resume_status.last_task_status <> 'DONE'
       OR resume_status.created_count <> 2
       OR resume_status.pass_count <> 1
       OR resume_status.active_task_count <> 0
       OR resume_status.stale_running_count <> 0
       OR resume_status.nonzero_cost_task_count <> 0
       OR resume_status.unsafe_terminal_task_count <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_STATUS_INVALID';
    END IF;

    SELECT * INTO replayed
      FROM autopilot.resume_online_pilot_after_deployment(
          'online-stale-running-deployment-resume-20260902-v1',
          '9064044d4c5b85803c6778060dff4843111ab888',
          33586404964,
          33586501046,
          deployment_canary.task_id
      );
    IF replayed.created
       OR replayed.circuit_open
       OR replayed.receipt_proof_sha256 <> resumed.receipt_proof_sha256 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_REPLAY_INVALID';
    END IF;

    PERFORM autopilot.open_online_pilot_circuit(
        'ONLINE_RESUME_TEST_REOPENED',
        'online-resume-test-reopened',
        NULL,
        NULL,
        '{}'::jsonb,
        'Test that a one-shot resume replay never closes a later circuit.'
    );
    SELECT * INTO replayed
      FROM autopilot.resume_online_pilot_after_deployment(
          'online-stale-running-deployment-resume-20260902-v1',
          '9064044d4c5b85803c6778060dff4843111ab888',
          33586404964,
          33586501046,
          deployment_canary.task_id
      );
    IF replayed.created OR NOT replayed.circuit_open THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_REPLAY_RECLOSED_CIRCUIT';
    END IF;

    IF (SELECT count(*) FROM autopilot.online_resume_receipt) <> 1
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.online_resume_receipt
             WHERE singleton
               AND resume_status = 'RESUMED_SHADOW_ONLY'
               AND NOT circuit_open_after
               AND proof_json->'deployment_verified' = 'true'::jsonb
               AND proof_json->'resume_scope' = '"SHADOW_ONLY"'::jsonb
               AND proof_json->'model_calls' = '0'::jsonb
               AND proof_json->'cost_actual_microusd' = '0'::jsonb
               AND proof_json->'production_mutation' = 'false'::jsonb
               AND proof_json->'oracle_instance_stop' = 'false'::jsonb
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_RECEIPT_INVALID';
    END IF;
END $$;

ROLLBACK;
