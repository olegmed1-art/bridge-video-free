\set ON_ERROR_STOP on
BEGIN;

-- School Autopilot Controller v1.5 — server-resident online shadow pilot.
-- The runtime principal still has no table access. It may only call a guarded
-- tick RPC that creates zero-cost AUTOPILOT_SMOKE_V1 tasks and opens a durable
-- circuit breaker on the first invalid terminal result.

CREATE TABLE autopilot.online_pilot_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    circuit_open boolean NOT NULL DEFAULT false,
    circuit_reason_code text,
    last_task_id uuid,
    last_task_key text,
    last_created_at timestamptz,
    last_pass_at timestamptz,
    created_count bigint NOT NULL DEFAULT 0 CHECK (created_count >= 0),
    pass_count bigint NOT NULL DEFAULT 0 CHECK (pass_count >= 0),
    finding_count bigint NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((NOT circuit_open AND circuit_reason_code IS NULL)
        OR (circuit_open AND circuit_reason_code ~ '^[A-Z][A-Z0-9_]{2,79}$')),
    CHECK (last_task_key IS NULL
        OR last_task_key ~ '^phase3b-oracle-online-[A-Za-z0-9._:-]{1,170}$')
);

CREATE TABLE autopilot.online_pilot_finding (
    finding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dedupe_key text NOT NULL UNIQUE
        CHECK (dedupe_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    finding_code text NOT NULL CHECK (finding_code ~ '^[A-Z][A-Z0-9_]{2,79}$'),
    severity text NOT NULL DEFAULT 'P1' CHECK (severity IN ('P0', 'P1', 'P2')),
    task_id uuid,
    task_key text,
    safe_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(safe_summary_json) = 'object'
            AND octet_length(safe_summary_json::text) <= 4096),
    required_fix text NOT NULL CHECK (length(required_fix) BETWEEN 1 AND 500),
    observed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO autopilot.online_pilot_state(singleton) VALUES (true)
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION autopilot.open_online_pilot_circuit(
    p_finding_code text,
    p_dedupe_key text,
    p_task_id uuid,
    p_task_key text,
    p_safe_summary jsonb,
    p_required_fix text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    inserted_count integer;
BEGIN
    IF p_finding_code IS NULL OR p_finding_code !~ '^[A-Z][A-Z0-9_]{2,79}$'
       OR p_dedupe_key IS NULL
       OR p_dedupe_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR jsonb_typeof(COALESCE(p_safe_summary, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_safe_summary, '{}'::jsonb)::text) > 4096
       OR length(COALESCE(p_required_fix, '')) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'AUTOPILOT_ONLINE_FINDING_INVALID';
    END IF;

    INSERT INTO autopilot.online_pilot_finding(
        dedupe_key, finding_code, task_id, task_key,
        safe_summary_json, required_fix
    ) VALUES (
        p_dedupe_key, p_finding_code, p_task_id, p_task_key,
        COALESCE(p_safe_summary, '{}'::jsonb), p_required_fix
    ) ON CONFLICT (dedupe_key) DO NOTHING;
    GET DIAGNOSTICS inserted_count = ROW_COUNT;

    UPDATE autopilot.online_pilot_state
       SET circuit_open = true,
           circuit_reason_code = p_finding_code,
           finding_count = finding_count + inserted_count,
           updated_at = now()
     WHERE singleton;
END;
$$;

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
             WHERE source = 'ORACLE_ONLINE_PILOT_V1'
             ORDER BY created_at DESC, task_id DESC
             LIMIT 1;

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

CREATE OR REPLACE FUNCTION autopilot.online_pilot_status()
RETURNS TABLE(
    observer_mode text,
    circuit_open boolean,
    circuit_reason_code text,
    last_task_key text,
    last_task_status text,
    last_created_at timestamptz,
    last_pass_at timestamptz,
    created_count bigint,
    pass_count bigint,
    finding_count bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
    SELECT
        'SHADOW_ONLY'::text,
        s.circuit_open,
        s.circuit_reason_code,
        s.last_task_key,
        t.status,
        s.last_created_at,
        s.last_pass_at,
        s.created_count,
        s.pass_count,
        s.finding_count
      FROM autopilot.online_pilot_state s
      LEFT JOIN autopilot.task t ON t.task_id = s.last_task_id
     WHERE s.singleton;
$$;

REVOKE ALL ON TABLE autopilot.online_pilot_state FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.online_pilot_finding FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.open_online_pilot_circuit(text,text,uuid,text,jsonb,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.online_pilot_tick(text,integer,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.online_pilot_status() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.online_pilot_tick(text,integer,integer) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.online_pilot_status() TO autopilot_runtime;

COMMENT ON TABLE autopilot.online_pilot_finding IS
'Durable, token-free required-fix ledger for the server-resident SHADOW_ONLY online pilot.';

COMMENT ON SCHEMA autopilot IS
'School Autopilot canonical orchestration state. v1.5 adds a guarded server-resident online shadow pilot with a durable circuit breaker.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0304_autopilot_online_pilot')
ON CONFLICT DO NOTHING;

COMMIT;
