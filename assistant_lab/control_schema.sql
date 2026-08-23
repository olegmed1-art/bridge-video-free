-- Assistant Lab Control Bridge v0.1.
-- Separate queue for ChatGPT/Neon -> Oracle localhost Control API.
-- Payload is intentionally limited to a registered tool_id and bounded metadata.

CREATE TABLE IF NOT EXISTS assistant_lab.control_command (
    command_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'CHATGPT',
    tool_id text NOT NULL CHECK (tool_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'),
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

CREATE OR REPLACE FUNCTION assistant_lab.enqueue_control_command(
    p_tool_id text,
    p_idempotency_key text,
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
        tool_id, idempotency_key, experiment_id, timeout_seconds, label, source
    ) VALUES (
        p_tool_id, p_idempotency_key, p_experiment_id, p_timeout_seconds, p_label, p_source
    )
    ON CONFLICT (idempotency_key) DO UPDATE
       SET idempotency_key = EXCLUDED.idempotency_key
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;

REVOKE ALL ON assistant_lab.control_command FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant_lab.enqueue_control_command(text,text,text,integer,text,text) FROM PUBLIC;

-- The existing Oracle worker principal can claim and finish commands but cannot insert them.
GRANT SELECT, UPDATE ON assistant_lab.control_command TO assistant_lab_worker;
