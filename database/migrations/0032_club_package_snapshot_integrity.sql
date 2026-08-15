\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Package acquisition snapshot integrity.
-- Once a package version has been acquired by a person, its service-rule set must not
-- grow underneath that historical acquisition. An acquisition that records an agreed
-- package price must also remain bound to that exact price version.
-- -----------------------------------------------------------------------------

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
    v_price_from timestamptz;
    v_price_to timestamptz;
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
        SELECT package_id, effective_from, effective_to
          INTO v_price_package_id, v_price_from, v_price_to
          FROM package_price_version
         WHERE package_price_version_id=NEW.package_price_version_id;
        IF v_price_package_id IS NULL OR v_price_package_id <> v_package_id THEN
            RAISE EXCEPTION 'package grant price belongs to another package or is missing';
        END IF;
        IF NEW.granted_at < v_price_from
           OR (v_price_to IS NOT NULL AND NEW.granted_at >= v_price_to) THEN
            RAISE EXCEPTION 'package grant price was not effective at acquisition time';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_package_rule_frozen_after_grant()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM person_package_grant g
         WHERE g.package_version_id=NEW.package_version_id
    ) THEN
        RAISE EXCEPTION 'package service rules are frozen after first acquisition';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS package_rule_frozen_after_grant_guard ON package_service_rule;
CREATE TRIGGER package_rule_frozen_after_grant_guard
BEFORE INSERT ON package_service_rule
FOR EACH ROW EXECUTE FUNCTION validate_package_rule_frozen_after_grant();

CREATE OR REPLACE FUNCTION validate_charge_package_grant()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
    v_person uuid;
    v_package_version uuid;
    v_package_id uuid;
    v_grant_price_version uuid;
    v_price_package_id uuid;
    v_price_currency text;
BEGIN
    IF NEW.package_grant_id IS NULL THEN
        IF NEW.package_price_version_id IS NOT NULL THEN
            RAISE EXCEPTION 'package price charge requires package grant';
        END IF;
        RETURN NEW;
    END IF;

    SELECT g.school_id, g.person_id, g.package_version_id, p.package_id,
           g.package_price_version_id
      INTO v_school, v_person, v_package_version, v_package_id, v_grant_price_version
      FROM person_package_grant g
      JOIN club_package_version pv ON pv.package_version_id=g.package_version_id
      JOIN club_package p ON p.package_id=pv.package_id
     WHERE g.package_grant_id=NEW.package_grant_id;

    IF v_school IS NULL OR v_school <> NEW.school_id OR v_person <> NEW.person_id THEN
        RAISE EXCEPTION 'charge package grant scope mismatch';
    END IF;

    IF v_grant_price_version IS NOT NULL
       AND NEW.package_price_version_id IS DISTINCT FROM v_grant_price_version THEN
        RAISE EXCEPTION 'charge package price must match acquisition price version';
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

REVOKE ALL ON FUNCTION validate_person_package_grant_scope()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_package_rule_frozen_after_grant()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;
REVOKE ALL ON FUNCTION validate_charge_package_grant()
FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker,
     bridge_school_health, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0032_club_package_snapshot_integrity')
ON CONFLICT DO NOTHING;

COMMIT;
