\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Actor-aware audit context for sensitive club operations.
-- Existing append-only business histories remain authoritative for their own domain;
-- this table records who/request/principal performed sensitive database changes.
-- -----------------------------------------------------------------------------

CREATE TABLE audit_event (
    audit_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid REFERENCES school(school_id),
    actor_person_id uuid REFERENCES person(person_id),
    auth_identity_id uuid REFERENCES auth_identity(auth_identity_id),
    request_id uuid,
    db_principal text NOT NULL,
    action_key text NOT NULL,
    object_type text NOT NULL,
    object_id uuid,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_event_school_time_idx
    ON audit_event(school_id, occurred_at DESC, audit_event_id DESC);
CREATE INDEX audit_event_actor_time_idx
    ON audit_event(actor_person_id, occurred_at DESC, audit_event_id DESC)
    WHERE actor_person_id IS NOT NULL;
CREATE INDEX audit_event_object_idx
    ON audit_event(object_type, object_id, occurred_at DESC)
    WHERE object_id IS NOT NULL;

-- Audit history is not a broad runtime read surface and cannot be forged by runtime.
REVOKE ALL ON TABLE audit_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member;

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

    v_actor_person_id := NULLIF(current_setting('bridge.actor_person_id', true),'')::uuid;
    v_auth_identity_id := NULLIF(current_setting('bridge.actor_auth_identity_id', true),'')::uuid;
    v_request_id := NULLIF(current_setting('bridge.request_id', true),'')::uuid;

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

-- Sensitive identity/authorization changes.
CREATE TRIGGER auth_identity_audit
AFTER INSERT OR UPDATE ON auth_identity
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER person_role_assignment_audit
AFTER INSERT OR UPDATE ON person_role_assignment
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER person_access_grant_audit
AFTER INSERT OR UPDATE ON person_access_grant
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();

-- Sensitive club operational changes named by the architecture acceptance gate.
CREATE TRIGGER club_membership_audit
AFTER INSERT OR UPDATE ON club_membership
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER contact_method_audit
AFTER INSERT OR UPDATE ON contact_method
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER contact_preference_audit
AFTER INSERT OR UPDATE ON contact_preference
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER person_entitlement_audit
AFTER INSERT OR UPDATE ON person_entitlement
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER club_booking_audit
AFTER INSERT OR UPDATE ON club_booking
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER club_charge_audit
AFTER INSERT ON club_charge
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER club_payment_audit
AFTER INSERT ON club_payment
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER payment_allocation_audit
AFTER INSERT ON payment_allocation
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER financial_adjustment_audit
AFTER INSERT ON financial_adjustment
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();
CREATE TRIGGER club_payment_refund_audit
AFTER INSERT ON club_payment_refund
FOR EACH ROW EXECUTE FUNCTION capture_sensitive_audit_event();

-- Enrich existing immutable membership lifecycle events with request actor identity when
-- an authenticated request context exists. Historical rows remain unchanged.
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
    v_actor_person_id := NULLIF(current_setting('bridge.actor_person_id', true),'')::uuid;
    v_auth_identity_id := NULLIF(current_setting('bridge.actor_auth_identity_id', true),'')::uuid;
    v_request_id := NULLIF(current_setting('bridge.request_id', true),'')::uuid;

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
        v_actor_person_id := NULLIF(current_setting('bridge.actor_person_id', true),'')::uuid;
        v_auth_identity_id := NULLIF(current_setting('bridge.actor_auth_identity_id', true),'')::uuid;
        v_request_id := NULLIF(current_setting('bridge.request_id', true),'')::uuid;

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
     bridge_school_health, bridge_school_finance, bridge_school_member;
REVOKE ALL ON FUNCTION seed_club_membership_initial_state()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member;
REVOKE ALL ON FUNCTION capture_club_membership_status_change()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance, bridge_school_member;

INSERT INTO schema_migration(migration_key)
VALUES ('0040_actor_audit_context')
ON CONFLICT DO NOTHING;

COMMIT;
