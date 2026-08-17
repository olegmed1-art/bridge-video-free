\set ON_ERROR_STOP on
BEGIN;

-- Hardening for identity-import staging introduced in 0046.
ALTER TABLE identity_import_item RENAME COLUMN raw_payload_hash TO source_payload_hash;
ALTER TABLE identity_import_item ALTER COLUMN source_payload_hash DROP NOT NULL;
ALTER TABLE identity_import_item ADD COLUMN raw_payload_sha256 text;
UPDATE identity_import_item
   SET raw_payload_sha256=encode(digest(raw_payload::text,'sha256'),'hex');
ALTER TABLE identity_import_item ALTER COLUMN raw_payload_sha256 SET NOT NULL;

CREATE OR REPLACE FUNCTION compute_identity_import_item_hash()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.raw_payload_sha256 := encode(digest(NEW.raw_payload::text,'sha256'),'hex');
    RETURN NEW;
END;
$$;
CREATE TRIGGER a_identity_import_item_hash_guard
BEFORE INSERT OR UPDATE OF raw_payload,raw_payload_sha256 ON identity_import_item
FOR EACH ROW EXECUTE FUNCTION compute_identity_import_item_hash();

-- There is no apply operation in staging, therefore staging cannot claim applied state.
ALTER TABLE identity_import_batch_state_event
    ADD CONSTRAINT identity_import_batch_state_no_apply_ck
    CHECK (state <> 'applied') NOT VALID;
ALTER TABLE identity_import_batch_state_event VALIDATE CONSTRAINT identity_import_batch_state_no_apply_ck;
ALTER TABLE identity_import_item_state_event
    ADD CONSTRAINT identity_import_item_state_no_apply_ck
    CHECK (state <> 'applied') NOT VALID;
ALTER TABLE identity_import_item_state_event VALIDATE CONSTRAINT identity_import_item_state_no_apply_ck;

ALTER TABLE identity_import_action
    ADD COLUMN action_sequence bigint GENERATED ALWAYS AS IDENTITY
        (SEQUENCE NAME identity_import_action_sequence_seq);
CREATE UNIQUE INDEX identity_import_action_sequence_uk
    ON identity_import_action(identity_import_item_id,action_sequence);
CREATE INDEX identity_import_action_sequence_idx
    ON identity_import_action(identity_import_item_id,action_sequence DESC);

CREATE OR REPLACE FUNCTION validate_identity_import_state_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_created_at timestamptz;
BEGIN
    IF TG_TABLE_NAME='identity_import_batch_state_event' THEN
        SELECT created_at INTO v_created_at FROM identity_import_batch
         WHERE identity_import_batch_id=NEW.identity_import_batch_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'identity import batch state target missing'; END IF;
    ELSE
        SELECT created_at INTO v_created_at FROM identity_import_item
         WHERE identity_import_item_id=NEW.identity_import_item_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'identity import item state target missing'; END IF;
    END IF;
    IF NEW.occurred_at < v_created_at THEN
        RAISE EXCEPTION 'identity import workflow state cannot precede staged record creation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION seed_identity_import_batch_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO identity_import_batch_state_event(identity_import_batch_id,state,occurred_at,metadata)
    VALUES (NEW.identity_import_batch_id,'staged',NEW.created_at,jsonb_build_object('source','batch_insert'));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION seed_identity_import_item_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO identity_import_item_state_event(identity_import_item_id,state,occurred_at,metadata)
    VALUES (NEW.identity_import_item_id,'staged',NEW.created_at,jsonb_build_object('source','item_insert'));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_identity_import_action()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_item_source_identity uuid;
    v_item_created_at timestamptz;
    v_resolution_source_identity uuid;
    v_resolution_target uuid;
    v_resolution_type text;
    v_resolution_status text;
    v_sup_item uuid;
BEGIN
    SELECT source_identity_id,created_at INTO v_item_source_identity,v_item_created_at
      FROM identity_import_item WHERE identity_import_item_id=NEW.identity_import_item_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'identity import action item missing'; END IF;
    IF NEW.decided_at < v_item_created_at THEN
        RAISE EXCEPTION 'identity import action cannot precede staged item creation';
    END IF;
    IF NEW.action_type='link_existing_person' THEN
        IF v_item_source_identity IS NULL THEN RAISE EXCEPTION 'link-existing import action requires item source identity'; END IF;
        SELECT source_identity_id,target_person_id,decision_type,status
          INTO v_resolution_source_identity,v_resolution_target,v_resolution_type,v_resolution_status
          FROM entity_resolution_decision WHERE resolution_id=NEW.entity_resolution_decision_id;
        IF v_resolution_source_identity IS NULL
           OR v_resolution_source_identity <> v_item_source_identity
           OR v_resolution_target IS DISTINCT FROM NEW.target_person_id
           OR v_resolution_type <> 'link'
           OR v_resolution_status <> 'active' THEN
            RAISE EXCEPTION 'link-existing import action does not match active canonical entity resolution';
        END IF;
    END IF;
    IF NEW.supersedes_action_id IS NOT NULL THEN
        SELECT identity_import_item_id INTO v_sup_item FROM identity_import_action
         WHERE identity_import_action_id=NEW.supersedes_action_id;
        IF v_sup_item IS NULL OR v_sup_item <> NEW.identity_import_item_id THEN
            RAISE EXCEPTION 'identity import action supersedes another item or missing action';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Current workflow state follows immutable append order, not caller-supplied timestamps.
CREATE OR REPLACE VIEW identity_import_batch_current_state AS
SELECT DISTINCT ON (b.identity_import_batch_id)
    b.identity_import_batch_id,b.school_id,b.source_id,b.external_batch_key,b.import_label,
    se.state,se.occurred_at AS state_occurred_at,se.actor_person_id,se.reason,b.created_at
FROM identity_import_batch b
LEFT JOIN identity_import_batch_state_event se USING (identity_import_batch_id)
ORDER BY b.identity_import_batch_id,se.state_sequence DESC NULLS LAST;

CREATE OR REPLACE VIEW identity_import_item_current_state AS
SELECT DISTINCT ON (i.identity_import_item_id)
    i.identity_import_item_id,i.identity_import_batch_id,i.source_record_key,
    i.raw_payload_sha256 AS raw_payload_hash,i.source_identity_id,se.state,
    se.occurred_at AS state_occurred_at,se.actor_person_id,se.reason,i.created_at
FROM identity_import_item i
LEFT JOIN identity_import_item_state_event se USING (identity_import_item_id)
ORDER BY i.identity_import_item_id,se.state_sequence DESC NULLS LAST;

CREATE OR REPLACE VIEW identity_import_current_action AS
SELECT DISTINCT ON (a.identity_import_item_id)
    a.identity_import_item_id,a.identity_import_action_id,a.action_type,a.target_person_id,
    a.entity_resolution_decision_id,a.supersedes_action_id,a.actor_person_id,a.reason,
    a.decided_at,a.created_at
FROM identity_import_action a
ORDER BY a.identity_import_item_id,a.action_sequence DESC;

REVOKE ALL ON SEQUENCE identity_import_action_sequence_seq
FROM bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;
REVOKE ALL ON FUNCTION compute_identity_import_item_hash(),validate_identity_import_state_scope(),
    seed_identity_import_batch_state(),seed_identity_import_item_state(),validate_identity_import_action()
FROM PUBLIC,bridge_school_reader,bridge_school_app,bridge_school_worker,bridge_school_health,
     bridge_school_finance,bridge_school_member,bridge_school_member_principal,bridge_school_auth_gateway;

INSERT INTO schema_migration(migration_key)
VALUES ('0047_identity_import_evidence_hardening')
ON CONFLICT DO NOTHING;
COMMIT;
