\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Harden actor context against direct SET of custom PostgreSQL settings.
-- Custom GUCs are user-settable, so raw bridge.actor_* values are not an authorization
-- boundary by themselves. Sign the context with a protected database secret and bind
-- it to the current backend and transaction. Member-facing projections resolve actor
-- identity only when that signature is valid.
-- -----------------------------------------------------------------------------

CREATE TABLE actor_context_signing_secret (
    singleton_id smallint PRIMARY KEY CHECK (singleton_id=1),
    secret_bytes bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO actor_context_signing_secret(singleton_id,secret_bytes)
VALUES (1,gen_random_bytes(32))
ON CONFLICT (singleton_id) DO NOTHING;

REVOKE ALL ON TABLE actor_context_signing_secret
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_member_principal;

CREATE OR REPLACE FUNCTION bridge_actor_context_is_valid()
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_auth_identity text;
    v_person text;
    v_school text;
    v_request text;
    v_xid text;
    v_signature text;
    v_secret bytea;
    v_payload text;
    v_expected text;
BEGIN
    v_auth_identity := NULLIF(current_setting('bridge.actor_auth_identity_id', true),'');
    v_person := NULLIF(current_setting('bridge.actor_person_id', true),'');
    v_school := NULLIF(current_setting('bridge.actor_school_id', true),'');
    v_request := NULLIF(current_setting('bridge.request_id', true),'');
    v_xid := NULLIF(current_setting('bridge.actor_xid', true),'');
    v_signature := NULLIF(current_setting('bridge.actor_context_signature', true),'');

    IF v_auth_identity IS NULL OR v_person IS NULL OR v_school IS NULL
       OR v_request IS NULL OR v_xid IS NULL OR v_signature IS NULL THEN
        RETURN false;
    END IF;

    -- Reject malformed settings without turning a member SELECT into an exception.
    PERFORM v_auth_identity::uuid;
    PERFORM v_person::uuid;
    PERFORM v_school::uuid;
    PERFORM v_request::uuid;
    PERFORM v_xid::bigint;

    IF v_xid::bigint <> txid_current() THEN
        RETURN false;
    END IF;

    SELECT secret_bytes INTO v_secret
      FROM actor_context_signing_secret
     WHERE singleton_id=1;
    IF v_secret IS NULL THEN
        RETURN false;
    END IF;

    v_payload := concat_ws('|',
        v_auth_identity,
        v_person,
        v_school,
        v_request,
        v_xid,
        pg_backend_pid()::text
    );
    v_expected := encode(hmac(convert_to(v_payload,'UTF8'),v_secret,'sha256'),'hex');

    RETURN v_signature=v_expected;
EXCEPTION WHEN OTHERS THEN
    RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION bridge_current_actor_person_id()
RETURNS uuid
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT bridge_actor_context_is_valid() THEN
        RETURN NULL;
    END IF;
    RETURN NULLIF(current_setting('bridge.actor_person_id', true),'')::uuid;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION bridge_current_actor_school_id()
RETURNS uuid
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT bridge_actor_context_is_valid() THEN
        RETURN NULL;
    END IF;
    RETURN NULLIF(current_setting('bridge.actor_school_id', true),'')::uuid;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION bridge_current_actor_auth_identity_id()
RETURNS uuid
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT bridge_actor_context_is_valid() THEN
        RETURN NULL;
    END IF;
    RETURN NULLIF(current_setting('bridge.actor_auth_identity_id', true),'')::uuid;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION bridge_current_request_id()
RETURNS uuid
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT bridge_actor_context_is_valid() THEN
        RETURN NULL;
    END IF;
    RETURN NULLIF(current_setting('bridge.request_id', true),'')::uuid;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$;

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
    v_xid bigint;
    v_secret bytea;
    v_payload text;
    v_signature text;
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

    SELECT secret_bytes INTO v_secret
      FROM actor_context_signing_secret
     WHERE singleton_id=1;
    IF v_secret IS NULL THEN
        RAISE EXCEPTION 'actor context signing secret missing';
    END IF;

    v_xid := txid_current();
    v_payload := concat_ws('|',
        p_auth_identity_id::text,
        v_person_id::text,
        p_school_id::text,
        p_request_id::text,
        v_xid::text,
        pg_backend_pid()::text
    );
    v_signature := encode(hmac(convert_to(v_payload,'UTF8'),v_secret,'sha256'),'hex');

    PERFORM set_config('bridge.actor_auth_identity_id',p_auth_identity_id::text,true);
    PERFORM set_config('bridge.actor_person_id',v_person_id::text,true);
    PERFORM set_config('bridge.actor_school_id',p_school_id::text,true);
    PERFORM set_config('bridge.request_id',p_request_id::text,true);
    PERFORM set_config('bridge.actor_xid',v_xid::text,true);
    PERFORM set_config('bridge.actor_context_signature',v_signature,true);

    IF NOT bridge_actor_context_is_valid() THEN
        RAISE EXCEPTION 'actor context signature verification failed';
    END IF;

    RETURN v_person_id;
END;
$$;

REVOKE ALL ON FUNCTION bridge_actor_context_is_valid()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_member_principal;

-- The four resolved context accessors and the establishment entry point retain the
-- narrow member capability granted in 0038. No function exposing the signing secret or
-- an arbitrary-signature primitive exists.
REVOKE ALL ON FUNCTION bridge_current_actor_person_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_current_actor_school_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_current_actor_auth_identity_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_current_request_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bridge_current_actor_person_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_current_actor_school_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_current_actor_auth_identity_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_current_request_id() TO bridge_school_member;
GRANT EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) TO bridge_school_member;

INSERT INTO schema_migration(migration_key)
VALUES ('0041_actor_context_signature_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
