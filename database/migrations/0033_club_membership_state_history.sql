\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- ClubMembership lifecycle history.
-- The base row is the stable membership-period identity. Lifecycle changes are
-- append-only state events so pause/resume/end/cancel history is not overwritten.
-- No transition policy is invented here; this migration only preserves chronology.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS club_membership_state_event (
    club_membership_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    club_membership_id uuid NOT NULL REFERENCES club_membership(club_membership_id),
    state text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_person_id uuid REFERENCES person(person_id),
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    state_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME club_membership_state_event_state_sequence_seq),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state IN ('pending','active','paused','ended','cancelled','invalid'))
);
CREATE UNIQUE INDEX IF NOT EXISTS club_membership_state_sequence_uk
    ON club_membership_state_event(club_membership_id, state_sequence);
CREATE INDEX IF NOT EXISTS club_membership_state_time_idx
    ON club_membership_state_event(club_membership_id, occurred_at DESC, state_sequence DESC);

CREATE OR REPLACE FUNCTION validate_club_membership_state_event_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM club_membership m
         WHERE m.club_membership_id=NEW.club_membership_id
    ) THEN
        RAISE EXCEPTION 'membership state event membership missing';
    END IF;
    IF NEW.actor_person_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.actor_person_id) THEN
        RAISE EXCEPTION 'membership state event actor missing';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_membership_state_event_scope_guard ON club_membership_state_event;
CREATE TRIGGER club_membership_state_event_scope_guard
BEFORE INSERT ON club_membership_state_event
FOR EACH ROW EXECUTE FUNCTION validate_club_membership_state_event_scope();

-- If a non-production branch already has membership rows, seed one initial event for
-- each row before switching runtime updates to the append-only lifecycle model.
INSERT INTO club_membership_state_event(
    club_membership_id, state, occurred_at, metadata
)
SELECT
    m.club_membership_id,
    m.status,
    m.valid_from,
    jsonb_build_object('source','membership_state_history_backfill')
FROM club_membership m
WHERE NOT EXISTS (
    SELECT 1
      FROM club_membership_state_event se
     WHERE se.club_membership_id=m.club_membership_id
);

-- Every future membership period gets an initial immutable state event. This keeps old
-- insert callers compatible while making the event stream complete by construction.
CREATE OR REPLACE FUNCTION seed_club_membership_initial_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO club_membership_state_event(
        club_membership_id, state, occurred_at, metadata
    ) VALUES (
        NEW.club_membership_id,
        NEW.status,
        NEW.valid_from,
        jsonb_build_object('source','membership_insert')
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_membership_initial_state_seed ON club_membership;
CREATE TRIGGER club_membership_initial_state_seed
AFTER INSERT ON club_membership
FOR EACH ROW EXECUTE FUNCTION seed_club_membership_initial_state();

CREATE OR REPLACE VIEW club_membership_current_state AS
SELECT DISTINCT ON (m.club_membership_id)
    m.club_membership_id,
    m.school_id,
    m.person_id,
    m.membership_type,
    m.valid_from,
    m.valid_to,
    se.state,
    se.occurred_at AS state_occurred_at,
    se.actor_person_id,
    se.reason,
    m.created_at
FROM club_membership m
LEFT JOIN club_membership_state_event se
  ON se.club_membership_id=m.club_membership_id
ORDER BY m.club_membership_id,
         se.occurred_at DESC NULLS LAST,
         se.state_sequence DESC NULLS LAST;

-- The one-open-period invariant now depends only on the stable period boundary.
-- Lifecycle state lives in events; a new membership period requires the prior period
-- to be closed with valid_to regardless of its last state label.
DROP INDEX IF EXISTS club_membership_one_open_type_uk;
CREATE UNIQUE INDEX club_membership_one_open_type_uk
    ON club_membership(school_id, person_id, membership_type)
    WHERE valid_to IS NULL;

-- Runtime no longer rewrites membership status. End of the membership period may still
-- close valid_to; lifecycle state itself is appended to the event table.
REVOKE UPDATE ON TABLE club_membership FROM bridge_school_app, bridge_school_worker;
GRANT UPDATE (valid_to) ON club_membership TO bridge_school_app;
GRANT INSERT ON TABLE club_membership_state_event TO bridge_school_app;
REVOKE UPDATE, DELETE ON TABLE club_membership_state_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
GRANT USAGE, SELECT ON SEQUENCE club_membership_state_event_state_sequence_seq
TO bridge_school_app;
REVOKE UPDATE ON SEQUENCE club_membership_state_event_state_sequence_seq
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
GRANT SELECT ON club_membership_current_state TO bridge_school_reader;

REVOKE ALL ON FUNCTION validate_club_membership_state_event_scope()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION seed_club_membership_initial_state()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0033_club_membership_state_history')
ON CONFLICT DO NOTHING;

COMMIT;
