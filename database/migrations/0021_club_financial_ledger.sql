\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Club financial ledger.
-- Operational accounting facts are append-oriented. Balances are derived views.
-- Official tax/accounting documents remain external references / Artifacts.
-- -----------------------------------------------------------------------------

DO $$
DECLARE r record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_finance') THEN
        CREATE ROLE bridge_school_finance NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bridge_school_finance_principal') THEN
        CREATE ROLE bridge_school_finance_principal NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION INHERIT;
    END IF;
    FOR r IN SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
               FROM pg_roles
              WHERE rolname IN ('bridge_school_finance','bridge_school_finance_principal')
    LOOP
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'finance runtime role has unsafe attributes: %', r.rolname;
        END IF;
    END LOOP;
END $$;

COMMENT ON ROLE bridge_school_finance IS 'Bridge School financial operations capability; append-oriented, no DELETE/DDL';
COMMENT ON ROLE bridge_school_finance_principal IS 'Dormant finance principal; enable LOGIN only with external secret and explicit deployment';
GRANT bridge_school_reader TO bridge_school_finance;
GRANT bridge_school_finance TO bridge_school_finance_principal;
REVOKE CREATE ON SCHEMA public FROM bridge_school_finance, bridge_school_finance_principal;
GRANT USAGE ON SCHEMA public TO bridge_school_finance, bridge_school_finance_principal;

CREATE TABLE IF NOT EXISTS club_charge (
    charge_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    service_id uuid REFERENCES club_service(service_id),
    booking_id uuid REFERENCES club_booking(booking_id),
    price_version_id uuid REFERENCES service_price_version(price_version_id),
    amount numeric(14,2) NOT NULL CHECK (amount >= 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    charged_at timestamptz NOT NULL DEFAULT now(),
    due_at timestamptz,
    charge_type text NOT NULL DEFAULT 'service',
    external_reference text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS club_charge_person_idx
    ON club_charge(school_id, person_id, currency_code, charged_at DESC);
CREATE INDEX IF NOT EXISTS club_charge_booking_idx
    ON club_charge(booking_id) WHERE booking_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS club_payment (
    payment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    amount numeric(14,2) NOT NULL CHECK (amount > 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    paid_at timestamptz NOT NULL,
    payment_method text,
    provider_reference text,
    external_reference text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS club_payment_person_idx
    ON club_payment(school_id, person_id, currency_code, paid_at DESC);

CREATE TABLE IF NOT EXISTS payment_allocation (
    payment_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    payment_id uuid NOT NULL REFERENCES club_payment(payment_id),
    charge_id uuid NOT NULL REFERENCES club_charge(charge_id),
    amount numeric(14,2) NOT NULL CHECK (amount > 0),
    allocated_at timestamptz NOT NULL DEFAULT now(),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payment_allocation_payment_idx
    ON payment_allocation(payment_id, allocated_at DESC);
CREATE INDEX IF NOT EXISTS payment_allocation_charge_idx
    ON payment_allocation(charge_id, allocated_at DESC);

CREATE TABLE IF NOT EXISTS financial_adjustment (
    adjustment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    balance_delta numeric(14,2) NOT NULL CHECK (balance_delta <> 0),
    adjustment_type text NOT NULL,
    related_charge_id uuid REFERENCES club_charge(charge_id),
    related_payment_id uuid REFERENCES club_payment(payment_id),
    related_allocation_id uuid REFERENCES payment_allocation(payment_allocation_id),
    reason text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    reversal_of_adjustment_id uuid REFERENCES financial_adjustment(adjustment_id),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (reversal_of_adjustment_id IS NULL OR reversal_of_adjustment_id <> adjustment_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS financial_adjustment_one_reversal_uk
    ON financial_adjustment(reversal_of_adjustment_id)
    WHERE reversal_of_adjustment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS financial_adjustment_person_idx
    ON financial_adjustment(school_id, person_id, currency_code, occurred_at DESC);

CREATE TABLE IF NOT EXISTS accounting_document_reference (
    accounting_document_reference_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    charge_id uuid REFERENCES club_charge(charge_id),
    payment_id uuid REFERENCES club_payment(payment_id),
    adjustment_id uuid REFERENCES financial_adjustment(adjustment_id),
    artifact_version_id uuid REFERENCES artifact_version(artifact_version_id),
    external_locator text,
    document_type text NOT NULL,
    document_number text,
    issued_at timestamptz,
    provider_name text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(charge_id, payment_id, adjustment_id) = 1),
    CHECK (artifact_version_id IS NOT NULL OR external_locator IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS accounting_document_person_idx
    ON accounting_document_reference(school_id, person_id, issued_at DESC NULLS LAST, created_at DESC);

CREATE OR REPLACE VIEW person_financial_balance AS
WITH charge_totals AS (
    SELECT school_id, person_id, currency_code, SUM(amount) AS charge_amount
      FROM club_charge
     GROUP BY school_id, person_id, currency_code
), allocation_totals AS (
    SELECT c.school_id, c.person_id, c.currency_code, SUM(a.amount) AS allocated_amount
      FROM payment_allocation a
      JOIN club_charge c ON c.charge_id=a.charge_id
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
    COALESCE(SUM(a.amount),0)::numeric(14,2) AS allocated_amount,
    (p.amount - COALESCE(SUM(a.amount),0))::numeric(14,2) AS unallocated_amount,
    p.paid_at
FROM club_payment p
LEFT JOIN payment_allocation a ON a.payment_id=p.payment_id
GROUP BY p.payment_id;

CREATE OR REPLACE FUNCTION validate_financial_ledger_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_currency text;
BEGIN
    IF TG_TABLE_NAME='club_charge' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN RAISE EXCEPTION 'charge person missing'; END IF;
        IF NEW.service_id IS NOT NULL THEN
            SELECT school_id INTO v_school FROM club_service WHERE service_id=NEW.service_id;
            IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'charge service school mismatch'; END IF;
        END IF;
        IF NEW.booking_id IS NOT NULL THEN
            SELECT school_id, person_id INTO v_school, v_person FROM club_booking WHERE booking_id=NEW.booking_id;
            IF v_school IS NULL OR v_school <> NEW.school_id OR v_person <> NEW.person_id THEN RAISE EXCEPTION 'charge booking scope mismatch'; END IF;
        END IF;
        IF NEW.price_version_id IS NOT NULL THEN
            SELECT s.school_id, pv.currency_code INTO v_school, v_currency
              FROM service_price_version pv JOIN club_service s ON s.service_id=pv.service_id
             WHERE pv.price_version_id=NEW.price_version_id;
            IF v_school IS NULL OR v_school <> NEW.school_id OR v_currency <> NEW.currency_code THEN
                RAISE EXCEPTION 'charge price version scope/currency mismatch';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='club_payment' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN RAISE EXCEPTION 'payment person missing'; END IF;
    ELSIF TG_TABLE_NAME='payment_allocation' THEN
        SELECT school_id, person_id, currency_code INTO v_school, v_person, v_currency FROM club_payment WHERE payment_id=NEW.payment_id;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'allocation payment school mismatch'; END IF;
        IF NOT EXISTS (
            SELECT 1 FROM club_charge c
             WHERE c.charge_id=NEW.charge_id AND c.school_id=NEW.school_id
               AND c.person_id=v_person AND c.currency_code=v_currency
        ) THEN RAISE EXCEPTION 'allocation charge person/currency/school mismatch'; END IF;
    ELSIF TG_TABLE_NAME='financial_adjustment' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN RAISE EXCEPTION 'adjustment person missing'; END IF;
        IF NEW.related_charge_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_charge c WHERE c.charge_id=NEW.related_charge_id AND c.school_id=NEW.school_id AND c.person_id=NEW.person_id AND c.currency_code=NEW.currency_code
        ) THEN RAISE EXCEPTION 'adjustment charge scope mismatch'; END IF;
        IF NEW.related_payment_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM club_payment p WHERE p.payment_id=NEW.related_payment_id AND p.school_id=NEW.school_id AND p.person_id=NEW.person_id AND p.currency_code=NEW.currency_code
        ) THEN RAISE EXCEPTION 'adjustment payment scope mismatch'; END IF;
    ELSIF TG_TABLE_NAME='accounting_document_reference' THEN
        IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN RAISE EXCEPTION 'accounting document person missing'; END IF;
        IF NEW.charge_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM club_charge c WHERE c.charge_id=NEW.charge_id AND c.school_id=NEW.school_id AND c.person_id=NEW.person_id) THEN RAISE EXCEPTION 'document charge scope mismatch'; END IF;
        IF NEW.payment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM club_payment p WHERE p.payment_id=NEW.payment_id AND p.school_id=NEW.school_id AND p.person_id=NEW.person_id) THEN RAISE EXCEPTION 'document payment scope mismatch'; END IF;
        IF NEW.adjustment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM financial_adjustment a WHERE a.adjustment_id=NEW.adjustment_id AND a.school_id=NEW.school_id AND a.person_id=NEW.person_id) THEN RAISE EXCEPTION 'document adjustment scope mismatch'; END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_charge_scope_guard ON club_charge;
CREATE TRIGGER club_charge_scope_guard BEFORE INSERT ON club_charge FOR EACH ROW EXECUTE FUNCTION validate_financial_ledger_scope();
DROP TRIGGER IF EXISTS club_payment_scope_guard ON club_payment;
CREATE TRIGGER club_payment_scope_guard BEFORE INSERT ON club_payment FOR EACH ROW EXECUTE FUNCTION validate_financial_ledger_scope();
DROP TRIGGER IF EXISTS payment_allocation_scope_guard ON payment_allocation;
CREATE TRIGGER payment_allocation_scope_guard BEFORE INSERT ON payment_allocation FOR EACH ROW EXECUTE FUNCTION validate_financial_ledger_scope();
DROP TRIGGER IF EXISTS financial_adjustment_scope_guard ON financial_adjustment;
CREATE TRIGGER financial_adjustment_scope_guard BEFORE INSERT ON financial_adjustment FOR EACH ROW EXECUTE FUNCTION validate_financial_ledger_scope();
DROP TRIGGER IF EXISTS accounting_document_scope_guard ON accounting_document_reference;
CREATE TRIGGER accounting_document_scope_guard BEFORE INSERT ON accounting_document_reference FOR EACH ROW EXECUTE FUNCTION validate_financial_ledger_scope();

-- Finance may maintain commercial definitions introduced by 0020 and append ledger facts.
GRANT INSERT, UPDATE ON TABLE
    club_service,
    service_price_version,
    club_package,
    club_package_version,
    package_service_rule
TO bridge_school_finance;

GRANT INSERT ON TABLE
    club_charge,
    club_payment,
    payment_allocation,
    financial_adjustment,
    accounting_document_reference
TO bridge_school_finance;

REVOKE UPDATE, DELETE ON TABLE
    club_charge,
    club_payment,
    payment_allocation,
    financial_adjustment,
    accounting_document_reference
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;

REVOKE DELETE ON TABLE
    club_service,
    service_price_version,
    club_package,
    club_package_version,
    package_service_rule
FROM bridge_school_finance;

GRANT SELECT ON person_financial_balance, person_unallocated_payment TO bridge_school_reader, bridge_school_finance;

REVOKE ALL ON FUNCTION validate_financial_ledger_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0021_club_financial_ledger')
ON CONFLICT DO NOTHING;

COMMIT;
