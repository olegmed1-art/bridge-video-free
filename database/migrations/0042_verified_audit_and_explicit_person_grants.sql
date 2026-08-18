\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Round-8 authorization hardening:
--   * a person-to-person permission is explicit; being the target person does not
--     automatically imply every possible permission key;
--   * audit and membership lifecycle attribution consume only the signed actor
--     resolver introduced in 0041, never raw user-settable custom settings.
-- -----------------------------------------------------------------------------

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
        AND EXISTS (
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
$$;

REVOKE ALL ON FUNCTION bridge_actor_has_person_permission(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bridge_actor_has_person_permission(uuid,text) TO bridge_school_member;

CREATE OR REPLACE FUNCTION capture_sensitive_audit_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row jsonb;
    v_school_id uuid;
    v_object_id uuid;
    v_actor_person_id uuid;
    v_auth_identity_id uuid;
    v_request_id uuid;
BEGIN
    IF TG_OP='DELETE' THEN
        v_row := to_jsonb(OLD);
    ELSE
        v_row := to_jsonb(NEW);
    END IF;

    v_school_id := NULLIF(v_row->>'school_id','')::uuid;

    v_object_id := CASE TG_TABLE_NAME
        WHEN 'auth_identity' THEN NULLIF(v_row->>'auth_identity_id','')::uuid
        WHEN 'person_role_assignment' THEN NULLIF(v_row->>'person_role_assignment_id','')::uuid
        WHEN 'person_access_grant' THEN NULLIF(v_row->>'person_access_grant_id','')::uuid
        WHEN 'club_membership' THEN NULLIF(v_row->>'club_membership_id','')::uuid
        WHEN 'contact_method' THEN NULLIF(v_row->>'contact_method_id','')::uuid
        WHEN 'contact_preference' THEN NULLIF(v_row->>'contact_preference_id','')::uuid
        WHEN 'person_entitlement' THEN NULLIF(v_row->>'entitlement_id','')::uuid
        WHEN 'club_booking' THEN NULLIF(v_row->>'booking_id','')::uuid
        WHEN 'club_charge' THEN NULLIF(v_row->>'charge_id','')::uuid
        WHEN 'club_payment' THEN NULLIF(v_row->>'payment_id','')::uuid
        WHEN 'payment_allocation' THEN NULLIF(v_row->>'payment_allocation_id','')::uuid
        WHEN 'financial_adjustment' THEN NULLIF(v_row->>'adjustment_id','')::uuid
        WHEN 'club_payment_refund' THEN NULLIF(v_row->>'payment_refund_id','')::uuid
        ELSE NULL
    END;

    -- These accessors validate the 0041 signature. Forged bridge.actor_* settings
    -- therefore produce an unattributed audit event rather than a false identity.
    v_actor_person_id := bridge_current_actor_person_id();
    v_auth_identity_id := bridge_current_actor_auth_identity_id();
    v_request_id := bridge_current_request_id();

    INSERT INTO audit_event(
        school_id,
        actor_person_id,
        auth_identity_id,
        request_id,
        db_principal,
        action_key,
        object_type,
        object_id,
        metadata
    ) VALUES (
        v_school_id,
        v_actor_person_id,
        v_auth_identity_id,
        v_request_id,
        session_user,
        lower(TG_OP),
        TG_TABLE_NAME,
        v_object_id,
        jsonb_build_object(
            'trigger_name', TG_NAME,
            'attributed', v_actor_person_id IS NOT NULL AND v_auth_identity_id IS NOT NULL
        )
    );

    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION seed_club_membership_initial_state()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor_person_id uuid;
    v_auth_identity_id uuid;
    v_request_id uuid;
BEGIN
    v_actor_person_id := bridge_current_actor_person_id();
    v_auth_identity_id := bridge_current_actor_auth_identity_id();
    v_request_id := bridge_current_request_id();

    INSERT INTO club_membership_state_event(
        club_membership_id,
        state,
        occurred_at,
        actor_person_id,
        metadata
    ) VALUES (
        NEW.club_membership_id,
        NEW.status,
        NEW.valid_from,
        v_actor_person_id,
        jsonb_strip_nulls(jsonb_build_object(
            'source','membership_insert',
            'auth_identity_id',v_auth_identity_id,
            'request_id',v_request_id
        ))
    );
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION capture_club_membership_status_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor_person_id uuid;
    v_auth_identity_id uuid;
    v_request_id uuid;
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        v_actor_person_id := bridge_current_actor_person_id();
        v_auth_identity_id := bridge_current_actor_auth_identity_id();
        v_request_id := bridge_current_request_id();

        INSERT INTO club_membership_state_event(
            club_membership_id,
            state,
            occurred_at,
            actor_person_id,
            metadata
        ) VALUES (
            NEW.club_membership_id,
            NEW.status,
            now(),
            v_actor_person_id,
            jsonb_strip_nulls(jsonb_build_object(
                'source','membership_status_update',
                'auth_identity_id',v_auth_identity_id,
                'request_id',v_request_id
            ))
        );
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION capture_sensitive_audit_event()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_member_principal;
REVOKE ALL ON FUNCTION seed_club_membership_initial_state()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_member_principal;
REVOKE ALL ON FUNCTION capture_club_membership_status_change()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member,
     bridge_school_member_principal;

INSERT INTO schema_migration(migration_key)
VALUES ('0042_verified_audit_and_explicit_person_grants')
ON CONFLICT DO NOTHING;

COMMIT;
