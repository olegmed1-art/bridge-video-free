\set ON_ERROR_STOP on
BEGIN;
-- Migration 0018 will narrow unintended function execution privileges.
INSERT INTO schema_migration(migration_key)
VALUES ('0018_runtime_function_acl_hardening')
ON CONFLICT DO NOTHING;
COMMIT;
