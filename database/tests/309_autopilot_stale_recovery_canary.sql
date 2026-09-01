\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    source_created record;
    first_claim record;
    second_claim record;
    finding bigint;
    registered record;
    replay record;
    canary_claim record;
    evaluated record;
    evaluated_again record;
BEGIN
    IF has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.register_online_stale_recovery_canary(text,bigint,uuid)',
           'EXECUTE'
       ) OR has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.evaluate_online_stale_recovery_canary(text)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_OWNER_RPC_EXPOSED';
    END IF;
    IF has_table_privilege(
           'autopilot_runtime_principal',
           'autopilot.online_recovery_canary',
           'SELECT,INSERT,UPDATE,DELETE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_TABLE_EXPOSED';
    END IF;

    SELECT * INTO source_created
      FROM autopilot.create_shadow_task(
          'phase3b-oracle-online-sql-recovery-source',
          'AUTOPILOT_SMOKE_V1',
          '{}'::jsonb,
          20,
          0,
          'DIRECTOR_DELEGATED_PILOT',
          'ORACLE_ONLINE_PILOT_V1'
      );
    SELECT * INTO first_claim
      FROM autopilot.claim_next_task('sql-recovery-source-worker', 60);
    IF first_claim.task_id <> source_created.task_id
       OR first_claim.lease_epoch <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_SOURCE_FIRST_CLAIM_INVALID';
    END IF;

    UPDATE autopilot.task
       SET lease_until = now() - interval '1 second'
     WHERE task_id = source_created.task_id;
    PERFORM autopilot.reconcile_stale_tasks();

    SELECT * INTO second_claim
      FROM autopilot.claim_next_task('sql-recovery-source-worker', 60);
    IF second_claim.task_id <> source_created.task_id
       OR second_claim.lease_epoch <> 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_SOURCE_SECOND_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        source_created.task_id,
        'sql-recovery-source-worker',
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
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_SOURCE_COMPLETE_FAILED';
    END IF;

    UPDATE autopilot.online_pilot_state
       SET circuit_open = false,
           circuit_reason_code = NULL,
           last_task_id = source_created.task_id,
           last_task_key = 'phase3b-oracle-online-sql-recovery-source',
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

    SELECT * INTO registered
      FROM autopilot.register_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1',
          finding,
          source_created.task_id
      );
    IF NOT registered.created
       OR registered.task_status <> 'READY'
       OR NOT registered.circuit_open THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CANARY_CREATE_INVALID';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM autopilot.online_recovery_canary AS receipt
          JOIN autopilot.task AS task
            ON task.task_id = receipt.canary_task_id
         WHERE receipt.singleton
           AND receipt.recovery_key =
               'online-stale-running-recovery-20260901-v1'
           AND receipt.finding_id = finding
           AND receipt.source_task_id = source_created.task_id
           AND receipt.evaluation_status = 'CREATED'
           AND receipt.proof_json->'fencing_proved' = 'true'::jsonb
           AND receipt.proof_json->'circuit_remains_open' = 'true'::jsonb
           AND task.task_key =
               'recovery-canary-online-stale-running-20260901-v1'
           AND task.goal_type = 'AUTOPILOT_SMOKE_V1'
           AND task.status = 'READY'
           AND task.max_attempts = 1
           AND task.model_turn_cap = 0
           AND task.cost_cap_microusd = 0
           AND task.source = 'ORACLE_RECOVERY_CANARY_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_RECEIPT_INVALID';
    END IF;

    SELECT * INTO replay
      FROM autopilot.register_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1',
          finding,
          source_created.task_id
      );
    IF replay.created
       OR replay.canary_task_id <> registered.canary_task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_REPLAY_FAILED';
    END IF;

    SELECT * INTO canary_claim
      FROM autopilot.claim_next_task('sql-recovery-canary-worker', 60);
    IF canary_claim.task_id <> registered.canary_task_id
       OR canary_claim.lease_epoch <> 1
       OR canary_claim.max_attempts <> 1
       OR canary_claim.cost_cap_microusd <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CANARY_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        registered.canary_task_id,
        'sql-recovery-canary-worker',
        canary_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('b', 64),
        jsonb_build_object(
            'task_id', registered.canary_task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CANARY_COMPLETE_FAILED';
    END IF;

    SELECT * INTO evaluated
      FROM autopilot.evaluate_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1'
      );
    IF evaluated.evaluation <> 'PASS'
       OR NOT evaluated.passed
       OR NOT evaluated.circuit_open
       OR evaluated.task_status <> 'DONE' THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CANARY_EVALUATION_INVALID';
    END IF;

    SELECT * INTO evaluated_again
      FROM autopilot.evaluate_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1'
      );
    IF evaluated_again.evaluation <> 'PASS'
       OR NOT evaluated_again.passed
       OR evaluated_again.canary_task_id <> registered.canary_task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_EVALUATION_REPLAY_FAILED';
    END IF;

    IF NOT (SELECT circuit_open FROM autopilot.online_pilot_state WHERE singleton)
       OR (SELECT circuit_reason_code FROM autopilot.online_pilot_state WHERE singleton)
          <> 'ONLINE_STALE_RUNNING'
       OR (SELECT count(*) FROM autopilot.task
            WHERE source = 'ORACLE_RECOVERY_CANARY_V1') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_BOUNDARY_VIOLATED';
    END IF;
END $$;

ROLLBACK;
