\set ON_ERROR_STOP on
-- 0500: install only the Books authority component. No runtime grants or seed data.
-- A complete known component is verified without changes; partial/foreign state is HOLD.
\if :{?migration_checksum}
\else
\echo BOOKS_AUTHORITY_CHECKSUM_REQUIRED
\quit 3
\endif
\if :{?books_expected_database}
\else
\echo BOOKS_AUTHORITY_EXPECTED_TARGET_REQUIRED
\quit 3
\endif
\if :{?books_expected_owner}
\else
\echo BOOKS_AUTHORITY_EXPECTED_OWNER_REQUIRED
\quit 3
\endif
BEGIN;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='2s';
SET LOCAL idle_in_transaction_session_timeout='30s';
SET LOCAL search_path=pg_catalog;
SELECT set_config('books.authority_expected_checksum', :'migration_checksum',true) AS books_checksum,
       set_config('books.authority_expected_database', :'books_expected_database',true) AS books_database,
       set_config('books.authority_expected_owner', :'books_expected_owner',true) AS books_owner \gset
DO $books_preflight$
DECLARE
    owner_id oid := (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user);
    public_id oid := pg_catalog.to_regnamespace('public');
    stored text; known boolean;
    reader_id oid := (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='bridge_school_reader');
BEGIN
    IF current_database() IS DISTINCT FROM current_setting('books.authority_expected_database')
       OR current_user IS DISTINCT FROM current_setting('books.authority_expected_owner') OR current_user<>session_user
       OR current_setting('server_version_num')::integer/10000<>18
       OR current_user IN ('bridge_school_worker_principal','bridge_school_worker','bridge_school_app_principal','bridge_school_app','bridge_school_health_principal','bridge_school_health')
       OR owner_id IS DISTINCT FROM (SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database())
       OR public_id IS NULL OR NOT pg_catalog.has_schema_privilege(current_user,'public','CREATE')
       OR NOT EXISTS (SELECT FROM pg_catalog.pg_namespace WHERE oid=public_id AND
            (nspowner=owner_id OR nspowner=COALESCE((SELECT oid FROM pg_catalog.pg_roles WHERE rolname='pg_database_owner'),0))) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_TARGET_OR_OWNER_MISMATCH';
    END IF;
    IF reader_id IS NULL THEN RAISE EXCEPTION 'BOOKS_AUTHORITY_READER_ROLE_REQUIRED'; END IF;
    IF current_setting('books.authority_expected_checksum')!~'^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_CHECKSUM_REQUIRED';
    END IF;
    IF pg_catalog.to_regclass('public.schema_migration') IS NULL THEN RAISE EXCEPTION 'BOOKS_AUTHORITY_HISTORY_REQUIRED'; END IF;
    IF NOT pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtext(current_database()),pg_catalog.hashtext('bridge_school_schema_migrations')) THEN
        RAISE EXCEPTION 'BOOKS_AUTHORITY_MIGRATION_BUSY';
    END IF;
    LOCK TABLE public.schema_migration IN SHARE ROW EXCLUSIVE MODE NOWAIT;
    SELECT checksum INTO stored FROM public.schema_migration WHERE migration_key='0500_books_authority_core';
    known:=FOUND;
    IF known THEN
        IF stored IS DISTINCT FROM current_setting('books.authority_expected_checksum') THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_PROVENANCE_MISMATCH';
        END IF;
        PERFORM set_config('books.authority_apply','false',true);
    ELSE
        IF EXISTS (SELECT FROM pg_catalog.pg_class WHERE relnamespace=public_id AND relname IN
              ('books_authority_object','books_authority_state','books_authority_state_audit','books_authority_attempt','books_authority_phase'))
           OR EXISTS (SELECT FROM pg_catalog.pg_type WHERE typnamespace=public_id AND typname IN
              ('books_authority_object','books_authority_state','books_authority_state_audit','books_authority_attempt','books_authority_phase'))
           OR EXISTS (SELECT FROM pg_catalog.pg_proc WHERE pronamespace=public_id AND proname IN
              ('books_authority_immutable','books_authority_state_transition','books_authority_state_log'))
           OR EXISTS (SELECT FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                       WHERE c.relnamespace=public_id AND t.tgname IN ('books_authority_object_immutable','books_authority_phase_immutable',
                          'books_authority_audit_immutable','books_authority_state_guard','books_authority_state_history')) THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_PARTIAL_OR_FOREIGN_SCHEMA';
        END IF;
        IF EXISTS (SELECT FROM pg_catalog.pg_default_acl d CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) x
                   WHERE d.defaclrole=owner_id AND d.defaclnamespace IN(0,public_id) AND d.defaclobjtype IN('r','f')
                     AND x.grantee<>owner_id
                     AND NOT(d.defaclobjtype='f'AND x.grantee=0 AND x.privilege_type='EXECUTE'AND NOT x.is_grantable)
                     AND NOT(d.defaclobjtype='r' AND d.defaclnamespace=public_id
                             AND x.grantee=reader_id AND x.privilege_type='SELECT' AND NOT x.is_grantable)) THEN
            RAISE EXCEPTION 'BOOKS_AUTHORITY_DEFAULT_ACL_HOLD';
        END IF;
        PERFORM set_config('books.authority_apply','true',true);
    END IF;
END $books_preflight$;
SELECT current_setting('books.authority_apply')::boolean AS books_authority_apply \gset
\if :books_authority_apply
\ir 0500_books_authority_core/001_core.sql
INSERT INTO public.schema_migration(migration_key,checksum)
VALUES ('0500_books_authority_core',current_setting('books.authority_expected_checksum'));
\endif
\ir 0500_books_authority_core/002_assert.sql
COMMIT;
\echo BOOKS_AUTHORITY_CORE_VERIFIED
