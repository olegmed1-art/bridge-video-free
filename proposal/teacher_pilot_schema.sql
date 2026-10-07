-- SOURCE-ONLY PROPOSAL. Not an automatic migration; do not execute without approved DB scope.
-- No canon, student/person identity, credential, job, assessment, role or GRANT.
BEGIN;
CREATE SCHEMA teacher_pilot;
CREATE TABLE teacher_pilot.session (
    session_id uuid PRIMARY KEY,
    identity jsonb NOT NULL CHECK (jsonb_typeof(identity) = 'object'),
    turn integer NOT NULL DEFAULT 0 CHECK (turn BETWEEN 0 AND 200),
    checkpoint jsonb NOT NULL CHECK (jsonb_typeof(checkpoint) = 'object'
                                    AND octet_length(checkpoint::text) <= 131072),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '30 minutes'),
    CHECK (expires_at > created_at),
    CHECK ((checkpoint->>'session_id' = session_id::text) IS TRUE),
    CHECK ((checkpoint->'runtime'->'identity' = identity) IS TRUE),
    CHECK (((checkpoint->'runtime'->'state'->>'_turn')::integer = turn) IS TRUE)
);
CREATE TABLE teacher_pilot.event (
    session_id uuid NOT NULL REFERENCES teacher_pilot.session(session_id),
    turn integer NOT NULL CHECK (turn BETWEEN 0 AND 199),
    event jsonb NOT NULL CHECK (jsonb_typeof(event) = 'object'
                               AND octet_length(event::text) <= 8192),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (session_id, turn),
    CHECK (((event->>'turn')::integer = turn) IS TRUE)
);
CREATE FUNCTION teacher_pilot.reject_event_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER AS $$
BEGIN
    RAISE EXCEPTION 'TEACHER_PILOT_JOURNAL_IMMUTABLE';
END;
$$;
CREATE TRIGGER teacher_pilot_event_immutable
BEFORE UPDATE OR DELETE ON teacher_pilot.event
FOR EACH ROW EXECUTE FUNCTION teacher_pilot.reject_event_mutation();

-- Grant needs after approval: schema USAGE; session SELECT, INSERT, UPDATE(turn, checkpoint);
-- event SELECT, INSERT. No app DELETE, event UPDATE, production table or role changes.
-- Retention cleanup must be separately selected and approved; expiry only blocks session access.
COMMIT;
