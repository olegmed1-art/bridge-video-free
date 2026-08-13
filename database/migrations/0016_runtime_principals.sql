\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Runtime credential boundary.
--
-- Capability roles remain NOLOGIN and hold database privileges. These principal roles
-- are also created NOLOGIN: they are deliberately dormant until a credential is
-- provisioned outside the repository. No password, token or connection string belongs
-- in a migration or in Git history.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    role_name text;
    r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_health') THEN
        CREATE ROLE bridge_school_health NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_app_principal') THEN
        CREATE ROLE bridge_school_app_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_worker_principal') THEN
        CREATE ROLE bridge_school_worker_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_health_principal') THEN
        CREATE ROLE bridge_school_health_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;

    -- Fail closed if an earlier/manual role with one of these names is more privileged
    -- than the contract permits. As with capability roles, avoid ALTER ... NOSUPERUSER
    -- because Neon migration owners are managed non-superusers.
    FOREACH role_name IN ARRAY ARRAY[
        'bridge_school_health',
        'bridge_school_app_principal',
        'bridge_school_worker_principal',
        'bridge_school_health_principal'
    ] LOOP
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
          INTO r
          FROM pg_roles
         WHERE rolname=role_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime access role missing after provisioning: %', role_name;
        END IF;
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'runtime access role has unsafe attributes: %', role_name;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_health IS 'Bridge School minimal read-only operational-health capability';
COMMENT ON ROLE bridge_school_app_principal IS 'Dormant application principal; enable LOGIN only when an external secret is provisioned';
COMMENT ON ROLE bridge_school_worker_principal IS 'Dormant worker principal; enable LOGIN only when an external secret is provisioned';
COMMENT ON ROLE bridge_school_health_principal IS 'Dormant health-monitor principal; enable LOGIN only when an external secret is provisioned';

-- Health monitoring gets only the technical read models, not the broad reader role.
REVOKE CREATE ON SCHEMA public FROM bridge_school_health;
GRANT USAGE ON SCHEMA public TO bridge_school_health;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM bridge_school_health;
GRANT SELECT ON TABLE
    database_runtime_fingerprint,
    operational_health_signal,
    operational_health_issue,
    operational_health_summary
TO bridge_school_health;

-- Principals inherit exactly one capability boundary. They remain NOLOGIN here.
GRANT bridge_school_app TO bridge_school_app_principal;
GRANT bridge_school_worker TO bridge_school_worker_principal;
GRANT bridge_school_health TO bridge_school_health_principal;

INSERT INTO schema_migration(migration_key)
VALUES ('0016_runtime_principals')
ON CONFLICT DO NOTHING;

COMMIT;
