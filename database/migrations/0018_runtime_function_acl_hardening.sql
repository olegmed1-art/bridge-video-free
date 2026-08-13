\set ON_ERROR_STOP on
BEGIN;

-- show_db_tree() exists in production as an untracked administrative helper,
-- has no database dependencies, and exposes schema metadata to every runtime
-- principal through PostgreSQL's default PUBLIC EXECUTE grant. Remove the drift.
DROP FUNCTION IF EXISTS public.show_db_tree();

-- The dependency-cycle function is a trigger implementation detail, not a
-- callable runtime API. Keep the trigger intact but remove direct execution.
REVOKE ALL ON FUNCTION public.prevent_dependency_cycle() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.prevent_dependency_cycle() FROM
    bridge_school_reader,
    bridge_school_app,
    bridge_school_worker,
    bridge_school_health;

-- Fail closed for future owner-created functions. New callable runtime APIs
-- must receive an explicit GRANT in the migration that introduces them.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

DO $$
DECLARE
    role_name text;
BEGIN
    IF to_regprocedure('public.show_db_tree()') IS NOT NULL THEN
        RAISE EXCEPTION 'untracked show_db_tree() remains installed';
    END IF;

    FOREACH role_name IN ARRAY ARRAY[
        'bridge_school_app_principal',
        'bridge_school_worker_principal',
        'bridge_school_health_principal'
    ] LOOP
        IF has_function_privilege(role_name, 'public.prevent_dependency_cycle()', 'EXECUTE') THEN
            RAISE EXCEPTION 'runtime principal can execute trigger helper: %', role_name;
        END IF;
    END LOOP;
END $$;

INSERT INTO schema_migration(migration_key)
VALUES ('0018_runtime_function_acl_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
