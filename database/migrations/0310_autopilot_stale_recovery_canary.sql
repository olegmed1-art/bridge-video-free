\set ON_ERROR_STOP on
BEGIN;

-- One-shot, audited recovery canary for an ONLINE_STALE_RUNNING circuit.
-- This migration never closes the circuit. It proves the exact two-epoch fencing
-- chain, admits exactly one zero-cost smoke, and retains an immutable receipt.
CREATE TABLE autopilot.online_recovery_canary (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    recovery_key text NOT NULL UNIQUE
        CHECK (recovery_key = 'online-stale-running-recovery-20260901-v1'),
    finding_id bigint NOT NULL UNIQUE
        REFERENCES autopilot.online_pilot_finding(finding_id),
    source_task_id uuid NOT NULL UNIQUE REFERENCES autopilot.task(task_id),
    canary_task_id uuid NOT NULL UNIQUE REFERENCES autopilot.task(task_id),
    proof_sha256 text NOT NULL CHECK (proof_sha256 ~ '^[0-9a-f]{64}$'),
    proof_json jsonb NOT NULL CHECK (
        jsonb_typeof(proof_json) = 'object'
        AND octet_length(proof_json::text) <= 8192
    ),
    evaluation_status text NOT NULL DEFAULT 'CREATED'
        CHECK (evaluation_status IN ('CREATED', 'PASS', 'NON_PASS')),
    evaluation_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(evaluation_summary_json) = 'object'
        AND octet_length(evaluation_summary_json::text) <= 8192
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    evaluated_at timestamptz
);

REVOKE ALL ON TABLE autopilot.online_recovery_canary FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.online_recovery_canary FROM autopilot_runtime;

CREATE OR REPLACE FUNCTION autopilot.register_online_stale_recovery_canary(
    p_recovery_key text,
    p_finding_id bigint,
    p_source_task_id uuid
)
RETURNS TABLE(
    recovery_key text,
    canary_task_id uuid,
    task_status text,
    created boolean,
    circuit_open boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    state_row autopilot.online_pilot_state;
    finding_row autopilot.online_pilot_finding;
    source_row autopilot.task;
    existing_receipt autopilot.online_recovery_canary;
    inserted_task autopilot.task;
    proof jsonb;
    proof_hash text;
    active_count integer;
BEGIN
    IF p_recovery_key <> 'online-stale-running-recovery-20260901-v1'
       OR p_finding_id IS NULL
       OR p_source_task_id IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_REQUEST_NOT_APPROVED';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot:online_stale_recovery_canary', 20260901)
    );

    SELECT * INTO existing_receipt
      FROM autopilot.online_recovery_canary
     WHERE singleton;
    IF FOUND THEN
        IF existing_receipt.recovery_key <> p_recovery_key
           OR existing_receipt.finding_id <> p_finding_id
           OR existing_receipt.source_task_id <> p_source_task_id THEN
            RAISE EXCEPTION 'AUTOPILOT_RECOVERY_SINGLETON_CONFLICT';
        END IF;
        SELECT * INTO inserted_task
          FROM autopilot.task
         WHERE task_id = existing_receipt.canary_task_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CANARY_MISSING';
        END IF;
        SELECT * INTO state_row
          FROM autopilot.online_pilot_state
         WHERE singleton;
        IF NOT state_row.circuit_open
           OR state_row.circuit_reason_code <> 'ONLINE_STALE_RUNNING' THEN
            RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CIRCUIT_CHANGED';
        END IF;
        RETURN QUERY SELECT
            existing_receipt.recovery_key,
            existing_receipt.canary_task_id,
            inserted_task.status,
            false,
            state_row.circuit_open;
        RETURN;
    END IF;

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton
     FOR UPDATE;
    IF NOT FOUND
       OR NOT state_row.circuit_open
       OR state_row.circuit_reason_code <> 'ONLINE_STALE_RUNNING'
       OR state_row.last_task_id IS DISTINCT FROM p_source_task_id
       OR state_row.last_task_key IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CIRCUIT_PROOF_INVALID';
    END IF;

    SELECT * INTO finding_row
      FROM autopilot.online_pilot_finding
     WHERE finding_id = p_finding_id;
    IF NOT FOUND
       OR finding_row.finding_code <> 'ONLINE_STALE_RUNNING'
       OR finding_row.dedupe_key <> 'online-stale-running'
       OR finding_row.severity <> 'P1'
       OR finding_row.task_id IS NOT NULL
       OR finding_row.task_key IS NOT NULL
       OR finding_row.safe_summary_json <> '{"stale_running":1}'::jsonb
       OR finding_row.required_fix
          <> 'Reconcile the stale lease and prove fencing before resuming the online pilot.' THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_FINDING_PROOF_INVALID';
    END IF;

    SELECT * INTO source_row
      FROM autopilot.task
     WHERE task_id = p_source_task_id;
    IF NOT FOUND
       OR source_row.task_key <> state_row.last_task_key
       OR source_row.source <> 'ORACLE_ONLINE_PILOT_V1'
       OR source_row.created_by <> 'DIRECTOR_DELEGATED_PILOT'
       OR source_row.goal_type <> 'AUTOPILOT_SMOKE_V1'
       OR source_row.goal_json <> '{}'::jsonb
       OR source_row.current_step_key <> 'shadow.noop'
       OR source_row.allowed_capabilities_json <> '["shadow.noop"]'::jsonb
       OR source_row.status <> 'DONE'
       OR source_row.attempts <> 2
       OR source_row.lease_epoch <> 2
       OR source_row.terminal_reason_code <> 'ACCEPTANCE_EVIDENCE_RETAINED'
       OR source_row.model_turn_cap <> 0
       OR source_row.cost_cap_microusd <> 0
       OR source_row.cost_reserved_microusd <> 0
       OR source_row.cost_actual_microusd <> 0
       OR source_row.safe_summary_json->>'task_kind' <> 'AUTOPILOT_SMOKE_V1'
       OR source_row.safe_summary_json->>'runtime' <> 'ORACLE_RESIDENT'
       OR source_row.safe_summary_json->'model_calls' IS DISTINCT FROM '0'::jsonb
       OR source_row.safe_summary_json->'production_mutation'
          IS DISTINCT FROM 'false'::jsonb THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_SOURCE_TASK_PROOF_INVALID';
    END IF;

    SELECT count(*) INTO active_count
      FROM autopilot.task
     WHERE status NOT IN (
         'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
         'DONE', 'CANCELLED'
     );
    IF active_count <> 0
       OR EXISTS (
           SELECT 1 FROM autopilot.task
            WHERE status = 'RUNNING' AND lease_until <= now()
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_QUEUE_NOT_EMPTY';
    END IF;

    IF (SELECT count(*) FROM autopilot.task_event
         WHERE task_id = p_source_task_id) <> 5
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task_event
            WHERE task_id = p_source_task_id AND sequence_no = 1
              AND event_type = 'TASK_READY'
              AND state_from = 'NEW' AND state_to = 'READY'
       )
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task_event
            WHERE task_id = p_source_task_id AND sequence_no = 2
              AND event_type = 'TASK_CLAIMED'
              AND state_from = 'READY' AND state_to = 'RUNNING'
              AND payload_json->'lease_epoch' = '1'::jsonb
       )
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task_event
            WHERE task_id = p_source_task_id AND sequence_no = 3
              AND event_type = 'STALE_LEASE_RECOVERED'
              AND state_from = 'RUNNING' AND state_to = 'READY'
              AND actor_type = 'SYSTEM' AND actor_ref = 'stale-reconciler'
              AND idempotency_key =
                  'stale-requeue:' || p_source_task_id::text || ':1'
       )
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task_event
            WHERE task_id = p_source_task_id AND sequence_no = 4
              AND event_type = 'TASK_CLAIMED'
              AND state_from = 'READY' AND state_to = 'RUNNING'
              AND payload_json->'lease_epoch' = '2'::jsonb
       )
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task_event
            WHERE task_id = p_source_task_id AND sequence_no = 5
              AND event_type = 'TASK_DONE'
              AND state_from = 'RUNNING' AND state_to = 'DONE'
              AND payload_json->>'evidence_class' =
                  'SYNTHETIC_SHADOW_COMPLETION'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_EVENT_CHAIN_INVALID';
    END IF;

    IF (SELECT count(*) FROM autopilot.step_attempt
         WHERE task_id = p_source_task_id) <> 2
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.step_attempt
            WHERE task_id = p_source_task_id
              AND attempt_no = 1
              AND capability_name = 'shadow.noop'
              AND lease_epoch = 1
              AND status = 'FAILED_CLOSED'
       )
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.step_attempt
            WHERE task_id = p_source_task_id
              AND attempt_no = 2
              AND capability_name = 'shadow.noop'
              AND lease_epoch = 2
              AND status = 'COMPLETED'
       )
       OR (SELECT count(*) FROM autopilot.evidence
            WHERE task_id = p_source_task_id
              AND evidence_class = 'SYNTHETIC_SHADOW_COMPLETION'
              AND retained) <> 1
       OR NOT EXISTS (
           SELECT 1
             FROM autopilot.evidence AS evidence
             JOIN autopilot.step_attempt AS attempt
               ON attempt.step_attempt_id = evidence.step_attempt_id
            WHERE evidence.task_id = p_source_task_id
              AND evidence.evidence_class = 'SYNTHETIC_SHADOW_COMPLETION'
              AND evidence.retained
              AND attempt.attempt_no = 2
              AND attempt.lease_epoch = 2
              AND attempt.status = 'COMPLETED'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_FENCING_PROOF_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_key = 'recovery-canary-online-stale-running-20260901-v1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CANARY_ORPHAN';
    END IF;

    proof := jsonb_build_object(
        'schema', 'ONLINE_STALE_RECOVERY_PROOF_V1',
        'recovery_key', p_recovery_key,
        'finding_id', p_finding_id,
        'finding_code', finding_row.finding_code,
        'source_task_id', p_source_task_id,
        'source_task_key', source_row.task_key,
        'source_attempts', source_row.attempts,
        'source_lease_epoch', source_row.lease_epoch,
        'task_event_count', 5,
        'step_attempt_count', 2,
        'retained_evidence_count', 1,
        'active_task_count', 0,
        'fencing_proved', true,
        'circuit_remains_open', true,
        'model_calls', 0,
        'cost_actual_microusd', 0,
        'production_mutation', false
    );
    proof_hash := encode(
        public.digest(convert_to(proof::text, 'UTF8'), 'sha256'),
        'hex'
    );

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json,
        priority, max_attempts, model_turn_cap,
        cost_cap_microusd, created_by, source
    ) VALUES (
        'recovery-canary-online-stale-running-20260901-v1',
        'AUTOPILOT_SMOKE_V1',
        '{}'::jsonb,
        'READY',
        'shadow.noop',
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'recovery_key', p_recovery_key,
            'finding_id', p_finding_id,
            'source_task_id', p_source_task_id,
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
        'DIRECTOR_APPROVED_RECOVERY_CANARY',
        'ORACLE_RECOVERY_CANARY_V1'
    )
    RETURNING * INTO inserted_task;

    PERFORM autopilot.record_event(
        inserted_task.task_id,
        'TASK_READY',
        'NEW',
        'READY',
        jsonb_build_object(
            'goal_type', inserted_task.goal_type,
            'recovery_key', p_recovery_key,
            'finding_id', p_finding_id,
            'source_task_id', p_source_task_id,
            'circuit_remains_open', true
        ),
        'SYSTEM',
        'online-stale-recovery-canary-v1',
        'recovery-canary:' || p_recovery_key
    );

    INSERT INTO autopilot.online_recovery_canary (
        recovery_key, finding_id, source_task_id, canary_task_id,
        proof_sha256, proof_json
    ) VALUES (
        p_recovery_key, p_finding_id, p_source_task_id,
        inserted_task.task_id, proof_hash, proof
    );

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton;
    IF NOT state_row.circuit_open
       OR state_row.circuit_reason_code <> 'ONLINE_STALE_RUNNING' THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_CIRCUIT_CHANGED';
    END IF;

    RETURN QUERY SELECT
        p_recovery_key,
        inserted_task.task_id,
        inserted_task.status,
        true,
        state_row.circuit_open;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.evaluate_online_stale_recovery_canary(
    p_recovery_key text
)
RETURNS TABLE(
    canary_task_id uuid,
    task_status text,
    evaluation text,
    passed boolean,
    circuit_open boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    receipt autopilot.online_recovery_canary;
    canary autopilot.task;
    state_row autopilot.online_pilot_state;
    pass_ok boolean;
    summary jsonb;
    retained_count integer;
BEGIN
    IF p_recovery_key <> 'online-stale-running-recovery-20260901-v1' THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_REQUEST_NOT_APPROVED';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot:online_stale_recovery_canary', 20260901)
    );

    SELECT * INTO receipt
      FROM autopilot.online_recovery_canary
     WHERE singleton
       AND recovery_key = p_recovery_key
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_RECOVERY_RECEIPT_MISSING';
    END IF;

    SELECT * INTO canary
      FROM autopilot.task
     WHERE task_id = receipt.canary_task_id;
    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton;

    IF receipt.evaluation_status <> 'CREATED' THEN
        RETURN QUERY SELECT
            receipt.canary_task_id,
            canary.status,
            receipt.evaluation_status,
            receipt.evaluation_status = 'PASS',
            state_row.circuit_open;
        RETURN;
    END IF;

    IF canary.status NOT IN (
        'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
        'DONE', 'CANCELLED'
    ) THEN
        RETURN QUERY SELECT
            receipt.canary_task_id,
            canary.status,
            'MONITORING'::text,
            false,
            state_row.circuit_open;
        RETURN;
    END IF;

    SELECT count(*) INTO retained_count
      FROM autopilot.evidence
     WHERE task_id = canary.task_id
       AND evidence_class = 'SYNTHETIC_SHADOW_COMPLETION'
       AND retained;

    pass_ok :=
        state_row.circuit_open
        AND state_row.circuit_reason_code = 'ONLINE_STALE_RUNNING'
        AND canary.task_key =
            'recovery-canary-online-stale-running-20260901-v1'
        AND canary.source = 'ORACLE_RECOVERY_CANARY_V1'
        AND canary.created_by = 'DIRECTOR_APPROVED_RECOVERY_CANARY'
        AND canary.goal_type = 'AUTOPILOT_SMOKE_V1'
        AND canary.status = 'DONE'
        AND canary.attempts = 1
        AND canary.max_attempts = 1
        AND canary.lease_epoch = 1
        AND canary.terminal_reason_code =
            'ACCEPTANCE_EVIDENCE_RETAINED'
        AND canary.model_turn_cap = 0
        AND canary.cost_cap_microusd = 0
        AND canary.cost_reserved_microusd = 0
        AND canary.cost_actual_microusd = 0
        AND canary.safe_summary_json->>'task_kind' =
            'AUTOPILOT_SMOKE_V1'
        AND canary.safe_summary_json->>'runtime' = 'ORACLE_RESIDENT'
        AND canary.safe_summary_json->'model_calls'
            IS NOT DISTINCT FROM '0'::jsonb
        AND canary.safe_summary_json->'production_mutation'
            IS NOT DISTINCT FROM 'false'::jsonb
        AND retained_count = 1
        AND (SELECT count(*) FROM autopilot.step_attempt
              WHERE task_id = canary.task_id) = 1
        AND EXISTS (
            SELECT 1 FROM autopilot.step_attempt
             WHERE task_id = canary.task_id
               AND attempt_no = 1
               AND capability_name = 'shadow.noop'
               AND lease_epoch = 1
               AND status = 'COMPLETED'
        )
        AND (SELECT count(*) FROM autopilot.task_event
              WHERE task_id = canary.task_id) = 3
        AND EXISTS (
            SELECT 1 FROM autopilot.task_event
             WHERE task_id = canary.task_id
               AND sequence_no = 1
               AND event_type = 'TASK_READY'
               AND state_from = 'NEW' AND state_to = 'READY'
        )
        AND EXISTS (
            SELECT 1 FROM autopilot.task_event
             WHERE task_id = canary.task_id
               AND sequence_no = 2
               AND event_type = 'TASK_CLAIMED'
               AND state_from = 'READY' AND state_to = 'RUNNING'
               AND payload_json->'lease_epoch' = '1'::jsonb
        )
        AND EXISTS (
            SELECT 1 FROM autopilot.task_event
             WHERE task_id = canary.task_id
               AND sequence_no = 3
               AND event_type = 'TASK_DONE'
               AND state_from = 'RUNNING' AND state_to = 'DONE'
               AND payload_json->>'evidence_class' =
                   'SYNTHETIC_SHADOW_COMPLETION'
        );

    summary := jsonb_build_object(
        'schema', 'ONLINE_STALE_RECOVERY_CANARY_RESULT_V1',
        'canary_task_id', canary.task_id,
        'task_status', canary.status,
        'attempts', canary.attempts,
        'max_attempts', canary.max_attempts,
        'lease_epoch', canary.lease_epoch,
        'terminal_reason_code', canary.terminal_reason_code,
        'retained_evidence_count', retained_count,
        'model_calls', canary.safe_summary_json->'model_calls',
        'cost_actual_microusd', canary.cost_actual_microusd,
        'production_mutation',
            canary.safe_summary_json->'production_mutation',
        'circuit_open', state_row.circuit_open,
        'circuit_reason_code', state_row.circuit_reason_code,
        'passed', pass_ok
    );

    UPDATE autopilot.online_recovery_canary
       SET evaluation_status = CASE WHEN pass_ok THEN 'PASS' ELSE 'NON_PASS' END,
           evaluation_summary_json = summary,
           evaluated_at = now()
     WHERE singleton;

    RETURN QUERY SELECT
        canary.task_id,
        canary.status,
        CASE WHEN pass_ok THEN 'PASS' ELSE 'NON_PASS' END,
        pass_ok,
        state_row.circuit_open;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.register_online_stale_recovery_canary(
    text,bigint,uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.evaluate_online_stale_recovery_canary(text)
    FROM PUBLIC;

COMMENT ON TABLE autopilot.online_recovery_canary IS
'Singleton audit receipt for one ONLINE_STALE_RUNNING recovery canary; it never closes the online pilot circuit.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0310_autopilot_stale_recovery_canary')
ON CONFLICT DO NOTHING;

COMMIT;
