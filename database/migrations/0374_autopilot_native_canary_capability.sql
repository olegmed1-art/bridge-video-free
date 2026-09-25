\set ON_ERROR_STOP on
BEGIN;

DO $guard$ BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration
               WHERE migration_key='0373_autopilot_native_single_canary_permit') THEN
  RAISE EXCEPTION 'NATIVE_CAPABILITY_REQUIRES_0373';
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='autopilot_native_canary') THEN
  RAISE EXCEPTION 'NATIVE_CAPABILITY_ROLE_ALREADY_EXISTS';
 END IF;
END $guard$;

-- A dormant capability, never a connection identity. A separately reviewed
-- activation must create its own dedicated LOGIN and grant membership only
-- after reconciling the role, schema, CLI account and broker publication.
CREATE ROLE autopilot_native_canary NOLOGIN NOINHERIT
 NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA autopilot TO autopilot_native_canary;
GRANT EXECUTE ON FUNCTION autopilot.native_cli_reserve_canary(uuid,jsonb,text),
 autopilot.native_cli_snapshot(uuid),
 autopilot.native_cli_begin_canary(jsonb),
 autopilot.native_cli_canary_current(jsonb),
 autopilot.native_cli_ack(jsonb,text,text),
 autopilot.native_cli_finish(jsonb,text,jsonb)
 TO autopilot_native_canary;

-- Never grant native_cli_reserve/native_cli_begin (the unrestricted 0339 RPCs),
-- direct receipt/table writes or a credential to this capability.
DO $verify$ BEGIN
 IF (SELECT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
            OR rolreplication OR rolbypassrls
       FROM pg_roles WHERE rolname='autopilot_native_canary') IS DISTINCT FROM false
 -- PostgreSQL 18 grants the non-superuser CREATEROLE creator ADMIN-only
 -- membership automatically. It confers neither inherited RPCs nor SET ROLE.
 OR EXISTS(SELECT FROM pg_auth_members m
           JOIN pg_roles r ON r.oid=m.roleid
           JOIN pg_roles member ON member.oid=m.member
           WHERE r.rolname='autopilot_native_canary'
             AND (member.rolname<>SESSION_USER OR NOT m.admin_option
                  OR m.inherit_option OR m.set_option))
 OR has_function_privilege('autopilot_native_canary',
      'autopilot.native_cli_reserve(uuid,jsonb,text)','EXECUTE')
 OR has_function_privilege('autopilot_native_canary',
      'autopilot.native_cli_begin(jsonb)','EXECUTE')
 OR has_table_privilege('autopilot_native_canary',
      'autopilot.native_cli_receipt','INSERT') THEN
  RAISE EXCEPTION 'NATIVE_CAPABILITY_PRIVILEGE_DRIFT';
 END IF;
END $verify$;

INSERT INTO public.schema_migration(migration_key)
 VALUES('0374_autopilot_native_canary_capability');
COMMIT;
