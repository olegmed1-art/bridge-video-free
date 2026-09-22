-- Stage only the missing health principal. No password, LOGIN or CONNECT grant.
-- Execute through local postgres peer authentication in the candidate database.
-- psql -X -v ON_ERROR_STOP=1; the entire file is one atomic transaction.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '10s';
SELECT pg_advisory_xact_lock(724091, 20260922);
DO $guard$
BEGIN
  IF current_database() <> 'autopilot_candidate_20260922'
     OR session_user <> 'postgres' THEN
    RAISE EXCEPTION 'wrong staging database or administrator';
  END IF;
  IF NOT EXISTS (
    SELECT FROM pg_roles WHERE rolname = 'bridge_school_health'
      AND NOT (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
               OR rolreplication OR rolbypassrls)
  ) THEN
    RAISE EXCEPTION 'health capability missing or elevated';
  END IF;
  IF EXISTS (
    SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
    WHERE r.rolname = 'bridge_school_health'
  ) THEN
    RAISE EXCEPTION 'unexpected health capability inheritance';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bridge_school_health_principal') THEN
    CREATE ROLE bridge_school_health_principal NOLOGIN INHERIT
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (
    SELECT FROM pg_authid WHERE rolname = 'bridge_school_health_principal'
      AND rolinherit AND rolpassword IS NULL
      AND NOT (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
               OR rolreplication OR rolbypassrls)
  ) THEN
    RAISE EXCEPTION 'existing health principal drift';
  END IF;
  IF EXISTS (
    SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
    WHERE r.rolname = 'bridge_school_health_principal'
  ) THEN
    RAISE EXCEPTION 'unexpected members of health principal';
  END IF;
  IF EXISTS (
    SELECT FROM pg_auth_members m
    JOIN pg_roles member_role ON member_role.oid = m.member
    JOIN pg_roles parent_role ON parent_role.oid = m.roleid
    WHERE member_role.rolname = 'bridge_school_health_principal'
      AND (parent_role.rolname <> 'bridge_school_health' OR m.admin_option)
  ) THEN
    RAISE EXCEPTION 'unexpected health principal membership';
  END IF;
END
$guard$;
GRANT bridge_school_health TO bridge_school_health_principal
  WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
DO $verify$
BEGIN
  IF has_database_privilege('bridge_school_health_principal', current_database(), 'CONNECT') THEN
    RAISE EXCEPTION 'health principal unexpectedly has candidate CONNECT';
  END IF;
  IF (SELECT count(*) FROM pg_auth_members m
      JOIN pg_roles r ON r.oid = m.member
      WHERE r.rolname = 'bridge_school_health_principal') <> 1 THEN
    RAISE EXCEPTION 'health membership cardinality mismatch';
  END IF;
  IF NOT EXISTS (
    SELECT FROM pg_auth_members m
    JOIN pg_roles r ON r.oid = m.member
    JOIN pg_roles p ON p.oid = m.roleid
    WHERE r.rolname = 'bridge_school_health_principal'
      AND p.rolname = 'bridge_school_health' AND NOT m.admin_option
      AND m.inherit_option AND m.set_option
  ) OR EXISTS (
    SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
    WHERE r.rolname = 'bridge_school_health_principal'
  ) THEN
    RAISE EXCEPTION 'final health membership mismatch';
  END IF;
END
$verify$;
SET LOCAL ROLE bridge_school_health_principal;
SELECT count(*) AS health_view_canary_rows FROM public.autopilot_operational_health_signal;
RESET ROLE;
COMMIT;
