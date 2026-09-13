\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Club Operations semantic integrity discovered by the post-0025 review.
-- Keep business definitions deterministic without inventing club pricing/consent policy.
-- -----------------------------------------------------------------------------

-- One currently preferred active contact per person/channel avoids ambiguous routing.
CREATE UNIQUE INDEX IF NOT EXISTS contact_method_one_preferred_open_uk
    ON contact_method(school_id, person_id, channel)
    WHERE status='active' AND valid_to IS NULL AND preferred_flag;

-- Active commercial versions must not overlap in effective time.
ALTER TABLE service_price_version
    ADD CONSTRAINT service_price_active_period_excl
    EXCLUDE USING gist (
        service_id WITH =,
        tstzrange(effective_from, effective_to, '[)') WITH &&
    ) WHERE (status='active');

ALTER TABLE club_package_version
    ADD CONSTRAINT club_package_active_period_excl
    EXCLUDE USING gist (
        package_id WITH =,
        tstzrange(effective_from, effective_to, '[)') WITH &&
    ) WHERE (status='active');

-- A club event may specialize as a lesson/session or a tournament, but not both at once.
ALTER TABLE club_event
    ADD CONSTRAINT club_event_one_specialized_reference_ck
    CHECK (num_nonnulls(session_id, tournament_id) <= 1) NOT VALID;
ALTER TABLE club_event VALIDATE CONSTRAINT club_event_one_specialized_reference_ck;

-- If an entitlement claims a package origin, the package definition must actually grant
-- that service and the single entitlement cannot exceed the package rule quantity.
CREATE OR REPLACE FUNCTION validate_entitlement_package_rule()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule_quantity numeric(12,3);
BEGIN
    IF NEW.package_version_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT quantity
      INTO v_rule_quantity
      FROM package_service_rule
     WHERE package_version_id=NEW.package_version_id
       AND service_id=NEW.service_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'entitlement service is not granted by package version';
    END IF;
    IF NEW.quantity_granted > v_rule_quantity THEN
        RAISE EXCEPTION 'entitlement quantity exceeds package service rule';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entitlement_package_rule_guard ON person_entitlement;
CREATE TRIGGER entitlement_package_rule_guard
BEFORE INSERT OR UPDATE OF package_version_id, service_id, quantity_granted
ON person_entitlement
FOR EACH ROW EXECUTE FUNCTION validate_entitlement_package_rule();

-- A fresh consumption needs an active entitlement. Reversals remain possible after
-- expiry/revocation so accounting corrections are never blocked by lifecycle state.
CREATE OR REPLACE FUNCTION validate_entitlement_usage_active_status()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.status='applied' AND NEW.reversal_of_usage_id IS NULL THEN
        SELECT status INTO v_status
          FROM person_entitlement
         WHERE entitlement_id=NEW.entitlement_id;
        IF v_status IS NULL THEN
            RAISE EXCEPTION 'entitlement usage entitlement missing';
        END IF;
        IF v_status <> 'active' THEN
            RAISE EXCEPTION 'new entitlement consumption requires an active entitlement';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entitlement_usage_active_status_guard ON entitlement_usage;
CREATE TRIGGER entitlement_usage_active_status_guard
BEFORE INSERT ON entitlement_usage
FOR EACH ROW EXECUTE FUNCTION validate_entitlement_usage_active_status();

-- A price version on a charge must belong to the same service as the charge. School and
-- currency checks alone are insufficient because two services may share both.
CREATE OR REPLACE FUNCTION validate_charge_price_service_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_price_service uuid;
BEGIN
    IF NEW.price_version_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT service_id INTO v_price_service
      FROM service_price_version
     WHERE price_version_id=NEW.price_version_id;

    IF v_price_service IS NULL THEN
        RAISE EXCEPTION 'charge price version missing';
    END IF;
    IF NEW.service_id IS NULL OR NEW.service_id <> v_price_service THEN
        RAISE EXCEPTION 'charge price version belongs to another service';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_charge_price_service_guard ON club_charge;
CREATE TRIGGER club_charge_price_service_guard
BEFORE INSERT ON club_charge
FOR EACH ROW EXECUTE FUNCTION validate_charge_price_service_integrity();

-- Delivery never routes through a contact that is currently revoked/superseded/invalid.
-- Historical delivery records remain untouched; this guard applies to new routing choices.
CREATE OR REPLACE FUNCTION validate_delivery_active_contact()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.contact_method_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT status INTO v_status
      FROM contact_method
     WHERE contact_method_id=NEW.contact_method_id;

    IF v_status IS NULL THEN
        RAISE EXCEPTION 'delivery contact method missing';
    END IF;
    IF v_status <> 'active' THEN
        RAISE EXCEPTION 'delivery requires an active contact method';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS message_delivery_active_contact_guard ON message_delivery;
CREATE TRIGGER message_delivery_active_contact_guard
BEFORE INSERT OR UPDATE OF contact_method_id
ON message_delivery
FOR EACH ROW EXECUTE FUNCTION validate_delivery_active_contact();

REVOKE ALL ON FUNCTION validate_entitlement_package_rule()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_entitlement_usage_active_status()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_charge_price_service_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_delivery_active_contact()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0026_club_semantic_integrity')
ON CONFLICT DO NOTHING;

COMMIT;
