-- Assistant Lab v1: isolated, non-canonical experiment/compute queue.
-- This file does not create users/roles, change school canon, or touch production
-- tables outside assistant_lab. The existing application role receives only the
-- minimal job-table rights required for one-job capability dispatch.

CREATE SCHEMA IF NOT EXISTS assistant_lab;

CREATE TABLE IF NOT EXISTS assistant_lab.job (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'CHATGPT',
    kind text NOT NULL CHECK (kind IN ('DDS3_COMPUTE', 'NOOP')),
    priority smallint NOT NULL DEFAULT 20 CHECK (priority IN (0, 10, 20, 30)),
    status text NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    payload_json jsonb NOT NULL,
    result_json jsonb,
    provenance_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_text text,
    idempotency_key text NOT NULL UNIQUE,
    dispatch_nonce_sha256 text CHECK (
        dispatch_nonce_sha256 IS NULL OR dispatch_nonce_sha256 ~ '^[0-9a-f]{64}$'
    ),
    not_before timestamptz NOT NULL DEFAULT now(),
    deadline_at timestamptz,
    claimed_by text,
    claimed_at timestamptz,
    heartbeat_at timestamptz,
    completed_at timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts smallint NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS assistant_lab_job_queue_idx
    ON assistant_lab.job (priority, created_at)
    WHERE status = 'QUEUED';

CREATE INDEX IF NOT EXISTS assistant_lab_job_running_idx
    ON assistant_lab.job (heartbeat_at)
    WHERE status = 'RUNNING';

CREATE TABLE IF NOT EXISTS assistant_lab.experiment (
    experiment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    experiment_key text NOT NULL UNIQUE,
    title text NOT NULL,
    hypothesis text NOT NULL,
    status text NOT NULL DEFAULT 'IDEA'
        CHECK (status IN ('IDEA', 'RUNNING', 'PROVEN', 'REJECTED', 'PAUSED')),
    baseline_ref text,
    candidate_ref text,
    metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_uri text,
    notes text
);

CREATE TABLE IF NOT EXISTS assistant_lab.regression_case (
    case_key text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    title text NOT NULL,
    failure_class text NOT NULL,
    input_json jsonb NOT NULL,
    expected_json jsonb NOT NULL,
    source_ref text,
    active boolean NOT NULL DEFAULT true,
    notes text
);

CREATE OR REPLACE FUNCTION assistant_lab.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assistant_lab_job_touch ON assistant_lab.job;
CREATE TRIGGER assistant_lab_job_touch
BEFORE UPDATE ON assistant_lab.job
FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_updated_at();

DROP TRIGGER IF EXISTS assistant_lab_experiment_touch ON assistant_lab.experiment;
CREATE TRIGGER assistant_lab_experiment_touch
BEFORE UPDATE ON assistant_lab.experiment
FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_updated_at();

DROP TRIGGER IF EXISTS assistant_lab_regression_touch ON assistant_lab.regression_case;
CREATE TRIGGER assistant_lab_regression_touch
BEFORE UPDATE ON assistant_lab.regression_case
FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_updated_at();

CREATE OR REPLACE FUNCTION assistant_lab.notify_queued_job()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'QUEUED' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
        PERFORM pg_notify('assistant_lab_jobs', NEW.job_id::text);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assistant_lab_job_notify ON assistant_lab.job;
CREATE TRIGGER assistant_lab_job_notify
AFTER INSERT OR UPDATE OF status ON assistant_lab.job
FOR EACH ROW EXECUTE FUNCTION assistant_lab.notify_queued_job();

CREATE OR REPLACE FUNCTION assistant_lab.enqueue_job(
    p_kind text,
    p_payload jsonb,
    p_priority smallint,
    p_idempotency_key text,
    p_source text DEFAULT 'CHATGPT',
    p_provenance jsonb DEFAULT '{}'::jsonb,
    p_deadline_at timestamptz DEFAULT NULL,
    p_dispatch_nonce_sha256 text DEFAULT NULL
)
RETURNS assistant_lab.job
LANGUAGE plpgsql
AS $$
DECLARE
    v_row assistant_lab.job;
BEGIN
    INSERT INTO assistant_lab.job (
        kind, payload_json, priority, idempotency_key, source, provenance_json,
        deadline_at, dispatch_nonce_sha256
    ) VALUES (
        upper(p_kind), p_payload, p_priority, p_idempotency_key, p_source,
        p_provenance, p_deadline_at, p_dispatch_nonce_sha256
    )
    ON CONFLICT (idempotency_key) DO UPDATE
       SET idempotency_key = EXCLUDED.idempotency_key
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;

COMMENT ON SCHEMA assistant_lab IS
'Experimental Assistant Lab. Non-canonical; no automatic promotion to school canon or production behavior.';

-- Existing Vercel application role: only enough access to atomically claim and
-- finish a pre-created lab job. It cannot write experiments/regression cases and
-- cannot touch any additional school objects through these grants.
GRANT USAGE ON SCHEMA assistant_lab TO bridge_school_app;
GRANT SELECT, UPDATE ON assistant_lab.job TO bridge_school_app;
