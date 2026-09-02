\set ON_ERROR_STOP on
BEGIN;

-- 0102 narrowed personal-cabinet authorization to the school-scoped `member` role,
-- but its replacement of bridge_establish_verified_actor_context() accidentally
-- omitted the signed-context protocol introduced in 0041. Restore the signature,
-- backend and transaction binding while retaining the narrower portal/member gate.

CREATE OR REPLACE FUNCTION bridge_establish_verified_actor_context(
    p_auth_identity_id uuid,
    p_school_id uuid,
    p_request_id uuid
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
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

    SELECT ai.person_id INTO v_person_id
      FROM public.auth_identity ai
     WHERE ai.auth_identity_id=p_auth_identity_id
       AND ai.status='active'
       AND ai.valid_from<=now()
       AND (ai.valid_to IS NULL OR now()<ai.valid_to);
    IF v_person_id IS NULL THEN
        RAISE EXCEPTION 'active auth identity mapping missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.person_role_assignment ra
         WHERE ra.school_id=p_school_id
           AND ra.person_id=v_person_id
           AND ra.role_key='member'
           AND ra.scope_type='school'
           AND ra.scope_id IS NULL
           AND ra.status='active'
           AND ra.valid_from<=now()
           AND (ra.valid_to IS NULL OR now()<ra.valid_to)
    ) THEN
        RAISE EXCEPTION 'actor has no active portal/member access in requested school';
    END IF;

    SELECT secret_bytes INTO v_secret
      FROM public.actor_context_signing_secret
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

    IF NOT public.bridge_actor_context_is_valid() THEN
        RAISE EXCEPTION 'actor context signature verification failed';
    END IF;

    RETURN v_person_id;
END;
$$;

REVOKE ALL ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid)
FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal;
GRANT EXECUTE ON FUNCTION bridge_establish_verified_actor_context(uuid,uuid,uuid)
TO bridge_school_auth_gateway;

INSERT INTO schema_migration(migration_key)
VALUES ('0103_auth_portal_actor_context_signature_fix')
ON CONFLICT DO NOTHING;
COMMIT;
