\set ON_ERROR_STOP on
BEGIN;

-- This rollback changes only the callback function and migration-owned state.
-- Accepted terminal receipts, retained evidence, task progress, and released
-- resource leases are deliberately left untouched.
DO $$
DECLARE
    signature constant regprocedure :=
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure;
    backup record;
    current_definition text;
    current_owner text;
    current_acl text;
    current_comment text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0336_autopilot_codex_terminal_implicit_delivery'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IMPLICIT_DELIVERY_NOT_APPLIED';
    END IF;
    IF to_regclass('autopilot.migration_0336_function_backup') IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IMPLICIT_DELIVERY_BACKUP_MISSING';
    END IF;

    SELECT * INTO backup
      FROM autopilot.migration_0336_function_backup
     WHERE function_key='accept_role_dispatch_codex_terminal';
    IF NOT FOUND
       OR backup.previous_definition IS NULL
       OR backup.previous_owner IS NULL
       OR backup.previous_acl IS NULL
       OR backup.installed_definition IS NULL
       OR backup.installed_owner IS NULL
       OR backup.installed_acl IS NULL
       OR (SELECT count(*) FROM autopilot.migration_0336_function_backup)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IMPLICIT_DELIVERY_BACKUP_INVALID';
    END IF;

    SELECT pg_get_functiondef(proc.oid),pg_get_userbyid(proc.proowner),
           COALESCE(proc.proacl::text,''),obj_description(proc.oid,'pg_proc')
      INTO current_definition,current_owner,current_acl,current_comment
      FROM pg_proc AS proc
     WHERE proc.oid=signature;
    IF current_definition IS DISTINCT FROM backup.installed_definition
       OR current_owner IS DISTINCT FROM backup.installed_owner
       OR current_acl IS DISTINCT FROM backup.installed_acl
       OR current_comment IS DISTINCT FROM backup.installed_comment THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IMPLICIT_DELIVERY_DEFINITION_DRIFT';
    END IF;

    EXECUTE backup.previous_definition;
    EXECUTE format(
        'COMMENT ON FUNCTION %s IS %s',
        signature::text,
        CASE WHEN backup.previous_comment IS NULL THEN 'NULL'
             ELSE quote_literal(backup.previous_comment) END
    );

    -- Re-establish the exact pre-0336 least-privilege contract. CREATE OR
    -- REPLACE preserves ownership; these statements make ACL restoration
    -- explicit and independently verifiable.
    REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
        text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
    ) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
    GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
        text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
    ) TO autopilot_callback;

    SELECT pg_get_functiondef(proc.oid),pg_get_userbyid(proc.proowner),
           COALESCE(proc.proacl::text,''),obj_description(proc.oid,'pg_proc')
      INTO current_definition,current_owner,current_acl,current_comment
      FROM pg_proc AS proc
     WHERE proc.oid=signature;
    IF current_definition IS DISTINCT FROM backup.previous_definition
       OR current_owner IS DISTINCT FROM backup.previous_owner
       OR current_acl IS DISTINCT FROM backup.previous_acl
       OR current_comment IS DISTINCT FROM backup.previous_comment THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IMPLICIT_DELIVERY_RESTORE_MISMATCH';
    END IF;
END $$;

DROP TABLE autopilot.migration_0336_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0336_autopilot_codex_terminal_implicit_delivery';
COMMIT;
