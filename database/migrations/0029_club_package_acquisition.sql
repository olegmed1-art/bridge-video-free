\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Person-specific package acquisition.
-- Catalog definitions (club_package / club_package_version) are not purchases.
-- This migration introduces a durable instance for each assigned/acquired package so
-- two purchases of the same package remain distinct and their entitlements can be
-- traced back to the correct acquisition.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS package_price_version (
    package_price_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    package_id uuid NOT NULL REFERENCES club_package(package_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    amount numeric(14,2) NOT NULL CHECK (amount >= 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (status IN ('candidate','active','superseded','archived')),
    UNIQUE (package_id, version_no)
);
CREATE INDEX IF NOT EXISTS package_price_effective_idx
    ON package_price_version(package_id, effective_from DESC);
ALTER TABLE package_price_version
    ADD CONSTRAINT package_price_active_period_excl
    EXCLUDE USING gist (
        package_id WITH =,
        tstzrange(effective_from, effective_to, '[)') WITH &&
    ) WHERE (status='active');

CREATE TABLE IF NOT EXISTS person_package_grant (
    package_grant_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    package_version_id uuid NOT NULL REFERENCES club_package_version(package_version_id),
    package_price_version_id uuid REFERENCES package_price_version(package_price_version_id),
    granted_at timestamptz NOT NULL DEFAULT now(),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    grant_reason text,
    external_reference text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','expired','revoked','invalid'))
);
CREATE INDEX IF NOT EXISTS person_package_grant_person_idx
    ON person_package_grant(school_id, person_id, granted_at DESC);
CREATE INDEX IF NOT EXISTS person_package_grant_package_idx
    ON person_package_grant(package_version_id, granted_at DESC);

ALTER TABLE person_entitlement
    ADD COLUMN IF NOT EXISTS package_grant_id uuid REFERENCES person_package_grant(package_grant_id);

ALTER TABLE person_entitlement
    ADD CONSTRAINT person_entitlement_package_source_ck
    CHECK (
        (package_version_id IS NULL AND package_grant_id IS NULL)
        OR (package_version_id IS NOT NULL AND package_grant_id IS NOT NULL)
    ) NOT VALID;
ALTER TABLE person_entitlement VALIDATE CONSTRAINT person_entitlement_package_source_ck;

CREATE UNIQUE INDEX IF NOT EXISTS person_entitlement_one_service_per_package_grant_uk
    ON person_entitlement(package_grant_id, service_id)
    WHERE package_grant_id IS NOT NULL AND status <> 'invalid';

ALTER TABLE club_charge
    ADD COLUMN IF NOT EXISTS package_grant_id uuid REFERENCES person_package_grant(package_grant_id),
    ADD COLUMN IF NOT EXISTS package_price_version_id uuid REFERENCES package_price_version(package_price_version_id);

ALTER TABLE club_charge
    ADD CONSTRAINT club_charge_one_price_source_ck
    CHECK (num_nonnulls(price_version_id, package_price_version_id) <= 1) NOT VALID;
ALTER TABLE club_charge VALIDATE CONSTRAINT club_charge_one_price_source_ck;

ALTER TABLE club_charge
    ADD CONSTRAINT club_charge_package_price_requires_grant_ck
    CHECK (package_price_version_id IS NULL OR package_grant_id IS NOT NULL) NOT VALID;
ALTER TABLE club_charge VALIDATE CONSTRAINT club_charge_package_price_requires_grant_ck;

CREATE OR REPLACE FUNCTION validate_person_package_grant_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_package_id uuid;
    v_package_school uuid;
    v_version_from timestamptz;
    v_version_to timestamptz;
    v_price_package_id uuid;
BEGIN
    SELECT p.package_id, p.school_id, pv.effective_from, pv.effective_to
      INTO v_package_id, v_package_school, v_version_from, v_version_to
      FROM club_package_version pv
      JOIN club_package p ON p.package_id=pv.package_id
     WHERE pv.package_version_id=NEW.package_version_id;

    IF v_package_id IS NULL OR v_package_school <> NEW.school_id THEN
        RAISE EXCEPTION 'package grant package version belongs to another school or is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
        RAISE EXCEPTION 'package grant person missing';
    END IF;
    IF v_version_from IS NOT NULL AND NEW.granted_at < v_version_from THEN
        RAISE EXCEPTION 'package grant predates package version';
    END IF;
    IF v_version_to IS NOT NULL AND NEW.granted_at >= v_version_to THEN
        RAISE EXCEPTION 'package grant is after package version validity';
    END IF;

    IF NEW.package_price_version_id IS NOT NULL THEN
        SELECT package_id INTO v_price_package_id
          FROM package_price_version
         WHERE package_price_version_id=NEW.package_price_version_id;
        IF v_price_package_id IS NULL OR v_price_package_id <> v_package_id THEN
            RAISE EXCEPTION 'package grant price belongs to another package or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS person_package_grant_scope_guard ON person_package_grant;
CREATE TRIGGER person_package_grant_scope_guard
BEFORE INSERT OR UPDATE OF school_id, person_id, package_version_id, package_price_version_id, granted_at
ON person_package_grant
FOR EACH ROW EXECUTE FUNCTION validate_person_package_grant_scope();

CREATE OR REPLACE FUNCTION validate_entitlement_package_grant()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_package_version uuid;
    v_grant_from timestamptz;
    v_grant_to timestamptz;
    v_grant_status text;
BEGIN
    IF NEW.package_grant_id IS NULL THEN
        IF NEW.package_version_id IS NOT NULL THEN
            RAISE EXCEPTION 'package entitlement requires a person package grant';
        END IF;
        RETURN NEW;
    END IF;

    SELECT school_id, person_id, package_version_id, valid_from, valid_to, status
      INTO v_school, v_person, v_package_version, v_grant_from, v_grant_to, v_grant_status
      FROM person_package_grant
     WHERE package_grant_id=NEW.package_grant_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'package entitlement grant missing';
    END IF;
    IF v_school <> NEW.school_id OR v_person <> NEW.person_id OR v_package_version <> NEW.package_version_id THEN
        RAISE EXCEPTION 'package entitlement grant scope mismatch';
    END IF;
    IF v_grant_status <> 'active' THEN
        RAISE EXCEPTION 'new package entitlement requires an active package grant';
    END IF;
    IF NEW.valid_from < v_grant_from THEN
        RAISE EXCEPTION 'entitlement starts before package grant';
    END IF;
    IF v_grant_to IS NOT NULL AND (NEW.valid_to IS NULL OR NEW.valid_to > v_grant_to) THEN
        RAISE EXCEPTION 'entitlement extends beyond package grant';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS entitlement_package_grant_guard ON person_entitlement;
CREATE TRIGGER entitlement_package_grant_guard
BEFORE INSERT OR UPDATE OF school_id, person_id, package_version_id, package_grant_id, valid_from, valid_to
ON person_entitlement
FOR EACH ROW EXECUTE FUNCTION validate_entitlement_package_grant();

CREATE OR REPLACE FUNCTION validate_charge_package_grant()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_package_version uuid;
    v_package_id uuid;
    v_price_package_id uuid;
    v_price_currency text;
BEGIN
    IF NEW.package_grant_id IS NULL THEN
        IF NEW.package_price_version_id IS NOT NULL THEN
            RAISE EXCEPTION 'package price charge requires package grant';
        END IF;
        RETURN NEW;
    END IF;

    SELECT g.school_id, g.person_id, g.package_version_id, p.package_id
      INTO v_school, v_person, v_package_version, v_package_id
      FROM person_package_grant g
      JOIN club_package_version pv ON pv.package_version_id=g.package_version_id
      JOIN club_package p ON p.package_id=pv.package_id
     WHERE g.package_grant_id=NEW.package_grant_id;

    IF v_school IS NULL OR v_school <> NEW.school_id OR v_person <> NEW.person_id THEN
        RAISE EXCEPTION 'charge package grant scope mismatch';
    END IF;

    IF NEW.package_price_version_id IS NOT NULL THEN
        SELECT package_id, currency_code
          INTO v_price_package_id, v_price_currency
          FROM package_price_version
         WHERE package_price_version_id=NEW.package_price_version_id;
        IF v_price_package_id IS NULL OR v_price_package_id <> v_package_id THEN
            RAISE EXCEPTION 'charge package price belongs to another package';
        END IF;
        IF v_price_currency <> NEW.currency_code THEN
            RAISE EXCEPTION 'charge package price currency mismatch';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_charge_package_grant_guard ON club_charge;
CREATE TRIGGER club_charge_package_grant_guard
BEFORE INSERT ON club_charge
FOR EACH ROW EXECUTE FUNCTION validate_charge_package_grant();

-- Finance/admin capability defines package prices and grants package instances.
GRANT INSERT, UPDATE ON TABLE package_price_version TO bridge_school_finance;
GRANT INSERT ON TABLE person_package_grant TO bridge_school_finance;
GRANT UPDATE (valid_to, status) ON person_package_grant TO bridge_school_finance;
GRANT SELECT ON package_price_version, person_package_grant TO bridge_school_finance;
REVOKE DELETE ON TABLE package_price_version, person_package_grant
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;
REVOKE INSERT, UPDATE ON TABLE person_package_grant FROM bridge_school_app, bridge_school_worker;
REVOKE INSERT, UPDATE, DELETE ON TABLE package_price_version FROM bridge_school_app, bridge_school_worker;

REVOKE ALL ON FUNCTION validate_person_package_grant_scope()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_entitlement_package_grant()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_charge_package_grant()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0029_club_package_acquisition')
ON CONFLICT DO NOTHING;

COMMIT;
