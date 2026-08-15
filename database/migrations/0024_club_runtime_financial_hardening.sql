\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Harden the candidate Club Operations model before any real member/financial data
-- is admitted. Finance gets a narrow capability, ledger allocation is bounded, and
-- runtime roles cannot rewrite immutable identities through broad UPDATE grants.
-- -----------------------------------------------------------------------------

-- Finance must not inherit the broad school reader capability. Give it only the
-- operational reads needed for financial work and trigger scope validation.
REVOKE bridge_school_reader FROM bridge_school_finance;

GRANT SELECT ON TABLE
    school,
    person,
    club_membership,
    club_service,
    service_price_version,
    club_package,
    club_package_version,
    package_service_rule,
    person_entitlement,
    person_entitlement_balance,
    club_event,
    club_booking,
    club_booking_current_state,
    club_charge,
    club_payment,
    payment_allocation,
    financial_adjustment,
    accounting_document_reference,
    person_financial_balance,
    person_unallocated_payment
TO bridge_school_finance;

-- Tighten interactive app updates. Base identities stay stable; lifecycle changes use
-- status/validity fields or append-only state-event tables.
REVOKE UPDATE ON TABLE
    club_membership,
    contact_method,
    contact_preference,
    person_entitlement,
    club_booking
FROM bridge_school_app;

GRANT UPDATE (valid_to, status) ON club_membership TO bridge_school_app;
GRANT UPDATE (verification_status, preferred_flag, valid_to, status) ON contact_method TO bridge_school_app;
GRANT UPDATE (valid_to) ON contact_preference TO bridge_school_app;
GRANT UPDATE (valid_to, status) ON person_entitlement TO bridge_school_app;

-- Booking identity is immutable after creation; changes are appended to state events.
REVOKE UPDATE ON TABLE club_booking FROM bridge_school_worker;

-- Delivery workers may advance delivery telemetry, not rewrite message/recipient/contact
-- identity or the original queued timestamp.
REVOKE UPDATE ON TABLE message_delivery FROM bridge_school_worker;
GRANT UPDATE (
    provider_reference,
    status,
    sent_at,
    delivered_at,
    read_at,
    failed_at,
    error_category,
    metadata
) ON message_delivery TO bridge_school_worker;

CREATE OR REPLACE FUNCTION validate_financial_ledger_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_currency text;
    v_payment_amount numeric(14,2);
    v_charge_amount numeric(14,2);
    v_payment_allocated numeric(14,2);
    v_charge_allocated numeric(14,2);
BEGIN
    IF TG_TABLE_NAME='club_charge' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
            RAISE EXCEPTION 'charge person missing';
        END IF;
        IF NEW.service_id IS NOT NULL THEN
            SELECT school_id INTO v_school FROM club_service WHERE service_id=NEW.service_id;
            IF v_school IS NULL OR v_school <> NEW.school_id THEN
                RAISE EXCEPTION 'charge service school mismatch';
            END IF;
        END IF;
        IF NEW.booking_id IS NOT NULL THEN
            SELECT school_id, person_id INTO v_school, v_person
              FROM club_booking WHERE booking_id=NEW.booking_id;
            IF v_school IS NULL OR v_school <> NEW.school_id OR v_person <> NEW.person_id THEN
                RAISE EXCEPTION 'charge booking scope mismatch';
            END IF;
        END IF;
        IF NEW.price_version_id IS NOT NULL THEN
            SELECT s.school_id, pv.currency_code INTO v_school, v_currency
              FROM service_price_version pv
              JOIN club_service s ON s.service_id=pv.service_id
             WHERE pv.price_version_id=NEW.price_version_id;
            IF v_school IS NULL OR v_school <> NEW.school_id OR v_currency <> NEW.currency_code THEN
                RAISE EXCEPTION 'charge price version scope/currency mismatch';
            END IF;
        END IF;

    ELSIF TG_TABLE_NAME='club_payment' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
            RAISE EXCEPTION 'payment person missing';
        END IF;

    ELSIF TG_TABLE_NAME='payment_allocation' THEN
        -- Serialize concurrent allocations for this payment and charge so two writers
        -- cannot both pass a stale SUM check and over-allocate the ledger.
        SELECT school_id, person_id, currency_code, amount
          INTO v_school, v_person, v_currency, v_payment_amount
          FROM club_payment
         WHERE payment_id=NEW.payment_id
         FOR UPDATE;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN
            RAISE EXCEPTION 'allocation payment school mismatch';
        END IF;

        SELECT amount INTO v_charge_amount
          FROM club_charge c
         WHERE c.charge_id=NEW.charge_id
           AND c.school_id=NEW.school_id
           AND c.person_id=v_person
           AND c.currency_code=v_currency
         FOR UPDATE;
        IF v_charge_amount IS NULL THEN
            RAISE EXCEPTION 'allocation charge person/currency/school mismatch';
        END IF;

        SELECT COALESCE(SUM(amount),0)::numeric(14,2)
          INTO v_payment_allocated
          FROM payment_allocation
         WHERE payment_id=NEW.payment_id;
        SELECT COALESCE(SUM(amount),0)::numeric(14,2)
          INTO v_charge_allocated
          FROM payment_allocation
         WHERE charge_id=NEW.charge_id;

        IF v_payment_allocated + NEW.amount > v_payment_amount THEN
            RAISE EXCEPTION 'payment allocation exceeds payment amount';
        END IF;
        IF v_charge_allocated + NEW.amount > v_charge_amount THEN
            RAISE EXCEPTION 'payment allocation exceeds charge amount';
        END IF;

    ELSIF TG_TABLE_NAME='financial_adjustment' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
            RAISE EXCEPTION 'adjustment person missing';
        END IF;
        IF NEW.related_charge_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_charge c
             WHERE c.charge_id=NEW.related_charge_id
               AND c.school_id=NEW.school_id
               AND c.person_id=NEW.person_id
               AND c.currency_code=NEW.currency_code
        ) THEN
            RAISE EXCEPTION 'adjustment charge scope mismatch';
        END IF;
        IF NEW.related_payment_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_payment p
             WHERE p.payment_id=NEW.related_payment_id
               AND p.school_id=NEW.school_id
               AND p.person_id=NEW.person_id
               AND p.currency_code=NEW.currency_code
        ) THEN
            RAISE EXCEPTION 'adjustment payment scope mismatch';
        END IF;
        IF NEW.related_allocation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
              FROM payment_allocation pa
              JOIN club_charge c ON c.charge_id=pa.charge_id
             WHERE pa.payment_allocation_id=NEW.related_allocation_id
               AND pa.school_id=NEW.school_id
               AND c.person_id=NEW.person_id
               AND c.currency_code=NEW.currency_code
        ) THEN
            RAISE EXCEPTION 'adjustment allocation scope mismatch';
        END IF;
        IF NEW.reversal_of_adjustment_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM financial_adjustment a
             WHERE a.adjustment_id=NEW.reversal_of_adjustment_id
               AND a.school_id=NEW.school_id
               AND a.person_id=NEW.person_id
               AND a.currency_code=NEW.currency_code
        ) THEN
            RAISE EXCEPTION 'adjustment reversal scope mismatch';
        END IF;

    ELSIF TG_TABLE_NAME='accounting_document_reference' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
            RAISE EXCEPTION 'accounting document person missing';
        END IF;
        IF NEW.charge_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_charge c
             WHERE c.charge_id=NEW.charge_id
               AND c.school_id=NEW.school_id
               AND c.person_id=NEW.person_id
        ) THEN
            RAISE EXCEPTION 'document charge scope mismatch';
        END IF;
        IF NEW.payment_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_payment p
             WHERE p.payment_id=NEW.payment_id
               AND p.school_id=NEW.school_id
               AND p.person_id=NEW.person_id
        ) THEN
            RAISE EXCEPTION 'document payment scope mismatch';
        END IF;
        IF NEW.adjustment_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM financial_adjustment a
             WHERE a.adjustment_id=NEW.adjustment_id
               AND a.school_id=NEW.school_id
               AND a.person_id=NEW.person_id
        ) THEN
            RAISE EXCEPTION 'document adjustment scope mismatch';
        END IF;
        IF NEW.artifact_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
              FROM artifact_version av
              JOIN artifact a ON a.artifact_id=av.artifact_id
             WHERE av.artifact_version_id=NEW.artifact_version_id
               AND a.school_id=NEW.school_id
        ) THEN
            RAISE EXCEPTION 'accounting document artifact belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION validate_financial_ledger_scope()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0024_club_runtime_financial_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
