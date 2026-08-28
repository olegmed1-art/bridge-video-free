-- School Autopilot Controller v1 canonical state.
-- This schema is intentionally separate from public.admin_task and
-- assistant_lab.* executor queues.

CREATE SCHEMA IF NOT EXISTS autopilot;

CREATE TABLE IF NOT EXISTS autopilot.task (
    task_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_key text NOT NULL,
    goal_type text NOT NULL,
    goal_version text NOT NULL,
    goal_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'NEW',
    governance_mode text NOT NULL DEFAULT 'ASSURED',
    risk_class text NOT NULL DEFAULT 'P1',
    current_step_key text,
    acceptance_contract_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    allowed_capabilities_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    workflow_provider text,
    workflow_run_ref text,
    workflow_version text,
    lease_owner text,
    lease_epoch bigint NOT NULL DEFAULT 0,
    lease_until timestamptz,
    model_turn_cap integer NOT NULL DEFAULT 4,
    input_token_cap bigint NOT NULL DEFAULT 40000,
    output_token_cap bigint NOT NULL DEFAULT 8000,
    cost_cap_usd numeric(14,6) NOT NULL DEFAULT 0.500000,
    cost_reserved_usd numeric(14,6) NOT NULL DEFAULT 0,
    cost_actual_usd numeric(14,6) NOT NULL DEFAULT 0,
    created_by text NOT NULL,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    terminal_at timestamptz,
    terminal_reason_code text,
    terminal_summary text,
    row_version bigint NOT NULL DEFAULT 1,
    CONSTRAINT autopilot_task_key_uk UNIQUE (task_key),
    CONSTRAINT autopilot_task_key_ck CHECK (char_length(task_key) BETWEEN 1 AND 200),
    CONSTRAINT autopilot_task_goal_type_ck CHECK (char_length(goal_type) BETWEEN 1 AND 120),
    CONSTRAINT autopilot_task_goal_version_ck CHECK (char_length(goal_version) BETWEEN 1 AND 80),
    CONSTRAINT autopilot_task_status_ck CHECK (status IN (
        'NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING',
        'OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED'
    )),
    CONSTRAINT autopilot_task_governance_mode_ck CHECK (
        governance_mode IN ('LIGHTWEIGHT','STANDARD','ASSURED','INCIDENT')
    ),
    CONSTRAINT autopilot_task_risk_class_ck CHECK (risk_class IN ('P0','P1','P2','P3')),
    CONSTRAINT autopilot_task_goal_json_ck CHECK (jsonb_typeof(goal_json)='object'),
    CONSTRAINT autopilot_task_acceptance_json_ck CHECK (
        jsonb_typeof(acceptance_contract_json)='object'
    ),
    CONSTRAINT autopilot_task_capabilities_json_ck CHECK (
        jsonb_typeof(allowed_capabilities_json)='array'
    ),
    CONSTRAINT autopilot_task_lease_epoch_ck CHECK (lease_epoch >= 0),
    CONSTRAINT autopilot_task_lease_pair_ck CHECK (
        (lease_owner IS NULL AND lease_until IS NULL)
        OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL)
    ),
    CONSTRAINT autopilot_task_model_turn_cap_ck CHECK (model_turn_cap >= 0),
    CONSTRAINT autopilot_task_input_token_cap_ck CHECK (input_token_cap >= 0),
    CONSTRAINT autopilot_task_output_token_cap_ck CHECK (output_token_cap >= 0),
    CONSTRAINT autopilot_task_cost_cap_ck CHECK (cost_cap_usd >= 0),
    CONSTRAINT autopilot_task_cost_reserved_ck CHECK (cost_reserved_usd >= 0),
    CONSTRAINT autopilot_task_cost_actual_ck CHECK (cost_actual_usd >= 0),
    CONSTRAINT autopilot_task_cost_total_ck CHECK (
        cost_reserved_usd + cost_actual_usd <= cost_cap_usd
    ),
    CONSTRAINT autopilot_task_row_version_ck CHECK (row_version > 0),
    CONSTRAINT autopilot_task_time_ck CHECK (updated_at >= created_at),
    CONSTRAINT autopilot_task_terminal_ck CHECK (
        (
            status IN ('OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED')
            AND terminal_at IS NOT NULL
            AND terminal_reason_code IS NOT NULL
            AND char_length(terminal_reason_code) BETWEEN 1 AND 120
        )
        OR
        (
            status NOT IN ('OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED')
            AND terminal_at IS NULL
            AND terminal_reason_code IS NULL
            AND terminal_summary IS NULL
        )
    )
);
CREATE INDEX IF NOT EXISTS autopilot_task_status_age_idx
    ON autopilot.task(status, updated_at, task_id);
CREATE INDEX IF NOT EXISTS autopilot_task_waiting_idx
    ON autopilot.task(updated_at, task_id)
    WHERE status='WAITING_EXTERNAL';

CREATE TABLE IF NOT EXISTS autopilot.task_event (
    task_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    sequence_no bigint NOT NULL,
    event_type text NOT NULL,
    state_from text,
    state_to text NOT NULL,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor_type text NOT NULL,
    actor_ref text NOT NULL,
    idempotency_key text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT autopilot_task_event_sequence_uk UNIQUE (task_id, sequence_no),
    CONSTRAINT autopilot_task_event_idempotency_uk UNIQUE (task_id, idempotency_key),
    CONSTRAINT autopilot_task_event_sequence_ck CHECK (sequence_no > 0),
    CONSTRAINT autopilot_task_event_type_ck CHECK (char_length(event_type) BETWEEN 1 AND 120),
    CONSTRAINT autopilot_task_event_state_from_ck CHECK (
        state_from IS NULL OR state_from IN (
            'NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING',
            'OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED'
        )
    ),
    CONSTRAINT autopilot_task_event_state_to_ck CHECK (state_to IN (
        'NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING',
        'OWNER_REQUIRED','FAILED_CLOSED','BUDGET_STOP','DONE','CANCELLED'
    )),
    CONSTRAINT autopilot_task_event_payload_ck CHECK (jsonb_typeof(payload_json)='object'),
    CONSTRAINT autopilot_task_event_actor_type_ck CHECK (
        actor_type IN ('DIRECTOR','AUTOPILOT','SYSTEM','PROVIDER','TEST')
    ),
    CONSTRAINT autopilot_task_event_actor_ref_ck CHECK (char_length(actor_ref) BETWEEN 1 AND 200),
    CONSTRAINT autopilot_task_event_idempotency_ck CHECK (
        char_length(idempotency_key) BETWEEN 1 AND 240
    ),
    CONSTRAINT autopilot_task_event_time_ck CHECK (recorded_at >= occurred_at)
);
CREATE INDEX IF NOT EXISTS autopilot_task_event_task_time_idx
    ON autopilot.task_event(task_id, sequence_no);

CREATE TABLE IF NOT EXISTS autopilot.step_attempt (
    step_attempt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_key text NOT NULL,
    attempt_no integer NOT NULL,
    executor_type text NOT NULL,
    capability_name text NOT NULL,
    idempotency_key text NOT NULL,
    input_fingerprint text NOT NULL,
    status text NOT NULL DEFAULT 'RESERVED',
    external_ref text,
    result_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    lease_epoch bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT autopilot_step_attempt_no_uk UNIQUE (task_id, step_key, attempt_no),
    CONSTRAINT autopilot_step_idempotency_uk UNIQUE (task_id, idempotency_key),
    CONSTRAINT autopilot_step_key_ck CHECK (char_length(step_key) BETWEEN 1 AND 160),
    CONSTRAINT autopilot_step_attempt_no_ck CHECK (attempt_no > 0),
    CONSTRAINT autopilot_step_executor_ck CHECK (char_length(executor_type) BETWEEN 1 AND 80),
    CONSTRAINT autopilot_step_capability_ck CHECK (char_length(capability_name) BETWEEN 1 AND 160),
    CONSTRAINT autopilot_step_idempotency_ck CHECK (char_length(idempotency_key) BETWEEN 1 AND 240),
    CONSTRAINT autopilot_step_input_fingerprint_ck CHECK (
        input_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT autopilot_step_status_ck CHECK (
        status IN ('RESERVED','DISPATCHED','WAITING','SUCCEEDED','FAILED','CANCELLED')
    ),
    CONSTRAINT autopilot_step_result_json_ck CHECK (
        jsonb_typeof(result_summary_json)='object'
    ),
    CONSTRAINT autopilot_step_lease_epoch_ck CHECK (lease_epoch >= 0),
    CONSTRAINT autopilot_step_completion_ck CHECK (
        (status IN ('SUCCEEDED','FAILED','CANCELLED') AND completed_at IS NOT NULL)
        OR (status NOT IN ('SUCCEEDED','FAILED','CANCELLED') AND completed_at IS NULL)
    ),
    CONSTRAINT autopilot_step_time_ck CHECK (
        (started_at IS NULL OR started_at >= created_at)
        AND (completed_at IS NULL OR completed_at >= COALESCE(started_at, created_at))
    )
);
CREATE INDEX IF NOT EXISTS autopilot_step_task_status_idx
    ON autopilot.step_attempt(task_id, status, created_at);
CREATE INDEX IF NOT EXISTS autopilot_step_external_ref_idx
    ON autopilot.step_attempt(external_ref)
    WHERE external_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS autopilot.external_event (
    external_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    provider text NOT NULL,
    provider_event_id text NOT NULL,
    event_type text NOT NULL,
    correlation_id text NOT NULL,
    signature_verified boolean NOT NULL,
    payload_fingerprint text NOT NULL,
    normalized_payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    CONSTRAINT autopilot_external_event_provider_uk UNIQUE (provider, provider_event_id),
    CONSTRAINT autopilot_external_event_provider_ck CHECK (char_length(provider) BETWEEN 1 AND 80),
    CONSTRAINT autopilot_external_event_provider_id_ck CHECK (
        char_length(provider_event_id) BETWEEN 1 AND 240
    ),
    CONSTRAINT autopilot_external_event_type_ck CHECK (char_length(event_type) BETWEEN 1 AND 120),
    CONSTRAINT autopilot_external_event_correlation_ck CHECK (
        char_length(correlation_id) BETWEEN 1 AND 240
    ),
    CONSTRAINT autopilot_external_event_signature_ck CHECK (signature_verified),
    CONSTRAINT autopilot_external_event_fingerprint_ck CHECK (
        payload_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT autopilot_external_event_payload_ck CHECK (
        jsonb_typeof(normalized_payload_json)='object'
    ),
    CONSTRAINT autopilot_external_event_time_ck CHECK (
        processed_at IS NULL OR processed_at >= received_at
    )
);
CREATE INDEX IF NOT EXISTS autopilot_external_event_correlation_idx
    ON autopilot.external_event(provider, correlation_id, received_at);

CREATE TABLE IF NOT EXISTS autopilot.wait_condition (
    wait_condition_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_attempt_id uuid REFERENCES autopilot.step_attempt(step_attempt_id),
    provider text NOT NULL,
    correlation_id text NOT NULL,
    expected_event_types_json jsonb NOT NULL,
    hook_generation integer NOT NULL,
    hook_token_hash text NOT NULL,
    deadline_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    last_reconciled_at timestamptz,
    satisfied_by_event_id uuid REFERENCES autopilot.external_event(external_event_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    satisfied_at timestamptz,
    CONSTRAINT autopilot_wait_provider_correlation_uk UNIQUE (
        provider, correlation_id, hook_generation
    ),
    CONSTRAINT autopilot_wait_provider_ck CHECK (char_length(provider) BETWEEN 1 AND 80),
    CONSTRAINT autopilot_wait_correlation_ck CHECK (char_length(correlation_id) BETWEEN 1 AND 240),
    CONSTRAINT autopilot_wait_event_types_ck CHECK (
        jsonb_typeof(expected_event_types_json)='array'
        AND jsonb_array_length(expected_event_types_json) > 0
    ),
    CONSTRAINT autopilot_wait_hook_generation_ck CHECK (hook_generation > 0),
    CONSTRAINT autopilot_wait_hook_hash_ck CHECK (hook_token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT autopilot_wait_deadline_ck CHECK (deadline_at > created_at),
    CONSTRAINT autopilot_wait_status_ck CHECK (
        status IN ('ACTIVE','SATISFIED','EXPIRED','CANCELLED')
    ),
    CONSTRAINT autopilot_wait_satisfaction_ck CHECK (
        (
            status='SATISFIED'
            AND satisfied_by_event_id IS NOT NULL
            AND satisfied_at IS NOT NULL
        )
        OR
        (
            status<>'SATISFIED'
            AND satisfied_by_event_id IS NULL
            AND satisfied_at IS NULL
        )
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS autopilot_wait_one_active_per_task_uk
    ON autopilot.wait_condition(task_id)
    WHERE status='ACTIVE';
CREATE INDEX IF NOT EXISTS autopilot_wait_active_deadline_idx
    ON autopilot.wait_condition(deadline_at, task_id)
    WHERE status='ACTIVE';

CREATE TABLE IF NOT EXISTS autopilot.evidence (
    evidence_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_attempt_id uuid REFERENCES autopilot.step_attempt(step_attempt_id),
    evidence_class text NOT NULL,
    provider text NOT NULL,
    external_ref text NOT NULL,
    content_sha256 text,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    retained boolean NOT NULL DEFAULT true,
    idempotency_key text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT autopilot_evidence_idempotency_uk UNIQUE (task_id, idempotency_key),
    CONSTRAINT autopilot_evidence_class_ck CHECK (char_length(evidence_class) BETWEEN 1 AND 120),
    CONSTRAINT autopilot_evidence_provider_ck CHECK (char_length(provider) BETWEEN 1 AND 80),
    CONSTRAINT autopilot_evidence_ref_ck CHECK (char_length(external_ref) BETWEEN 1 AND 500),
    CONSTRAINT autopilot_evidence_sha_ck CHECK (
        content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT autopilot_evidence_metadata_ck CHECK (jsonb_typeof(metadata_json)='object'),
    CONSTRAINT autopilot_evidence_expiry_ck CHECK (
        expires_at IS NULL OR expires_at > observed_at
    ),
    CONSTRAINT autopilot_evidence_idempotency_ck CHECK (
        char_length(idempotency_key) BETWEEN 1 AND 240
    ),
    CONSTRAINT autopilot_evidence_time_ck CHECK (recorded_at >= observed_at)
);
CREATE INDEX IF NOT EXISTS autopilot_evidence_task_class_idx
    ON autopilot.evidence(task_id, evidence_class, observed_at DESC);

CREATE TABLE IF NOT EXISTS autopilot.approval (
    approval_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    approval_type text NOT NULL,
    action_fingerprint text NOT NULL,
    action_summary text NOT NULL,
    status text NOT NULL DEFAULT 'REQUESTED',
    request_idempotency_key text NOT NULL,
    requested_by text NOT NULL,
    decided_by text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    expires_at timestamptz,
    decision_notes text,
    CONSTRAINT autopilot_approval_request_uk UNIQUE (task_id, request_idempotency_key),
    CONSTRAINT autopilot_approval_type_ck CHECK (char_length(approval_type) BETWEEN 1 AND 120),
    CONSTRAINT autopilot_approval_fingerprint_ck CHECK (
        action_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT autopilot_approval_summary_ck CHECK (char_length(action_summary) BETWEEN 1 AND 1000),
    CONSTRAINT autopilot_approval_status_ck CHECK (
        status IN ('REQUESTED','APPROVED','REJECTED','EXPIRED','CONSUMED')
    ),
    CONSTRAINT autopilot_approval_decision_ck CHECK (
        (
            status='REQUESTED'
            AND decided_by IS NULL
            AND decided_at IS NULL
        )
        OR
        (
            status<>'REQUESTED'
            AND decided_by IS NOT NULL
            AND decided_at IS NOT NULL
        )
    ),
    CONSTRAINT autopilot_approval_expiry_ck CHECK (
        expires_at IS NULL OR expires_at > requested_at
    )
);
CREATE INDEX IF NOT EXISTS autopilot_approval_pending_idx
    ON autopilot.approval(task_id, requested_at)
    WHERE status='REQUESTED';

CREATE TABLE IF NOT EXISTS autopilot.usage_ledger (
    usage_id uuid PRIMARY KEY DEFAULT uuidv7(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_attempt_id uuid REFERENCES autopilot.step_attempt(step_attempt_id),
    provider text NOT NULL,
    model text,
    status text NOT NULL DEFAULT 'RESERVED',
    reserved_cost_usd numeric(14,6) NOT NULL,
    actual_cost_usd numeric(14,6) NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0,
    cached_input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    provider_response_ref text,
    idempotency_key text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    finalized_at timestamptz,
    CONSTRAINT autopilot_usage_idempotency_uk UNIQUE (task_id, idempotency_key),
    CONSTRAINT autopilot_usage_provider_ck CHECK (char_length(provider) BETWEEN 1 AND 80),
    CONSTRAINT autopilot_usage_status_ck CHECK (
        status IN ('RESERVED','FINALIZED','RELEASED')
    ),
    CONSTRAINT autopilot_usage_reserved_ck CHECK (reserved_cost_usd >= 0),
    CONSTRAINT autopilot_usage_actual_ck CHECK (
        actual_cost_usd >= 0 AND actual_cost_usd <= reserved_cost_usd
    ),
    CONSTRAINT autopilot_usage_tokens_ck CHECK (
        input_tokens >= 0
        AND cached_input_tokens >= 0
        AND output_tokens >= 0
        AND cached_input_tokens <= input_tokens
    ),
    CONSTRAINT autopilot_usage_idempotency_ck CHECK (
        char_length(idempotency_key) BETWEEN 1 AND 240
    ),
    CONSTRAINT autopilot_usage_finalized_ck CHECK (
        (status='RESERVED' AND finalized_at IS NULL)
        OR (status IN ('FINALIZED','RELEASED') AND finalized_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS autopilot_usage_task_time_idx
    ON autopilot.usage_ledger(task_id, recorded_at);

CREATE TABLE IF NOT EXISTS autopilot.resource_lease (
    resource_key text PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    lease_epoch bigint NOT NULL,
    expires_at timestamptz NOT NULL,
    scope_fingerprint text NOT NULL,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT autopilot_resource_key_ck CHECK (char_length(resource_key) BETWEEN 1 AND 240),
    CONSTRAINT autopilot_resource_lease_epoch_ck CHECK (lease_epoch > 0),
    CONSTRAINT autopilot_resource_scope_fingerprint_ck CHECK (
        scope_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT autopilot_resource_lease_time_ck CHECK (
        expires_at > acquired_at AND updated_at >= acquired_at
    )
);
CREATE INDEX IF NOT EXISTS autopilot_resource_lease_expiry_idx
    ON autopilot.resource_lease(expires_at);

CREATE OR REPLACE VIEW autopilot.task_status AS
SELECT
    t.task_id,
    t.task_key,
    t.goal_type,
    t.goal_version,
    t.status,
    t.governance_mode,
    t.risk_class,
    t.current_step_key,
    t.workflow_provider,
    t.workflow_run_ref,
    t.workflow_version,
    t.model_turn_cap,
    t.cost_cap_usd,
    t.cost_reserved_usd,
    t.cost_actual_usd,
    t.created_at,
    t.updated_at,
    t.terminal_at,
    t.terminal_reason_code,
    t.terminal_summary,
    (
        SELECT count(*)
        FROM autopilot.step_attempt s
        WHERE s.task_id=t.task_id
    ) AS step_attempt_count,
    (
        SELECT count(*)
        FROM autopilot.evidence e
        WHERE e.task_id=t.task_id AND e.retained
    ) AS retained_evidence_count,
    (
        SELECT min(w.deadline_at)
        FROM autopilot.wait_condition w
        WHERE w.task_id=t.task_id AND w.status='ACTIVE'
    ) AS active_wait_deadline
FROM autopilot.task t;
