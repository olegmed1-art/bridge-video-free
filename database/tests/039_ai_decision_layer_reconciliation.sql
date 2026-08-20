\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_table_count integer;
    v_view_count integer;
    v_index_count integer;
    v_line_count integer;
    v_fingerprint text;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM schema_migration
         WHERE migration_key='0054_ai_decision_layer_reconciliation'
    ) THEN
        RAISE EXCEPTION 'AI reconciliation migration is not registered';
    END IF;

    SELECT count(*) INTO v_table_count
      FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='ai'
       AND c.relkind='r';
    IF v_table_count <> 14 THEN
        RAISE EXCEPTION 'expected 14 AI tables, found %', v_table_count;
    END IF;

    SELECT count(*) INTO v_view_count
      FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='ai'
       AND c.relkind='v';
    IF v_view_count <> 4 THEN
        RAISE EXCEPTION 'expected 4 AI views, found %', v_view_count;
    END IF;

    SELECT count(*) INTO v_index_count
      FROM pg_indexes
     WHERE schemaname='ai'
       AND indexname LIKE 'idx_ai_%';
    IF v_index_count <> 8 THEN
        RAISE EXCEPTION 'expected 8 explicit AI indexes, found %', v_index_count;
    END IF;

    WITH column_lines AS (
        SELECT format(
            'C|%s|%s|%s|%s|%s|%s',
            c.table_name,c.ordinal_position,c.column_name,c.udt_name,
            c.is_nullable,COALESCE(c.column_default,'')
        ) AS line
          FROM information_schema.columns c
         WHERE c.table_schema='ai'
           AND c.table_name IN (SELECT tablename FROM pg_tables WHERE schemaname='ai')
    ), constraint_lines AS (
        SELECT format(
            'K|%s|%s|%s|%s',
            conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid,true)
        ) AS line
          FROM pg_constraint
         WHERE connamespace='ai'::regnamespace
           AND contype IN ('p','u','f','c')
    ), index_lines AS (
        SELECT format('I|%s|%s',indexname,indexdef) AS line
          FROM pg_indexes
         WHERE schemaname='ai'
           AND indexname LIKE 'idx_ai_%'
    ), view_lines AS (
        SELECT format(
            'V|%s|%s',c.relname,
            regexp_replace(pg_get_viewdef(c.oid,true),'\s+',' ','g')
        ) AS line
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname='ai'
           AND c.relkind='v'
    ), all_lines AS (
        SELECT line FROM column_lines
        UNION ALL SELECT line FROM constraint_lines
        UNION ALL SELECT line FROM index_lines
        UNION ALL SELECT line FROM view_lines
    )
    SELECT count(*), encode(digest(string_agg(line,E'\n' ORDER BY line),'sha256'),'hex')
      INTO v_line_count, v_fingerprint
      FROM all_lines;

    IF v_line_count <> 225 THEN
        RAISE EXCEPTION 'AI schema signature line count mismatch: %', v_line_count;
    END IF;
    IF v_fingerprint <> '02a74f7aa59f1c428728b55facf6ba3ed9394d0d550bb50fe4e3913f4cf387dc' THEN
        RAISE EXCEPTION 'AI schema fingerprint mismatch: %', v_fingerprint;
    END IF;
END $$;

ROLLBACK;
