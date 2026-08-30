\set ON_ERROR_STOP on
BEGIN;

-- School Autopilot Controller v1.1 — Oracle-resident shadow state.
--
-- This migration deliberately enables only three non-mutating task templates.
-- It creates no LOGIN credential, performs no external call, and grants the
-- future Oracle principal access only through bounded SECURITY DEFINER RPCs.

CREATE SCHEMA IF NOT EXISTS autopilot;

DO $$
DECLARE
    role_name text;
    attrs record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autopilot_runtime') THEN
        CREATE ROLE autopilot_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autopilot_runtime_principal') THEN
        CREATE ROLE autopilot_runtime_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;

    FOREACH role_name IN ARRAY ARRAY['autopilot_runtime', 'autopilot_runtime_principal'] LOOP
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
          INTO attrs
          FROM pg_roles
         WHERE rolname = role_name;
        IF attrs.rolcanlogin OR attrs.rolsuper OR attrs.rolcreatedb OR attrs.rolcreaterole
           OR attrs.rolreplication OR attrs.rolbypassrls THEN
            RAISE EXCEPTION 'AUTOPILOT_ROLE_UNSAFE: %', role_name;
        END IF;
    END LOOP;
END $$;

GRANT autopilot_runtime TO autopilot_runtime_principal;

CREATE TABLE autopilot.task (
    task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_key text NOT NULL UNIQUE
        CHECK (task_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    goal_type text NOT NULL CHECK (goal_type IN (
        'AUTOPILOT_SMOKE_V1',
        'EXTERNAL_WAIT_SHADOW_V1',
        'OWNER_BOUNDARY_V1'
    )),
    goal_version text NOT NULL DEFAULT '1.0'
        CHECK (goal_version ~ '^[0-9]+\.[0-9]+$'),
    goal_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL CHECK (status IN (
        'NEW', 'VALIDATING', 'READY', 'RUNNING', 'WAITING_EXTERNAL',
        'EVALUATING', 'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
        'DONE', 'CANCELLED'
    )),
    governance_mode text NOT NULL DEFAULT 'ASSURED'
        CHECK (governance_mode = 'ASSURED'),
    risk_class text NOT NULL DEFAULT 'SHADOW_READ_ONLY'
        CHECK (risk_class = 'SHADOW_READ_ONLY'),
    current_step_key text NOT NULL DEFAULT 'validate',
    step_cursor smallint NOT NULL DEFAULT 0 CHECK (step_cursor BETWEEN 0 AND 32),
    acceptance_contract_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    allowed_capabilities_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    priority smallint NOT NULL DEFAULT 20 CHECK (priority IN (0, 10, 20, 30)),
    not_before timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    lease_until timestamptz,
    attempts smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts smallint NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 5),
    model_turn_cap smallint NOT NULL DEFAULT 0 CHECK (model_turn_cap = 0),
    cost_cap_microusd bigint NOT NULL DEFAULT 0
        CHECK (cost_cap_microusd BETWEEN 0 AND 20000000),
    cost_reserved_microusd bigint NOT NULL DEFAULT 0 CHECK (cost_reserved_microusd >= 0),
    cost_actual_microusd bigint NOT NULL DEFAULT 0 CHECK (cost_actual_microusd >= 0),
    terminal_reason_code text,
    safe_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL DEFAULT 'DIRECTOR'
        CHECK (length(created_by) BETWEEN 1 AND 256),
    source text NOT NULL DEFAULT 'CHATGPT'
        CHECK (length(source) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT autopilot_task_goal_json_object CHECK (jsonb_typeof(goal_json) = 'object'),
    CONSTRAINT autopilot_task_acceptance_object CHECK (jsonb_typeof(acceptance_contract_json) = 'object'),
    CONSTRAINT autopilot_task_capabilities_array CHECK (jsonb_typeof(allowed_capabilities_json) = 'array'),
    CONSTRAINT autopilot_task_safe_summary_object CHECK (jsonb_typeof(safe_summary_json) = 'object'),
    CONSTRAINT autopilot_task_payload_bounds CHECK (
        octet_length(goal_json::text) <= 8192
        AND octet_length(acceptance_contract_json::text) <= 8192
        AND octet_length(allowed_capabilities_json::text) <= 4096
        AND octet_length(safe_summary_json::text) <= 8192
    ),
    CONSTRAINT autopilot_task_lease_shape CHECK (
        (status = 'RUNNING' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL)
        OR (status <> 'RUNNING' AND lease_owner IS NULL AND lease_until IS NULL)
    ),
    CONSTRAINT autopilot_task_terminal_shape CHECK (
        (status IN ('OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP', 'DONE', 'CANCELLED')
         AND completed_at IS NOT NULL AND terminal_reason_code IS NOT NULL)
        OR (status NOT IN ('OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP', 'DONE', 'CANCELLED')
            AND completed_at IS NULL)
    ),
    CONSTRAINT autopilot_task_cost_shape CHECK (
        cost_actual_microusd <= cost_reserved_microusd
        AND cost_reserved_microusd <= cost_cap_microusd
    )
);

CREATE INDEX autopilot_task_ready_idx
    ON autopilot.task (priority, not_before, created_at)
    WHERE status = 'READY';
CREATE INDEX autopilot_task_running_lease_idx
    ON autopilot.task (lease_until)
    WHERE status = 'RUNNING';

CREATE TABLE autopilot.task_event (
    task_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    event_type text NOT NULL CHECK (event_type ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    state_from text,
    state_to text,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor_type text NOT NULL CHECK (actor_type IN ('DIRECTOR', 'ORACLE_WORKER', 'EXTERNAL_EVENT', 'SYSTEM')),
    actor_ref text NOT NULL CHECK (length(actor_ref) BETWEEN 1 AND 256),
    idempotency_key text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, sequence_no),
    CONSTRAINT autopilot_event_payload_object CHECK (jsonb_typeof(payload_json) = 'object'),
    CONSTRAINT autopilot_event_payload_bound CHECK (octet_length(payload_json::text) <= 8192)
);

CREATE TABLE autopilot.step_attempt (
    step_attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_key text NOT NULL CHECK (step_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    attempt_no smallint NOT NULL CHECK (attempt_no BETWEEN 1 AND 5),
    executor_type text NOT NULL DEFAULT 'ORACLE_RESIDENT',
    capability_name text NOT NULL CHECK (capability_name IN (
        'shadow.noop', 'shadow.wait', 'policy.owner_boundary'
    )),
    idempotency_key text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK (status IN ('RUNNING', 'WAITING_EXTERNAL', 'COMPLETED', 'FAILED_CLOSED')),
    result_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    lease_epoch bigint NOT NULL CHECK (lease_epoch > 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (task_id, step_key, attempt_no),
    CONSTRAINT autopilot_step_summary_object CHECK (jsonb_typeof(result_summary_json) = 'object'),
    CONSTRAINT autopilot_step_summary_bound CHECK (octet_length(result_summary_json::text) <= 8192)
);

CREATE TABLE autopilot.wait_condition (
    wait_condition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_attempt_id uuid NOT NULL REFERENCES autopilot.step_attempt(step_attempt_id),
    provider text NOT NULL CHECK (provider IN ('SYNTHETIC', 'GITHUB_READ_ONLY')),
    correlation_id text NOT NULL CHECK (correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    expected_event_type text NOT NULL CHECK (expected_event_type ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    deadline_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SATISFIED', 'EXPIRED', 'CANCELLED')),
    satisfied_by_event_id bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    satisfied_at timestamptz
);

CREATE UNIQUE INDEX autopilot_one_active_wait_per_task
    ON autopilot.wait_condition(task_id)
    WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX autopilot_wait_correlation_active
    ON autopilot.wait_condition(provider, correlation_id)
    WHERE status = 'ACTIVE';

CREATE TABLE autopilot.external_event (
    external_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider text NOT NULL CHECK (provider IN ('SYNTHETIC', 'GITHUB_READ_ONLY')),
    provider_event_id text NOT NULL CHECK (provider_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    event_type text NOT NULL CHECK (event_type ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    correlation_id text NOT NULL CHECK (correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    signature_verified boolean NOT NULL,
    payload_fingerprint text NOT NULL CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    UNIQUE (provider, provider_event_id)
);

ALTER TABLE autopilot.wait_condition
    ADD CONSTRAINT autopilot_wait_satisfied_event_fk
    FOREIGN KEY (satisfied_by_event_id) REFERENCES autopilot.external_event(external_event_id);

CREATE TABLE autopilot.evidence (
    evidence_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    step_attempt_id uuid REFERENCES autopilot.step_attempt(step_attempt_id),
    evidence_class text NOT NULL CHECK (evidence_class IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'OWNER_BOUNDARY_PROOF'
    )),
    provider text NOT NULL CHECK (provider IN ('ORACLE_RESIDENT', 'NEON_STATE_MACHINE')),
    external_ref text,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    retained boolean NOT NULL DEFAULT true CHECK (retained),
    observed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, evidence_class, content_sha256),
    CONSTRAINT autopilot_evidence_metadata_object CHECK (jsonb_typeof(metadata_json) = 'object'),
    CONSTRAINT autopilot_evidence_metadata_bound CHECK (octet_length(metadata_json::text) <= 8192)
);

CREATE TABLE autopilot.usage_ledger (
    usage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    provider text NOT NULL CHECK (provider IN ('OPENAI', 'OCI', 'GITHUB', 'NEON')),
    reserved_microusd bigint NOT NULL DEFAULT 0 CHECK (reserved_microusd >= 0),
    actual_microusd bigint NOT NULL DEFAULT 0 CHECK (actual_microusd >= 0 AND actual_microusd <= reserved_microusd),
    input_tokens integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    idempotency_key text NOT NULL UNIQUE,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE autopilot.resource_lease (
    resource_key text PRIMARY KEY CHECK (resource_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$'),
    task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    lease_epoch bigint NOT NULL CHECK (lease_epoch > 0),
    owner_ref text NOT NULL CHECK (length(owner_ref) BETWEEN 1 AND 256),
    expires_at timestamptz NOT NULL,
    scope_fingerprint text NOT NULL CHECK (scope_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE OR REPLACE FUNCTION autopilot.touch_task_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_task_touch
BEFORE UPDATE ON autopilot.task
FOR EACH ROW EXECUTE FUNCTION autopilot.touch_task_updated_at();

CREATE OR REPLACE FUNCTION autopilot.notify_ready_task()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'READY' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
        PERFORM pg_notify('autopilot_ready', NEW.task_id::text);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_task_notify
AFTER INSERT OR UPDATE OF status ON autopilot.task
FOR EACH ROW EXECUTE FUNCTION autopilot.notify_ready_task();

CREATE OR REPLACE FUNCTION autopilot.prevent_immutable_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'AUTOPILOT_APPEND_ONLY';
END;
$$;

CREATE TRIGGER autopilot_task_event_immutable
BEFORE UPDATE OR DELETE ON autopilot.task_event
FOR EACH ROW EXECUTE FUNCTION autopilot.prevent_immutable_change();
CREATE TRIGGER autopilot_external_event_immutable
BEFORE UPDATE OR DELETE ON autopilot.external_event
FOR EACH ROW EXECUTE FUNCTION autopilot.prevent_immutable_change();
CREATE TRIGGER autopilot_evidence_immutable
BEFORE UPDATE OR DELETE ON autopilot.evidence
FOR EACH ROW EXECUTE FUNCTION autopilot.prevent_immutable_change();
CREATE TRIGGER autopilot_usage_immutable
BEFORE UPDATE OR DELETE ON autopilot.usage_ledger
FOR EACH ROW EXECUTE FUNCTION autopilot.prevent_immutable_change();

CREATE OR REPLACE FUNCTION autopilot.next_event_sequence(p_task_id uuid)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    next_sequence integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(p_task_id::text, 782));
    SELECT COALESCE(max(sequence_no), 0) + 1
      INTO next_sequence
      FROM autopilot.task_event
     WHERE task_id = p_task_id;
    RETURN next_sequence;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.record_event(
    p_task_id uuid,
    p_event_type text,
    p_state_from text,
    p_state_to text,
    p_payload jsonb,
    p_actor_type text,
    p_actor_ref text,
    p_idempotency_key text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    result_id bigint;
    existing_event autopilot.task_event;
BEGIN
    INSERT INTO autopilot.task_event (
        task_id, sequence_no, event_type, state_from, state_to, payload_json,
        actor_type, actor_ref, idempotency_key
    ) VALUES (
        p_task_id, autopilot.next_event_sequence(p_task_id), p_event_type,
        p_state_from, p_state_to, COALESCE(p_payload, '{}'::jsonb),
        p_actor_type, p_actor_ref, p_idempotency_key
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING task_event_id INTO result_id;
    IF result_id IS NULL THEN
        SELECT * INTO existing_event
          FROM autopilot.task_event
         WHERE idempotency_key = p_idempotency_key;
        IF NOT FOUND OR existing_event.task_id <> p_task_id
           OR existing_event.event_type <> p_event_type
           OR existing_event.state_from IS DISTINCT FROM p_state_from
           OR existing_event.state_to IS DISTINCT FROM p_state_to
           OR existing_event.payload_json <> COALESCE(p_payload, '{}'::jsonb)
           OR existing_event.actor_type <> p_actor_type
           OR existing_event.actor_ref <> p_actor_ref THEN
            RAISE EXCEPTION 'AUTOPILOT_EVENT_IDEMPOTENCY_CONFLICT';
        END IF;
        result_id := existing_event.task_event_id;
    END IF;
    RETURN result_id;
END;
$$;

DROP FUNCTION IF EXISTS autopilot.create_shadow_task(text, text, jsonb, smallint, bigint, text, text);

CREATE OR REPLACE FUNCTION autopilot.create_shadow_task(
    p_task_key text,
    p_goal_type text,
    p_goal_json jsonb DEFAULT '{}'::jsonb,
    p_priority integer DEFAULT 20,
    p_cost_cap_microusd bigint DEFAULT 0,
    p_created_by text DEFAULT 'DIRECTOR',
    p_source text DEFAULT 'CHATGPT'
)
RETURNS TABLE(task_id uuid, status text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.task;
    inserted autopilot.task;
BEGIN
    IF p_task_key IS NULL OR p_task_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_INVALID';
    END IF;
    IF p_goal_type NOT IN ('AUTOPILOT_SMOKE_V1', 'EXTERNAL_WAIT_SHADOW_V1', 'OWNER_BOUNDARY_V1') THEN
        RAISE EXCEPTION 'AUTOPILOT_CAPABILITY_UNKNOWN';
    END IF;
    IF p_priority NOT IN (0, 10, 20, 30) THEN
        RAISE EXCEPTION 'AUTOPILOT_PRIORITY_INVALID';
    END IF;
    IF jsonb_typeof(COALESCE(p_goal_json, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_goal_json, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_GOAL_INVALID';
    END IF;
    IF p_goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
       AND COALESCE(p_goal_json->>'correlation_id', '') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_CORRELATION_INVALID';
    END IF;
    IF (p_goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
        AND (SELECT count(*) FROM jsonb_object_keys(COALESCE(p_goal_json, '{}'::jsonb))) <> 1)
       OR (p_goal_type <> 'EXTERNAL_WAIT_SHADOW_V1'
           AND COALESCE(p_goal_json, '{}'::jsonb) <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'AUTOPILOT_GOAL_FIELDS_INVALID';
    END IF;
    IF p_cost_cap_microusd NOT BETWEEN 0 AND 20000000 THEN
        RAISE EXCEPTION 'AUTOPILOT_COST_CAP_INVALID';
    END IF;
    IF length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_ORIGIN_INVALID';
    END IF;

    SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> p_goal_type OR existing.goal_json <> COALESCE(p_goal_json, '{}'::jsonb)
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> p_cost_cap_microusd
           OR existing.created_by <> p_created_by OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json, priority,
        cost_cap_microusd, created_by, source
    ) VALUES (
        p_task_key, p_goal_type, COALESCE(p_goal_json, '{}'::jsonb), 'READY',
        CASE p_goal_type
            WHEN 'AUTOPILOT_SMOKE_V1' THEN 'shadow.noop'
            WHEN 'EXTERNAL_WAIT_SHADOW_V1' THEN 'shadow.wait'
            ELSE 'policy.owner_boundary'
        END,
        jsonb_build_object('retained_evidence_required', true, 'production_mutation', false),
        CASE p_goal_type
            WHEN 'AUTOPILOT_SMOKE_V1' THEN '["shadow.noop"]'::jsonb
            WHEN 'EXTERNAL_WAIT_SHADOW_V1' THEN '["shadow.wait"]'::jsonb
            ELSE '["policy.owner_boundary"]'::jsonb
        END,
        p_priority::smallint, p_cost_cap_microusd, p_created_by, p_source
    ) ON CONFLICT (task_key) DO NOTHING
    RETURNING * INTO inserted;

    IF inserted.task_id IS NULL THEN
        SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
        IF NOT FOUND OR existing.goal_type <> p_goal_type
           OR existing.goal_json <> COALESCE(p_goal_json, '{}'::jsonb)
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> p_cost_cap_microusd
           OR existing.created_by <> p_created_by OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    PERFORM autopilot.record_event(
        inserted.task_id, 'TASK_READY', 'NEW', 'READY',
        jsonb_build_object('goal_type', inserted.goal_type),
        'DIRECTOR', left(p_created_by, 256), 'create:' || p_task_key
    );
    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.reconcile_stale_tasks()
RETURNS TABLE(requeued integer, failed_closed integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    task_row record;
    wait_row record;
    requeued_count integer := 0;
    failed_count integer := 0;
BEGIN
    FOR wait_row IN
        SELECT w.wait_condition_id, w.task_id, w.step_attempt_id
          FROM autopilot.wait_condition w
          JOIN autopilot.task t ON t.task_id = w.task_id
         WHERE w.status = 'ACTIVE' AND w.deadline_at <= now()
           AND t.status = 'WAITING_EXTERNAL'
         FOR UPDATE OF w, t SKIP LOCKED
    LOOP
        UPDATE autopilot.wait_condition
           SET status = 'EXPIRED'
         WHERE wait_condition_id = wait_row.wait_condition_id;
        UPDATE autopilot.step_attempt
           SET status = 'FAILED_CLOSED', error_code = 'EXTERNAL_WAIT_EXPIRED',
               completed_at = now()
         WHERE step_attempt_id = wait_row.step_attempt_id
           AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.task
           SET status = 'FAILED_CLOSED', terminal_reason_code = 'EXTERNAL_WAIT_EXPIRED',
               completed_at = now()
         WHERE task_id = wait_row.task_id AND status = 'WAITING_EXTERNAL';
        PERFORM autopilot.record_event(
            wait_row.task_id, 'TASK_FAILED_CLOSED', 'WAITING_EXTERNAL', 'FAILED_CLOSED',
            jsonb_build_object('reason_code', 'EXTERNAL_WAIT_EXPIRED'),
            'SYSTEM', 'stale-reconciler',
            'wait-expired:' || wait_row.wait_condition_id::text
        );
        failed_count := failed_count + 1;
    END LOOP;

    FOR task_row IN
        SELECT task_id, status, attempts, max_attempts, lease_epoch
          FROM autopilot.task
         WHERE status = 'RUNNING' AND lease_until <= now()
         FOR UPDATE SKIP LOCKED
    LOOP
        IF task_row.attempts >= task_row.max_attempts THEN
            UPDATE autopilot.step_attempt
               SET status = 'FAILED_CLOSED', error_code = 'STALE_RETRY_BUDGET_EXHAUSTED',
                   completed_at = now()
             WHERE task_id = task_row.task_id AND lease_epoch = task_row.lease_epoch
               AND status = 'RUNNING';
            UPDATE autopilot.task
               SET status = 'FAILED_CLOSED', lease_owner = NULL, lease_until = NULL,
                   terminal_reason_code = 'STALE_RETRY_BUDGET_EXHAUSTED', completed_at = now()
             WHERE task_id = task_row.task_id;
            PERFORM autopilot.record_event(
                task_row.task_id, 'TASK_FAILED_CLOSED', 'RUNNING', 'FAILED_CLOSED',
                jsonb_build_object('reason_code', 'STALE_RETRY_BUDGET_EXHAUSTED'),
                'SYSTEM', 'stale-reconciler',
                'stale-fail:' || task_row.task_id::text || ':' || task_row.lease_epoch::text
            );
            failed_count := failed_count + 1;
        ELSE
            UPDATE autopilot.step_attempt
               SET status = 'FAILED_CLOSED', error_code = 'STALE_LEASE_RECOVERED',
                   completed_at = now()
             WHERE task_id = task_row.task_id AND lease_epoch = task_row.lease_epoch
               AND status = 'RUNNING';
            UPDATE autopilot.task
               SET status = 'READY', lease_owner = NULL, lease_until = NULL,
                   not_before = now(), terminal_reason_code = NULL
             WHERE task_id = task_row.task_id;
            PERFORM autopilot.record_event(
                task_row.task_id, 'STALE_LEASE_RECOVERED', 'RUNNING', 'READY', '{}'::jsonb,
                'SYSTEM', 'stale-reconciler',
                'stale-requeue:' || task_row.task_id::text || ':' || task_row.lease_epoch::text
            );
            requeued_count := requeued_count + 1;
        END IF;
    END LOOP;
    RETURN QUERY SELECT requeued_count, failed_count;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.claim_next_task(p_worker_id text, p_lease_seconds integer DEFAULT 60)
RETURNS TABLE(
    task_id uuid,
    goal_type text,
    goal_json jsonb,
    current_step_key text,
    step_cursor smallint,
    lease_epoch bigint,
    attempts smallint,
    max_attempts smallint,
    cost_cap_microusd bigint,
    cost_reserved_microusd bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    claimed autopilot.task;
BEGIN
    IF p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_WORKER_ID_INVALID';
    END IF;
    IF p_lease_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'AUTOPILOT_LEASE_SECONDS_INVALID';
    END IF;

    WITH candidate AS (
        SELECT t.task_id
          FROM autopilot.task t
         WHERE t.status = 'READY' AND t.not_before <= now()
           AND t.attempts < t.max_attempts
         ORDER BY t.priority, t.not_before, t.created_at
         FOR UPDATE SKIP LOCKED
         LIMIT 1
    )
    UPDATE autopilot.task t
       SET status = 'RUNNING', lease_owner = p_worker_id,
           lease_epoch = t.lease_epoch + 1,
           lease_until = now() + make_interval(secs => p_lease_seconds),
           attempts = t.attempts + 1,
           started_at = COALESCE(t.started_at, now())
      FROM candidate
     WHERE t.task_id = candidate.task_id
    RETURNING t.* INTO claimed;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    INSERT INTO autopilot.step_attempt (
        task_id, step_key, attempt_no, capability_name, idempotency_key,
        input_fingerprint, status, lease_epoch
    ) VALUES (
        claimed.task_id, claimed.current_step_key, claimed.attempts,
        claimed.current_step_key,
        'step:' || claimed.task_id::text || ':' || claimed.current_step_key || ':'
            || claimed.step_cursor::text || ':attempt:' || claimed.attempts::text,
        encode(public.digest(convert_to(claimed.goal_json::text, 'UTF8'), 'sha256'), 'hex'),
        'RUNNING', claimed.lease_epoch
    ) ON CONFLICT (idempotency_key) DO NOTHING;

    PERFORM autopilot.record_event(
        claimed.task_id, 'TASK_CLAIMED', 'READY', 'RUNNING',
        jsonb_build_object('lease_epoch', claimed.lease_epoch, 'step_key', claimed.current_step_key),
        'ORACLE_WORKER', p_worker_id,
        'claim:' || claimed.task_id::text || ':' || claimed.lease_epoch::text
    );

    RETURN QUERY SELECT claimed.task_id, claimed.goal_type, claimed.goal_json,
        claimed.current_step_key, claimed.step_cursor, claimed.lease_epoch,
        claimed.attempts, claimed.max_attempts, claimed.cost_cap_microusd,
        claimed.cost_reserved_microusd;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.heartbeat_task(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint, p_lease_seconds integer DEFAULT 60
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    changed boolean;
BEGIN
    IF p_lease_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'AUTOPILOT_LEASE_SECONDS_INVALID';
    END IF;
    UPDATE autopilot.task
       SET lease_until = now() + make_interval(secs => p_lease_seconds)
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch
       AND lease_until > now();
    changed := FOUND;
    RETURN changed;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.mark_waiting_external(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_provider text, p_correlation_id text, p_expected_event_type text,
    p_deadline_seconds integer DEFAULT 300
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    step_id uuid;
BEGIN
    IF p_provider NOT IN ('SYNTHETIC', 'GITHUB_READ_ONLY')
       OR p_correlation_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_expected_event_type !~ '^[A-Z][A-Z0-9_]{1,63}$'
       OR p_deadline_seconds NOT BETWEEN 30 AND 86400 THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_CONTRACT_INVALID';
    END IF;

    SELECT step_attempt_id INTO step_id
      FROM autopilot.step_attempt
     WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch AND status = 'RUNNING'
     ORDER BY started_at DESC LIMIT 1;
    IF step_id IS NULL THEN
        RETURN false;
    END IF;

    UPDATE autopilot.task
       SET status = 'WAITING_EXTERNAL', current_step_key = 'shadow.wait.resume',
           step_cursor = 1, lease_owner = NULL, lease_until = NULL
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    UPDATE autopilot.step_attempt
       SET status = 'WAITING_EXTERNAL'
     WHERE step_attempt_id = step_id;
    INSERT INTO autopilot.wait_condition (
        task_id, step_attempt_id, provider, correlation_id,
        expected_event_type, deadline_at
    ) VALUES (
        p_task_id, step_id, p_provider, p_correlation_id,
        p_expected_event_type, now() + make_interval(secs => p_deadline_seconds)
    );
    PERFORM autopilot.record_event(
        p_task_id, 'WAIT_CREATED', 'RUNNING', 'WAITING_EXTERNAL',
        jsonb_build_object('provider', p_provider, 'correlation_id', p_correlation_id,
                           'expected_event_type', p_expected_event_type),
        'ORACLE_WORKER', p_worker_id,
        'wait:' || p_task_id::text || ':' || p_lease_epoch::text
    );
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.ingest_external_event(
    p_provider text, p_provider_event_id text, p_event_type text,
    p_correlation_id text, p_payload_fingerprint text,
    p_signature_verified boolean
)
RETURNS TABLE(accepted boolean, resumed_task_id uuid, resulting_state text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    event_id bigint;
    wait_row autopilot.wait_condition;
BEGIN
    IF NOT p_signature_verified THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_SIGNATURE_INVALID';
    END IF;
    IF p_provider NOT IN ('SYNTHETIC', 'GITHUB_READ_ONLY')
       OR p_provider_event_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_event_type !~ '^[A-Z][A-Z0-9_]{1,63}$'
       OR p_correlation_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_INVALID';
    END IF;

    INSERT INTO autopilot.external_event (
        provider, provider_event_id, event_type, correlation_id,
        signature_verified, payload_fingerprint
    ) VALUES (
        p_provider, p_provider_event_id, p_event_type, p_correlation_id,
        true, p_payload_fingerprint
    ) ON CONFLICT (provider, provider_event_id) DO NOTHING
    RETURNING external_event_id INTO event_id;
    IF event_id IS NULL THEN
        RETURN QUERY SELECT false, NULL::uuid, 'DUPLICATE'::text;
        RETURN;
    END IF;

    SELECT * INTO wait_row
      FROM autopilot.wait_condition
     WHERE provider = p_provider AND correlation_id = p_correlation_id
       AND expected_event_type = p_event_type AND status = 'ACTIVE'
       AND deadline_at > now()
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT true, NULL::uuid, 'UNMATCHED'::text;
        RETURN;
    END IF;

    UPDATE autopilot.wait_condition
       SET status = 'SATISFIED', satisfied_by_event_id = event_id,
           satisfied_at = now()
     WHERE wait_condition_id = wait_row.wait_condition_id;
    UPDATE autopilot.step_attempt
       SET status = 'COMPLETED',
           result_summary_json = jsonb_build_object(
               'provider', p_provider,
               'provider_event_id', p_provider_event_id,
               'event_type', p_event_type
           ),
           completed_at = now()
     WHERE step_attempt_id = wait_row.step_attempt_id
       AND status = 'WAITING_EXTERNAL';
    UPDATE autopilot.task
       SET status = 'READY', current_step_key = 'shadow.wait', not_before = now()
     WHERE task_id = wait_row.task_id AND status = 'WAITING_EXTERNAL'
       AND attempts < max_attempts;
    IF NOT FOUND THEN
        -- The verified event is retained and linked to the satisfied wait, but
        -- an exhausted retry budget must never be bypassed by a resume claim.
        -- Terminalize explicitly instead of publishing an unclaimable READY
        -- task or allowing the next claim to exceed max_attempts.
        UPDATE autopilot.task
           SET status = 'FAILED_CLOSED',
               terminal_reason_code = 'EXTERNAL_RESUME_BUDGET_EXHAUSTED',
               completed_at = now()
         WHERE task_id = wait_row.task_id AND status = 'WAITING_EXTERNAL'
           AND attempts >= max_attempts;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AUTOPILOT_WAIT_TASK_STATE_MISMATCH';
        END IF;
        PERFORM autopilot.record_event(
            wait_row.task_id, 'EXTERNAL_EVENT_ACCEPTED',
            'WAITING_EXTERNAL', 'FAILED_CLOSED',
            jsonb_build_object(
                'provider', p_provider,
                'provider_event_id', p_provider_event_id,
                'event_type', p_event_type,
                'reason_code', 'EXTERNAL_RESUME_BUDGET_EXHAUSTED'
            ),
            'EXTERNAL_EVENT', p_provider,
            'external:' || p_provider || ':' || p_provider_event_id
        );
        RETURN QUERY SELECT true, wait_row.task_id, 'FAILED_CLOSED'::text;
        RETURN;
    END IF;
    PERFORM autopilot.record_event(
        wait_row.task_id, 'EXTERNAL_EVENT_ACCEPTED', 'WAITING_EXTERNAL', 'READY',
        jsonb_build_object('provider', p_provider, 'provider_event_id', p_provider_event_id,
                           'event_type', p_event_type),
        'EXTERNAL_EVENT', p_provider,
        'external:' || p_provider || ':' || p_provider_event_id
    );
    RETURN QUERY SELECT true, wait_row.task_id, 'READY'::text;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.complete_task(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_evidence_class text, p_content_sha256 text, p_summary jsonb DEFAULT '{}'::jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    step_id uuid;
    old_state text;
BEGIN
    IF p_evidence_class NOT IN ('SYNTHETIC_SHADOW_COMPLETION', 'SYNTHETIC_SHADOW_RESUME')
       OR p_content_sha256 !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_summary, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_summary, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_INVALID';
    END IF;
    SELECT status INTO old_state FROM autopilot.task
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    SELECT step_attempt_id INTO step_id
      FROM autopilot.step_attempt
     WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch AND status = 'RUNNING'
     ORDER BY started_at DESC LIMIT 1;
    IF step_id IS NULL THEN
        RETURN false;
    END IF;

    INSERT INTO autopilot.evidence (
        task_id, step_attempt_id, evidence_class, provider,
        content_sha256, metadata_json
    ) VALUES (
        p_task_id, step_id, p_evidence_class, 'ORACLE_RESIDENT',
        p_content_sha256, COALESCE(p_summary, '{}'::jsonb)
    ) ON CONFLICT DO NOTHING;

    UPDATE autopilot.step_attempt
       SET status = 'COMPLETED', result_summary_json = COALESCE(p_summary, '{}'::jsonb),
           completed_at = now()
     WHERE step_attempt_id = step_id;
    UPDATE autopilot.task
       SET status = 'DONE', lease_owner = NULL, lease_until = NULL,
           terminal_reason_code = 'ACCEPTANCE_EVIDENCE_RETAINED',
           safe_summary_json = COALESCE(p_summary, '{}'::jsonb), completed_at = now()
     WHERE task_id = p_task_id;
    IF NOT EXISTS (SELECT 1 FROM autopilot.evidence WHERE task_id = p_task_id AND retained) THEN
        RAISE EXCEPTION 'AUTOPILOT_DONE_WITHOUT_EVIDENCE';
    END IF;
    PERFORM autopilot.record_event(
        p_task_id, 'TASK_DONE', old_state, 'DONE',
        jsonb_build_object('evidence_class', p_evidence_class, 'content_sha256', p_content_sha256),
        'ORACLE_WORKER', p_worker_id,
        'done:' || p_task_id::text || ':' || p_lease_epoch::text
    );
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.mark_owner_required(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_reason_code text DEFAULT 'ACCOUNT_OWNER_ACTION_REQUIRED'
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    step_id uuid;
    evidence_hash text;
BEGIN
    IF p_reason_code !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_REASON_CODE_INVALID';
    END IF;
    SELECT s.step_attempt_id INTO step_id
      FROM autopilot.step_attempt s
      JOIN autopilot.task t ON t.task_id = s.task_id
     WHERE s.task_id = p_task_id AND s.lease_epoch = p_lease_epoch
       AND s.status = 'RUNNING'
       AND t.status = 'RUNNING' AND t.goal_type = 'OWNER_BOUNDARY_V1'
       AND t.lease_owner = p_worker_id AND t.lease_epoch = p_lease_epoch
     ORDER BY s.started_at DESC LIMIT 1
     FOR UPDATE OF t;
    IF step_id IS NULL THEN RETURN false; END IF;

    evidence_hash := encode(public.digest(convert_to(p_task_id::text || ':' || p_reason_code, 'UTF8'), 'sha256'), 'hex');
    INSERT INTO autopilot.evidence (
        task_id, step_attempt_id, evidence_class, provider, content_sha256, metadata_json
    ) VALUES (
        p_task_id, step_id, 'OWNER_BOUNDARY_PROOF', 'NEON_STATE_MACHINE', evidence_hash,
        jsonb_build_object('reason_code', p_reason_code, 'action_performed', false)
    ) ON CONFLICT DO NOTHING;
    UPDATE autopilot.step_attempt
       SET status = 'COMPLETED', result_summary_json = jsonb_build_object('reason_code', p_reason_code),
           completed_at = now()
     WHERE step_attempt_id = step_id;
    UPDATE autopilot.task
       SET status = 'OWNER_REQUIRED', lease_owner = NULL, lease_until = NULL,
           terminal_reason_code = p_reason_code,
           safe_summary_json = jsonb_build_object('required_action', p_reason_code),
           completed_at = now()
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND goal_type = 'OWNER_BOUNDARY_V1'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch;
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM autopilot.record_event(
        p_task_id, 'OWNER_REQUIRED', 'RUNNING', 'OWNER_REQUIRED',
        jsonb_build_object('reason_code', p_reason_code), 'ORACLE_WORKER', p_worker_id,
        'owner:' || p_task_id::text || ':' || p_lease_epoch::text
    );
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.reserve_usage(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_provider text, p_amount_microusd bigint, p_idempotency_key text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    target autopilot.task;
BEGIN
    IF p_provider NOT IN ('OPENAI', 'OCI', 'GITHUB', 'NEON')
       OR p_amount_microusd < 0 OR length(p_idempotency_key) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_USAGE_RESERVATION_INVALID';
    END IF;
    SELECT * INTO target FROM autopilot.task
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch
     FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    IF EXISTS (SELECT 1 FROM autopilot.usage_ledger WHERE idempotency_key = p_idempotency_key) THEN
        RETURN true;
    END IF;
    IF target.cost_reserved_microusd + p_amount_microusd > target.cost_cap_microusd THEN
        UPDATE autopilot.step_attempt
           SET status = 'FAILED_CLOSED', error_code = 'TASK_COST_CAP_EXCEEDED',
               completed_at = now()
         WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch
           AND status = 'RUNNING';
        UPDATE autopilot.task
           SET status = 'BUDGET_STOP', lease_owner = NULL, lease_until = NULL,
               terminal_reason_code = 'TASK_COST_CAP_EXCEEDED', completed_at = now()
         WHERE task_id = p_task_id;
        PERFORM autopilot.record_event(
            p_task_id, 'BUDGET_STOP', 'RUNNING', 'BUDGET_STOP',
            jsonb_build_object('requested_microusd', p_amount_microusd,
                               'remaining_microusd', target.cost_cap_microusd - target.cost_reserved_microusd),
            'ORACLE_WORKER', p_worker_id,
            'budget-stop:' || p_idempotency_key
        );
        RETURN false;
    END IF;
    INSERT INTO autopilot.usage_ledger (
        task_id, provider, reserved_microusd, idempotency_key
    ) VALUES (p_task_id, p_provider, p_amount_microusd, p_idempotency_key);
    UPDATE autopilot.task
       SET cost_reserved_microusd = cost_reserved_microusd + p_amount_microusd
     WHERE task_id = p_task_id;
    RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.fail_task(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_error_code text, p_retryable boolean
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    target autopilot.task;
    next_state text;
BEGIN
    IF p_error_code !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_ERROR_CODE_INVALID';
    END IF;
    SELECT * INTO target FROM autopilot.task
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch
     FOR UPDATE;
    IF NOT FOUND THEN RETURN 'FENCED'; END IF;
    IF p_retryable AND target.attempts < target.max_attempts THEN
        next_state := 'READY';
        UPDATE autopilot.task
           SET status = 'READY', lease_owner = NULL, lease_until = NULL,
               not_before = now() + interval '2 seconds', terminal_reason_code = NULL
         WHERE task_id = p_task_id;
    ELSE
        next_state := 'FAILED_CLOSED';
        UPDATE autopilot.task
           SET status = 'FAILED_CLOSED', lease_owner = NULL, lease_until = NULL,
               terminal_reason_code = p_error_code, completed_at = now()
         WHERE task_id = p_task_id;
    END IF;
    UPDATE autopilot.step_attempt
       SET status = 'FAILED_CLOSED', error_code = p_error_code, completed_at = now()
     WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch AND status = 'RUNNING';
    PERFORM autopilot.record_event(
        p_task_id, CASE WHEN next_state = 'READY' THEN 'TASK_RETRY_SCHEDULED' ELSE 'TASK_FAILED_CLOSED' END,
        'RUNNING', next_state, jsonb_build_object('error_code', p_error_code),
        'ORACLE_WORKER', p_worker_id,
        'failure:' || p_task_id::text || ':' || p_lease_epoch::text
    );
    RETURN next_state;
END;
$$;

CREATE VIEW autopilot.task_status AS
SELECT task_id, task_key, goal_type, goal_version, status, current_step_key,
       step_cursor, priority, attempts, max_attempts, lease_epoch, lease_until,
       cost_cap_microusd, cost_reserved_microusd, cost_actual_microusd,
       terminal_reason_code, safe_summary_json, created_at, updated_at,
       started_at, completed_at
  FROM autopilot.task;

COMMENT ON SCHEMA autopilot IS
'School Autopilot canonical orchestration state. v1.1 migration is shadow-only and exposes no arbitrary execution.';

REVOKE ALL ON SCHEMA autopilot FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA autopilot FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA autopilot FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA autopilot FROM PUBLIC;

GRANT USAGE ON SCHEMA autopilot TO autopilot_runtime;
GRANT SELECT ON autopilot.task_status TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_stale_tasks() TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.claim_next_task(text, integer) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.heartbeat_task(uuid, text, bigint, integer) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.mark_waiting_external(uuid, text, bigint, text, text, text, integer) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.complete_task(uuid, text, bigint, text, text, jsonb) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.mark_owner_required(uuid, text, bigint, text) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.reserve_usage(uuid, text, bigint, text, bigint, text) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.fail_task(uuid, text, bigint, text, boolean) TO autopilot_runtime;

-- Ingress is separate from the Oracle execution principal. The migration owner
-- may call these functions during temporary-branch verification; no public or
-- runtime-principal grant is created here.
REVOKE ALL ON FUNCTION autopilot.create_shadow_task(text, text, jsonb, integer, bigint, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.ingest_external_event(text, text, text, text, text, boolean) FROM PUBLIC;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0300_autopilot_oracle_shadow')
ON CONFLICT DO NOTHING;

COMMIT;
