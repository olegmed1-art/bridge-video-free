\set ON_ERROR_STOP on
BEGIN;

-- Immutable pre-resume receipt for the ONLINE_STALE_RUNNING incident.
-- This gate proves the recovery canary and reviewed deployment-race fix, but
-- deliberately cannot close the circuit or claim that the fix is deployed.
CREATE TABLE autopilot.online_resume_gate (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    gate_key text NOT NULL UNIQUE CHECK (
        gate_key = 'online-stale-running-pre-resume-20260901-v1'
    ),
    recovery_key text NOT NULL UNIQUE
        REFERENCES autopilot.online_recovery_canary(recovery_key),
    root_cause_code text NOT NULL CHECK (
        root_cause_code = 'DEPLOYMENT_INGRESS_RACE'
    ),
    fix_commit_sha text NOT NULL CHECK (
        fix_commit_sha = '44a9308106b0d06d3dc0cf25622a40342c56e6ff'
    ),
    gate_status text NOT NULL CHECK (
        gate_status = 'FIX_VALIDATED_NOT_DEPLOYED'
    ),
    deployment_revision_sha text CHECK (
        deployment_revision_sha IS NULL
        OR deployment_revision_sha ~ '^[0-9a-f]{40}$'
    ),
    ready boolean NOT NULL DEFAULT false CHECK (NOT ready),
    circuit_open_at_registration boolean NOT NULL CHECK (
        circuit_open_at_registration
    ),
    proof_sha256 text NOT NULL CHECK (proof_sha256 ~ '^[0-9a-f]{64}$'),
    proof_json jsonb NOT NULL CHECK (
        jsonb_typeof(proof_json) = 'object'
        AND octet_length(proof_json::text) <= 8192
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        gate_status <> 'FIX_VALIDATED_NOT_DEPLOYED'
        OR (deployment_revision_sha IS NULL AND NOT ready)
    )
);

REVOKE ALL ON TABLE autopilot.online_resume_gate FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.online_resume_gate FROM autopilot_runtime;

CREATE OR REPLACE FUNCTION autopilot.register_online_stale_resume_gate(
    p_gate_key text,
    p_recovery_key text
)
RETURNS TABLE(
    gate_key text,
    gate_status text,
    ready boolean,
    circuit_open boolean,
    proof_sha256 text,
    created boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    recovery_row autopilot.online_recovery_canary;
    canary_row autopilot.task;
    state_row autopilot.online_pilot_state;
    gate_row autopilot.online_resume_gate;
    active_count integer;
    retained_count integer;
    inserted_count integer;
    proof jsonb;
    proof_hash text;
BEGIN
    IF p_gate_key <> 'online-stale-running-pre-resume-20260901-v1'
       OR p_recovery_key <> 'online-stale-running-recovery-20260901-v1' THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_REQUEST_NOT_APPROVED';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot:online_stale_resume_gate', 20260901)
    );

    SELECT * INTO state_row
      FROM autopilot.online_pilot_state
     WHERE singleton
     FOR UPDATE;
    IF NOT FOUND
       OR NOT state_row.circuit_open
       OR state_row.circuit_reason_code <> 'ONLINE_STALE_RUNNING' THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_CIRCUIT_REQUIRED';
    END IF;

    SELECT * INTO recovery_row
      FROM autopilot.online_recovery_canary
     WHERE singleton
       AND recovery_key = p_recovery_key;
    IF NOT FOUND
       OR recovery_row.evaluation_status <> 'PASS'
       OR recovery_row.proof_json->'fencing_proved'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.proof_json->'circuit_remains_open'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.evaluation_summary_json->'passed'
            IS DISTINCT FROM 'true'::jsonb
       OR recovery_row.evaluation_summary_json->'circuit_open'
            IS DISTINCT FROM 'true'::jsonb THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_RECOVERY_NOT_PROVED';
    END IF;

    SELECT * INTO canary_row
      FROM autopilot.task
     WHERE task_id = recovery_row.canary_task_id;
    SELECT count(*) INTO retained_count
      FROM autopilot.evidence
     WHERE task_id = recovery_row.canary_task_id
       AND evidence_class = 'SYNTHETIC_SHADOW_COMPLETION'
       AND retained;
    IF canary_row.task_id IS NULL
       OR canary_row.status <> 'DONE'
       OR canary_row.source <> 'ORACLE_RECOVERY_CANARY_V1'
       OR canary_row.goal_type <> 'AUTOPILOT_SMOKE_V1'
       OR canary_row.attempts <> 1
       OR canary_row.max_attempts <> 1
       OR canary_row.lease_epoch <> 1
       OR canary_row.terminal_reason_code <>
            'ACCEPTANCE_EVIDENCE_RETAINED'
       OR canary_row.cost_cap_microusd <> 0
       OR canary_row.cost_reserved_microusd <> 0
       OR canary_row.cost_actual_microusd <> 0
       OR canary_row.safe_summary_json->'model_calls'
            IS DISTINCT FROM '0'::jsonb
       OR canary_row.safe_summary_json->'production_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR retained_count <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_CANARY_INVALID';
    END IF;

    SELECT count(*) INTO active_count
      FROM autopilot.task
     WHERE status IN ('READY', 'RUNNING', 'WAITING_EXTERNAL');
    IF active_count <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_QUEUE_NOT_IDLE';
    END IF;

    proof := jsonb_build_object(
        'schema', 'ONLINE_STALE_PRE_RESUME_GATE_V1',
        'gate_key', p_gate_key,
        'recovery_key', p_recovery_key,
        'recovery_proof_sha256', recovery_row.proof_sha256,
        'canary_task_id', recovery_row.canary_task_id,
        'root_cause_code', 'DEPLOYMENT_INGRESS_RACE',
        'incident_staging_run_id', 33524549847,
        'incident_activation_run_id', 33524955360,
        'fix_commit_sha', '44a9308106b0d06d3dc0cf25622a40342c56e6ff',
        'fix_ci', jsonb_build_array(
            jsonb_build_object(
                'name', 'Oracle Autopilot encrypted staging',
                'run_id', 33555499986,
                'result', 'PASS'
            ),
            jsonb_build_object(
                'name', 'Oracle Autopilot shadow activation',
                'run_id', 33555499808,
                'result', 'PASS'
            ),
            jsonb_build_object(
                'name', 'Oracle Autopilot Lite shadow CI',
                'run_id', 33555499749,
                'result', 'PASS'
            ),
            jsonb_build_object(
                'name', 'Bridge School Database CI',
                'run_id', 33555499531,
                'result', 'PASS'
            ),
            jsonb_build_object(
                'name', 'Assistant Lab schema execution',
                'run_id', 33555499878,
                'attempt', 2,
                'result', 'PASS'
            )
        ),
        'active_task_count', active_count,
        'retained_canary_evidence_count', retained_count,
        'deployment_verified', false,
        'deployment_revision_sha', NULL,
        'gate_status', 'FIX_VALIDATED_NOT_DEPLOYED',
        'ready', false,
        'circuit_remains_open', true,
        'model_calls', 0,
        'cost_actual_microusd', 0,
        'production_mutation', false
    );
    proof_hash := encode(
        public.digest(convert_to(proof::text, 'UTF8'), 'sha256'),
        'hex'
    );

    INSERT INTO autopilot.online_resume_gate(
        gate_key, recovery_key, root_cause_code, fix_commit_sha,
        gate_status, deployment_revision_sha, ready,
        circuit_open_at_registration, proof_sha256, proof_json
    ) VALUES (
        p_gate_key, p_recovery_key, 'DEPLOYMENT_INGRESS_RACE',
        '44a9308106b0d06d3dc0cf25622a40342c56e6ff',
        'FIX_VALIDATED_NOT_DEPLOYED', NULL, false, true,
        proof_hash, proof
    ) ON CONFLICT (singleton) DO NOTHING;
    GET DIAGNOSTICS inserted_count = ROW_COUNT;

    SELECT * INTO gate_row
      FROM autopilot.online_resume_gate
     WHERE singleton;
    IF gate_row.gate_key <> p_gate_key
       OR gate_row.recovery_key <> p_recovery_key
       OR gate_row.proof_sha256 <> proof_hash
       OR gate_row.proof_json <> proof
       OR gate_row.gate_status <> 'FIX_VALIDATED_NOT_DEPLOYED'
       OR gate_row.ready
       OR gate_row.deployment_revision_sha IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_RESUME_GATE_REPLAY_MISMATCH';
    END IF;

    -- Deliberately no UPDATE of online_pilot_state occurs in this function.
    RETURN QUERY SELECT
        gate_row.gate_key,
        gate_row.gate_status,
        gate_row.ready,
        state_row.circuit_open,
        gate_row.proof_sha256,
        inserted_count = 1;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.register_online_stale_resume_gate(text,text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.register_online_stale_resume_gate(text,text)
    FROM autopilot_runtime;

COMMENT ON TABLE autopilot.online_resume_gate IS
'Immutable owner-only pre-resume receipt: the incident fix and recovery canary passed, but deployment remains unverified and the online circuit remains open.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0311_autopilot_online_resume_gate')
ON CONFLICT DO NOTHING;

COMMIT;
