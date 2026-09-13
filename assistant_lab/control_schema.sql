-- Assistant Lab Control Bridge v0.2.
-- Separate queue for ChatGPT/Neon -> Oracle localhost Control API.
-- Payload is limited to a registered tool_id, bounded metadata, and verified source identity.

CREATE TABLE IF NOT EXISTS assistant_lab.control_command (
    command_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'CHATGPT',
    tool_id text NOT NULL CHECK (tool_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'),
    source_path text,
    source_sha256 text,
    experiment_id text CHECK (experiment_id IS NULL OR experiment_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'),
    timeout_seconds integer NOT NULL DEFAULT 3600 CHECK (timeout_seconds BETWEEN 1 AND 86400),
    label text NOT NULL DEFAULT '' CHECK (length(label) <= 256),
    status text NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    result_json jsonb,
    error_text text,
    idempotency_key text NOT NULL UNIQUE,
    claimed_by text,
    claimed_at timestamptz,
    completed_at timestamptz,
    attempts smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts smallint NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 3)
);

ALTER TABLE assistant_lab.control_command
    ADD COLUMN IF NOT EXISTS source_path text,
    ADD COLUMN IF NOT EXISTS source_sha256 text;

ALTER TABLE assistant_lab.control_command
    DROP CONSTRAINT IF EXISTS assistant_lab_control_command_source_contract;
ALTER TABLE assistant_lab.control_command
    ADD CONSTRAINT assistant_lab_control_command_source_contract CHECK (
        status NOT IN ('QUEUED', 'RUNNING')
        OR (
            source_path IS NOT NULL
            AND length(source_path) BETWEEN 1 AND 4096
            AND source_sha256 ~ '^[0-9a-fA-F]{64}$'
        )
    );

CREATE INDEX IF NOT EXISTS assistant_lab_control_command_queue_idx
    ON assistant_lab.control_command (created_at)
    WHERE status = 'QUEUED';

CREATE INDEX IF NOT EXISTS assistant_lab_control_command_running_idx
    ON assistant_lab.control_command (claimed_at)
    WHERE status = 'RUNNING';

DROP TRIGGER IF EXISTS assistant_lab_control_command_touch ON assistant_lab.control_command;
CREATE TRIGGER assistant_lab_control_command_touch
BEFORE UPDATE ON assistant_lab.control_command
FOR EACH ROW EXECUTE FUNCTION assistant_lab.touch_updated_at();

CREATE OR REPLACE FUNCTION assistant_lab.notify_queued_control_command()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'QUEUED' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
        PERFORM pg_notify('assistant_lab_control_commands', NEW.command_id::text);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS assistant_lab_control_command_notify ON assistant_lab.control_command;
CREATE TRIGGER assistant_lab_control_command_notify
AFTER INSERT OR UPDATE OF status ON assistant_lab.control_command
FOR EACH ROW EXECUTE FUNCTION assistant_lab.notify_queued_control_command();

DROP FUNCTION IF EXISTS assistant_lab.enqueue_control_command(text,text,text,integer,text,text);

CREATE OR REPLACE FUNCTION assistant_lab.enqueue_control_command(
    p_tool_id text,
    p_idempotency_key text,
    p_source_path text,
    p_source_sha256 text,
    p_experiment_id text DEFAULT NULL,
    p_timeout_seconds integer DEFAULT 3600,
    p_label text DEFAULT '',
    p_source text DEFAULT 'CHATGPT'
)
RETURNS assistant_lab.control_command
LANGUAGE plpgsql
AS $$
DECLARE
    v_row assistant_lab.control_command;
BEGIN
    INSERT INTO assistant_lab.control_command (
        tool_id, idempotency_key, source_path, source_sha256,
        experiment_id, timeout_seconds, label, source
    ) VALUES (
        p_tool_id, p_idempotency_key, p_source_path, lower(p_source_sha256),
        p_experiment_id, p_timeout_seconds, p_label, p_source
    )
    ON CONFLICT (idempotency_key) DO UPDATE
       SET idempotency_key = EXCLUDED.idempotency_key
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;

REVOKE ALL ON assistant_lab.control_command FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.enqueue_control_command(text,text,text,text,text,integer,text,text) FROM PUBLIC;

-- The Oracle principal has no direct table privileges. All state transitions are
-- constrained by SECURITY DEFINER RPCs owned by the schema owner.
CREATE OR REPLACE FUNCTION assistant_lab.claim_control_command(p_worker_id text)
RETURNS SETOF assistant_lab.control_command
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, assistant_lab
AS $$
BEGIN
    IF p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'invalid worker id';
    END IF;
    RETURN QUERY
    WITH candidate AS (
        SELECT command_id
        FROM assistant_lab.control_command
        WHERE status = 'QUEUED'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE assistant_lab.control_command AS c
       SET status = 'RUNNING',
           claimed_by = p_worker_id,
           claimed_at = now(),
           attempts = attempts + 1
      FROM candidate
     WHERE c.command_id = candidate.command_id
    RETURNING c.*;
END;
$$;

CREATE OR REPLACE FUNCTION assistant_lab.finish_control_command(
    p_command_id uuid,
    p_worker_id text,
    p_status text,
    p_result_json jsonb DEFAULT NULL,
    p_error_text text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, assistant_lab
AS $$
BEGIN
    IF p_status NOT IN ('COMPLETED', 'FAILED') THEN
        RAISE EXCEPTION 'invalid terminal status';
    END IF;
    UPDATE assistant_lab.control_command
       SET status = p_status,
           result_json = p_result_json,
           error_text = left(p_error_text, 4000),
           completed_at = now()
     WHERE command_id = p_command_id
       AND status = 'RUNNING'
       AND claimed_by = p_worker_id;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION assistant_lab.recover_stale_control_commands(
    p_stale_after_seconds integer
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, assistant_lab
AS $$
DECLARE
    v_count bigint;
BEGIN
    IF p_stale_after_seconds NOT BETWEEN 60 AND 86400 THEN
        RAISE EXCEPTION 'invalid stale interval';
    END IF;
    UPDATE assistant_lab.control_command
       SET status = CASE WHEN attempts < max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
           error_text = CASE
               WHEN attempts < max_attempts THEN error_text
               ELSE 'stale control bridge claim exhausted retries'
           END,
           claimed_by = NULL,
           claimed_at = NULL,
           completed_at = CASE WHEN attempts < max_attempts THEN completed_at ELSE now() END
     WHERE status = 'RUNNING'
       AND claimed_at < now() - (p_stale_after_seconds * interval '1 second');
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

REVOKE ALL ON assistant_lab.control_command FROM assistant_lab_worker;
REVOKE ALL ON FUNCTION assistant_lab.claim_control_command(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.finish_control_command(uuid,text,text,jsonb,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.recover_stale_control_commands(integer) FROM PUBLIC;
-- Schema USAGE is required to invoke schema-qualified RPCs but grants no table access.
GRANT USAGE ON SCHEMA assistant_lab TO assistant_lab_worker;
GRANT EXECUTE ON FUNCTION assistant_lab.claim_control_command(text) TO assistant_lab_worker;
GRANT EXECUTE ON FUNCTION assistant_lab.finish_control_command(uuid,text,text,jsonb,text) TO assistant_lab_worker;
GRANT EXECUTE ON FUNCTION assistant_lab.recover_stale_control_commands(integer) TO assistant_lab_worker;
