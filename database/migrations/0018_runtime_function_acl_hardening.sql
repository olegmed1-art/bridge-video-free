\set ON_ERROR_STOP on
BEGIN;

-- These functions are internal implementation helpers, not runtime APIs.
-- PostgreSQL grants function EXECUTE to PUBLIC by default, so make the
-- intended owner-only boundary explicit.
REVOKE ALL ON FUNCTION show_db_tree() FROM PUBLIC;
REVOKE ALL ON FUNCTION prevent_dependency_cycle() FROM PUBLIC;

REVOKE ALL ON FUNCTION show_db_tree() FROM
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker,
    bridge_school_health;
REVOKE ALL ON FUNCTION prevent_dependency_cycle() FROM
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker,
    bridge_school_health;

INSERT INTO schema_migration(migration_key)
VALUES ('0018_runtime_function_acl_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
