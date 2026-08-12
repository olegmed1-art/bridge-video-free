\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    r record;
    role_name text;
    required_table text;
BEGIN
    -- Capability roles must exist and must never be login/admin roles.
    FOREACH role_name IN ARRAY ARRAY['bridge_school_reader','bridge_school_app','bridge_school_worker'] LOOP
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication
          INTO r
          FROM pg_roles
         WHERE rolname = role_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime role missing: %', role_name;
        END IF;
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
            RAISE EXCEPTION 'runtime role has unsafe role attributes: %', role_name;
        END IF;
        IF NOT has_schema_privilege(role_name, 'public', 'USAGE') THEN
            RAISE EXCEPTION 'runtime role lacks public schema USAGE: %', role_name;
        END IF;
        IF has_schema_privilege(role_name, 'public', 'CREATE') THEN
            RAISE EXCEPTION 'runtime role unexpectedly has persistent schema CREATE: %', role_name;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_auth_members m
          JOIN pg_roles parent ON parent.oid = m.roleid
          JOIN pg_roles child ON child.oid = m.member
         WHERE parent.rolname='bridge_school_reader'
           AND child.rolname='bridge_school_app'
    ) THEN
        RAISE EXCEPTION 'bridge_school_app must inherit bridge_school_reader';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_auth_members m
          JOIN pg_roles parent ON parent.oid = m.roleid
          JOIN pg_roles child ON child.oid = m.member
         WHERE parent.rolname='bridge_school_app'
           AND child.rolname='bridge_school_worker'
    ) THEN
        RAISE EXCEPTION 'bridge_school_worker must inherit bridge_school_app';
    END IF;

    -- Reader can inspect every persistent table but cannot mutate ordinary business tables.
    FOR r IN
        SELECT format('%I.%I', n.nspname, c.relname) AS table_name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='public'
           AND c.relkind IN ('r','p')
    LOOP
        IF NOT has_table_privilege('bridge_school_reader', r.table_name, 'SELECT') THEN
            RAISE EXCEPTION 'reader lacks SELECT on %', r.table_name;
        END IF;
    END LOOP;

    IF has_table_privilege('bridge_school_reader','person','INSERT')
       OR has_table_privilege('bridge_school_reader','person','UPDATE')
       OR has_table_privilege('bridge_school_reader','person','DELETE') THEN
        RAISE EXCEPTION 'reader unexpectedly has write access to person';
    END IF;

    -- Interactive app may write only student-facing operational tables.
    FOREACH required_table IN ARRAY ARRAY[
        'person','student','learning_interaction','deal','decision',
        'agreement_set','agreement_version','agreement_activation'
    ] LOOP
        IF NOT has_table_privilege('bridge_school_app', required_table, 'INSERT')
           OR NOT has_table_privilege('bridge_school_app', required_table, 'UPDATE') THEN
            RAISE EXCEPTION 'app lacks expected INSERT/UPDATE on %', required_table;
        END IF;
        IF has_table_privilege('bridge_school_app', required_table, 'DELETE') THEN
            RAISE EXCEPTION 'app unexpectedly has DELETE on %', required_table;
        END IF;
    END LOOP;

    IF has_table_privilege('bridge_school_app','source_observation','INSERT')
       OR has_table_privilege('bridge_school_app','domain_event','INSERT')
       OR has_table_privilege('bridge_school_app','schema_migration','UPDATE') THEN
        RAISE EXCEPTION 'app crossed infrastructure/admin write boundary';
    END IF;

    -- Worker may write ingestion/analysis/projection state but still cannot delete.
    FOREACH required_table IN ARRAY ARRAY[
        'source_observation','domain_event','outbox_message','ingestion_run','ingestion_item',
        'analysis_run','output_publication','projection_run','student_profile_snapshot',
        'dependency_edge','invalidation_record','version_relation'
    ] LOOP
        IF NOT has_table_privilege('bridge_school_worker', required_table, 'INSERT')
           OR NOT has_table_privilege('bridge_school_worker', required_table, 'UPDATE') THEN
            RAISE EXCEPTION 'worker lacks expected INSERT/UPDATE on %', required_table;
        END IF;
        IF has_table_privilege('bridge_school_worker', required_table, 'DELETE') THEN
            RAISE EXCEPTION 'worker unexpectedly has DELETE on %', required_table;
        END IF;
    END LOOP;

    IF has_table_privilege('bridge_school_worker','schema_migration','UPDATE')
       OR has_table_privilege('bridge_school_worker','metric_definition','INSERT')
       OR has_table_privilege('bridge_school_worker','projection_policy_version','UPDATE') THEN
        RAISE EXCEPTION 'worker crossed admin/configuration write boundary';
    END IF;

    -- Sensitive publication functions are callable only by the worker capability.
    IF NOT has_function_privilege('bridge_school_worker','allocate_event_position(text,uuid)','EXECUTE')
       OR NOT has_function_privilege('bridge_school_worker','publish_outbox_event(uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'worker lacks guarded publication function privilege';
    END IF;
    IF has_function_privilege('bridge_school_reader','allocate_event_position(text,uuid)','EXECUTE')
       OR has_function_privilege('bridge_school_app','allocate_event_position(text,uuid)','EXECUTE')
       OR has_function_privilege('bridge_school_reader','publish_outbox_event(uuid)','EXECUTE')
       OR has_function_privilege('bridge_school_app','publish_outbox_event(uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'publication function leaked to non-worker runtime role';
    END IF;
END $$;

ROLLBACK;
