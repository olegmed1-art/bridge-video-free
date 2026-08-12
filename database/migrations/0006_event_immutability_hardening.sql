\set ON_ERROR_STOP on
BEGIN;

-- Runtime event/source facts are append-only. The worker may create them but may not
-- rewrite them after insertion. Publication-time event_position is changed only by
-- the SECURITY DEFINER publication helper owned by the migration owner.
REVOKE UPDATE ON TABLE domain_event FROM bridge_school_worker;
REVOKE UPDATE ON TABLE source_observation FROM bridge_school_worker;

-- allocate_event_position() is an internal helper. Exposing it directly to the worker
-- would allow bypassing publish_outbox_event() and its committed-ChangeSet guard.
REVOKE ALL ON FUNCTION allocate_event_position(text, uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION allocate_event_position(text, uuid) FROM bridge_school_worker;

-- The worker gets only the guarded publication entry point.
REVOKE ALL ON FUNCTION publish_outbox_event(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION publish_outbox_event(uuid) TO bridge_school_worker;

INSERT INTO schema_migration(migration_key)
VALUES ('0006_event_immutability_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
