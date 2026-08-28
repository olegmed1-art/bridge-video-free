-- Guarded state mutation functions. Runtime roles receive EXECUTE only on the
-- explicitly granted public functions; they receive no direct table writes.

CREATE OR REPLACE FUNCTION autopilot.is_terminal_status(p_status text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT p_status IN ('OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED')
$$;

CREATE OR REPLACE FUNCTION autopilot.valid_transition(p_from text, p_to text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE p_from
        WHEN 'NEW' THEN p_to IN ('VALIDATING','CANCELLED')
        WHEN 'VALIDATING' THEN p_to IN ('READY','OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','CANCELLED')
        WHEN 'READY' THEN p_to IN ('RUNNING','CANCELLED')
        WHEN 'RUNNING' THEN p_to IN ('WAITING_EXTERNAL','EVALUATING','OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','CANCELLED')
        WHEN 'WAITING_EXTERNAL' THEN p_to IN ('EVALUATING','FAILED_CLOSED','CANCELLED')
        WHEN 'EVALUATING' THEN p_to IN ('RUNNING','OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED')
        ELSE false
    END
$$;

CREATE OR REPLACE FUNCTION autopilot.reject_immutable_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'AUTOPILOT_IMMUTABLE_RELATION: %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS autopilot_task_event_immutable ON autopilot.task_event;
CREATE TRIGGER autopilot_task_event_immutable
BEFORE UPDATE OR DELETE ON autopilot.task_event
FOR EACH ROW EXECUTE FUNCTION autopilot.reject_immutable_change();

DROP TRIGGER IF EXISTS autopilot_evidence_immutable ON autopilot.evidence;
CREATE TRIGGER autopilot_evidence_immutable
BEFORE UPDATE OR DELETE ON autopilot.evidence
FOR EACH ROW EXECUTE FUNCTION autopilot.reject_immutable_change();

DROP TRIGGER IF EXISTS autopilot_external_event_no_delete ON autopilot.external_event;
CREATE TRIGGER autopilot_external_event_no_delete
BEFORE DELETE ON autopilot.external_event
FOR EACH ROW EXECUTE FUNCTION autopilot.reject_immutable_change();

CREATE OR REPLACE FUNCTION autopilot.append_task_event_internal(
    p_task_id uuid,
    p_event_type text,
    p_state_from text,
    p_state_to text,
    p_payload_json jsonb,
    p_actor_type text,
    p_actor_ref text,
    p_idempotency_key text,
    p_occurred_at timestamptz DEFAULT now()
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_existing autopilot.task_event%ROWTYPE;
    v_event_id uuid;
    v_sequence bigint;
BEGIN
    IF jsonb_typeof(p_payload_json) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_PAYLOAD_NOT_OBJECT';
    END IF;

    SELECT * INTO v_existing
      FROM autopilot.task_event
     WHERE task_id=p_task_id AND idempotency_key=p_idempotency_key;

    IF FOUND THEN
        IF v_existing.event_type IS DISTINCT FROM p_event_type
           OR v_existing.state_from IS DISTINCT FROM p_state_from
           OR v_existing.state_to IS DISTINCT FROM p_state_to
           OR v_existing.payload_json IS DISTINCT FROM p_payload_json
           OR v_existing.actor_type IS DISTINCT FROM p_actor_type
           OR v_existing.actor_ref IS DISTINCT FROM p_actor_ref THEN
            RAISE EXCEPTION 'AUTOPILOT_EVENT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN v_existing.task_event_id;
    END IF;

    SELECT COALESCE(max(sequence_no),0)+1 INTO v_sequence
      FROM autopilot.task_event
     WHERE task_id=p_task_id;

    INSERT INTO autopilot.task_event(
        task_id, sequence_no, event_type, state_from, state_to, payload_json,
        actor_type, actor_ref, idempotency_key, occurred_at
    ) VALUES (
        p_task_id, v_sequence, p_event_type, p_state_from, p_state_to, p_payload_json,
        p_actor_type, p_actor_ref, p_idempotency_key, p_occurred_at
    ) RETURNING task_event_id INTO v_event_id;

    RETURN v_event_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.create_task(
    p_task_key text,
    p_goal_type text,
    p_goal_version text,
    p_goal_json jsonb,
    p_acceptance_contract_json jsonb,
    p_allowed_capabilities_json jsonb,
    p_created_by text,
    p_source text,
    p_event_idempotency_key text,
    p_governance_mode text DEFAULT 'ASSURED',
    p_risk_class text DEFAULT 'P1',
    p_model_turn_cap integer DEFAULT 4,
    p_input_token_cap bigint DEFAULT 40000,
    p_output_token_cap bigint DEFAULT 8000,
    p_cost_cap_usd numeric DEFAULT 0.500000
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_existing autopilot.task%ROWTYPE;
    v_task_id uuid;
BEGIN
    IF jsonb_typeof(p_goal_json) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_acceptance_contract_json) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_allowed_capabilities_json) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_JSON_SHAPE_INVALID';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(p_allowed_capabilities_json) x
         WHERE jsonb_typeof(x) <> 'string' OR length(trim(both '"' from x::text))=0
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CAPABILITY_LIST_INVALID';
    END IF;

    SELECT * INTO v_existing
      FROM autopilot.task
     WHERE task_key=p_task_key
     FOR UPDATE;

    IF FOUND THEN
        IF v_existing.goal_type IS DISTINCT FROM p_goal_type
           OR v_existing.goal_version IS DISTINCT FROM p_goal_version
           OR v_existing.goal_json IS DISTINCT FROM p_goal_json
           OR v_existing.acceptance_contract_json IS DISTINCT FROM p_acceptance_contract_json
           OR v_existing.allowed_capabilities_json IS DISTINCT FROM p_allowed_capabilities_json
           OR v_existing.created_by IS DISTINCT FROM p_created_by
           OR v_existing.source IS DISTINCT FROM p_source
           OR v_existing.governance_mode IS DISTINCT FROM p_governance_mode
           OR v_existing.risk_class IS DISTINCT FROM p_risk_class
           OR v_existing.model_turn_cap IS DISTINCT FROM p_model_turn_cap
           OR v_existing.input_token_cap IS DISTINCT FROM p_input_token_cap
           OR v_existing.output_token_cap IS DISTINCT FROM p_output_token_cap
           OR v_existing.cost_cap_usd IS DISTINCT FROM p_cost_cap_usd THEN
            RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_CONFLICT';
        END IF;
        RETURN v_existing.task_id;
    END IF;

    INSERT INTO autopilot.task(
        task_key, goal_type, goal_version, goal_json,
        acceptance_contract_json, allowed_capabilities_json,
        created_by, source, governance_mode, risk_class,
        model_turn_cap, input_token_cap, output_token_cap, cost_cap_usd
    ) VALUES (
        p_task_key, p_goal_type, p_goal_version, p_goal_json,
        p_acceptance_contract_json, p_allowed_capabilities_json,
        p_created_by, p_source, p_governance_mode, p_risk_class,
        p_model_turn_cap, p_input_token_cap, p_output_token_cap, p_cost_cap_usd
    ) RETURNING task_id INTO v_task_id;

    PERFORM autopilot.append_task_event_internal(
        v_task_id,
        'TASK_CREATED',
        NULL,
        'NEW',
        jsonb_build_object('goal_type',p_goal_type,'goal_version',p_goal_version),
        'AUTOPILOT',
        p_created_by,
        p_event_idempotency_key
    );

    RETURN v_task_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.transition_task(
    p_task_id uuid,
    p_expected_status text,
    p_new_status text,
    p_event_type text,
    p_payload_json jsonb,
    p_actor_type text,
    p_actor_ref text,
    p_event_idempotency_key text,
    p_current_step_key text DEFAULT NULL,
    p_terminal_reason_code text DEFAULT NULL,
    p_terminal_summary text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task autopilot.task%ROWTYPE;
    v_existing autopilot.task_event%ROWTYPE;
    v_event_id uuid;
BEGIN
    SELECT * INTO v_existing
      FROM autopilot.task_event
     WHERE task_id=p_task_id AND idempotency_key=p_event_idempotency_key;
    IF FOUND THEN
        IF v_existing.event_type IS DISTINCT FROM p_event_type
           OR v_existing.state_from IS DISTINCT FROM p_expected_status
           OR v_existing.state_to IS DISTINCT FROM p_new_status
           OR v_existing.payload_json IS DISTINCT FROM p_payload_json
           OR v_existing.actor_type IS DISTINCT FROM p_actor_type
           OR v_existing.actor_ref IS DISTINCT FROM p_actor_ref THEN
            RAISE EXCEPTION 'AUTOPILOT_EVENT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN v_existing.task_event_id;
    END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND';
    END IF;
    IF v_task.status IS DISTINCT FROM p_expected_status THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_STATUS_MISMATCH expected=% observed=%', p_expected_status, v_task.status;
    END IF;
    IF p_new_status='WAITING_EXTERNAL' THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_REQUIRES_BEGIN_WAIT';
    END IF;
    IF NOT autopilot.valid_transition(v_task.status,p_new_status) THEN
        RAISE EXCEPTION 'AUTOPILOT_TRANSITION_FORBIDDEN % -> %', v_task.status, p_new_status;
    END IF;

    IF autopilot.is_terminal_status(p_new_status) THEN
        IF p_terminal_reason_code IS NULL OR length(p_terminal_reason_code)=0 THEN
            RAISE EXCEPTION 'AUTOPILOT_TERMINAL_REASON_REQUIRED';
        END IF;
    ELSIF p_terminal_reason_code IS NOT NULL OR p_terminal_summary IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_NONTERMINAL_REASON_FORBIDDEN';
    END IF;

    IF p_new_status='DONE' THEN
        IF EXISTS (
            SELECT 1 FROM autopilot.wait_condition
             WHERE task_id=p_task_id AND status='ACTIVE'
        ) THEN
            RAISE EXCEPTION 'AUTOPILOT_DONE_WITH_ACTIVE_WAIT';
        END IF;
        IF EXISTS (
            SELECT 1 FROM autopilot.step_attempt
             WHERE task_id=p_task_id AND status IN ('RESERVED','DISPATCHED','WAITING')
        ) THEN
            RAISE EXCEPTION 'AUTOPILOT_DONE_WITH_ACTIVE_STEP';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM autopilot.evidence
             WHERE task_id=p_task_id AND retained
               AND (expires_at IS NULL OR expires_at > now())
        ) THEN
            RAISE EXCEPTION 'AUTOPILOT_DONE_WITHOUT_FRESH_RETAINED_EVIDENCE';
        END IF;
    END IF;

    UPDATE autopilot.task
       SET status=p_new_status,
           current_step_key=COALESCE(p_current_step_key,current_step_key),
           updated_at=now(),
           terminal_at=CASE WHEN autopilot.is_terminal_status(p_new_status) THEN now() ELSE NULL END,
           terminal_reason_code=CASE WHEN autopilot.is_terminal_status(p_new_status) THEN p_terminal_reason_code ELSE NULL END,
           terminal_summary=CASE WHEN autopilot.is_terminal_status(p_new_status) THEN p_terminal_summary ELSE NULL END,
           row_version=row_version+1
     WHERE task_id=p_task_id;

    v_event_id := autopilot.append_task_event_internal(
        p_task_id, p_event_type, p_expected_status, p_new_status, p_payload_json,
        p_actor_type, p_actor_ref, p_event_idempotency_key
    );
    RETURN v_event_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.reserve_step_attempt(
    p_task_id uuid,
    p_step_key text,
    p_executor_type text,
    p_capability_name text,
    p_idempotency_key text,
    p_input_fingerprint text,
    p_actor_ref text,
    p_lease_epoch bigint DEFAULT 0
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task autopilot.task%ROWTYPE;
    v_existing autopilot.step_attempt%ROWTYPE;
    v_attempt_no integer;
    v_step_attempt_id uuid;
BEGIN
    SELECT * INTO v_existing
      FROM autopilot.step_attempt
     WHERE task_id=p_task_id AND idempotency_key=p_idempotency_key;
    IF FOUND THEN
        IF v_existing.step_key IS DISTINCT FROM p_step_key
           OR v_existing.executor_type IS DISTINCT FROM p_executor_type
           OR v_existing.capability_name IS DISTINCT FROM p_capability_name
           OR v_existing.input_fingerprint IS DISTINCT FROM p_input_fingerprint
           OR v_existing.lease_epoch IS DISTINCT FROM p_lease_epoch THEN
            RAISE EXCEPTION 'AUTOPILOT_STEP_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN v_existing.step_attempt_id;
    END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND'; END IF;
    IF v_task.status NOT IN ('RUNNING','EVALUATING') THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_TASK_STATE_FORBIDDEN %', v_task.status;
    END IF;
    IF NOT (v_task.allowed_capabilities_json ? p_capability_name) THEN
        RAISE EXCEPTION 'AUTOPILOT_CAPABILITY_NOT_ALLOWED %', p_capability_name;
    END IF;
    IF p_lease_epoch IS DISTINCT FROM v_task.lease_epoch THEN
        RAISE EXCEPTION 'AUTOPILOT_STALE_LEASE_EPOCH';
    END IF;

    SELECT COALESCE(max(attempt_no),0)+1 INTO v_attempt_no
      FROM autopilot.step_attempt
     WHERE task_id=p_task_id AND step_key=p_step_key;

    INSERT INTO autopilot.step_attempt(
        task_id, step_key, attempt_no, executor_type, capability_name,
        idempotency_key, input_fingerprint, lease_epoch
    ) VALUES (
        p_task_id, p_step_key, v_attempt_no, p_executor_type, p_capability_name,
        p_idempotency_key, p_input_fingerprint, p_lease_epoch
    ) RETURNING step_attempt_id INTO v_step_attempt_id;

    UPDATE autopilot.task
       SET current_step_key=p_step_key,
           updated_at=now(),
           row_version=row_version+1
     WHERE task_id=p_task_id;

    PERFORM autopilot.append_task_event_internal(
        p_task_id,
        'STEP_RESERVED',
        v_task.status,
        v_task.status,
        jsonb_build_object(
            'step_attempt_id',v_step_attempt_id,
            'step_key',p_step_key,
            'executor_type',p_executor_type,
            'capability_name',p_capability_name,
            'attempt_no',v_attempt_no
        ),
        'AUTOPILOT',
        p_actor_ref,
        'step-reserved:' || p_idempotency_key
    );

    RETURN v_step_attempt_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.update_step_attempt(
    p_step_attempt_id uuid,
    p_expected_status text,
    p_new_status text,
    p_result_summary_json jsonb,
    p_actor_ref text,
    p_event_idempotency_key text,
    p_external_ref text DEFAULT NULL,
    p_error_code text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_step autopilot.step_attempt%ROWTYPE;
    v_task_status text;
    v_existing autopilot.task_event%ROWTYPE;
BEGIN
    SELECT * INTO v_existing
      FROM autopilot.task_event
     WHERE idempotency_key=p_event_idempotency_key
       AND task_id=(SELECT task_id FROM autopilot.step_attempt WHERE step_attempt_id=p_step_attempt_id);
    IF FOUND THEN
        RETURN p_step_attempt_id;
    END IF;

    SELECT * INTO v_step
      FROM autopilot.step_attempt
     WHERE step_attempt_id=p_step_attempt_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_STEP_NOT_FOUND'; END IF;

    PERFORM 1 FROM autopilot.task WHERE task_id=v_step.task_id FOR UPDATE;
    SELECT status INTO v_task_status FROM autopilot.task WHERE task_id=v_step.task_id;

    IF v_step.status IS DISTINCT FROM p_expected_status THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_STATUS_MISMATCH expected=% observed=%', p_expected_status, v_step.status;
    END IF;
    IF NOT (
        (v_step.status='RESERVED' AND p_new_status IN ('DISPATCHED','FAILED','CANCELLED'))
        OR (v_step.status='DISPATCHED' AND p_new_status IN ('WAITING','SUCCEEDED','FAILED','CANCELLED'))
        OR (v_step.status='WAITING' AND p_new_status IN ('SUCCEEDED','FAILED','CANCELLED'))
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_TRANSITION_FORBIDDEN % -> %', v_step.status, p_new_status;
    END IF;
    IF jsonb_typeof(p_result_summary_json) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_RESULT_NOT_OBJECT';
    END IF;
    IF p_new_status='FAILED' AND (p_error_code IS NULL OR length(p_error_code)=0) THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_ERROR_CODE_REQUIRED';
    END IF;
    IF p_new_status<>'FAILED' AND p_error_code IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_ERROR_CODE_FORBIDDEN';
    END IF;
    IF v_step.external_ref IS NOT NULL
       AND p_external_ref IS NOT NULL
       AND v_step.external_ref IS DISTINCT FROM p_external_ref THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_EXTERNAL_REF_CONFLICT';
    END IF;

    UPDATE autopilot.step_attempt
       SET status=p_new_status,
           external_ref=COALESCE(external_ref,p_external_ref),
           result_summary_json=p_result_summary_json,
           error_code=p_error_code,
           started_at=CASE
               WHEN p_new_status IN ('DISPATCHED','WAITING','SUCCEEDED','FAILED')
               THEN COALESCE(started_at,now())
               ELSE started_at
           END,
           completed_at=CASE
               WHEN p_new_status IN ('SUCCEEDED','FAILED','CANCELLED') THEN now()
               ELSE NULL
           END
     WHERE step_attempt_id=p_step_attempt_id;

    UPDATE autopilot.task
       SET updated_at=now(), row_version=row_version+1
     WHERE task_id=v_step.task_id;

    PERFORM autopilot.append_task_event_internal(
        v_step.task_id,
        'STEP_' || p_new_status,
        v_task_status,
        v_task_status,
        jsonb_build_object(
            'step_attempt_id',p_step_attempt_id,
            'step_key',v_step.step_key,
            'step_status_from',p_expected_status,
            'step_status_to',p_new_status,
            'external_ref',p_external_ref,
            'error_code',p_error_code
        ),
        'AUTOPILOT',
        p_actor_ref,
        p_event_idempotency_key
    );

    RETURN p_step_attempt_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.begin_wait(
    p_task_id uuid,
    p_expected_status text,
    p_provider text,
    p_correlation_id text,
    p_expected_event_types_json jsonb,
    p_hook_generation integer,
    p_hook_token_hash text,
    p_deadline_at timestamptz,
    p_actor_ref text,
    p_event_idempotency_key text,
    p_step_attempt_id uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task autopilot.task%ROWTYPE;
    v_existing autopilot.wait_condition%ROWTYPE;
    v_wait_id uuid;
BEGIN
    SELECT * INTO v_existing
      FROM autopilot.wait_condition
     WHERE provider=p_provider
       AND correlation_id=p_correlation_id
       AND hook_generation=p_hook_generation;
    IF FOUND THEN
        IF v_existing.task_id IS DISTINCT FROM p_task_id
           OR v_existing.step_attempt_id IS DISTINCT FROM p_step_attempt_id
           OR v_existing.expected_event_types_json IS DISTINCT FROM p_expected_event_types_json
           OR v_existing.hook_token_hash IS DISTINCT FROM p_hook_token_hash
           OR v_existing.deadline_at IS DISTINCT FROM p_deadline_at THEN
            RAISE EXCEPTION 'AUTOPILOT_WAIT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN v_existing.wait_condition_id;
    END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND'; END IF;
    IF v_task.status IS DISTINCT FROM p_expected_status THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_STATUS_MISMATCH expected=% observed=%', p_expected_status, v_task.status;
    END IF;
    IF NOT autopilot.valid_transition(v_task.status,'WAITING_EXTERNAL') THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_TRANSITION_FORBIDDEN %', v_task.status;
    END IF;
    IF jsonb_typeof(p_expected_event_types_json) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_expected_event_types_json)=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_EVENT_TYPES_INVALID';
    END IF;
    IF p_hook_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_HOOK_TOKEN_HASH_INVALID';
    END IF;
    IF p_deadline_at <= now() THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_DEADLINE_INVALID';
    END IF;
    IF p_step_attempt_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM autopilot.step_attempt
         WHERE step_attempt_id=p_step_attempt_id
           AND task_id=p_task_id
           AND status IN ('DISPATCHED','WAITING')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_STEP_INVALID';
    END IF;

    INSERT INTO autopilot.wait_condition(
        task_id, step_attempt_id, provider, correlation_id,
        expected_event_types_json, hook_generation, hook_token_hash, deadline_at
    ) VALUES (
        p_task_id, p_step_attempt_id, p_provider, p_correlation_id,
        p_expected_event_types_json, p_hook_generation, p_hook_token_hash, p_deadline_at
    ) RETURNING wait_condition_id INTO v_wait_id;

    UPDATE autopilot.task
       SET status='WAITING_EXTERNAL',
           updated_at=now(),
           row_version=row_version+1
     WHERE task_id=p_task_id;

    PERFORM autopilot.append_task_event_internal(
        p_task_id,
        'WAIT_STARTED',
        p_expected_status,
        'WAITING_EXTERNAL',
        jsonb_build_object(
            'wait_condition_id',v_wait_id,
            'provider',p_provider,
            'correlation_id',p_correlation_id,
            'expected_event_types',p_expected_event_types_json,
            'hook_generation',p_hook_generation,
            'deadline_at',p_deadline_at
        ),
        'AUTOPILOT',
        p_actor_ref,
        p_event_idempotency_key
    );

    RETURN v_wait_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.record_external_event(
    p_task_id uuid,
    p_provider text,
    p_provider_event_id text,
    p_event_type text,
    p_correlation_id text,
    p_signature_verified boolean,
    p_normalized_payload_json jsonb,
    p_actor_ref text DEFAULT 'verified-webhook'
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task autopilot.task%ROWTYPE;
    v_wait autopilot.wait_condition%ROWTYPE;
    v_existing autopilot.external_event%ROWTYPE;
    v_event_id uuid;
    v_fingerprint text;
BEGIN
    IF NOT p_signature_verified THEN
        RAISE EXCEPTION 'AUTOPILOT_EXTERNAL_SIGNATURE_INVALID';
    END IF;
    IF jsonb_typeof(p_normalized_payload_json) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AUTOPILOT_EXTERNAL_PAYLOAD_NOT_OBJECT';
    END IF;

    v_fingerprint := encode(public.digest(p_normalized_payload_json::text,'sha256'),'hex');

    SELECT * INTO v_existing
      FROM autopilot.external_event
     WHERE provider=p_provider AND provider_event_id=p_provider_event_id;
    IF FOUND THEN
        IF v_existing.task_id IS DISTINCT FROM p_task_id
           OR v_existing.event_type IS DISTINCT FROM p_event_type
           OR v_existing.correlation_id IS DISTINCT FROM p_correlation_id
           OR v_existing.payload_fingerprint IS DISTINCT FROM v_fingerprint
           OR v_existing.normalized_payload_json IS DISTINCT FROM p_normalized_payload_json THEN
            RAISE EXCEPTION 'AUTOPILOT_PROVIDER_EVENT_ID_CONFLICT';
        END IF;
        RETURN v_existing.external_event_id;
    END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND'; END IF;
    IF v_task.status<>'WAITING_EXTERNAL' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_WAITING observed=%', v_task.status;
    END IF;

    SELECT * INTO v_wait
      FROM autopilot.wait_condition
     WHERE task_id=p_task_id
       AND status='ACTIVE'
       AND provider=p_provider
       AND correlation_id=p_correlation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_ACTIVE_WAIT_NOT_FOUND';
    END IF;
    IF now() > v_wait.deadline_at THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_DEADLINE_EXPIRED';
    END IF;
    IF NOT (v_wait.expected_event_types_json ? p_event_type) THEN
        RAISE EXCEPTION 'AUTOPILOT_EXTERNAL_EVENT_TYPE_UNEXPECTED %', p_event_type;
    END IF;

    INSERT INTO autopilot.external_event(
        task_id, provider, provider_event_id, event_type, correlation_id,
        signature_verified, payload_fingerprint, normalized_payload_json
    ) VALUES (
        p_task_id, p_provider, p_provider_event_id, p_event_type, p_correlation_id,
        true, v_fingerprint, p_normalized_payload_json
    ) RETURNING external_event_id INTO v_event_id;

    UPDATE autopilot.wait_condition
       SET status='SATISFIED',
           satisfied_by_event_id=v_event_id,
           satisfied_at=now()
     WHERE wait_condition_id=v_wait.wait_condition_id;

    UPDATE autopilot.external_event
       SET processed_at=now()
     WHERE external_event_id=v_event_id;

    UPDATE autopilot.task
       SET status='EVALUATING',
           updated_at=now(),
           row_version=row_version+1
     WHERE task_id=p_task_id;

    PERFORM autopilot.append_task_event_internal(
        p_task_id,
        'EXTERNAL_EVENT_ACCEPTED',
        'WAITING_EXTERNAL',
        'EVALUATING',
        jsonb_build_object(
            'external_event_id',v_event_id,
            'provider',p_provider,
            'provider_event_id',p_provider_event_id,
            'event_type',p_event_type,
            'correlation_id',p_correlation_id,
            'payload_fingerprint',v_fingerprint
        ),
        'PROVIDER',
        p_actor_ref,
        'external-event:' || p_provider || ':' || p_provider_event_id
    );

    RETURN v_event_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.expire_wait(
    p_task_id uuid,
    p_actor_ref text,
    p_event_idempotency_key text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task autopilot.task%ROWTYPE;
    v_wait autopilot.wait_condition%ROWTYPE;
    v_event_id uuid;
BEGIN
    SELECT * INTO v_task FROM autopilot.task WHERE task_id=p_task_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND'; END IF;

    SELECT * INTO v_wait
      FROM autopilot.wait_condition
     WHERE task_id=p_task_id AND status='ACTIVE'
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_ACTIVE_WAIT_NOT_FOUND'; END IF;
    IF v_task.status<>'WAITING_EXTERNAL' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_WAITING observed=%', v_task.status;
    END IF;
    IF v_wait.deadline_at > now() THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_NOT_EXPIRED';
    END IF;

    UPDATE autopilot.wait_condition
       SET status='EXPIRED', last_reconciled_at=now()
     WHERE wait_condition_id=v_wait.wait_condition_id;

    UPDATE autopilot.task
       SET status='FAILED_CLOSED',
           updated_at=now(),
           terminal_at=now(),
           terminal_reason_code='EXTERNAL_WAIT_DEADLINE_EXPIRED',
           terminal_summary='Expected external evidence was not observed before the deadline.',
           row_version=row_version+1
     WHERE task_id=p_task_id;

    v_event_id := autopilot.append_task_event_internal(
        p_task_id,
        'WAIT_EXPIRED',
        'WAITING_EXTERNAL',
        'FAILED_CLOSED',
        jsonb_build_object(
            'wait_condition_id',v_wait.wait_condition_id,
            'provider',v_wait.provider,
            'correlation_id',v_wait.correlation_id,
            'deadline_at',v_wait.deadline_at
        ),
        'SYSTEM',
        p_actor_ref,
        p_event_idempotency_key
    );
    RETURN v_event_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.record_evidence(
    p_task_id uuid,
    p_evidence_class text,
    p_provider text,
    p_external_ref text,
    p_metadata_json jsonb,
    p_idempotency_key text,
    p_actor_ref text,
    p_content_sha256 text DEFAULT NULL,
    p_step_attempt_id uuid DEFAULT NULL,
    p_observed_at timestamptz DEFAULT now(),
    p_expires_at timestamptz DEFAULT NULL,
    p_retained boolean DEFAULT true
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task_status text;
    v_existing autopilot.evidence%ROWTYPE;
    v_evidence_id uuid;
BEGIN
    SELECT * INTO v_existing
      FROM autopilot.evidence
     WHERE task_id=p_task_id AND idempotency_key=p_idempotency_key;
    IF FOUND THEN
        IF v_existing.evidence_class IS DISTINCT FROM p_evidence_class
           OR v_existing.provider IS DISTINCT FROM p_provider
           OR v_existing.external_ref IS DISTINCT FROM p_external_ref
           OR v_existing.content_sha256 IS DISTINCT FROM p_content_sha256
           OR v_existing.metadata_json IS DISTINCT FROM p_metadata_json
           OR v_existing.step_attempt_id IS DISTINCT FROM p_step_attempt_id
           OR v_existing.observed_at IS DISTINCT FROM p_observed_at
           OR v_existing.expires_at IS DISTINCT FROM p_expires_at
           OR v_existing.retained IS DISTINCT FROM p_retained THEN
            RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN v_existing.evidence_id;
    END IF;

    SELECT status INTO v_task_status
      FROM autopilot.task
     WHERE task_id=p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND'; END IF;
    IF autopilot.is_terminal_status(v_task_status) THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_AFTER_TERMINAL_FORBIDDEN';
    END IF;
    IF jsonb_typeof(p_metadata_json) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_METADATA_NOT_OBJECT';
    END IF;
    IF p_step_attempt_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM autopilot.step_attempt
         WHERE step_attempt_id=p_step_attempt_id AND task_id=p_task_id
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_STEP_MISMATCH';
    END IF;

    INSERT INTO autopilot.evidence(
        task_id, step_attempt_id, evidence_class, provider, external_ref,
        content_sha256, metadata_json, observed_at, expires_at, retained,
        idempotency_key
    ) VALUES (
        p_task_id, p_step_attempt_id, p_evidence_class, p_provider, p_external_ref,
        p_content_sha256, p_metadata_json, p_observed_at, p_expires_at, p_retained,
        p_idempotency_key
    ) RETURNING evidence_id INTO v_evidence_id;

    UPDATE autopilot.task
       SET updated_at=now(), row_version=row_version+1
     WHERE task_id=p_task_id;

    PERFORM autopilot.append_task_event_internal(
        p_task_id,
        'EVIDENCE_RECORDED',
        v_task_status,
        v_task_status,
        jsonb_build_object(
            'evidence_id',v_evidence_id,
            'evidence_class',p_evidence_class,
            'provider',p_provider,
            'external_ref',p_external_ref,
            'retained',p_retained
        ),
        'AUTOPILOT',
        p_actor_ref,
        'evidence:' || p_idempotency_key
    );

    RETURN v_evidence_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.reserve_usage(
    p_task_id uuid,
    p_provider text,
    p_model text,
    p_reserved_cost_usd numeric,
    p_idempotency_key text,
    p_actor_ref text,
    p_step_attempt_id uuid DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_task autopilot.task%ROWTYPE;
    v_existing autopilot.usage_ledger%ROWTYPE;
    v_usage_id uuid;
    v_model_turns bigint;
BEGIN
    SELECT * INTO v_existing
      FROM autopilot.usage_ledger
     WHERE task_id=p_task_id AND idempotency_key=p_idempotency_key;
    IF FOUND THEN
        IF v_existing.provider IS DISTINCT FROM p_provider
           OR v_existing.model IS DISTINCT FROM p_model
           OR v_existing.reserved_cost_usd IS DISTINCT FROM p_reserved_cost_usd
           OR v_existing.step_attempt_id IS DISTINCT FROM p_step_attempt_id THEN
            RAISE EXCEPTION 'AUTOPILOT_USAGE_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN v_existing.usage_id;
    END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_TASK_NOT_FOUND'; END IF;
    IF autopilot.is_terminal_status(v_task.status) THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_TERMINAL_TASK_FORBIDDEN';
    END IF;
    IF p_reserved_cost_usd < 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_RESERVATION_NEGATIVE';
    END IF;
    IF v_task.cost_actual_usd + v_task.cost_reserved_usd + p_reserved_cost_usd > v_task.cost_cap_usd THEN
        RAISE EXCEPTION 'AUTOPILOT_BUDGET_CAP_EXCEEDED';
    END IF;

    SELECT count(*) INTO v_model_turns
      FROM autopilot.usage_ledger
     WHERE task_id=p_task_id
       AND provider='openai'
       AND status IN ('RESERVED','FINALIZED');
    IF p_provider='openai' AND v_model_turns >= v_task.model_turn_cap THEN
        RAISE EXCEPTION 'AUTOPILOT_MODEL_TURN_CAP_EXCEEDED';
    END IF;
    IF p_step_attempt_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM autopilot.step_attempt
         WHERE step_attempt_id=p_step_attempt_id AND task_id=p_task_id
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_STEP_MISMATCH';
    END IF;

    INSERT INTO autopilot.usage_ledger(
        task_id, step_attempt_id, provider, model, reserved_cost_usd,
        idempotency_key
    ) VALUES (
        p_task_id, p_step_attempt_id, p_provider, p_model, p_reserved_cost_usd,
        p_idempotency_key
    ) RETURNING usage_id INTO v_usage_id;

    UPDATE autopilot.task
       SET cost_reserved_usd=cost_reserved_usd+p_reserved_cost_usd,
           updated_at=now(),
           row_version=row_version+1
     WHERE task_id=p_task_id;

    PERFORM autopilot.append_task_event_internal(
        p_task_id,
        'USAGE_RESERVED',
        v_task.status,
        v_task.status,
        jsonb_build_object(
            'usage_id',v_usage_id,
            'provider',p_provider,
            'model',p_model,
            'reserved_cost_usd',p_reserved_cost_usd
        ),
        'AUTOPILOT',
        p_actor_ref,
        'usage-reserved:' || p_idempotency_key
    );

    RETURN v_usage_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.finalize_usage(
    p_usage_id uuid,
    p_actual_cost_usd numeric,
    p_input_tokens bigint,
    p_cached_input_tokens bigint,
    p_output_tokens bigint,
    p_provider_response_ref text,
    p_actor_ref text,
    p_event_idempotency_key text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_usage autopilot.usage_ledger%ROWTYPE;
    v_task autopilot.task%ROWTYPE;
    v_used_input bigint;
    v_used_output bigint;
BEGIN
    SELECT * INTO v_usage
      FROM autopilot.usage_ledger
     WHERE usage_id=p_usage_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_USAGE_NOT_FOUND'; END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=v_usage.task_id
     FOR UPDATE;

    IF v_usage.status='FINALIZED' THEN
        IF v_usage.actual_cost_usd IS DISTINCT FROM p_actual_cost_usd
           OR v_usage.input_tokens IS DISTINCT FROM p_input_tokens
           OR v_usage.cached_input_tokens IS DISTINCT FROM p_cached_input_tokens
           OR v_usage.output_tokens IS DISTINCT FROM p_output_tokens
           OR v_usage.provider_response_ref IS DISTINCT FROM p_provider_response_ref THEN
            RAISE EXCEPTION 'AUTOPILOT_USAGE_FINALIZE_CONFLICT';
        END IF;
        RETURN v_usage.usage_id;
    END IF;
    IF v_usage.status<>'RESERVED' THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_NOT_RESERVED';
    END IF;
    IF p_actual_cost_usd < 0 OR p_actual_cost_usd > v_usage.reserved_cost_usd THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_ACTUAL_OUTSIDE_RESERVATION';
    END IF;
    IF p_input_tokens < 0 OR p_cached_input_tokens < 0 OR p_output_tokens < 0
       OR p_cached_input_tokens > p_input_tokens THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_TOKEN_COUNTS_INVALID';
    END IF;

    SELECT COALESCE(sum(input_tokens),0), COALESCE(sum(output_tokens),0)
      INTO v_used_input, v_used_output
      FROM autopilot.usage_ledger
     WHERE task_id=v_task.task_id AND status='FINALIZED';
    IF v_used_input+p_input_tokens > v_task.input_token_cap
       OR v_used_output+p_output_tokens > v_task.output_token_cap THEN
        RAISE EXCEPTION 'AUTOPILOT_TOKEN_CAP_EXCEEDED';
    END IF;

    UPDATE autopilot.usage_ledger
       SET status='FINALIZED',
           actual_cost_usd=p_actual_cost_usd,
           input_tokens=p_input_tokens,
           cached_input_tokens=p_cached_input_tokens,
           output_tokens=p_output_tokens,
           provider_response_ref=p_provider_response_ref,
           finalized_at=now()
     WHERE usage_id=p_usage_id;

    UPDATE autopilot.task
       SET cost_reserved_usd=cost_reserved_usd-v_usage.reserved_cost_usd,
           cost_actual_usd=cost_actual_usd+p_actual_cost_usd,
           updated_at=now(),
           row_version=row_version+1
     WHERE task_id=v_task.task_id;

    PERFORM autopilot.append_task_event_internal(
        v_task.task_id,
        'USAGE_FINALIZED',
        v_task.status,
        v_task.status,
        jsonb_build_object(
            'usage_id',p_usage_id,
            'provider',v_usage.provider,
            'model',v_usage.model,
            'actual_cost_usd',p_actual_cost_usd,
            'input_tokens',p_input_tokens,
            'cached_input_tokens',p_cached_input_tokens,
            'output_tokens',p_output_tokens
        ),
        'AUTOPILOT',
        p_actor_ref,
        p_event_idempotency_key
    );

    RETURN p_usage_id;
END $$;

CREATE OR REPLACE FUNCTION autopilot.release_usage(
    p_usage_id uuid,
    p_actor_ref text,
    p_event_idempotency_key text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    v_usage autopilot.usage_ledger%ROWTYPE;
    v_task autopilot.task%ROWTYPE;
BEGIN
    SELECT * INTO v_usage
      FROM autopilot.usage_ledger
     WHERE usage_id=p_usage_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_USAGE_NOT_FOUND'; END IF;

    SELECT * INTO v_task
      FROM autopilot.task
     WHERE task_id=v_usage.task_id
     FOR UPDATE;

    IF v_usage.status='RELEASED' THEN RETURN p_usage_id; END IF;
    IF v_usage.status<>'RESERVED' THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_NOT_RESERVED';
    END IF;

    UPDATE autopilot.usage_ledger
       SET status='RELEASED', finalized_at=now()
     WHERE usage_id=p_usage_id;

    UPDATE autopilot.task
       SET cost_reserved_usd=cost_reserved_usd-v_usage.reserved_cost_usd,
           updated_at=now(),
           row_version=row_version+1
     WHERE task_id=v_task.task_id;

    PERFORM autopilot.append_task_event_internal(
        v_task.task_id,
        'USAGE_RELEASED',
        v_task.status,
        v_task.status,
        jsonb_build_object('usage_id',p_usage_id,'reserved_cost_usd',v_usage.reserved_cost_usd),
        'AUTOPILOT',
        p_actor_ref,
        p_event_idempotency_key
    );

    RETURN p_usage_id;
END $$;
