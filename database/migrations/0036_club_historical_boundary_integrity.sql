\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Historical boundary integrity.
-- Later lifecycle/catalog edits must not make already-recorded facts impossible in
-- hindsight. Corrections remain append-oriented; this migration only prevents a
-- validity/effective boundary from being moved before dependent historical facts.
-- -----------------------------------------------------------------------------

ALTER TABLE person_package_grant
    ADD CONSTRAINT person_package_grant_closed_status_requires_valid_to_ck
    CHECK (status NOT IN ('expired','revoked') OR valid_to IS NOT NULL) NOT VALID;
ALTER TABLE person_package_grant
    VALIDATE CONSTRAINT person_package_grant_closed_status_requires_valid_to_ck;

CREATE OR REPLACE FUNCTION validate_entitlement_usage_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_granted numeric(12,3);
    v_valid_from timestamptz;
    v_valid_to timestamptz;
    v_entitlement_status text;
    v_package_grant_id uuid;
    v_grant_from timestamptz;
    v_grant_to timestamptz;
    v_grant_status text;
    v_used_net numeric(12,3);
    v_target_entitlement uuid;
    v_target_quantity numeric(12,3);
    v_target_reversal uuid;
    v_target_status text;
    v_target_occurred_at timestamptz;
BEGIN
    SELECT quantity_granted, valid_from, valid_to, status, package_grant_id
      INTO v_granted, v_valid_from, v_valid_to, v_entitlement_status, v_package_grant_id
      FROM person_entitlement
     WHERE entitlement_id=NEW.entitlement_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'entitlement usage entitlement missing';
    END IF;

    IF NEW.status='invalid' THEN
        RETURN NEW;
    END IF;
    IF NEW.status <> 'applied' THEN
        RAISE EXCEPTION 'entitlement usage runtime status must be applied or invalid';
    END IF;

    IF NEW.reversal_of_usage_id IS NULL THEN
        IF v_entitlement_status='invalid' THEN
            RAISE EXCEPTION 'cannot use an invalid entitlement';
        END IF;
        IF NEW.occurred_at < v_valid_from
           OR (v_valid_to IS NOT NULL AND NEW.occurred_at >= v_valid_to) THEN
            RAISE EXCEPTION 'entitlement usage falls outside entitlement validity';
        END IF;

        IF v_package_grant_id IS NOT NULL THEN
            SELECT valid_from, valid_to, status
              INTO v_grant_from, v_grant_to, v_grant_status
              FROM person_package_grant
             WHERE package_grant_id=v_package_grant_id
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'package-backed entitlement grant missing';
            END IF;
            IF v_grant_status='invalid' THEN
                RAISE EXCEPTION 'cannot use entitlement from an invalid package grant';
            END IF;
            IF NEW.occurred_at < v_grant_from
               OR (v_grant_to IS NOT NULL AND NEW.occurred_at >= v_grant_to) THEN
                RAISE EXCEPTION 'entitlement usage falls outside package grant validity';
            END IF;
        END IF;

        SELECT COALESCE(SUM(
            CASE
                WHEN status='applied' AND reversal_of_usage_id IS NULL THEN quantity_used
                WHEN status='applied' AND reversal_of_usage_id IS NOT NULL THEN -quantity_used
                ELSE 0
            END
        ),0)::numeric(12,3)
          INTO v_used_net
          FROM entitlement_usage
         WHERE entitlement_id=NEW.entitlement_id;

        IF v_used_net + NEW.quantity_used > v_granted THEN
            RAISE EXCEPTION 'entitlement usage exceeds granted quantity';
        END IF;
    ELSE
        SELECT entitlement_id, quantity_used, reversal_of_usage_id, status, occurred_at
          INTO v_target_entitlement, v_target_quantity, v_target_reversal,
               v_target_status, v_target_occurred_at
          FROM entitlement_usage
         WHERE entitlement_usage_id=NEW.reversal_of_usage_id
         FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'entitlement reversal target missing';
        END IF;
        IF v_target_entitlement <> NEW.entitlement_id THEN
            RAISE EXCEPTION 'entitlement reversal target belongs to another entitlement';
        END IF;
        IF v_target_reversal IS NOT NULL THEN
            RAISE EXCEPTION 'reversal of an entitlement reversal is not supported';
        END IF;
        IF v_target_status <> 'applied' THEN
            RAISE EXCEPTION 'entitlement reversal target is not applied';
        END IF;
        IF NEW.quantity_used <> v_target_quantity THEN
            RAISE EXCEPTION 'entitlement reversal quantity must match original usage';
        END IF;
        IF NEW.occurred_at < v_target_occurred_at THEN
            RAISE EXCEPTION 'entitlement reversal cannot precede original usage';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_entitlement_valid_to_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.valid_to IS NULL THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM entitlement_usage u
         WHERE u.entitlement_id=NEW.entitlement_id
           AND u.status='applied'
           AND u.reversal_of_usage_id IS NULL
           AND u.occurred_at >= NEW.valid_to
    ) THEN
        RAISE EXCEPTION 'entitlement valid_to would precede recorded usage';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entitlement_valid_to_history_guard ON person_entitlement;
CREATE TRIGGER entitlement_valid_to_history_guard
BEFORE UPDATE OF valid_to ON person_entitlement
FOR EACH ROW EXECUTE FUNCTION validate_entitlement_valid_to_history();

CREATE OR REPLACE FUNCTION validate_package_grant_valid_to_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.valid_to IS NULL THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM person_entitlement e
          JOIN entitlement_usage u ON u.entitlement_id=e.entitlement_id
         WHERE e.package_grant_id=NEW.package_grant_id
           AND u.status='applied'
           AND u.reversal_of_usage_id IS NULL
           AND u.occurred_at >= NEW.valid_to
    ) THEN
        RAISE EXCEPTION 'package grant valid_to would precede recorded entitlement usage';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS package_grant_valid_to_history_guard ON person_package_grant;
CREATE TRIGGER package_grant_valid_to_history_guard
BEFORE UPDATE OF valid_to ON person_package_grant
FOR EACH ROW EXECUTE FUNCTION validate_package_grant_valid_to_history();

CREATE OR REPLACE FUNCTION validate_contact_valid_to_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.valid_to IS NULL THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM message_delivery d
         WHERE d.contact_method_id=NEW.contact_method_id
           AND d.queued_at >= NEW.valid_to
    ) THEN
        RAISE EXCEPTION 'contact valid_to would precede recorded delivery';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS contact_valid_to_history_guard ON contact_method;
CREATE TRIGGER contact_valid_to_history_guard
BEFORE UPDATE OF valid_to ON contact_method
FOR EACH ROW EXECUTE FUNCTION validate_contact_valid_to_history();

CREATE OR REPLACE FUNCTION validate_commercial_effective_to_history()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.effective_to IS NULL THEN
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME='service_price_version' THEN
        IF EXISTS (
            SELECT 1 FROM club_charge c
             WHERE c.price_version_id=NEW.price_version_id
               AND c.charged_at >= NEW.effective_to
        ) THEN
            RAISE EXCEPTION 'service price effective_to would invalidate recorded charge provenance';
        END IF;
    ELSIF TG_TABLE_NAME='package_price_version' THEN
        IF EXISTS (
            SELECT 1 FROM person_package_grant g
             WHERE g.package_price_version_id=NEW.package_price_version_id
               AND g.granted_at >= NEW.effective_to
        ) OR EXISTS (
            SELECT 1 FROM club_charge c
             WHERE c.package_price_version_id=NEW.package_price_version_id
               AND c.charged_at >= NEW.effective_to
        ) THEN
            RAISE EXCEPTION 'package price effective_to would invalidate recorded grant/charge provenance';
        END IF;
    ELSIF TG_TABLE_NAME='club_package_version' THEN
        IF EXISTS (
            SELECT 1 FROM person_package_grant g
             WHERE g.package_version_id=NEW.package_version_id
               AND g.granted_at >= NEW.effective_to
        ) THEN
            RAISE EXCEPTION 'package version effective_to would invalidate recorded acquisition provenance';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS service_price_effective_to_history_guard ON service_price_version;
CREATE TRIGGER service_price_effective_to_history_guard
BEFORE UPDATE OF effective_to ON service_price_version
FOR EACH ROW EXECUTE FUNCTION validate_commercial_effective_to_history();

DROP TRIGGER IF EXISTS package_price_effective_to_history_guard ON package_price_version;
CREATE TRIGGER package_price_effective_to_history_guard
BEFORE UPDATE OF effective_to ON package_price_version
FOR EACH ROW EXECUTE FUNCTION validate_commercial_effective_to_history();

DROP TRIGGER IF EXISTS package_version_effective_to_history_guard ON club_package_version;
CREATE TRIGGER package_version_effective_to_history_guard
BEFORE UPDATE OF effective_to ON club_package_version
FOR EACH ROW EXECUTE FUNCTION validate_commercial_effective_to_history();

REVOKE ALL ON FUNCTION validate_entitlement_usage_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_entitlement_valid_to_history()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_package_grant_valid_to_history()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_contact_valid_to_history()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_commercial_effective_to_history()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0036_club_historical_boundary_integrity')
ON CONFLICT DO NOTHING;

COMMIT;
