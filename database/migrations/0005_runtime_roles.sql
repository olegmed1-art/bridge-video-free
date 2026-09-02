\set ON_ERROR_STOP on
BEGIN;

-- Runtime roles are intentionally NOLOGIN capability roles.
-- Credentials/login roles will be created separately and granted only the capability they need.
DO $$
DECLARE
    role_name text;
    r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_school_reader') THEN
        CREATE ROLE bridge_school_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_school_app') THEN
        CREATE ROLE bridge_school_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_school_worker') THEN
        CREATE ROLE bridge_school_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;

    -- A managed PostgreSQL owner such as Neon may have CREATEROLE without true SUPERUSER.
    -- Do not issue ALTER ROLE ... NOSUPERUSER: PostgreSQL reserves changing the SUPERUSER
    -- attribute itself to a real superuser even when the target role is already non-superuser.
    -- Instead, validate the existing/created capability roles and fail closed if any are unsafe.
    FOREACH role_name IN ARRAY ARRAY['bridge_school_reader','bridge_school_app','bridge_school_worker'] LOOP
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication
          INTO r
          FROM pg_roles
         WHERE rolname = role_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime role missing after provisioning: %', role_name;
        END IF;
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication THEN
            RAISE EXCEPTION 'existing runtime role has unsafe attributes: %', role_name;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_reader IS 'Bridge School read-only runtime capability';
COMMENT ON ROLE bridge_school_app IS 'Bridge School interactive application write capability; no DELETE or DDL';
COMMENT ON ROLE bridge_school_worker IS 'Bridge School background ingestion/analysis capability; no DELETE or DDL';

GRANT bridge_school_reader TO bridge_school_app;
GRANT bridge_school_app TO bridge_school_worker;

-- Persistent schema creation is forbidden for runtime capabilities.
REVOKE CREATE ON SCHEMA public FROM bridge_school_reader, bridge_school_app, bridge_school_worker;
GRANT USAGE ON SCHEMA public TO bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Reader is deliberately broad: runtime services may inspect any school table but cannot mutate it.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bridge_school_reader;

-- Interactive application writes only student-facing operational state.
GRANT INSERT, UPDATE ON TABLE
    person,
    student,
    learning_interaction,
    deal,
    decision,
    agreement_set,
    agreement_version,
    agreement_activation
TO bridge_school_app;

-- Background worker adds ingestion, evidence, analysis, publication and projection state.
GRANT INSERT, UPDATE ON TABLE
    object_registry,
    source,
    asset,
    source_asset,
    asset_location,
    storage_verification,
    changeset,
    domain_event,
    outbox_message,
    ingestion_run,
    ingestion_item,
    source_observation,
    pending_reference,
    source_identity,
    entity_resolution_decision,
    decision_assessment,
    analysis_run,
    output_publication,
    projection_run,
    student_profile_snapshot,
    dependency_edge,
    invalidation_record,
    version_relation
TO bridge_school_worker;

-- No runtime capability receives DELETE on persistent school data.
REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Migration history and definition/configuration tables remain admin-owned.
REVOKE INSERT, UPDATE, DELETE ON TABLE
    schema_migration,
    school,
    event_type,
    event_schema_version,
    event_position_allocator,
    metric_definition,
    metric_version,
    projection_policy_version
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

-- Event publication is exposed only through guarded functions.
ALTER FUNCTION allocate_event_position(text, uuid) SECURITY DEFINER;
ALTER FUNCTION allocate_event_position(text, uuid) SET search_path = pg_catalog, public;
ALTER FUNCTION publish_outbox_event(uuid) SECURITY DEFINER;
ALTER FUNCTION publish_outbox_event(uuid) SET search_path = pg_catalog, public;

REVOKE ALL ON FUNCTION allocate_event_position(text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION publish_outbox_event(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION allocate_event_position(text, uuid) TO bridge_school_worker;
GRANT EXECUTE ON FUNCTION publish_outbox_event(uuid) TO bridge_school_worker;

-- Future tables are readable by default, but write access must be granted explicitly
-- in the migration that introduces the new table.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bridge_school_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

INSERT INTO schema_migration(migration_key)
VALUES ('0005_runtime_roles')
ON CONFLICT DO NOTHING;

COMMIT;
