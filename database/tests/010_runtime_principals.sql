\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    role_name text;
    r record;
    unexpected_membership_count integer;
BEGIN
    -- Runtime principals may be NOLOGIN while dormant or LOGIN after a credential
    -- is explicitly provisioned. LOGIN alone is operational state, not an admin
    -- capability. Administrative attributes remain forbidden in either state.
    FOREACH role_name IN ARRAY ARRAY[
        'bridge_school_health',
        'bridge_school_app_principal',
        'bridge_school_worker_principal',
        'bridge_school_health_principal'
    ] LOOP
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
          INTO r
          FROM pg_roles
         WHERE rolname=role_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime access role missing: %', role_name;
        END IF;
        IF r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'runtime access role has unsafe attributes: %', role_name;
        END IF;
        IF has_schema_privilege(role_name,'public','CREATE') THEN
            RAISE EXCEPTION 'runtime access role unexpectedly has schema CREATE: %', role_name;
        END IF;
    END LOOP;

    IF NOT has_schema_privilege('bridge_school_health','public','USAGE') THEN
        RAISE EXCEPTION 'health capability lacks public schema USAGE';
    END IF;

    -- Health capability is intentionally much narrower than bridge_school_reader.
    IF NOT has_table_privilege('bridge_school_health','database_runtime_fingerprint','SELECT')
       OR NOT has_table_privilege('bridge_school_health','operational_health_signal','SELECT')
       OR NOT has_table_privilege('bridge_school_health','operational_health_issue','SELECT')
       OR NOT has_table_privilege('bridge_school_health','operational_health_summary','SELECT') THEN
        RAISE EXCEPTION 'health capability lacks one or more operational-health read models';
    END IF;

    IF has_table_privilege('bridge_school_health','person','SELECT')
       OR has_table_privilege('bridge_school_health','student','SELECT')
       OR has_table_privilege('bridge_school_health','schema_migration','SELECT') THEN
        RAISE EXCEPTION 'health capability leaked broad school-data access';
    END IF;

    -- Each dormant principal has exactly one direct capability membership.
    IF NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles child ON child.oid=m.member
        WHERE parent.rolname='bridge_school_app'
          AND child.rolname='bridge_school_app_principal'
    ) THEN
        RAISE EXCEPTION 'application principal lacks bridge_school_app membership';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles child ON child.oid=m.member
        WHERE parent.rolname='bridge_school_worker'
          AND child.rolname='bridge_school_worker_principal'
    ) THEN
        RAISE EXCEPTION 'worker principal lacks bridge_school_worker membership';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles child ON child.oid=m.member
        WHERE parent.rolname='bridge_school_health'
          AND child.rolname='bridge_school_health_principal'
    ) THEN
        RAISE EXCEPTION 'health principal lacks bridge_school_health membership';
    END IF;

    SELECT count(*) INTO unexpected_membership_count
      FROM pg_auth_members m
      JOIN pg_roles parent ON parent.oid=m.roleid
      JOIN pg_roles child ON child.oid=m.member
     WHERE child.rolname IN (
            'bridge_school_app_principal',
            'bridge_school_worker_principal',
            'bridge_school_health_principal'
       )
       AND NOT (
            (child.rolname='bridge_school_app_principal' AND parent.rolname='bridge_school_app') OR
            (child.rolname='bridge_school_worker_principal' AND parent.rolname='bridge_school_worker') OR
            (child.rolname='bridge_school_health_principal' AND parent.rolname='bridge_school_health')
       );
    IF unexpected_membership_count <> 0 THEN
        RAISE EXCEPTION 'runtime principal has unexpected direct role membership';
    END IF;

    -- Effective app permissions must reflect controlled onboarding: the application
    -- principal can edit an existing Person but cannot create/delete Person rows or
    -- cross the infrastructure/source-observation write boundary.
    IF has_table_privilege('bridge_school_app_principal','person','INSERT')
       OR NOT has_table_privilege('bridge_school_app_principal','person','UPDATE')
       OR has_table_privilege('bridge_school_app_principal','source_observation','INSERT')
       OR has_table_privilege('bridge_school_app_principal','person','DELETE') THEN
        RAISE EXCEPTION 'application principal effective permissions are outside controlled-onboarding contract';
    END IF;

    -- Worker inherits app, so it also must not bypass automatic Person onboarding.
    IF has_table_privilege('bridge_school_worker_principal','person','INSERT')
       OR NOT has_table_privilege('bridge_school_worker_principal','source_observation','INSERT')
       OR has_table_privilege('bridge_school_worker_principal','source_observation','UPDATE')
       OR has_table_privilege('bridge_school_worker_principal','source_observation','DELETE') THEN
        RAISE EXCEPTION 'worker principal effective append-only/onboarding permissions are outside contract';
    END IF;

    IF NOT has_table_privilege('bridge_school_health_principal','operational_health_summary','SELECT')
       OR has_table_privilege('bridge_school_health_principal','person','SELECT')
       OR has_table_privilege('bridge_school_health_principal','schema_migration','SELECT') THEN
        RAISE EXCEPTION 'health principal effective permissions are outside contract';
    END IF;
END $$;

-- Any new owner-created function must be private by default. Runtime-callable
-- functions require an explicit GRANT in the migration that creates them.
CREATE FUNCTION public.__bridge_test_default_acl()
RETURNS integer
LANGUAGE sql
AS 'SELECT 1';

DO $$
BEGIN
    IF has_function_privilege('public', 'public.__bridge_test_default_acl()', 'EXECUTE') THEN
        RAISE EXCEPTION 'PUBLIC can execute a newly created owner function';
    END IF;
    IF has_function_privilege('bridge_school_app_principal', 'public.__bridge_test_default_acl()', 'EXECUTE')
       OR has_function_privilege('bridge_school_worker_principal', 'public.__bridge_test_default_acl()', 'EXECUTE')
       OR has_function_privilege('bridge_school_health_principal', 'public.__bridge_test_default_acl()', 'EXECUTE') THEN
        RAISE EXCEPTION 'runtime principal can execute a newly created owner function without an explicit grant';
    END IF;
END $$;

ROLLBACK;
