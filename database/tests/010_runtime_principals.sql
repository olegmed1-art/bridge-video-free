\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    role_name text;
    r record;
    unexpected_membership_count integer;
BEGIN
    -- Health capability and dormant principals must be non-login/non-admin roles.
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
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
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

    -- Effective permissions must match the inherited capability boundaries.
    IF NOT has_table_privilege('bridge_school_app_principal','person','INSERT')
       OR has_table_privilege('bridge_school_app_principal','source_observation','INSERT')
       OR has_table_privilege('bridge_school_app_principal','person','DELETE') THEN
        RAISE EXCEPTION 'application principal effective permissions are outside contract';
    END IF;

    IF NOT has_table_privilege('bridge_school_worker_principal','source_observation','INSERT')
       OR has_table_privilege('bridge_school_worker_principal','source_observation','UPDATE')
       OR has_table_privilege('bridge_school_worker_principal','source_observation','DELETE') THEN
        RAISE EXCEPTION 'worker principal effective append-only permissions are outside contract';
    END IF;

    IF NOT has_table_privilege('bridge_school_health_principal','operational_health_summary','SELECT')
       OR has_table_privilege('bridge_school_health_principal','person','SELECT')
       OR has_table_privilege('bridge_school_health_principal','schema_migration','SELECT') THEN
        RAISE EXCEPTION 'health principal effective permissions are outside contract';
    END IF;
END $$;

ROLLBACK;
