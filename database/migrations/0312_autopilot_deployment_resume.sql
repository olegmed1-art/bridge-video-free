\set ON_ERROR_STOP on
BEGIN;

-- One-shot, owner-only transition from the reviewed ONLINE_STALE_RUNNING
-- incident to the already deployed SHADOW_ONLY observer.  The database
-- transition is atomic; the Oracle-local marker is removed separately only
-- after the receipt below is visible through the read-only status RPC.
CREATE TABLE autopilot.online_resume_receipt (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    resume_key text NOT NULL UNIQUE CHECK (
        resume_key = 'online-stale-running-deployment-resume-20260902-v1'
    ),
    gate_key text NOT NULL UNIQUE
        REFERENCES autopilot.online_resume_gate(gate_key),
    recovery_key text NOT NULL UNIQUE
        REFERENCES autopilot.online_recovery_canary(recovery_key),
    deployment_revision_sha text NOT NULL CHECK (
        deployment_revision_sha =
            '9064044d4c5b85803c6778060dff4843111ab888'
    ),
    staging_run_id bigint NOT NULL CHECK (staging_run_id = 33586404964),
    activation_run_id bigint NOT NULL CHECK (activation_run_id = 33586501046),
    deployment_canary_task_id uuid NOT NULL UNIQUE
        REFERENCES autopilot.task(task_id),
    resume_status text NOT NULL CHECK (
        resume_status = 'RESUMED_SHADOW_ONLY'
    ),
    circuit_reason_before text NOT NULL CHECK (
        circuit_reason_before = 'ONLINE_STALE_RUNNING'
    ),
    circuit_open_after boolean NOT NULL DEFAULT false CHECK (
        NOT circuit_open_after
    ),
    proof_sha256 text NOT NULL CHECK (proof_sha256 ~ '^[0-9a-f]{64}$'),
    proof_json jsonb NOT NULL CHECK (
        jsonb_typeof(proof_json) = 'object'
        AND octet_length(proof_json::text) <= 8192
    ),
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON TABLE autopilot.online_resume_receipt FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.online_resume_receipt FROM autopilot_runtime;

CREATE OR REPLACE FUNCTION autopilot.resume_online_pilot_after_deployment(
    p_resume_key text,
    p_deployment_revision_sha text,
    p_staging_run_id bigint,
    p_activation_run_id bigint,
    p_deployment_canary_task_id uuid
)
RETURNS TABLE(
    resume_key text,
    resume_status text,
    deployment_revision_sha text,
    circuit_open boolean,
    online_created_count bigint,
    online_pass_count bigint,
    receipt_proof_sha256 text,
    created boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    state_row autopilot.online_pilot_state;
    gate_row autopilot.online_resume_gate;
    recovery_row autopilot.online_recovery_canary;
    deployment_canary autopilot.task;
    receipt_row autopilot.online_resume_receipt;
    active_count integer;
    stale_count integer;
    retained_count integer;
    step_count integer;
    event_count integer;
    updated_count integer;
    proof jsonb;
    proof_hash text;
BEGIN
    IF p_resume_key <>
            'online-stale-running-deployment-resume-20260902-v1'
       OR p_deployment_revision_sha <>
            '9064044d4c5b85803c6778060dff4843111ab888'
       OR p_staging_run_id <> 33586404964
       OR p_activation_run_id <> 33586501046
       OR p_deployment_canary_task_id IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_REQUEST_NOT_APPROVED';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot:deployment_resume', 20260902)
    );

    SELECT * INTO receipt_row
      FROM autopilot.online_resume_receipt
     WHERE singleton;
    IF FOUND THEN
        IF receipt_row.resume_key <> p_resume_key
           OR receipt_row.deployment_revision_sha <>
                p_deployment_revision_sha
           OR receipt_row.staging_run_id <> p_staging_run_id
           OR receipt_row.activation_run_id <> p_activation_run_id
           OR receipt_row.deployment_canary_task_id <>
                p_deployment_canary_task_id THEN
            RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_REPLAY_MISMATCH';
        END IF;
        SELECT * INTO state_row
          FROM autopilot.online_pilot_state
         WHERE singleton;
        RETURN QUERY SELECT
            receipt_row.resume_key,
            receipt_row.resume_status,
            receipt_row.deployment_revision_sha,
            state_row.circuit_open,
            state_row.created_count,
            state_row.pass_count,
            receipt_row.proof_sha256,
            false;
        RETURN;
    END IF;

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton
     FOR UPDATE;
    IF NOT FOUND
       OR NOT state_row.circuit_open
       OR state_row.circuit_reason_code <> 'ONLINE_STALE_RUNNING'
       OR state_row.last_task_id IS NULL
       OR state_row.last_task_key IS NULL
       OR state_row.created_count <> state_row.pass_count + 1
       OR state_row.finding_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_CIRCUIT_INVALID';
    END IF;

    SELECT * INTO gate_row
      FROM autopilot.online_resume_gate
     WHERE singleton
       AND gate_key = 'online-stale-running-pre-resume-20260901-v1';
    IF NOT FOUND
       OR gate_row.recovery_key <>
            'online-stale-running-recovery-20260901-v1'
       OR gate_row.root_cause_code <> 'DEPLOYMENT_INGRESS_RACE'
       OR gate_row.fix_commit_sha <>
            '44a9308106b0d06d3dc0cf25622a40342c56e6ff'
       OR gate_row.gate_status <> 'FIX_VALIDATED_NOT_DEPLOYED'
       OR gate_row.deployment_revision_sha IS NOT NULL
       OR gate_row.ready
       OR NOT gate_row.circuit_open_at_registration
       OR gate_row.proof_json->'deployment_verified'
            IS DISTINCT FROM 'false'::jsonb
       OR gate_row.proof_json->'circuit_remains_open'
            IS DISTINCT FROM 'true'::jsonb
       OR gate_row.proof_sha256 <>
            encode(
                public.digest(
                    convert_to(gate_row.proof_json::text, 'UTF8'),
                    'sha256'
                ),
                'hex'
            ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_GATE_INVALID';
    END IF;

    SELECT * INTO recovery_row
      FROM autopilot.online_recovery_canary
     WHERE singleton
       AND recovery_key = gate_row.recovery_key;
    IF NOT FOUND
       OR recovery_row.evaluation_status <> 'PASS'
       OR recovery_row.source_task_id <> state_row.last_task_id
       OR recovery_row.proof_json->'fencing_proved'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.proof_json->'circuit_remains_open'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.evaluation_summary_json->'passed'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.evaluation_summary_json->'circuit_open'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.proof_sha256 <>
            encode(
                public.digest(
                    convert_to(recovery_row.proof_json::text, 'UTF8'),
                    'sha256'
                ),
                'hex'
            ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_RECOVERY_INVALID';
    END IF;

    SELECT * INTO deployment_canary
      FROM autopilot.task
     WHERE task_id = p_deployment_canary_task_id;
    SELECT count(*) INTO retained_count
      FROM autopilot.evidence
     WHERE task_id = p_deployment_canary_task_id
       AND evidence_class = 'SYNTHETIC_SHADOW_COMPLETION'
       AND retained;
    SELECT count(*) INTO step_count
      FROM autopilot.step_attempt
     WHERE task_id = p_deployment_canary_task_id;
    SELECT count(*) INTO event_count
      FROM autopilot.task_event
     WHERE task_id = p_deployment_canary_task_id;

    IF deployment_canary.task_id IS NULL
       OR deployment_canary.task_key !~
            '^deployment-canary-autopilot-race-fix-[A-Za-z0-9._:-]{1,120}$'
       OR deployment_canary.source <> 'ORACLE_DEPLOYMENT_CANARY_V1'
       OR deployment_canary.created_by <>
            'DIRECTOR_APPROVED_DEPLOYMENT_CANARY'
       OR deployment_canary.goal_type <> 'AUTOPILOT_SMOKE_V1'
       OR deployment_canary.goal_json <> '{}'::jsonb
       OR deployment_canary.current_step_key <> 'shadow.noop'
       OR deployment_canary.allowed_capabilities_json <>
            '["shadow.noop"]'::jsonb
       OR deployment_canary.status <> 'DONE'
       OR deployment_canary.attempts <> 1
       OR deployment_canary.max_attempts <> 1
       OR deployment_canary.lease_epoch <> 1
       OR deployment_canary.terminal_reason_code <>
            'ACCEPTANCE_EVIDENCE_RETAINED'
       OR deployment_canary.model_turn_cap <> 0
       OR deployment_canary.cost_cap_microusd <> 0
       OR deployment_canary.cost_reserved_microusd <> 0
       OR deployment_canary.cost_actual_microusd <> 0
       OR deployment_canary.safe_summary_json->>'task_kind' <>
            'AUTOPILOT_SMOKE_V1'
       OR deployment_canary.safe_summary_json->>'runtime' <>
            'ORACLE_RESIDENT'
       OR deployment_canary.safe_summary_json->'model_calls'
            IS DISTINCT FROM '0'::jsonb
       OR deployment_canary.safe_summary_json->'production_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR deployment_canary.acceptance_contract_json->>'deployed_revision'
            <> p_deployment_revision_sha
       OR deployment_canary.acceptance_contract_json->'activation_run_id'
            IS DISTINCT FROM to_jsonb(p_activation_run_id)
       OR deployment_canary.acceptance_contract_json->'circuit_must_remain_open'
            IS DISTINCT FROM 'true'::jsonb
       OR deployment_canary.acceptance_contract_json->'model_calls'
            IS DISTINCT FROM '0'::jsonb
       OR deployment_canary.acceptance_contract_json->'cost_actual_microusd'
            IS DISTINCT FROM '0'::jsonb
       OR deployment_canary.acceptance_contract_json->'production_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR deployment_canary.acceptance_contract_json->'max_attempts'
            IS DISTINCT FROM '1'::jsonb
       OR deployment_canary.acceptance_contract_json->'retained_evidence_required'
            IS DISTINCT FROM 'true'::jsonb
       OR retained_count <> 1
       OR step_count <> 1
       OR event_count <> 3
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.step_attempt
             WHERE task_id = p_deployment_canary_task_id
               AND attempt_no = 1
               AND capability_name = 'shadow.noop'
               AND lease_epoch = 1
               AND status = 'COMPLETED'
       )
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.task_event
             WHERE task_id = p_deployment_canary_task_id
               AND sequence_no = 1
               AND event_type = 'TASK_READY'
               AND state_from = 'NEW' AND state_to = 'READY'
       )
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.task_event
             WHERE task_id = p_deployment_canary_task_id
               AND sequence_no = 2
               AND event_type = 'TASK_CLAIMED'
               AND state_from = 'READY' AND state_to = 'RUNNING'
               AND payload_json->'lease_epoch' = '1'::jsonb
       )
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.task_event
             WHERE task_id = p_deployment_canary_task_id
               AND sequence_no = 3
               AND event_type = 'TASK_DONE'
               AND state_from = 'RUNNING' AND state_to = 'DONE'
               AND payload_json->>'evidence_class' =
                    'SYNTHETIC_SHADOW_COMPLETION'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_CANARY_INVALID';
    END IF;

    SELECT count(*) INTO active_count
      FROM autopilot.task
     WHERE status NOT IN (
         'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
         'DONE', 'CANCELLED'
     );
    SELECT count(*) INTO stale_count
      FROM autopilot.task
     WHERE status = 'RUNNING' AND lease_until <= now();
    IF active_count <> 0 OR stale_count <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_QUEUE_NOT_IDLE';
    END IF;

    proof := jsonb_build_object(
        'schema', 'ONLINE_DEPLOYMENT_RESUME_RECEIPT_V1',
        'resume_key', p_resume_key,
        'gate_key', gate_row.gate_key,
        'gate_proof_sha256', gate_row.proof_sha256,
        'recovery_key', recovery_row.recovery_key,
        'recovery_proof_sha256', recovery_row.proof_sha256,
        'root_cause_code', gate_row.root_cause_code,
        'fix_commit_sha', gate_row.fix_commit_sha,
        'deployment_revision_sha', p_deployment_revision_sha,
        'staging_run_id', p_staging_run_id,
        'activation_run_id', p_activation_run_id,
        'deployment_canary_task_id', p_deployment_canary_task_id,
        'deployment_canary_completed_at', deployment_canary.completed_at,
        'deployment_verified', true,
        'resume_status', 'RESUMED_SHADOW_ONLY',
        'resume_scope', 'SHADOW_ONLY',
        'circuit_reason_before', state_row.circuit_reason_code,
        'circuit_open_before', true,
        'circuit_open_after', false,
        'online_created_count_before', state_row.created_count,
        'online_pass_count_before', state_row.pass_count,
        'finding_count_before', state_row.finding_count,
        'active_task_count', active_count,
        'stale_running_count', stale_count,
        'retained_deployment_canary_evidence_count', retained_count,
        'model_calls', 0,
        'cost_actual_microusd', 0,
        'production_mutation', false,
        'oracle_instance_stop', false
    );
    proof_hash := encode(
        public.digest(convert_to(proof::text, 'UTF8'), 'sha256'),
        'hex'
    );

    INSERT INTO autopilot.online_resume_receipt(
        resume_key, gate_key, recovery_key,
        deployment_revision_sha, staging_run_id, activation_run_id,
        deployment_canary_task_id, resume_status,
        circuit_reason_before, circuit_open_after,
        proof_sha256, proof_json
    ) VALUES (
        p_resume_key, gate_row.gate_key, recovery_row.recovery_key,
        p_deployment_revision_sha, p_staging_run_id, p_activation_run_id,
        p_deployment_canary_task_id, 'RESUMED_SHADOW_ONLY',
        state_row.circuit_reason_code, false,
        proof_hash, proof
    );

    UPDATE autopilot.online_pilot_state
       SET circuit_open = false,
           circuit_reason_code = NULL,
           last_task_id = NULL,
           last_task_key = NULL,
           last_created_at = NULL,
           updated_at = now()
     WHERE singleton
       AND circuit_open
       AND circuit_reason_code = 'ONLINE_STALE_RUNNING';
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_UPDATE_FAILED';
    END IF;

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton;
    IF state_row.circuit_open OR state_row.circuit_reason_code IS NOT NULL
       OR state_row.last_task_id IS NOT NULL
       OR state_row.last_task_key IS NOT NULL
       OR state_row.last_created_at IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_DEPLOYMENT_RESUME_POSTCONDITION_FAILED';
    END IF;

    SELECT * INTO receipt_row
      FROM autopilot.online_resume_receipt
     WHERE singleton;
    RETURN QUERY SELECT
        receipt_row.resume_key,
        receipt_row.resume_status,
        receipt_row.deployment_revision_sha,
        state_row.circuit_open,
        state_row.created_count,
        state_row.pass_count,
        receipt_row.proof_sha256,
        true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.online_resume_status()
RETURNS TABLE(
    resume_status text,
    deployment_revision_sha text,
    staging_run_id bigint,
    activation_run_id bigint,
    deployment_canary_task_id uuid,
    receipt_proof_sha256 text,
    circuit_open boolean,
    circuit_reason_code text,
    last_task_key text,
    last_task_status text,
    created_count bigint,
    pass_count bigint,
    finding_count bigint,
    active_task_count bigint,
    stale_running_count bigint,
    nonzero_cost_task_count bigint,
    unsafe_terminal_task_count bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
    SELECT
        r.resume_status,
        r.deployment_revision_sha,
        r.staging_run_id,
        r.activation_run_id,
        r.deployment_canary_task_id,
        r.proof_sha256,
        s.circuit_open,
        s.circuit_reason_code,
        s.last_task_key,
        t.status,
        s.created_count,
        s.pass_count,
        s.finding_count,
        (SELECT count(*) FROM autopilot.task active
          WHERE active.status NOT IN (
              'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
              'DONE', 'CANCELLED'
          )),
        (SELECT count(*) FROM autopilot.task stale
          WHERE stale.status = 'RUNNING' AND stale.lease_until <= now()),
        (SELECT count(*) FROM autopilot.task costed
          WHERE costed.source = 'ORACLE_ONLINE_PILOT_V1'
            AND costed.created_at >= r.created_at
            AND costed.cost_actual_microusd <> 0),
        (SELECT count(*) FROM autopilot.task unsafe
          WHERE unsafe.source = 'ORACLE_ONLINE_PILOT_V1'
            AND unsafe.created_at >= r.created_at
            AND unsafe.status IN (
                'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
                'DONE', 'CANCELLED'
            )
            AND (
                unsafe.status <> 'DONE'
                OR unsafe.attempts <> 1
                OR unsafe.terminal_reason_code <>
                    'ACCEPTANCE_EVIDENCE_RETAINED'
                OR unsafe.cost_actual_microusd <> 0
                OR unsafe.safe_summary_json->'model_calls'
                    IS DISTINCT FROM '0'::jsonb
                OR unsafe.safe_summary_json->'production_mutation'
                    IS DISTINCT FROM 'false'::jsonb
                OR NOT EXISTS (
                    SELECT 1 FROM autopilot.evidence evidence
                     WHERE evidence.task_id = unsafe.task_id
                       AND evidence.evidence_class =
                            'SYNTHETIC_SHADOW_COMPLETION'
                       AND evidence.retained
                )
            ))
      FROM autopilot.online_resume_receipt r
      JOIN autopilot.online_pilot_state s ON s.singleton
      LEFT JOIN autopilot.task t ON t.task_id = s.last_task_id
     WHERE r.singleton;
$$;

REVOKE ALL ON FUNCTION autopilot.resume_online_pilot_after_deployment(
    text,text,bigint,bigint,uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.resume_online_pilot_after_deployment(
    text,text,bigint,bigint,uuid
) FROM autopilot_runtime;
REVOKE ALL ON FUNCTION autopilot.online_resume_status() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.online_resume_status()
    TO autopilot_runtime;

COMMENT ON TABLE autopilot.online_resume_receipt IS
'Immutable one-shot receipt for the reviewed SHADOW_ONLY deployment resume; any later non-PASS reopens the circuit and replay cannot close it again.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0312_autopilot_deployment_resume')
ON CONFLICT DO NOTHING;

COMMIT;
