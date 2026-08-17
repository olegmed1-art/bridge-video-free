\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Club Operations integrity hardening before production promotion.
-- This migration closes gaps found by a second-pass audit of 0020-0024:
--   * entitlement usage/reversal integrity and over-consumption protection;
--   * separation of account balance from charge-allocation bookkeeping;
--   * exact financial-adjustment reversals;
--   * delivery channel/consent/timestamp integrity;
--   * entitlement grants moved out of the general interactive app capability.
-- -----------------------------------------------------------------------------

-- Entitlements are granted by the trusted financial/admin path, not by the general
-- interactive app. The app may consume an entitlement, but cannot mint one.
REVOKE INSERT ON TABLE person_entitlement FROM bridge_school_app, bridge_school_worker;
REVOKE UPDATE ON TABLE person_entitlement FROM bridge_school_app, bridge_school_worker;
GRANT INSERT ON TABLE person_entitlement TO bridge_school_finance;
GRANT UPDATE (valid_to, status) ON person_entitlement TO bridge_school_finance;

CREATE OR REPLACE FUNCTION validate_entitlement_usage_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_granted numeric(12,3);
    v_valid_from timestamptz;
    v_valid_to timestamptz;
    v_entitlement_status text;
    v_used_net numeric(12,3);
    v_target_entitlement uuid;
    v_target_quantity numeric(12,3);
    v_target_reversal uuid;
    v_target_status text;
    v_target_occurred_at timestamptz;
BEGIN
    SELECT quantity_granted, valid_from, valid_to, status
      INTO v_granted, v_valid_from, v_valid_to, v_entitlement_status
      FROM person_entitlement
     WHERE entitlement_id=NEW.entitlement_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'entitlement usage entitlement missing';
    END IF;

    -- Invalid audit/quarantine rows have no balance effect and are retained as evidence.
    IF NEW.status='invalid' THEN
        RETURN NEW;
    END IF;
    IF NEW.status <> 'applied' THEN
        RAISE EXCEPTION 'entitlement usage runtime status must be applied or invalid';
    END IF;
    IF v_entitlement_status='invalid' THEN
        RAISE EXCEPTION 'cannot use an invalid entitlement';
    END IF;
    IF NEW.occurred_at < v_valid_from
       OR (v_valid_to IS NOT NULL AND NEW.occurred_at >= v_valid_to) THEN
        RAISE EXCEPTION 'entitlement usage falls outside entitlement validity';
    END IF;

    IF NEW.reversal_of_usage_id IS NULL THEN
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

DROP TRIGGER IF EXISTS entitlement_usage_integrity_guard ON entitlement_usage;
CREATE TRIGGER entitlement_usage_integrity_guard
BEFORE INSERT ON entitlement_usage
FOR EACH ROW EXECUTE FUNCTION validate_entitlement_usage_integrity();

REVOKE ALL ON FUNCTION validate_entitlement_usage_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

-- The old view is useful for reconciling allocations against charges, but it is not the
-- person's true account balance when a payment has been received but not yet allocated.
ALTER VIEW person_financial_balance RENAME TO person_allocated_receivable_balance;

CREATE VIEW person_financial_balance AS
WITH charge_totals AS (
    SELECT school_id, person_id, currency_code, SUM(amount) AS charge_amount
      FROM club_charge
     GROUP BY school_id, person_id, currency_code
), payment_totals AS (
    SELECT school_id, person_id, currency_code, SUM(amount) AS payment_amount
      FROM club_payment
     GROUP BY school_id, person_id, currency_code
), adjustment_totals AS (
    SELECT school_id, person_id, currency_code, SUM(balance_delta) AS adjustment_amount
      FROM financial_adjustment
     GROUP BY school_id, person_id, currency_code
), keys AS (
    SELECT school_id, person_id, currency_code FROM charge_totals
    UNION
    SELECT school_id, person_id, currency_code FROM payment_totals
    UNION
    SELECT school_id, person_id, currency_code FROM adjustment_totals
)
SELECT
    k.school_id,
    k.person_id,
    k.currency_code,
    COALESCE(c.charge_amount,0)::numeric(14,2) AS charges,
    COALESCE(p.payment_amount,0)::numeric(14,2) AS payments,
    COALESCE(j.adjustment_amount,0)::numeric(14,2) AS adjustments,
    (COALESCE(c.charge_amount,0) - COALESCE(p.payment_amount,0) + COALESCE(j.adjustment_amount,0))::numeric(14,2) AS balance_due
FROM keys k
LEFT JOIN charge_totals c USING (school_id, person_id, currency_code)
LEFT JOIN payment_totals p USING (school_id, person_id, currency_code)
LEFT JOIN adjustment_totals j USING (school_id, person_id, currency_code);

GRANT SELECT ON person_financial_balance, person_allocated_receivable_balance
TO bridge_school_reader, bridge_school_finance;

CREATE OR REPLACE FUNCTION validate_financial_adjustment_reversal_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_currency text;
    v_delta numeric(14,2);
    v_occurred_at timestamptz;
BEGIN
    IF NEW.reversal_of_adjustment_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT school_id, person_id, currency_code, balance_delta, occurred_at
      INTO v_school, v_person, v_currency, v_delta, v_occurred_at
      FROM financial_adjustment
     WHERE adjustment_id=NEW.reversal_of_adjustment_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'financial adjustment reversal target missing';
    END IF;
    IF v_school <> NEW.school_id OR v_person <> NEW.person_id OR v_currency <> NEW.currency_code THEN
        RAISE EXCEPTION 'financial adjustment reversal scope mismatch';
    END IF;
    IF NEW.balance_delta <> -v_delta THEN
        RAISE EXCEPTION 'financial adjustment reversal must exactly negate original amount';
    END IF;
    IF NEW.occurred_at < v_occurred_at THEN
        RAISE EXCEPTION 'financial adjustment reversal cannot precede original adjustment';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS financial_adjustment_reversal_integrity_guard ON financial_adjustment;
CREATE TRIGGER financial_adjustment_reversal_integrity_guard
BEFORE INSERT ON financial_adjustment
FOR EACH ROW EXECUTE FUNCTION validate_financial_adjustment_reversal_integrity();

REVOKE ALL ON FUNCTION validate_financial_adjustment_reversal_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

-- Delivery chronology must remain internally consistent. Use NOT VALID + VALIDATE so
-- the migration pattern remains safe if a future non-production branch already has rows.
ALTER TABLE message_delivery
    ADD CONSTRAINT message_delivery_sent_time_ck
    CHECK (sent_at IS NULL OR sent_at >= queued_at) NOT VALID;
ALTER TABLE message_delivery VALIDATE CONSTRAINT message_delivery_sent_time_ck;

ALTER TABLE message_delivery
    ADD CONSTRAINT message_delivery_delivered_time_ck
    CHECK (delivered_at IS NULL OR (sent_at IS NOT NULL AND delivered_at >= sent_at)) NOT VALID;
ALTER TABLE message_delivery VALIDATE CONSTRAINT message_delivery_delivered_time_ck;

ALTER TABLE message_delivery
    ADD CONSTRAINT message_delivery_read_time_ck
    CHECK (read_at IS NULL OR (delivered_at IS NOT NULL AND read_at >= delivered_at)) NOT VALID;
ALTER TABLE message_delivery VALIDATE CONSTRAINT message_delivery_read_time_ck;

ALTER TABLE message_delivery
    ADD CONSTRAINT message_delivery_failed_time_ck
    CHECK (failed_at IS NULL OR failed_at >= queued_at) NOT VALID;
ALTER TABLE message_delivery VALIDATE CONSTRAINT message_delivery_failed_time_ck;

ALTER TABLE message_delivery
    ADD CONSTRAINT message_delivery_status_timestamp_ck
    CHECK (
        (status NOT IN ('sent','delivered','read') OR sent_at IS NOT NULL)
        AND (status NOT IN ('delivered','read') OR delivered_at IS NOT NULL)
        AND (status <> 'read' OR read_at IS NOT NULL)
        AND (status <> 'failed' OR failed_at IS NOT NULL)
    ) NOT VALID;
ALTER TABLE message_delivery VALIDATE CONSTRAINT message_delivery_status_timestamp_ck;

CREATE OR REPLACE FUNCTION validate_message_delivery_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_contact_channel text;
    v_contact_valid_from timestamptz;
    v_contact_valid_to timestamptz;
    v_contact_status text;
    v_communication_type text;
    v_preference_channel text;
BEGIN
    IF NEW.contact_method_id IS NULL THEN
        IF NEW.channel NOT IN ('web','other') THEN
            RAISE EXCEPTION 'delivery channel requires a contact method';
        END IF;
    ELSE
        SELECT channel, valid_from, valid_to, status
          INTO v_contact_channel, v_contact_valid_from, v_contact_valid_to, v_contact_status
          FROM contact_method
         WHERE contact_method_id=NEW.contact_method_id
           AND school_id=NEW.school_id
           AND person_id=NEW.recipient_person_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'delivery contact method missing or out of scope';
        END IF;
        IF v_contact_status='invalid'
           OR NEW.queued_at < v_contact_valid_from
           OR (v_contact_valid_to IS NOT NULL AND NEW.queued_at >= v_contact_valid_to) THEN
            RAISE EXCEPTION 'delivery contact method is not valid at queue time';
        END IF;
        IF NOT (
            v_contact_channel=NEW.channel
            OR (NEW.channel='sms' AND v_contact_channel='phone')
            OR (NEW.channel='other' AND v_contact_channel='other')
        ) THEN
            RAISE EXCEPTION 'delivery channel does not match contact method';
        END IF;
    END IF;

    SELECT c.communication_type
      INTO v_communication_type
      FROM club_message m
      JOIN club_communication c ON c.communication_id=m.communication_id
     WHERE m.message_id=NEW.message_id;

    IF v_communication_type IS NULL THEN
        RAISE EXCEPTION 'delivery communication type missing';
    END IF;

    v_preference_channel := CASE WHEN NEW.channel='sms' THEN 'phone' ELSE NEW.channel END;
    IF EXISTS (
        SELECT 1
          FROM contact_preference cp
         WHERE cp.school_id=NEW.school_id
           AND cp.person_id=NEW.recipient_person_id
           AND cp.channel=v_preference_channel
           AND cp.communication_type=v_communication_type
           AND cp.permission_state='denied'
           AND cp.valid_from <= NEW.queued_at
           AND (cp.valid_to IS NULL OR NEW.queued_at < cp.valid_to)
    ) THEN
        RAISE EXCEPTION 'delivery blocked by explicit contact preference';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS message_delivery_integrity_guard ON message_delivery;
CREATE TRIGGER message_delivery_integrity_guard
BEFORE INSERT OR UPDATE OF school_id, message_id, recipient_person_id,
                           contact_method_id, channel, queued_at
ON message_delivery
FOR EACH ROW EXECUTE FUNCTION validate_message_delivery_integrity();

REVOKE ALL ON FUNCTION validate_message_delivery_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0025_club_integrity_hardening')
ON CONFLICT DO NOTHING;

COMMIT;
