\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Club Operations core.
-- Person remains the single real-human identity. Student is not duplicated here.
-- Drive files stay in Source/Asset/Artifact; these tables hold operational facts.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS club_membership (
    club_membership_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    membership_type text NOT NULL DEFAULT 'standard',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    status text NOT NULL DEFAULT 'active',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('pending','active','paused','ended','cancelled','invalid'))
);
CREATE UNIQUE INDEX IF NOT EXISTS club_membership_one_open_type_uk
    ON club_membership(school_id, person_id, membership_type)
    WHERE status IN ('pending','active','paused') AND valid_to IS NULL;
CREATE INDEX IF NOT EXISTS club_membership_person_time_idx
    ON club_membership(school_id, person_id, valid_from DESC);

CREATE TABLE IF NOT EXISTS contact_method (
    contact_method_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    channel text NOT NULL,
    normalized_value text NOT NULL,
    display_value text,
    verification_status text NOT NULL DEFAULT 'unverified',
    preferred_flag boolean NOT NULL DEFAULT false,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    source_id uuid REFERENCES source(source_id),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (channel IN ('email','phone','telegram','whatsapp','other')),
    CHECK (verification_status IN ('unverified','pending','verified','rejected','expired')),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','superseded','revoked','invalid'))
);
CREATE UNIQUE INDEX IF NOT EXISTS contact_method_one_open_value_uk
    ON contact_method(school_id, person_id, channel, normalized_value)
    WHERE status='active' AND valid_to IS NULL;
CREATE INDEX IF NOT EXISTS contact_method_person_idx
    ON contact_method(school_id, person_id, preferred_flag DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS contact_preference (
    contact_preference_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    channel text NOT NULL,
    communication_type text NOT NULL,
    permission_state text NOT NULL DEFAULT 'unknown',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    recorded_source text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (channel IN ('email','phone','telegram','whatsapp','web','other')),
    CHECK (permission_state IN ('allowed','denied','unknown')),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS contact_preference_one_open_uk
    ON contact_preference(school_id, person_id, channel, communication_type)
    WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS club_service (
    service_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    name text NOT NULL,
    service_type text NOT NULL,
    description text,
    status text NOT NULL DEFAULT 'active',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, stable_key),
    CHECK (status IN ('draft','active','inactive','archived'))
);

CREATE TABLE IF NOT EXISTS service_price_version (
    price_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    service_id uuid NOT NULL REFERENCES club_service(service_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    amount numeric(14,2) NOT NULL CHECK (amount >= 0),
    currency_code text NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (currency_code ~ '^[A-Z]{3}$'),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (status IN ('candidate','active','superseded','archived')),
    UNIQUE (service_id, version_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS service_price_one_open_active_uk
    ON service_price_version(service_id)
    WHERE status='active' AND effective_to IS NULL;
CREATE INDEX IF NOT EXISTS service_price_effective_idx
    ON service_price_version(service_id, effective_from DESC);

CREATE TABLE IF NOT EXISTS club_package (
    package_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    stable_key text NOT NULL,
    name text NOT NULL,
    package_type text NOT NULL DEFAULT 'bundle',
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (school_id, stable_key),
    CHECK (status IN ('draft','active','inactive','archived'))
);

CREATE TABLE IF NOT EXISTS club_package_version (
    package_version_id uuid PRIMARY KEY DEFAULT uuidv7(),
    package_id uuid NOT NULL REFERENCES club_package(package_id),
    version_no integer NOT NULL CHECK (version_no > 0),
    effective_from timestamptz,
    effective_to timestamptz,
    terms jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'candidate',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
    CHECK (status IN ('candidate','active','superseded','archived')),
    UNIQUE (package_id, version_no)
);

CREATE TABLE IF NOT EXISTS package_service_rule (
    package_version_id uuid NOT NULL REFERENCES club_package_version(package_version_id),
    service_id uuid NOT NULL REFERENCES club_service(service_id),
    quantity numeric(12,3) NOT NULL CHECK (quantity > 0),
    rule_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (package_version_id, service_id)
);

CREATE TABLE IF NOT EXISTS person_entitlement (
    entitlement_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    service_id uuid NOT NULL REFERENCES club_service(service_id),
    package_version_id uuid REFERENCES club_package_version(package_version_id),
    quantity_granted numeric(12,3) NOT NULL CHECK (quantity_granted > 0),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    grant_reason text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status IN ('active','expired','revoked','invalid'))
);
CREATE INDEX IF NOT EXISTS person_entitlement_person_idx
    ON person_entitlement(school_id, person_id, service_id, valid_from DESC);

CREATE TABLE IF NOT EXISTS entitlement_usage (
    entitlement_usage_id uuid PRIMARY KEY DEFAULT uuidv7(),
    entitlement_id uuid NOT NULL REFERENCES person_entitlement(entitlement_id),
    quantity_used numeric(12,3) NOT NULL CHECK (quantity_used > 0),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    reference_type text,
    reference_id uuid,
    reversal_of_usage_id uuid REFERENCES entitlement_usage(entitlement_usage_id),
    status text NOT NULL DEFAULT 'applied',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('applied','reversed','invalid')),
    CHECK (reversal_of_usage_id IS NULL OR reversal_of_usage_id <> entitlement_usage_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS entitlement_usage_one_reversal_uk
    ON entitlement_usage(reversal_of_usage_id)
    WHERE reversal_of_usage_id IS NOT NULL AND status <> 'invalid';
CREATE INDEX IF NOT EXISTS entitlement_usage_entitlement_idx
    ON entitlement_usage(entitlement_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS club_event (
    club_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    event_type text NOT NULL,
    title text NOT NULL,
    service_id uuid REFERENCES club_service(service_id),
    session_id uuid REFERENCES session(session_id),
    tournament_id uuid REFERENCES tournament(tournament_id),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz,
    capacity integer CHECK (capacity IS NULL OR capacity >= 0),
    location_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'planned',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at IS NULL OR ends_at > starts_at),
    CHECK (status IN ('draft','planned','open','closed','completed','cancelled','archived'))
);
CREATE INDEX IF NOT EXISTS club_event_school_time_idx
    ON club_event(school_id, starts_at DESC);

CREATE TABLE IF NOT EXISTS club_booking (
    booking_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    club_event_id uuid NOT NULL REFERENCES club_event(club_event_id),
    person_id uuid NOT NULL REFERENCES person(person_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS club_booking_event_person_uk
    ON club_booking(club_event_id, person_id);
CREATE INDEX IF NOT EXISTS club_booking_person_idx
    ON club_booking(school_id, person_id, created_at DESC);

CREATE TABLE IF NOT EXISTS club_booking_state_event (
    booking_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    booking_id uuid NOT NULL REFERENCES club_booking(booking_id),
    state text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_person_id uuid REFERENCES person(person_id),
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state IN ('requested','confirmed','waitlisted','cancelled','attended','no_show','invalid'))
);
CREATE INDEX IF NOT EXISTS club_booking_state_time_idx
    ON club_booking_state_event(booking_id, occurred_at DESC, created_at DESC);

CREATE OR REPLACE VIEW club_booking_current_state AS
SELECT DISTINCT ON (b.booking_id)
    b.booking_id,
    b.school_id,
    b.club_event_id,
    b.person_id,
    se.state,
    se.occurred_at AS state_occurred_at,
    b.created_at
FROM club_booking b
LEFT JOIN club_booking_state_event se ON se.booking_id=b.booking_id
ORDER BY b.booking_id, se.occurred_at DESC NULLS LAST, se.created_at DESC NULLS LAST;

CREATE OR REPLACE VIEW person_entitlement_balance AS
SELECT
    e.entitlement_id,
    e.school_id,
    e.person_id,
    e.service_id,
    e.quantity_granted,
    COALESCE(SUM(
        CASE
            WHEN u.status='applied' AND u.reversal_of_usage_id IS NULL THEN u.quantity_used
            WHEN u.status='applied' AND u.reversal_of_usage_id IS NOT NULL THEN -u.quantity_used
            ELSE 0
        END
    ), 0)::numeric(12,3) AS quantity_used_net,
    (e.quantity_granted - COALESCE(SUM(
        CASE
            WHEN u.status='applied' AND u.reversal_of_usage_id IS NULL THEN u.quantity_used
            WHEN u.status='applied' AND u.reversal_of_usage_id IS NOT NULL THEN -u.quantity_used
            ELSE 0
        END
    ), 0))::numeric(12,3) AS quantity_remaining,
    e.valid_from,
    e.valid_to,
    e.status
FROM person_entitlement e
LEFT JOIN entitlement_usage u ON u.entitlement_id=e.entitlement_id
GROUP BY e.entitlement_id;

-- -----------------------------------------------------------------------------
-- Cross-school guards. Foreign keys alone cannot prove that referenced objects belong
-- to the same school because Person is intentionally global.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION validate_club_membership_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM school WHERE school_id=NEW.school_id) THEN
        RAISE EXCEPTION 'club membership school missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
        RAISE EXCEPTION 'club membership person missing';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_contact_method_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_school uuid;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM school WHERE school_id=NEW.school_id) THEN
        RAISE EXCEPTION 'contact method school missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
        RAISE EXCEPTION 'contact method person missing';
    END IF;
    IF NEW.source_id IS NOT NULL THEN
        SELECT school_id INTO v_source_school FROM source WHERE source_id=NEW.source_id;
        IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
            RAISE EXCEPTION 'contact source belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_contact_preference_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM school WHERE school_id=NEW.school_id) THEN
        RAISE EXCEPTION 'contact preference school missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
        RAISE EXCEPTION 'contact preference person missing';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_club_service_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_service_school uuid;
    v_package_school uuid;
BEGIN
    IF TG_TABLE_NAME='service_price_version' THEN
        SELECT school_id INTO v_service_school FROM club_service WHERE service_id=NEW.service_id;
        IF v_service_school IS NULL THEN RAISE EXCEPTION 'service price service missing'; END IF;
    ELSIF TG_TABLE_NAME='package_service_rule' THEN
        SELECT p.school_id INTO v_package_school
          FROM club_package_version pv JOIN club_package p ON p.package_id=pv.package_id
         WHERE pv.package_version_id=NEW.package_version_id;
        SELECT school_id INTO v_service_school FROM club_service WHERE service_id=NEW.service_id;
        IF v_package_school IS NULL OR v_service_school IS NULL OR v_package_school <> v_service_school THEN
            RAISE EXCEPTION 'package service rule school mismatch';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_entitlement_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_service_school uuid;
    v_package_school uuid;
BEGIN
    SELECT school_id INTO v_service_school FROM club_service WHERE service_id=NEW.service_id;
    IF v_service_school IS NULL OR v_service_school <> NEW.school_id THEN
        RAISE EXCEPTION 'entitlement service belongs to another school or is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
        RAISE EXCEPTION 'entitlement person missing';
    END IF;
    IF NEW.package_version_id IS NOT NULL THEN
        SELECT p.school_id INTO v_package_school
          FROM club_package_version pv JOIN club_package p ON p.package_id=pv.package_id
         WHERE pv.package_version_id=NEW.package_version_id;
        IF v_package_school IS NULL OR v_package_school <> NEW.school_id THEN
            RAISE EXCEPTION 'entitlement package belongs to another school or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_club_event_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school uuid;
BEGIN
    IF NEW.service_id IS NOT NULL THEN
        SELECT school_id INTO v_school FROM club_service WHERE service_id=NEW.service_id;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'club event service school mismatch'; END IF;
    END IF;
    IF NEW.session_id IS NOT NULL THEN
        SELECT school_id INTO v_school FROM session WHERE session_id=NEW.session_id;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'club event session school mismatch'; END IF;
    END IF;
    IF NEW.tournament_id IS NOT NULL THEN
        SELECT school_id INTO v_school FROM tournament WHERE tournament_id=NEW.tournament_id;
        IF v_school IS NULL OR v_school <> NEW.school_id THEN RAISE EXCEPTION 'club event tournament school mismatch'; END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_club_booking_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_school uuid;
BEGIN
    SELECT school_id INTO v_event_school FROM club_event WHERE club_event_id=NEW.club_event_id;
    IF v_event_school IS NULL OR v_event_school <> NEW.school_id THEN
        RAISE EXCEPTION 'club booking event belongs to another school or is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM person WHERE person_id=NEW.person_id) THEN
        RAISE EXCEPTION 'club booking person missing';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS club_membership_scope_guard ON club_membership;
CREATE TRIGGER club_membership_scope_guard BEFORE INSERT OR UPDATE OF school_id, person_id
ON club_membership FOR EACH ROW EXECUTE FUNCTION validate_club_membership_scope();

DROP TRIGGER IF EXISTS contact_method_scope_guard ON contact_method;
CREATE TRIGGER contact_method_scope_guard BEFORE INSERT OR UPDATE OF school_id, person_id, source_id
ON contact_method FOR EACH ROW EXECUTE FUNCTION validate_contact_method_scope();

DROP TRIGGER IF EXISTS contact_preference_scope_guard ON contact_preference;
CREATE TRIGGER contact_preference_scope_guard BEFORE INSERT OR UPDATE OF school_id, person_id
ON contact_preference FOR EACH ROW EXECUTE FUNCTION validate_contact_preference_scope();

DROP TRIGGER IF EXISTS service_price_scope_guard ON service_price_version;
CREATE TRIGGER service_price_scope_guard BEFORE INSERT OR UPDATE OF service_id
ON service_price_version FOR EACH ROW EXECUTE FUNCTION validate_club_service_scope();

DROP TRIGGER IF EXISTS package_service_rule_scope_guard ON package_service_rule;
CREATE TRIGGER package_service_rule_scope_guard BEFORE INSERT OR UPDATE OF package_version_id, service_id
ON package_service_rule FOR EACH ROW EXECUTE FUNCTION validate_club_service_scope();

DROP TRIGGER IF EXISTS entitlement_scope_guard ON person_entitlement;
CREATE TRIGGER entitlement_scope_guard BEFORE INSERT OR UPDATE OF school_id, person_id, service_id, package_version_id
ON person_entitlement FOR EACH ROW EXECUTE FUNCTION validate_entitlement_scope();

DROP TRIGGER IF EXISTS club_event_scope_guard ON club_event;
CREATE TRIGGER club_event_scope_guard BEFORE INSERT OR UPDATE OF school_id, service_id, session_id, tournament_id
ON club_event FOR EACH ROW EXECUTE FUNCTION validate_club_event_scope();

DROP TRIGGER IF EXISTS club_booking_scope_guard ON club_booking;
CREATE TRIGGER club_booking_scope_guard BEFORE INSERT OR UPDATE OF school_id, club_event_id, person_id
ON club_booking FOR EACH ROW EXECUTE FUNCTION validate_club_booking_scope();

-- Interactive application state. Catalog definitions/prices remain owner/admin configuration.
GRANT INSERT, UPDATE ON TABLE
    club_membership,
    contact_method,
    contact_preference,
    person_entitlement,
    club_event,
    club_booking
TO bridge_school_app;

GRANT INSERT ON TABLE
    entitlement_usage,
    club_booking_state_event
TO bridge_school_app;

REVOKE UPDATE ON TABLE entitlement_usage, club_booking_state_event FROM bridge_school_app, bridge_school_worker;

REVOKE INSERT, UPDATE, DELETE ON TABLE
    club_service,
    service_price_version,
    club_package,
    club_package_version,
    package_service_rule
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

REVOKE DELETE ON TABLE
    club_membership,
    contact_method,
    contact_preference,
    club_service,
    service_price_version,
    club_package,
    club_package_version,
    package_service_rule,
    person_entitlement,
    entitlement_usage,
    club_event,
    club_booking,
    club_booking_state_event
FROM bridge_school_reader, bridge_school_app, bridge_school_worker;

GRANT SELECT ON club_booking_current_state, person_entitlement_balance TO bridge_school_reader;

-- Trigger helpers are not runtime APIs.
REVOKE ALL ON FUNCTION validate_club_membership_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;
REVOKE ALL ON FUNCTION validate_contact_method_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;
REVOKE ALL ON FUNCTION validate_contact_preference_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;
REVOKE ALL ON FUNCTION validate_club_service_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;
REVOKE ALL ON FUNCTION validate_entitlement_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;
REVOKE ALL ON FUNCTION validate_club_event_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;
REVOKE ALL ON FUNCTION validate_club_booking_scope() FROM PUBLIC, bridge_school_reader, bridge_school_app, bridge_school_worker, bridge_school_health;

INSERT INTO schema_migration(migration_key)
VALUES ('0020_club_operations_core')
ON CONFLICT DO NOTHING;

COMMIT;
