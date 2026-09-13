\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Charge / ledger provenance integrity.
-- A charge must not claim two different commercial origins, a service charge linked to
-- a booking must agree with the event's service when that service is known, and an
-- allocation cannot occur before either of the financial facts it connects.
-- -----------------------------------------------------------------------------

ALTER TABLE club_charge
    ADD CONSTRAINT club_charge_one_commercial_origin_ck
    CHECK (num_nonnulls(service_id, package_grant_id) <= 1) NOT VALID;
ALTER TABLE club_charge
    VALIDATE CONSTRAINT club_charge_one_commercial_origin_ck;

CREATE OR REPLACE FUNCTION validate_charge_booking_service_provenance()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_booking_service uuid;
BEGIN
    IF NEW.booking_id IS NULL OR NEW.service_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT e.service_id
      INTO v_booking_service
      FROM club_booking b
      JOIN club_event e ON e.club_event_id=b.club_event_id
     WHERE b.booking_id=NEW.booking_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'charge booking missing';
    END IF;
    IF v_booking_service IS NOT NULL AND v_booking_service <> NEW.service_id THEN
        RAISE EXCEPTION 'charge service differs from booked event service';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_charge_booking_service_provenance_guard ON club_charge;
CREATE TRIGGER club_charge_booking_service_provenance_guard
BEFORE INSERT ON club_charge
FOR EACH ROW EXECUTE FUNCTION validate_charge_booking_service_provenance();

CREATE OR REPLACE FUNCTION validate_payment_allocation_business_chronology()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_paid_at timestamptz;
    v_charged_at timestamptz;
BEGIN
    SELECT paid_at INTO v_paid_at
      FROM club_payment
     WHERE payment_id=NEW.payment_id;
    IF v_paid_at IS NULL THEN
        RAISE EXCEPTION 'allocation payment missing';
    END IF;

    SELECT charged_at INTO v_charged_at
      FROM club_charge
     WHERE charge_id=NEW.charge_id;
    IF v_charged_at IS NULL THEN
        RAISE EXCEPTION 'allocation charge missing';
    END IF;

    IF NEW.allocated_at < v_paid_at THEN
        RAISE EXCEPTION 'payment allocation cannot precede payment';
    END IF;
    IF NEW.allocated_at < v_charged_at THEN
        RAISE EXCEPTION 'payment allocation cannot precede charge';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS payment_allocation_business_chronology_guard ON payment_allocation;
CREATE TRIGGER payment_allocation_business_chronology_guard
BEFORE INSERT ON payment_allocation
FOR EACH ROW EXECUTE FUNCTION validate_payment_allocation_business_chronology();

REVOKE ALL ON FUNCTION validate_charge_booking_service_provenance()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_payment_allocation_business_chronology()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0037_club_charge_ledger_provenance_integrity')
ON CONFLICT DO NOTHING;

COMMIT;
