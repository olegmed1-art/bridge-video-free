\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Club Operations correction semantics.
-- Closes two audit gaps found after 0026:
--   1) entitlement usage reversals must remain possible after entitlement expiry;
--   2) payment allocations need an append-only reversal mechanism so a mistaken
--      allocation can be corrected without UPDATE/DELETE of financial history.
-- -----------------------------------------------------------------------------

-- Rebuild the entitlement usage guard so validity/lifecycle checks apply to fresh
-- consumption, while exact reversals remain possible after expiry/revocation/invalid
-- lifecycle changes. A reversal still must exactly match the original applied usage.
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

REVOKE ALL ON FUNCTION validate_entitlement_usage_integrity()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

-- Allocation corrections are separate immutable facts. Reversal is full by design:
-- if only part of an allocation was wrong, reverse the original allocation and append
-- one or more corrected allocations. This preserves an unambiguous audit trail.
CREATE TABLE IF NOT EXISTS payment_allocation_reversal (
    payment_allocation_reversal_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    payment_allocation_id uuid NOT NULL REFERENCES payment_allocation(payment_allocation_id),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    reason text NOT NULL,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (payment_allocation_id)
);
CREATE INDEX IF NOT EXISTS payment_allocation_reversal_school_time_idx
    ON payment_allocation_reversal(school_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION validate_payment_allocation_reversal()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_allocated_at timestamptz;
BEGIN
    SELECT school_id, allocated_at
      INTO v_school, v_allocated_at
      FROM payment_allocation
     WHERE payment_allocation_id=NEW.payment_allocation_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'payment allocation reversal target missing';
    END IF;
    IF v_school <> NEW.school_id THEN
        RAISE EXCEPTION 'payment allocation reversal school mismatch';
    END IF;
    IF NEW.occurred_at < v_allocated_at THEN
        RAISE EXCEPTION 'payment allocation reversal cannot precede allocation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS payment_allocation_reversal_guard ON payment_allocation_reversal;
CREATE TRIGGER payment_allocation_reversal_guard
BEFORE INSERT ON payment_allocation_reversal
FOR EACH ROW EXECUTE FUNCTION validate_payment_allocation_reversal();

-- Net allocation view is the authoritative source for allocation reconciliation.
CREATE OR REPLACE VIEW payment_allocation_effective AS
SELECT
    a.payment_allocation_id,
    a.school_id,
    a.payment_id,
    a.charge_id,
    a.amount,
    a.allocated_at,
    (r.payment_allocation_reversal_id IS NULL) AS is_effective,
    r.payment_allocation_reversal_id,
    r.occurred_at AS reversed_at
FROM payment_allocation a
LEFT JOIN payment_allocation_reversal r
  ON r.payment_allocation_id=a.payment_allocation_id;

CREATE OR REPLACE VIEW person_allocated_receivable_balance AS
WITH charge_totals AS (
    SELECT school_id, person_id, currency_code, SUM(amount) AS charge_amount
      FROM club_charge
     GROUP BY school_id, person_id, currency_code
), allocation_totals AS (
    SELECT c.school_id, c.person_id, c.currency_code, SUM(a.amount) AS allocated_amount
      FROM payment_allocation_effective a
      JOIN club_charge c ON c.charge_id=a.charge_id
     WHERE a.is_effective
     GROUP BY c.school_id, c.person_id, c.currency_code
), adjustment_totals AS (
    SELECT school_id, person_id, currency_code, SUM(balance_delta) AS adjustment_amount
      FROM financial_adjustment
     GROUP BY school_id, person_id, currency_code
), keys AS (
    SELECT school_id, person_id, currency_code FROM charge_totals
    UNION
    SELECT school_id, person_id, currency_code FROM adjustment_totals
)
SELECT
    k.school_id,
    k.person_id,
    k.currency_code,
    COALESCE(c.charge_amount,0)::numeric(14,2) AS charges,
    COALESCE(a.allocated_amount,0)::numeric(14,2) AS allocated_payments,
    COALESCE(j.adjustment_amount,0)::numeric(14,2) AS adjustments,
    (COALESCE(c.charge_amount,0) - COALESCE(a.allocated_amount,0) + COALESCE(j.adjustment_amount,0))::numeric(14,2) AS balance_due
FROM keys k
LEFT JOIN charge_totals c USING (school_id, person_id, currency_code)
LEFT JOIN allocation_totals a USING (school_id, person_id, currency_code)
LEFT JOIN adjustment_totals j USING (school_id, person_id, currency_code);

CREATE OR REPLACE VIEW person_unallocated_payment AS
SELECT
    p.payment_id,
    p.school_id,
    p.person_id,
    p.currency_code,
    p.amount,
    COALESCE(SUM(CASE WHEN a.is_effective THEN a.amount ELSE 0 END),0)::numeric(14,2) AS allocated_amount,
    (p.amount - COALESCE(SUM(CASE WHEN a.is_effective THEN a.amount ELSE 0 END),0))::numeric(14,2) AS unallocated_amount,
    p.paid_at
FROM club_payment p
LEFT JOIN payment_allocation_effective a ON a.payment_id=p.payment_id
GROUP BY p.payment_id;

GRANT INSERT ON TABLE payment_allocation_reversal TO bridge_school_finance;
REVOKE UPDATE, DELETE ON TABLE payment_allocation_reversal
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
GRANT SELECT ON payment_allocation_reversal, payment_allocation_effective,
                person_allocated_receivable_balance, person_unallocated_payment
TO bridge_school_finance;
GRANT SELECT ON payment_allocation_effective,
                person_allocated_receivable_balance, person_unallocated_payment
TO bridge_school_reader;

REVOKE ALL ON FUNCTION validate_payment_allocation_reversal()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0027_club_correction_semantics')
ON CONFLICT DO NOTHING;

COMMIT;
