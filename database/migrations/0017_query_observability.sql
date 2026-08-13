\set ON_ERROR_STOP on
BEGIN;

-- Built-in query statistics for database performance diagnostics.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
    ) THEN
        RAISE EXCEPTION 'pg_stat_statements extension is not available after provisioning';
    END IF;
END $$;

INSERT INTO schema_migration(migration_key)
VALUES ('0017_query_observability')
ON CONFLICT DO NOTHING;

COMMIT;
