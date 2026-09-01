\set ON_ERROR_STOP on
BEGIN;

-- Resolve the current task by the identity retained in singleton state.
-- created_at uses transaction time, so ordering same-transaction tasks by
-- created_at/task_id can select a random UUID instead of the latest tick task.

CREATE OR REPLACE FUNCTION autopilot.online_pilot_tick(
    p_observer_id text,
    p_min_interval_seconds integer DEFAULT 5,
    p_max_tasks_per_hour integer DEFAULT 720
)
RETURNS TABLE(
    action text,
    task_key text,
    task_status text,
    circuit_open boolean,
    finding_code text,
    created_count bigint,
    pass_count bigint,
    finding_count bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    state_row autopilot.online_pilot_state;
    latest autopilot.task;
    active_count integer;
    stale_count integer;
    recent_count integer;
    next_task_key text;
    created_row record;
    result_action text;
    result_finding text;
BEGIN
    IF p_observer_id IS NULL
       OR p_observer_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
       OR p_min_interval_seconds NOT BETWEEN 5 AND 60
       OR p_max_tasks_per_hour NOT BETWEEN 1 AND 720
       OR p_max_tasks_per_hour > (3600 / p_min_interval_seconds) THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_CONFIG_INVALID';
    END IF;

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_STATE_MISSING';
    END IF;

    IF state_row.circuit_open THEN
        result_action := 'CIRCUIT_OPEN';
        result_finding := state_row.circuit_reason_code;
    ELSE
        SELECT count(*) INTO stale_count
          FROM autopilot.task
         WHERE status = 'RUNNING' AND lease_until <= now();
        IF stale_count > 0 THEN
            PERFORM autopilot.open_online_pilot_circuit(
                'ONLINE_STALE_RUNNING',
                'online-stale-running',
                NULL,
                NULL,
                jsonb_build_object('stale_running', stale_count),
                'Reconcile the stale lease and prove fencing before resuming the online pilot.'
            );
            result_action := 'CIRCUIT_OPEN';
            result_finding := 'ONLINE_STALE_RUNNING';
        ELSE
            SELECT * INTO latest
              FROM autopilot.task
             WHERE task_id = state_row.last_task_id
               AND source = 'ORACLE_ONLINE_PILOT_V1';

            IF FOUND AND latest.status IN ('READY', 'RUNNING') THEN
                result_action := 'MONITORING';
            ELSIF FOUND AND latest.status = 'WAITING_EXTERNAL' THEN
                PERFORM autopilot.open_online_pilot_circuit(
                    'ONLINE_SMOKE_WAIT_INVALID',
                    'online-smoke-wait:' || latest.task_id::text,
                    latest.task_id,
                    latest.task_key,
                    jsonb_build_object(
                        'status', latest.status,
                        'attempts', latest.attempts,
                        'terminal_reason_code', latest.terminal_reason_code
                    ),
                    'Inspect the smoke capability transition; it must never enter WAITING_EXTERNAL.'
                );
                result_action := 'CIRCUIT_OPEN';
                result_finding := 'ONLINE_SMOKE_WAIT_INVALID';
            ELSIF FOUND AND (
                latest.status <> 'DONE'
                OR latest.attempts <> 1
                OR latest.terminal_reason_code <> 'ACCEPTANCE_EVIDENCE_RETAINED'
                OR latest.cost_actual_microusd <> 0
                OR latest.safe_summary_json->>'task_kind' <> 'AUTOPILOT_SMOKE_V1'
                OR latest.safe_summary_json->>'runtime' <> 'ORACLE_RESIDENT'
                OR latest.safe_summary_json->'model_calls' IS DISTINCT FROM '0'::jsonb
                OR latest.safe_summary_json->'production_mutation' IS DISTINCT FROM 'false'::jsonb
                OR NOT EXISTS (
                    SELECT 1 FROM autopilot.evidence
                     WHERE task_id = latest.task_id
                       AND evidence_class = 'SYNTHETIC_SHADOW_COMPLETION'
                       AND retained
                )
            ) THEN
                PERFORM autopilot.open_online_pilot_circuit(
                    'ONLINE_SMOKE_EVIDENCE_INVALID',
                    'online-smoke-invalid:' || latest.task_id::text,
                    latest.task_id,
                    latest.task_key,
                    jsonb_build_object(
                        'status', latest.status,
                        'attempts', latest.attempts,
                        'terminal_reason_code', latest.terminal_reason_code,
                        'cost_actual_microusd', latest.cost_actual_microusd,
                        'model_calls', latest.safe_summary_json->'model_calls',
                        'production_mutation', latest.safe_summary_json->'production_mutation'
                    ),
                    'Inspect retained evidence and the Oracle consumer before resuming task creation.'
                );
                result_action := 'CIRCUIT_OPEN';
                result_finding := 'ONLINE_SMOKE_EVIDENCE_INVALID';
            ELSE
                IF FOUND AND (
                    state_row.last_pass_at IS NULL
                    OR latest.completed_at > state_row.last_pass_at
                ) THEN
                    UPDATE autopilot.online_pilot_state AS pilot_state
                       SET last_pass_at = latest.completed_at,
                           pass_count = pilot_state.pass_count + 1,
                           updated_at = now()
                     WHERE singleton;
                    SELECT * INTO state_row
                      FROM autopilot.online_pilot_state
                     WHERE singleton;
                END IF;

                SELECT count(*) INTO active_count
                  FROM autopilot.task
                 WHERE status IN ('READY', 'RUNNING', 'WAITING_EXTERNAL');
                IF active_count > 0 THEN
                    result_action := 'QUEUE_BUSY';
                ELSIF state_row.last_created_at IS NOT NULL
                   AND state_row.last_created_at
                       > now() - make_interval(secs => p_min_interval_seconds) THEN
                    result_action := 'INTERVAL_HOLD';
                ELSE
                    SELECT count(*) INTO recent_count
                      FROM autopilot.task
                     WHERE source = 'ORACLE_ONLINE_PILOT_V1'
                       AND created_at > now() - interval '1 hour';
                    IF recent_count >= p_max_tasks_per_hour THEN
                        result_action := 'RATE_HOLD';
                    ELSE
                        next_task_key := format(
                            'phase3b-oracle-online-%s-%s',
                            to_char(clock_timestamp(), 'YYYYMMDD"T"HH24MISSMS"Z"'),
                            lpad((state_row.created_count + 1)::text, 8, '0')
                        );
                        SELECT * INTO created_row
                          FROM autopilot.create_shadow_task(
                              next_task_key,
                              'AUTOPILOT_SMOKE_V1',
                              '{}'::jsonb,
                              20,
                              0,
                              'DIRECTOR_DELEGATED_PILOT',
                              'ORACLE_ONLINE_PILOT_V1'
                          );
                        IF NOT created_row.created OR created_row.status <> 'READY' THEN
                            RAISE EXCEPTION 'AUTOPILOT_ONLINE_CREATE_INVALID';
                        END IF;
                        UPDATE autopilot.online_pilot_state AS pilot_state
                           SET last_task_id = created_row.task_id,
                               last_task_key = next_task_key,
                               last_created_at = now(),
                               created_count = pilot_state.created_count + 1,
                               updated_at = now()
                         WHERE singleton;
                        result_action := 'CREATED';
                        latest.task_key := next_task_key;
                        latest.status := 'READY';
                    END IF;
                END IF;
            END IF;
        END IF;
    END IF;

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton;
    RETURN QUERY SELECT
        result_action,
        COALESCE(latest.task_key, state_row.last_task_key),
        latest.status,
        state_row.circuit_open,
        COALESCE(result_finding, state_row.circuit_reason_code),
        state_row.created_count,
        state_row.pass_count,
        state_row.finding_count;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.online_pilot_tick(text,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.online_pilot_tick(text,integer,integer) TO autopilot_runtime;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0305_autopilot_online_pilot_identity')
ON CONFLICT DO NOTHING;

COMMIT;
