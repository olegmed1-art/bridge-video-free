\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    source_created record;
    first_claim record;
    second_claim record;
    finding bigint;
    canary_registered record;
    canary_claim record;
    canary_evaluated record;
    gate_registered record;
    gate_replayed record;
    circuit_rejected boolean := false;
BEGIN
    IF has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.register_online_stale_resume_gate(text,text)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_OWNER_RPC_EXPOSED';
    END IF;
    IF has_table_privilege(
           'autopilot_runtime_principal',
           'autopilot.online_resume_gate',
           'SELECT,INSERT,UPDATE,DELETE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_TABLE_EXPOSED';
    END IF;

    SELECT * INTO source_created
      FROM autopilot.create_shadow_task(
          'phase3b-oracle-online-sql-resume-source',
          'AUTOPILOT_SMOKE_V1',
          '{}'::jsonb,
          20,
          0,
          'DIRECTOR_DELEGATED_PILOT',
          'ORACLE_ONLINE_PILOT_V1'
      );
    SELECT * INTO first_claim
      FROM autopilot.claim_next_task('sql-resume-source-worker', 60);
    UPDATE autopilot.task
       SET lease_until = now() - interval '1 second'
     WHERE task_id = source_created.task_id;
    PERFORM autopilot.reconcile_stale_tasks();
    SELECT * INTO second_claim
      FROM autopilot.claim_next_task('sql-resume-source-worker', 60);
    IF first_claim.lease_epoch <> 1 OR second_claim.lease_epoch <> 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_SOURCE_FENCING_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        source_created.task_id,
        'sql-resume-source-worker',
        second_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('c', 64),
        jsonb_build_object(
            'task_id', source_created.task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_SOURCE_COMPLETE_FAILED';
    END IF;

    UPDATE autopilot.online_pilot_state
       SET circuit_open = false,
           circuit_reason_code = NULL,
           last_task_id = source_created.task_id,
           last_task_key = 'phase3b-oracle-online-sql-resume-source',
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

    SELECT * INTO canary_registered
      FROM autopilot.register_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1',
          finding,
          source_created.task_id
      );
    SELECT * INTO canary_claim
      FROM autopilot.claim_next_task('sql-resume-canary-worker', 60);
    IF canary_claim.task_id <> canary_registered.canary_task_id
       OR canary_claim.lease_epoch <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_CANARY_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        canary_registered.canary_task_id,
        'sql-resume-canary-worker',
        canary_claim.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('d', 64),
        jsonb_build_object(
            'task_id', canary_registered.canary_task_id::text,
            'task_kind', 'AUTOPILOT_SMOKE_V1',
            'runtime', 'ORACLE_RESIDENT',
            'model_calls', 0,
            'production_mutation', false
        )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_CANARY_COMPLETE_FAILED';
    END IF;
    SELECT * INTO canary_evaluated
      FROM autopilot.evaluate_online_stale_recovery_canary(
          'online-stale-running-recovery-20260901-v1'
      );
    IF canary_evaluated.evaluation <> 'PASS'
       OR NOT canary_evaluated.passed
       OR NOT canary_evaluated.circuit_open THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_CANARY_EVALUATION_INVALID';
    END IF;

    SELECT * INTO gate_registered
      FROM autopilot.register_online_stale_resume_gate(
          'online-stale-running-pre-resume-20260901-v1',
          'online-stale-running-recovery-20260901-v1'
      );
    IF NOT gate_registered.created
       OR gate_registered.gate_status <> 'FIX_VALIDATED_NOT_DEPLOYED'
       OR gate_registered.ready
       OR NOT gate_registered.circuit_open
       OR gate_registered.proof_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_REGISTRATION_INVALID';
    END IF;

    SELECT * INTO gate_replayed
      FROM autopilot.register_online_stale_resume_gate(
          'online-stale-running-pre-resume-20260901-v1',
          'online-stale-running-recovery-20260901-v1'
      );
    IF gate_replayed.created
       OR gate_replayed.proof_sha256 <> gate_registered.proof_sha256
       OR gate_replayed.gate_status <> gate_registered.gate_status
       OR gate_replayed.ready THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_REPLAY_INVALID';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM autopilot.online_resume_gate
         WHERE singleton
           AND root_cause_code = 'DEPLOYMENT_INGRESS_RACE'
           AND fix_commit_sha =
               '44a9308106b0d06d3dc0cf25622a40342c56e6ff'
           AND gate_status = 'FIX_VALIDATED_NOT_DEPLOYED'
           AND deployment_revision_sha IS NULL
           AND NOT ready
           AND circuit_open_at_registration
           AND proof_json->'deployment_verified' = 'false'::jsonb
           AND proof_json->'circuit_remains_open' = 'true'::jsonb
           AND proof_json->'production_mutation' = 'false'::jsonb
           AND proof_json->'model_calls' = '0'::jsonb
           AND jsonb_array_length(proof_json->'fix_ci') = 5
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_RECEIPT_INVALID';
    END IF;

    UPDATE autopilot.online_pilot_state
       SET circuit_open = false,
           circuit_reason_code = NULL,
           updated_at = now()
     WHERE singleton;
    BEGIN
        PERFORM * FROM autopilot.register_online_stale_resume_gate(
            'online-stale-running-pre-resume-20260901-v1',
            'online-stale-running-recovery-20260901-v1'
        );
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM LIKE '%AUTOPILOT_RESUME_GATE_CIRCUIT_REQUIRED%' THEN
            circuit_rejected := true;
        ELSE
            RAISE;
        END IF;
    END;
    IF NOT circuit_rejected THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_ACCEPTED_CLOSED_CIRCUIT';
    END IF;

    IF (SELECT count(*) FROM autopilot.task
         WHERE status IN ('READY', 'RUNNING', 'WAITING_EXTERNAL')) <> 0
       OR (SELECT count(*) FROM autopilot.online_resume_gate) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_BOUNDARY_VIOLATED';
    END IF;
END $$;

ROLLBACK;
