\set ON_ERROR_STOP on
BEGIN;

-- -----------------------------------------------------------------------------
-- Controlled Person/Student/ClubMember/AuthIdentity import staging.
--
-- This layer deliberately does NOT create Person/Student/ClubMembership/AuthIdentity
-- rows and does NOT perform fuzzy/automatic identity matching. It stores immutable raw
-- import evidence plus explicit reconciliation intent. Existing source_identity and
-- entity_resolution_decision remain the canonical identity-resolution mechanism for
-- linking an imported source identity to an existing Person.
--
-- Raw import payloads may contain PII, so no runtime/member/reader capability receives
-- direct access in this migration. A future import service can receive a separately
-- reviewed least-privilege capability after the actual import source/format is known.
-- -----------------------------------------------------------------------------

CREATE TABLE identity_import_batch (
    identity_import_batch_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES school(school_id),
    source_id uuid NOT NULL REFERENCES source(source_id),
    external_batch_key text NOT NULL,
    import_label text,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(external_batch_key) <> ''),
    UNIQUE (school_id, source_id, external_batch_key)
);

CREATE TABLE identity_import_batch_state_event (
    identity_import_batch_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    identity_import_batch_id uuid NOT NULL REFERENCES identity_import_batch(identity_import_batch_id),
    state text NOT NULL,
    actor_person_id uuid REFERENCES person(person_id),
    reason text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    state_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME identity_import_batch_state_sequence_seq),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state IN ('staged','validated','ready','rejected','invalid'))
);
CREATE UNIQUE INDEX identity_import_batch_state_sequence_uk
    ON identity_import_batch_state_event(identity_import_batch_id,state_sequence);
CREATE INDEX identity_import_batch_state_time_idx
    ON identity_import_batch_state_event(identity_import_batch_id,state_sequence DESC);

CREATE TABLE identity_import_item (
    identity_import_item_id uuid PRIMARY KEY DEFAULT uuidv7(),
    identity_import_batch_id uuid NOT NULL REFERENCES identity_import_batch(identity_import_batch_id),
    source_record_key text NOT NULL,
    raw_payload jsonb NOT NULL,
    raw_payload_sha256 text GENERATED ALWAYS AS (
        encode(digest(convert_to(raw_payload::text,'UTF8'),'sha256'),'hex')
    ) STORED,
    normalized_candidate jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_identity_id uuid REFERENCES source_identity(source_identity_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(source_record_key) <> ''),
    UNIQUE (identity_import_batch_id,source_record_key)
);
CREATE INDEX identity_import_item_source_identity_idx
    ON identity_import_item(source_identity_id)
    WHERE source_identity_id IS NOT NULL;

CREATE TABLE identity_import_item_state_event (
    identity_import_item_state_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    identity_import_item_id uuid NOT NULL REFERENCES identity_import_item(identity_import_item_id),
    state text NOT NULL,
    actor_person_id uuid REFERENCES person(person_id),
    reason text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    state_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME identity_import_item_state_sequence_seq),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (state IN ('staged','validated','needs_review','ready','rejected','invalid'))
);
CREATE UNIQUE INDEX identity_import_item_state_sequence_uk
    ON identity_import_item_state_event(identity_import_item_id,state_sequence);
CREATE INDEX identity_import_item_state_time_idx
    ON identity_import_item_state_event(identity_import_item_id,state_sequence DESC);

CREATE TABLE identity_import_action (
    identity_import_action_id uuid PRIMARY KEY DEFAULT uuidv7(),
    identity_import_item_id uuid NOT NULL REFERENCES identity_import_item(identity_import_item_id),
    action_type text NOT NULL,
    target_person_id uuid REFERENCES person(person_id),
    entity_resolution_decision_id uuid REFERENCES entity_resolution_decision(resolution_id),
    supersedes_action_id uuid REFERENCES identity_import_action(identity_import_action_id),
    actor_person_id uuid REFERENCES person(person_id),
    reason text,
    decided_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    action_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME identity_import_action_sequence_seq),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (action_type IN ('link_existing_person','create_new_person','reject','defer')),
    CHECK (supersedes_action_id IS NULL OR supersedes_action_id <> identity_import_action_id),
    CHECK (
        (action_type='link_existing_person' AND target_person_id IS NOT NULL AND entity_resolution_decision_id IS NOT NULL)
        OR (action_type IN ('create_new_person','reject','defer') AND target_person_id IS NULL AND entity_resolution_decision_id IS NULL)
    )
);
CREATE UNIQUE INDEX identity_import_action_sequence_uk
    ON identity_import_action(identity_import_item_id,action_sequence);
CREATE INDEX identity_import_action_item_time_idx
    ON identity_import_action(identity_import_item_id,action_sequence DESC);

CREATE OR REPLACE FUNCTION validate_identity_import_batch_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_source_school uuid;
BEGIN
    SELECT school_id INTO v_source_school FROM source WHERE source_id=NEW.source_id;
    IF v_source_school IS NULL OR v_source_school <> NEW.school_id THEN
        RAISE EXCEPTION 'identity import batch source belongs to another school or is missing';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_batch_scope_guard
BEFORE INSERT ON identity_import_batch
FOR EACH ROW EXECUTE FUNCTION validate_identity_import_batch_scope();

CREATE OR REPLACE FUNCTION validate_identity_import_item_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_batch_source uuid;
    v_identity_source uuid;
BEGIN
    SELECT source_id INTO v_batch_source
      FROM identity_import_batch
     WHERE identity_import_batch_id=NEW.identity_import_batch_id;
    IF v_batch_source IS NULL THEN
        RAISE EXCEPTION 'identity import item batch missing';
    END IF;

    IF NEW.source_identity_id IS NOT NULL THEN
        SELECT source_id INTO v_identity_source
          FROM source_identity
         WHERE source_identity_id=NEW.source_identity_id;
        IF v_identity_source IS NULL OR v_identity_source <> v_batch_source THEN
            RAISE EXCEPTION 'identity import item source identity belongs to another source or is missing';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_item_scope_guard
BEFORE INSERT ON identity_import_item
FOR EACH ROW EXECUTE FUNCTION validate_identity_import_item_scope();

CREATE OR REPLACE FUNCTION validate_identity_import_state_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_created_at timestamptz;
BEGIN
    IF TG_TABLE_NAME='identity_import_batch_state_event' THEN
        SELECT created_at INTO v_created_at
          FROM identity_import_batch
         WHERE identity_import_batch_id=NEW.identity_import_batch_id
         FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'identity import batch state target missing';
        END IF;
    ELSE
        SELECT created_at INTO v_created_at
          FROM identity_import_item
         WHERE identity_import_item_id=NEW.identity_import_item_id
         FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'identity import item state target missing';
        END IF;
    END IF;

    IF NEW.occurred_at < v_created_at THEN
        RAISE EXCEPTION 'identity import workflow state cannot precede staged record creation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_batch_state_scope_guard
BEFORE INSERT ON identity_import_batch_state_event
FOR EACH ROW EXECUTE FUNCTION validate_identity_import_state_scope();
CREATE TRIGGER identity_import_item_state_scope_guard
BEFORE INSERT ON identity_import_item_state_event
FOR EACH ROW EXECUTE FUNCTION validate_identity_import_state_scope();

CREATE OR REPLACE FUNCTION seed_identity_import_batch_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO identity_import_batch_state_event(identity_import_batch_id,state,occurred_at,metadata)
    VALUES (NEW.identity_import_batch_id,'staged',NEW.created_at,jsonb_build_object('source','batch_insert'));
    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_batch_initial_state
AFTER INSERT ON identity_import_batch
FOR EACH ROW EXECUTE FUNCTION seed_identity_import_batch_state();

CREATE OR REPLACE FUNCTION seed_identity_import_item_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,occurred_at,metadata)
    VALUES (NEW.identity_import_item_id,'staged',NEW.created_at,jsonb_build_object('source','item_insert'));
    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_item_initial_state
AFTER INSERT ON identity_import_item
FOR EACH ROW EXECUTE FUNCTION seed_identity_import_item_state();

CREATE OR REPLACE FUNCTION validate_identity_import_action()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_item_source_identity uuid;
    v_item_created_at timestamptz;
    v_resolution_source_identity uuid;
    v_resolution_target uuid;
    v_resolution_type text;
    v_resolution_status text;
    v_sup_item uuid;
BEGIN
    SELECT source_identity_id,created_at
      INTO v_item_source_identity,v_item_created_at
      FROM identity_import_item
     WHERE identity_import_item_id=NEW.identity_import_item_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'identity import action item missing';
    END IF;

    IF NEW.decided_at < v_item_created_at THEN
        RAISE EXCEPTION 'identity import action cannot precede staged item creation';
    END IF;

    IF NEW.action_type='link_existing_person' THEN
        IF v_item_source_identity IS NULL THEN
            RAISE EXCEPTION 'link-existing import action requires item source identity';
        END IF;
        SELECT source_identity_id,target_person_id,decision_type,status
          INTO v_resolution_source_identity,v_resolution_target,v_resolution_type,v_resolution_status
          FROM entity_resolution_decision
         WHERE resolution_id=NEW.entity_resolution_decision_id;
        IF v_resolution_source_identity IS NULL
           OR v_resolution_source_identity <> v_item_source_identity
           OR v_resolution_target IS DISTINCT FROM NEW.target_person_id
           OR v_resolution_type <> 'link'
           OR v_resolution_status <> 'active' THEN
            RAISE EXCEPTION 'link-existing import action does not match active canonical entity resolution';
        END IF;
    END IF;

    IF NEW.supersedes_action_id IS NOT NULL THEN
        SELECT identity_import_item_id INTO v_sup_item
          FROM identity_import_action
         WHERE identity_import_action_id=NEW.supersedes_action_id;
        IF v_sup_item IS NULL OR v_sup_item <> NEW.identity_import_item_id THEN
            RAISE EXCEPTION 'identity import action supersedes another item or missing action';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;
CREATE TRIGGER identity_import_action_guard
BEFORE INSERT ON identity_import_action
FOR EACH ROW EXECUTE FUNCTION validate_identity_import_action();

CREATE OR REPLACE FUNCTION reject_identity_import_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'identity import staging history is append-only';
END;
$$;

CREATE TRIGGER identity_import_batch_immutable
BEFORE UPDATE OR DELETE ON identity_import_batch
FOR EACH ROW EXECUTE FUNCTION reject_identity_import_mutation();
CREATE TRIGGER identity_import_batch_state_immutable
BEFORE UPDATE OR DELETE ON identity_import_batch_state_event
FOR EACH ROW EXECUTE FUNCTION reject_identity_import_mutation();
CREATE TRIGGER identity_import_item_immutable
BEFORE UPDATE OR DELETE ON identity_import_item
FOR EACH ROW EXECUTE FUNCTION reject_identity_import_mutation();
CREATE TRIGGER identity_import_item_state_immutable
BEFORE UPDATE OR DELETE ON identity_import_item_state_event
FOR EACH ROW EXECUTE FUNCTION reject_identity_import_mutation();
CREATE TRIGGER identity_import_action_immutable
BEFORE UPDATE OR DELETE ON identity_import_action
FOR EACH ROW EXECUTE FUNCTION reject_identity_import_mutation();

CREATE VIEW identity_import_batch_current_state AS
SELECT DISTINCT ON (b.identity_import_batch_id)
    b.identity_import_batch_id,
    b.school_id,
    b.source_id,
    b.external_batch_key,
    b.import_label,
    se.state,
    se.occurred_at AS state_occurred_at,
    se.actor_person_id,
    se.reason,
    b.created_at
FROM identity_import_batch b
LEFT JOIN identity_import_batch_state_event se
  ON se.identity_import_batch_id=b.identity_import_batch_id
ORDER BY b.identity_import_batch_id,se.state_sequence DESC NULLS LAST;

CREATE VIEW identity_import_item_current_state AS
SELECT DISTINCT ON (i.identity_import_item_id)
    i.identity_import_item_id,
    i.identity_import_batch_id,
    i.source_record_key,
    i.raw_payload_sha256,
    i.source_identity_id,
    se.state,
    se.occurred_at AS state_occurred_at,
    se.actor_person_id,
    se.reason,
    i.created_at
FROM identity_import_item i
LEFT JOIN identity_import_item_state_event se
  ON se.identity_import_item_id=i.identity_import_item_id
ORDER BY i.identity_import_item_id,se.state_sequence DESC NULLS LAST;

CREATE VIEW identity_import_current_action AS
SELECT DISTINCT ON (a.identity_import_item_id)
    a.identity_import_item_id,
    a.identity_import_action_id,
    a.action_type,
    a.target_person_id,
    a.entity_resolution_decision_id,
    a.supersedes_action_id,
    a.actor_person_id,
    a.reason,
    a.decided_at,
    a.action_sequence,
    a.created_at
FROM identity_import_action a
ORDER BY a.identity_import_item_id,a.action_sequence DESC;

CREATE VIEW identity_import_batch_summary AS
SELECT
    b.identity_import_batch_id,
    b.school_id,
    b.source_id,
    b.external_batch_key,
    bs.state AS batch_state,
    count(i.identity_import_item_id)::bigint AS item_count,
    count(i.identity_import_item_id) FILTER (WHERE isv.state='needs_review')::bigint AS needs_review_count,
    count(i.identity_import_item_id) FILTER (WHERE ia.action_type='link_existing_person')::bigint AS link_existing_count,
    count(i.identity_import_item_id) FILTER (WHERE ia.action_type='create_new_person')::bigint AS create_new_count,
    count(i.identity_import_item_id) FILTER (WHERE ia.action_type='reject')::bigint AS reject_count,
    count(i.identity_import_item_id) FILTER (WHERE ia.action_type IS NULL)::bigint AS unresolved_count,
    b.created_at
FROM identity_import_batch b
LEFT JOIN identity_import_batch_current_state bs
  ON bs.identity_import_batch_id=b.identity_import_batch_id
LEFT JOIN identity_import_item i
  ON i.identity_import_batch_id=b.identity_import_batch_id
LEFT JOIN identity_import_item_current_state isv
  ON isv.identity_import_item_id=i.identity_import_item_id
LEFT JOIN identity_import_current_action ia
  ON ia.identity_import_item_id=i.identity_import_item_id
GROUP BY b.identity_import_batch_id,b.school_id,b.source_id,b.external_batch_key,bs.state,b.created_at;

-- Raw import data and reconciliation intent are owner-only at this stage. Earlier
-- default privileges would otherwise give the broad internal reader access to new
-- tables/views, which is inappropriate for unverified PII staging.
REVOKE ALL ON TABLE
    identity_import_batch,
    identity_import_batch_state_event,
    identity_import_item,
    identity_import_item_state_event,
    identity_import_action,
    identity_import_batch_current_state,
    identity_import_item_current_state,
    identity_import_current_action,
    identity_import_batch_summary
FROM bridge_school_reader,bridge_school_app,bridge_school_worker,
     bridge_school_health,bridge_school_finance,bridge_school_member,
     bridge_school_member_principal,bridge_school_auth_gateway;

REVOKE ALL ON SEQUENCE
    identity_import_batch_state_sequence_seq,
    identity_import_item_state_sequence_seq,
    identity_import_action_sequence_seq
FROM bridge_school_reader,bridge_school_app,bridge_school_worker,
     bridge_school_health,bridge_school_finance,bridge_school_member,
     bridge_school_member_principal,bridge_school_auth_gateway;

REVOKE ALL ON FUNCTION validate_identity_import_batch_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_identity_import_item_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_identity_import_state_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION seed_identity_import_batch_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION seed_identity_import_item_state() FROM PUBLIC;
REVOKE ALL ON FUNCTION validate_identity_import_action() FROM PUBLIC;
REVOKE ALL ON FUNCTION reject_identity_import_mutation() FROM PUBLIC;

REVOKE ALL ON FUNCTION validate_identity_import_batch_scope(),
    validate_identity_import_item_scope(),
    validate_identity_import_state_scope(),
    seed_identity_import_batch_state(),
    seed_identity_import_item_state(),
    validate_identity_import_action(),
    reject_identity_import_mutation()
FROM bridge_school_reader,bridge_school_app,bridge_school_worker,
     bridge_school_health,bridge_school_finance,bridge_school_member,
     bridge_school_member_principal,bridge_school_auth_gateway;

INSERT INTO schema_migration(migration_key)
VALUES ('0045_identity_import_staging')
ON CONFLICT DO NOTHING;

COMMIT;
