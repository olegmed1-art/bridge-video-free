\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Separate the trusted external-auth gateway from the ordinary member capability.
-- An end-user/member database role must never be able to select an arbitrary
-- AuthIdentity and establish that person's context. Only the server-side auth gateway
-- may establish a context after it has independently verified the provider token/session.
-- -----------------------------------------------------------------------------

DO $$
DECLARE r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_auth_gateway') THEN
        CREATE ROLE bridge_school_auth_gateway NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;

    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
      INTO r
      FROM pg_roles
     WHERE rolname='bridge_school_auth_gateway';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'auth gateway role missing';
    END IF;
    IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
        RAISE EXCEPTION 'auth gateway role has unsafe attributes';
    END IF;
END $$;

COMMENT ON ROLE bridge_school_auth_gateway IS
    'Trusted server-side capability for establishing a database actor context only after external provider authentication has already succeeded.';

REVOKE CREATE ON SCHEMA public FROM bridge_school_auth_gateway;
GRANT USAGE ON SCHEMA public TO bridge_school_auth_gateway;

-- The dormant server principal combines the narrow member read surface with the
-- trusted gateway capability. It remains NOLOGIN until a deliberate API credential
-- provisioning step. The member capability by itself cannot establish identity.
GRANT bridge_school_auth_gateway TO bridge_school_member_principal;

REVOKE EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid)
FROM bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid)
TO bridge_school_auth_gateway;

-- A one-argument role check is deliberately limited to school-wide assignments.
-- Scoped roles require a future explicit scoped authorization helper so a role on one
-- group/course/object cannot silently become a school-wide privilege.
CREATE OR REPLACE FUNCTION bridge_actor_has_role(p_role_key text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM person_role_assignment ra
         WHERE ra.school_id=bridge_current_actor_school_id()
           AND ra.person_id=bridge_current_actor_person_id()
           AND ra.role_key=p_role_key
           AND ra.scope_type='school'
           AND ra.scope_id IS NULL
           AND ra.status='active'
           AND ra.valid_from <= now()
           AND (ra.valid_to IS NULL OR now() < ra.valid_to)
    )
$$;

REVOKE ALL ON FUNCTION bridge_actor_has_role(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bridge_actor_has_role(text) TO bridge_school_member;

-- The gateway has no direct business-table read capability and no access to the
-- signing secret. SECURITY DEFINER establishment is its only identity entry point.
REVOKE ALL ON TABLE
    auth_identity,
    person_role_assignment,
    person_access_grant,
    actor_context_signing_secret,
    person,
    club_membership,
    club_charge,
    club_payment,
    club_message,
    audit_event
FROM bridge_school_auth_gateway;

REVOKE ALL ON FUNCTION bridge_actor_context_is_valid()
FROM bridge_school_auth_gateway;
REVOKE ALL ON FUNCTION capture_sensitive_audit_event()
FROM bridge_school_auth_gateway;

INSERT INTO schema_migration(migration_key)
VALUES ('0043_auth_gateway_and_school_role_scope')
ON CONFLICT DO NOTHING;

COMMIT;
