\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Commercial catalog immutability.
-- Version rows are historical facts: runtime finance may append a new version and
-- close/supersede the previous one, but must not rewrite price amounts, currencies,
-- effective starts, package terms, or package service quantities in place.
-- Stable catalog identity is also not a runtime-editable field.
-- -----------------------------------------------------------------------------

REVOKE UPDATE ON TABLE
    club_service,
    service_price_version,
    club_package,
    club_package_version,
    package_service_rule,
    package_price_version
FROM bridge_school_finance;

-- Catalog labels/lifecycle may be maintained without rewriting stable identity.
GRANT UPDATE (name, description, status, metadata)
    ON club_service TO bridge_school_finance;
GRANT UPDATE (name, status)
    ON club_package TO bridge_school_finance;

-- Version content is immutable. Closing/superseding a version is the only runtime edit.
GRANT UPDATE (effective_to, status)
    ON service_price_version TO bridge_school_finance;
GRANT UPDATE (effective_to, status)
    ON club_package_version TO bridge_school_finance;
GRANT UPDATE (effective_to, status)
    ON package_price_version TO bridge_school_finance;

-- Package rules belong to a package version. A changed quantity/service set requires a
-- new package version rather than mutation of the old rule row.
REVOKE UPDATE, DELETE ON TABLE package_service_rule
FROM bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_finance;

INSERT INTO schema_migration(migration_key)
VALUES ('0030_club_commercial_immutability')
ON CONFLICT DO NOTHING;

COMMIT;
