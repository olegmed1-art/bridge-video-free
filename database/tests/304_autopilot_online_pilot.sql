\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    first_tick record;
    second_tick record;
    failed_tick record;
    claimed record;
    safe_summary jsonb;
BEGIN
    IF has_table_privilege(
           'autopilot_runtime_principal', 'autopilot.online_pilot_state', 'SELECT'
       ) OR has_table_privilege(
           'autopilot_runtime_principal', 'autopilot.online_pilot_finding', 'INSERT'
       ) OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.online_pilot_tick(text,integer,integer)',
           'EXECUTE'
       ) OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.online_pilot_status()',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_RUNTIME_BOUNDARY_INVALID';
    END IF;

    SELECT * INTO first_tick
      FROM autopilot.online_pilot_tick('sql-online-observer', 5, 720);
    IF first_tick.action <> 'CREATED'
       OR first_tick.task_status <> 'READY'
       OR first_tick.circuit_open
       OR first_tick.created_count <> 1
       OR first_tick.pass_count <> 0
       OR first_tick.finding_count <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_FIRST_TICK_INVALID';
    END IF;

    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-online-worker-1', 60);
    IF claimed.goal_type <> 'AUTOPILOT_SMOKE_V1'
       OR claimed.cost_cap_microusd <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_CLAIM_INVALID';
    END IF;
    IF (SELECT task_key FROM autopilot.task WHERE task_id = claimed.task_id)
       <> first_tick.task_key THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_CLAIM_IDENTITY_INVALID';
    END IF;

    safe_summary := jsonb_build_object(
        'task_id', claimed.task_id::text,
        'task_kind', 'AUTOPILOT_SMOKE_V1',
        'runtime', 'ORACLE_RESIDENT',
        'production_mutation', false,
        'model_calls', 0
    );
    IF NOT autopilot.complete_task(
        claimed.task_id,
        'sql-online-worker-1',
        claimed.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION',
        repeat('a', 64),
        safe_summary
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_COMPLETION_REJECTED';
    END IF;

    SELECT * INTO second_tick
      FROM autopilot.online_pilot_tick('sql-online-observer', 5, 720);
    IF second_tick.action <> 'INTERVAL_HOLD'
       OR second_tick.circuit_open
       OR second_tick.pass_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_PASS_ACCOUNTING_INVALID';
    END IF;

    UPDATE autopilot.online_pilot_state
       SET last_created_at = now() - interval '10 seconds'
     WHERE singleton;
    SELECT * INTO second_tick
      FROM autopilot.online_pilot_tick('sql-online-observer', 5, 720);
    IF second_tick.action <> 'CREATED'
       OR second_tick.created_count <> 2
       OR second_tick.pass_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_SECOND_CREATE_INVALID';
    END IF;

    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-online-worker-2', 60);
    IF autopilot.fail_task(
        claimed.task_id,
        'sql-online-worker-2',
        claimed.lease_epoch,
        'SYNTHETIC_FORCED_FAILURE',
        false
    ) <> 'FAILED_CLOSED' THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_FORCED_FAILURE_INVALID';
    END IF;

    SELECT * INTO failed_tick
      FROM autopilot.online_pilot_tick('sql-online-observer', 5, 720);
    IF failed_tick.action <> 'CIRCUIT_OPEN'
       OR NOT failed_tick.circuit_open
       OR failed_tick.finding_code <> 'ONLINE_SMOKE_EVIDENCE_INVALID'
       OR failed_tick.finding_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_CIRCUIT_INVALID action=% open=% code=% findings=%',
            failed_tick.action, failed_tick.circuit_open,
            failed_tick.finding_code, failed_tick.finding_count;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM autopilot.online_pilot_finding
         WHERE task_id = claimed.task_id
           AND finding_code = 'ONLINE_SMOKE_EVIDENCE_INVALID'
           AND required_fix LIKE 'Inspect retained evidence%'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_FINDING_NOT_RETAINED';
    END IF;

    SELECT * INTO failed_tick
      FROM autopilot.online_pilot_tick('sql-online-observer', 5, 720);
    IF failed_tick.action <> 'CIRCUIT_OPEN'
       OR failed_tick.created_count <> 2
       OR failed_tick.finding_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_CIRCUIT_NOT_IDEMPOTENT';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.online_pilot_tick('unsafe-observer', 1, 720);
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_UNSAFE_INTERVAL_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_ONLINE_CONFIG_INVALID%' THEN RAISE; END IF;
    END;
END $$;

ROLLBACK;
