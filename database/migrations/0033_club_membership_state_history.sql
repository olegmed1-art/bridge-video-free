\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- ClubMembership lifecycle history.
-- The base row remains a convenient current-state mirror, while every lifecycle
-- change is also appended as an immutable state event. This preserves existing app
-- update semantics without losing pause/resume/end/cancel history.
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
-- each row before switching to the lifecycle-history model.
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

-- Every future membership period gets an initial immutable state event. SECURITY
-- DEFINER allows the trigger to append the event without granting direct INSERT on the
-- event table to the application principal.
CREATE OR REPLACE FUNCTION seed_club_membership_initial_state()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
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

-- Existing callers may update the current status column. Capture every actual status
-- change automatically; direct event insertion by runtime is intentionally forbidden so
-- the event stream cannot diverge from the current-state mirror.
CREATE OR REPLACE FUNCTION capture_club_membership_status_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO club_membership_state_event(
            club_membership_id, state, occurred_at, metadata
        ) VALUES (
            NEW.club_membership_id,
            NEW.status,
            now(),
            jsonb_build_object('source','membership_status_update')
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_membership_status_history ON club_membership;
CREATE TRIGGER club_membership_status_history
AFTER UPDATE OF status ON club_membership
FOR EACH ROW EXECUTE FUNCTION capture_club_membership_status_change();

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

-- The one-open-period invariant depends only on the stable period boundary. State can
-- pause/resume many times; a new membership period requires the prior period to close.
DROP INDEX IF EXISTS club_membership_one_open_type_uk;
CREATE UNIQUE INDEX club_membership_one_open_type_uk
    ON club_membership(school_id, person_id, membership_type)
    WHERE valid_to IS NULL;

-- Keep existing runtime status/valid_to update contract, but event history is automatic.
REVOKE UPDATE ON TABLE club_membership FROM bridge_school_app, bridge_school_worker;
GRANT UPDATE (valid_to, status) ON club_membership TO bridge_school_app;
REVOKE INSERT, UPDATE, DELETE ON TABLE club_membership_state_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
REVOKE ALL ON SEQUENCE club_membership_state_event_state_sequence_seq
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
GRANT SELECT ON club_membership_current_state TO bridge_school_reader;

REVOKE ALL ON FUNCTION validate_club_membership_state_event_scope()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION seed_club_membership_initial_state()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION capture_club_membership_status_change()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0033_club_membership_state_history')
ON CONFLICT DO NOTHING;

COMMIT;
