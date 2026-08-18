\set ON_ERROR_STOP on
BEGIN;

-- State-event timestamps may intentionally be equal (for example during imports or
-- one transaction). Add a recorded order so current-state projections are deterministic.

ALTER TABLE club_booking_state_event
    ADD COLUMN IF NOT EXISTS state_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME club_booking_state_event_state_sequence_seq);

CREATE UNIQUE INDEX IF NOT EXISTS club_booking_state_sequence_uk
    ON club_booking_state_event(booking_id, state_sequence);

CREATE OR REPLACE VIEW club_booking_current_state AS
SELECT DISTINCT ON (b.booking_id)
    b.booking_id,
    b.school_id,
    b.club_event_id,
    b.person_id,
    se.state,
    se.occurred_at AS state_occurred_at,
    b.created_at
FROM club_booking b
LEFT JOIN club_booking_state_event se ON se.booking_id=b.booking_id
ORDER BY b.booking_id,
         se.occurred_at DESC NULLS LAST,
         se.state_sequence DESC NULLS LAST;

ALTER TABLE admin_task_state_event
    ADD COLUMN IF NOT EXISTS state_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME admin_task_state_event_state_sequence_seq);

CREATE UNIQUE INDEX IF NOT EXISTS admin_task_state_sequence_uk
    ON admin_task_state_event(admin_task_id, state_sequence);

CREATE OR REPLACE VIEW admin_task_current_state AS
SELECT DISTINCT ON (t.admin_task_id)
    t.admin_task_id,
    t.school_id,
    t.title,
    t.task_type,
    t.subject_person_id,
    t.related_entity_type,
    t.related_entity_id,
    t.assigned_to_person_id,
    t.priority,
    t.due_at,
    se.state,
    se.occurred_at AS state_occurred_at,
    t.created_at
FROM admin_task t
LEFT JOIN admin_task_state_event se ON se.admin_task_id=t.admin_task_id
ORDER BY t.admin_task_id,
         se.occurred_at DESC NULLS LAST,
         se.state_sequence DESC NULLS LAST;

GRANT USAGE, SELECT ON SEQUENCE club_booking_state_event_state_sequence_seq TO bridge_school_app;
GRANT USAGE, SELECT ON SEQUENCE admin_task_state_event_state_sequence_seq TO bridge_school_app;

REVOKE UPDATE ON SEQUENCE club_booking_state_event_state_sequence_seq FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
REVOKE UPDATE ON SEQUENCE admin_task_state_event_state_sequence_seq FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0023_club_state_order_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
