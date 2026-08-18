\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Auth identity and request actor context for future member-facing access.
-- This migration does not choose an authentication provider and does not expose
-- database credentials to members. External token/session validation remains an API
-- responsibility; this layer binds the already-verified external subject to Person.
-- -----------------------------------------------------------------------------

DO $$
DECLARE r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_member') THEN
        CREATE ROLE bridge_school_member NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_member_principal') THEN
        CREATE ROLE bridge_school_member_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;

    FOR r IN
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
          FROM pg_roles
         WHERE rolname IN ('bridge_school_member','bridge_school_member_principal')
    LOOP
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'member runtime role has unsafe attributes: %', r.rolname;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_member IS
    'Member-facing read capability. No broad reader inheritance; access only through fail-closed member projections/functions.';
COMMENT ON ROLE bridge_school_member_principal IS
    'Dormant member API principal. Keep NOLOGIN until an external secret and verified auth gateway are deliberately provisioned.';

GRANT bridge_school_member TO bridge_school_member_principal;
REVOKE CREATE ON SCHEMA public FROM bridge_school_member, bridge_school_member_principal;
GRANT USAGE ON SCHEMA public TO bridge_school_member, bridge_school_member_principal;

CREATE TABLE auth_identity (
    auth_identity_id uuid PRIMARY KEY DEFAULT uuidv7(),
    person_id uuid NOT NULL REFERENCES person(person_id),
    provider_key text NOT NULL,
    provider_subject text NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'active',
    last_authenticated_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(provider_key) <> ''),
    CHECK (btrim(provider_subject) <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','revoked','invalid')),
    CHECK (status <> 'revoked' OR valid_to IS NOT NULL),
    UNIQUE(provider_key, provider_subject)
);
CREATE INDEX auth_identity_person_idx
    ON auth_identity(person_id, status, valid_from DESC);

CREATE TABLE person_role_assignment (
    person_role_assignment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    role_key text NOT NULL,
    scope_type text NOT NULL DEFAULT 'school',
    scope_id uuid,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'active',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(role_key) <> ''),
    CHECK (btrim(scope_type) <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','revoked','invalid')),
    CHECK (status <> 'revoked' OR valid_to IS NOT NULL)
);
CREATE UNIQUE INDEX person_role_assignment_one_open_uk
    ON person_role_assignment(
        school_id,
        person_id,
        role_key,
        scope_type,
        COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid)
    )
    WHERE status='active' AND valid_to IS NULL;
CREATE INDEX person_role_assignment_active_idx
    ON person_role_assignment(school_id, person_id, role_key, valid_from DESC);

-- Explicit person-to-person grants provide the future instructor/student boundary
-- without automatically granting every instructor access to every student.
CREATE TABLE person_access_grant (
    person_access_grant_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    grantee_person_id uuid NOT NULL REFERENCES person(person_id),
    target_person_id uuid NOT NULL REFERENCES person(person_id),
    permission_key text NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'active',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (grantee_person_id <> target_person_id),
    CHECK (btrim(permission_key) <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','revoked','invalid')),
    CHECK (status <> 'revoked' OR valid_to IS NOT NULL)
);
CREATE UNIQUE INDEX person_access_grant_one_open_uk
    ON person_access_grant(school_id, grantee_person_id, target_person_id, permission_key)
    WHERE status='active' AND valid_to IS NULL;
CREATE INDEX person_access_grant_target_idx
    ON person_access_grant(school_id, target_person_id, permission_key, valid_from DESC);

-- Authentication mappings and authorization assignments are not part of the broad
-- internal reader surface, even though older default privileges grant new tables to it.
REVOKE ALL ON TABLE auth_identity, person_role_assignment, person_access_grant
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member;

CREATE OR REPLACE FUNCTION bridge_current_actor_person_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('bridge.actor_person_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION bridge_current_actor_school_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('bridge.actor_school_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION bridge_current_actor_auth_identity_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('bridge.actor_auth_identity_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION bridge_current_request_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('bridge.request_id', true), '')::uuid
$$;

-- The caller must already have verified the external provider token/session. This
-- function validates only the database-side mapping and school role, then stores the
-- actor context transaction-locally so pooled connections do not retain identity.
CREATE OR REPLACE FUNCTION bridge_establish_verified_actor_context(
    p_auth_identity_id uuid,
    p_school_id uuid,
    p_request_id uuid
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_person_id uuid;
BEGIN
    IF p_auth_identity_id IS NULL OR p_school_id IS NULL OR p_request_id IS NULL THEN
        RAISE EXCEPTION 'actor context requires auth identity, school and request id';
    END IF;

    SELECT ai.person_id
      INTO v_person_id
      FROM auth_identity ai
     WHERE ai.auth_identity_id=p_auth_identity_id
       AND ai.status='active'
       AND ai.valid_from <= now()
       AND (ai.valid_to IS NULL OR now() < ai.valid_to);

    IF v_person_id IS NULL THEN
        RAISE EXCEPTION 'active auth identity mapping missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM person_role_assignment ra
         WHERE ra.school_id=p_school_id
           AND ra.person_id=v_person_id
           AND ra.status='active'
           AND ra.valid_from <= now()
           AND (ra.valid_to IS NULL OR now() < ra.valid_to)
    ) THEN
        RAISE EXCEPTION 'actor has no active role in requested school';
    END IF;

    PERFORM set_config('bridge.actor_auth_identity_id', p_auth_identity_id::text, true);
    PERFORM set_config('bridge.actor_person_id', v_person_id::text, true);
    PERFORM set_config('bridge.actor_school_id', p_school_id::text, true);
    PERFORM set_config('bridge.request_id', p_request_id::text, true);

    RETURN v_person_id;
END;
$$;

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
           AND ra.status='active'
           AND ra.valid_from <= now()
           AND (ra.valid_to IS NULL OR now() < ra.valid_to)
    )
$$;

CREATE OR REPLACE FUNCTION bridge_actor_has_person_permission(
    p_target_person_id uuid,
    p_permission_key text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT
        bridge_current_actor_person_id() IS NOT NULL
        AND bridge_current_actor_school_id() IS NOT NULL
        AND (
            p_target_person_id=bridge_current_actor_person_id()
            OR EXISTS (
                SELECT 1
                  FROM person_access_grant g
                 WHERE g.school_id=bridge_current_actor_school_id()
                   AND g.grantee_person_id=bridge_current_actor_person_id()
                   AND g.target_person_id=p_target_person_id
                   AND g.permission_key=p_permission_key
                   AND g.status='active'
                   AND g.valid_from <= now()
                   AND (g.valid_to IS NULL OR now() < g.valid_to)
            )
        )
$$;

REVOKE ALL ON FUNCTION bridge_current_actor_person_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_current_actor_school_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_current_actor_auth_identity_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_current_request_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_actor_has_role(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_actor_has_person_permission(uuid,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION bridge_current_actor_person_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_current_actor_school_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_current_actor_auth_identity_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_current_request_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_actor_has_role(text) TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_actor_has_person_permission(uuid,text) TO bridge_school_member;

INSERT INTO schema_migration(migration_key)
VALUES ('0038_auth_identity_actor_context')
ON CONFLICT DO NOTHING;

COMMIT;
