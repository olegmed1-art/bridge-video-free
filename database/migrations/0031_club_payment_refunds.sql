\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Payment refund semantics.
-- A financial_adjustment changes the member account, but it does not mean cash was
-- returned. Refunds are therefore first-class append-only cash facts tied to Payment.
-- Effective allocations may never exceed the remaining net cash after refunds.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS club_payment_refund (
    payment_refund_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    payment_id uuid NOT NULL REFERENCES club_payment(payment_id),
    amount numeric(14,2) NOT NULL CHECK (amount > 0),
    refunded_at timestamptz NOT NULL DEFAULT now(),
    refund_method text,
    provider_reference text,
    external_reference text,
    reason text NOT NULL,
    reversal_of_refund_id uuid REFERENCES club_payment_refund(payment_refund_id),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (reversal_of_refund_id IS NULL OR reversal_of_refund_id <> payment_refund_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS club_payment_refund_one_reversal_uk
    ON club_payment_refund(reversal_of_refund_id)
    WHERE reversal_of_refund_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS club_payment_refund_payment_idx
    ON club_payment_refund(payment_id, refunded_at DESC);
CREATE INDEX IF NOT EXISTS club_payment_refund_school_time_idx
    ON club_payment_refund(school_id, refunded_at DESC);

CREATE OR REPLACE VIEW club_payment_net AS
WITH refund_totals AS (
    SELECT
        payment_id,
        SUM(CASE WHEN reversal_of_refund_id IS NULL THEN amount ELSE -amount END)::numeric(14,2)
            AS refunded_amount
    FROM club_payment_refund
    GROUP BY payment_id
)
SELECT
    p.payment_id,
    p.school_id,
    p.person_id,
    p.currency_code,
    p.amount AS gross_amount,
    COALESCE(r.refunded_amount,0)::numeric(14,2) AS refunded_amount,
    (p.amount - COALESCE(r.refunded_amount,0))::numeric(14,2) AS net_amount,
    p.paid_at
FROM club_payment p
LEFT JOIN refund_totals r ON r.payment_id=p.payment_id;

CREATE OR REPLACE FUNCTION validate_payment_refund_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_payment_amount numeric(14,2);
    v_paid_at timestamptz;
    v_existing_refunded numeric(14,2);
    v_effective_allocated numeric(14,2);
    v_target_payment uuid;
    v_target_amount numeric(14,2);
    v_target_reversal uuid;
    v_target_refunded_at timestamptz;
BEGIN
    -- The payment row is the serialization point shared with allocation writes.
    SELECT school_id, amount, paid_at
      INTO v_school, v_payment_amount, v_paid_at
      FROM club_payment
     WHERE payment_id=NEW.payment_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'payment refund payment missing';
    END IF;
    IF v_school <> NEW.school_id THEN
        RAISE EXCEPTION 'payment refund school mismatch';
    END IF;
    IF NEW.refunded_at < v_paid_at THEN
        RAISE EXCEPTION 'payment refund cannot precede payment';
    END IF;

    SELECT COALESCE(SUM(
        CASE WHEN reversal_of_refund_id IS NULL THEN amount ELSE -amount END
    ),0)::numeric(14,2)
      INTO v_existing_refunded
      FROM club_payment_refund
     WHERE payment_id=NEW.payment_id;

    SELECT COALESCE(SUM(amount),0)::numeric(14,2)
      INTO v_effective_allocated
      FROM payment_allocation_effective
     WHERE payment_id=NEW.payment_id
       AND is_effective;

    IF NEW.reversal_of_refund_id IS NULL THEN
        IF v_existing_refunded + NEW.amount < 0
           OR v_existing_refunded + NEW.amount + v_effective_allocated > v_payment_amount THEN
            RAISE EXCEPTION 'payment refund exceeds unallocated net payment capacity';
        END IF;
    ELSE
        SELECT payment_id, amount, reversal_of_refund_id, refunded_at
          INTO v_target_payment, v_target_amount, v_target_reversal, v_target_refunded_at
          FROM club_payment_refund
         WHERE payment_refund_id=NEW.reversal_of_refund_id
         FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'payment refund reversal target missing';
        END IF;
        IF v_target_payment <> NEW.payment_id THEN
            RAISE EXCEPTION 'payment refund reversal target belongs to another payment';
        END IF;
        IF v_target_reversal IS NOT NULL THEN
            RAISE EXCEPTION 'reversal of a payment refund reversal is not supported';
        END IF;
        IF NEW.amount <> v_target_amount THEN
            RAISE EXCEPTION 'payment refund reversal must exactly match original refund';
        END IF;
        IF NEW.refunded_at < v_target_refunded_at THEN
            RAISE EXCEPTION 'payment refund reversal cannot precede original refund';
        END IF;
        IF v_existing_refunded - NEW.amount < 0 THEN
            RAISE EXCEPTION 'payment refund reversal would make net refunds negative';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_payment_refund_integrity_guard ON club_payment_refund;
CREATE TRIGGER club_payment_refund_integrity_guard
BEFORE INSERT ON club_payment_refund
FOR EACH ROW EXECUTE FUNCTION validate_payment_refund_integrity();

-- Existing allocation scope/bounds remain in force. This additional guard makes the
-- capacity refund-aware without rewriting earlier immutable migration history.
CREATE OR REPLACE FUNCTION validate_payment_allocation_net_capacity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_payment_amount numeric(14,2);
    v_refunded_amount numeric(14,2);
    v_effective_allocated numeric(14,2);
BEGIN
    SELECT amount
      INTO v_payment_amount
      FROM club_payment
     WHERE payment_id=NEW.payment_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'allocation payment missing';
    END IF;

    SELECT COALESCE(SUM(
        CASE WHEN reversal_of_refund_id IS NULL THEN amount ELSE -amount END
    ),0)::numeric(14,2)
      INTO v_refunded_amount
      FROM club_payment_refund
     WHERE payment_id=NEW.payment_id;

    SELECT COALESCE(SUM(a.amount),0)::numeric(14,2)
      INTO v_effective_allocated
      FROM payment_allocation a
      LEFT JOIN payment_allocation_reversal r
        ON r.payment_allocation_id=a.payment_allocation_id
     WHERE a.payment_id=NEW.payment_id
       AND r.payment_allocation_reversal_id IS NULL;

    IF v_effective_allocated + NEW.amount > v_payment_amount - v_refunded_amount THEN
        RAISE EXCEPTION 'payment allocation exceeds net payment after refunds';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS payment_allocation_net_capacity_guard ON payment_allocation;
CREATE TRIGGER payment_allocation_net_capacity_guard
BEFORE INSERT ON payment_allocation
FOR EACH ROW EXECUTE FUNCTION validate_payment_allocation_net_capacity();

CREATE OR REPLACE VIEW person_financial_balance AS
WITH charge_totals AS (
    SELECT school_id, person_id, currency_code, SUM(amount) AS charge_amount
      FROM club_charge
     GROUP BY school_id, person_id, currency_code
), payment_totals AS (
    SELECT school_id, person_id, currency_code, SUM(net_amount) AS payment_amount
      FROM club_payment_net
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

-- The prior version has fewer columns. PostgreSQL does not allow CREATE OR REPLACE
-- VIEW to insert columns in the middle, so recreate this projection explicitly.
DROP VIEW person_unallocated_payment;
CREATE VIEW person_unallocated_payment AS
SELECT
    p.payment_id,
    p.school_id,
    p.person_id,
    p.currency_code,
    p.gross_amount AS amount,
    p.refunded_amount,
    p.net_amount,
    COALESCE(SUM(CASE WHEN a.is_effective THEN a.amount ELSE 0 END),0)::numeric(14,2) AS allocated_amount,
    (p.net_amount - COALESCE(SUM(CASE WHEN a.is_effective THEN a.amount ELSE 0 END),0))::numeric(14,2)
        AS unallocated_amount,
    p.paid_at
FROM club_payment_net p
LEFT JOIN payment_allocation_effective a ON a.payment_id=p.payment_id
GROUP BY p.payment_id, p.school_id, p.person_id, p.currency_code,
         p.gross_amount, p.refunded_amount, p.net_amount, p.paid_at;

ALTER TABLE accounting_document_reference
    ADD COLUMN IF NOT EXISTS payment_refund_id uuid REFERENCES club_payment_refund(payment_refund_id);
ALTER TABLE accounting_document_reference
    ADD CONSTRAINT accounting_document_refund_requires_payment_ck
    CHECK (payment_refund_id IS NULL OR payment_id IS NOT NULL) NOT VALID;
ALTER TABLE accounting_document_reference
    VALIDATE CONSTRAINT accounting_document_refund_requires_payment_ck;

CREATE OR REPLACE FUNCTION validate_accounting_document_refund_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.payment_refund_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.payment_id IS NULL OR NOT EXISTS (
        SELECT 1
          FROM club_payment_refund r
          JOIN club_payment p ON p.payment_id=r.payment_id
         WHERE r.payment_refund_id=NEW.payment_refund_id
           AND r.payment_id=NEW.payment_id
           AND r.school_id=NEW.school_id
           AND p.person_id=NEW.person_id
    ) THEN
        RAISE EXCEPTION 'accounting document refund scope mismatch';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS accounting_document_refund_scope_guard ON accounting_document_reference;
CREATE TRIGGER accounting_document_refund_scope_guard
BEFORE INSERT OR UPDATE OF school_id, person_id, payment_id, payment_refund_id
ON accounting_document_reference
FOR EACH ROW EXECUTE FUNCTION validate_accounting_document_refund_scope();

GRANT INSERT ON TABLE club_payment_refund TO bridge_school_finance;
REVOKE UPDATE, DELETE ON TABLE club_payment_refund
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
GRANT SELECT ON club_payment_refund, club_payment_net,
                person_financial_balance, person_unallocated_payment
TO bridge_school_finance;
GRANT SELECT ON club_payment_net, person_financial_balance, person_unallocated_payment
TO bridge_school_reader;

REVOKE ALL ON FUNCTION validate_payment_refund_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_payment_allocation_net_capacity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_accounting_document_refund_scope()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0031_club_payment_refunds')
ON CONFLICT DO NOTHING;

COMMIT;
