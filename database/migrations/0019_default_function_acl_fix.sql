\set ON_ERROR_STOP on
BEGIN;

-- PostgreSQL grants EXECUTE on newly created functions to PUBLIC by default.
-- A schema-scoped REVOKE cannot remove that global default; set the creator's
-- global default privilege instead. This applies to the migration role in CI
-- and to the production owner role in Neon without hard-coding either name.
ALTER DEFAULT PRIVILEGES
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Prove the effective default in the same transaction so production fails
-- closed if the database behaves differently from the expected PostgreSQL 18
-- privilege model.
CREATE FUNCTION public.__bridge_default_acl_probe()
RETURNS integer
LANGUAGE sql
AS 'SELECT 1';

DO $$
BEGIN
    IF has_function_privilege('public', 'public.__bridge_default_acl_probe()', 'EXECUTE') THEN
        RAISE EXCEPTION 'new owner-created functions still grant EXECUTE to PUBLIC';
    END IF;
END $$;

DROP FUNCTION public.__bridge_default_acl_probe();

INSERT INTO schema_migration(migration_key)
VALUES ('0019_default_function_acl_fix')
ON CONFLICT DO NOTHING;

COMMIT;
