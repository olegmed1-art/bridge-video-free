\set ON_ERROR_STOP on
BEGIN;

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Two confirmed active agreement versions cannot govern the same pair/scope/time.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname='agreement_activation_no_confirmed_overlap'
    ) THEN
        ALTER TABLE agreement_activation
        ADD CONSTRAINT agreement_activation_no_confirmed_overlap
        EXCLUDE USING gist (
            agreement_set_id WITH =,
            scope_key WITH =,
            tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
        ) WHERE (status='active' AND authority_state='confirmed');
    END IF;
END $$;

-- Causal derivation/dependency edges form a DAG.
CREATE OR REPLACE FUNCTION prevent_dependency_cycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cycle_found boolean;
BEGIN
    IF NEW.parent_entity_id = NEW.child_entity_id THEN
        RAISE EXCEPTION 'dependency self-cycle is forbidden';
    END IF;

    WITH RECURSIVE descendants(id) AS (
        SELECT child_entity_id
          FROM dependency_edge
         WHERE parent_entity_id = NEW.child_entity_id
           AND dependency_type IN ('derived_from','depends_on')
        UNION
        SELECT d.child_entity_id
          FROM dependency_edge d
          JOIN descendants x ON d.parent_entity_id = x.id
         WHERE d.dependency_type IN ('derived_from','depends_on')
    )
    SELECT EXISTS(SELECT 1 FROM descendants WHERE id = NEW.parent_entity_id)
      INTO cycle_found;

    IF cycle_found THEN
        RAISE EXCEPTION 'causal dependency cycle is forbidden';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS dependency_cycle_guard ON dependency_edge;
CREATE TRIGGER dependency_cycle_guard
BEFORE INSERT OR UPDATE OF parent_entity_id, child_entity_id, dependency_type
ON dependency_edge
FOR EACH ROW
WHEN (NEW.dependency_type IN ('derived_from','depends_on'))
EXECUTE FUNCTION prevent_dependency_cycle();

-- Atomic publication helper: only committed ChangeSets can enter replay stream.
CREATE OR REPLACE FUNCTION publish_outbox_event(p_outbox_id uuid)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_id uuid;
    v_partition text;
    v_changeset uuid;
    v_changeset_status text;
    v_existing_position bigint;
    v_position bigint;
BEGIN
    SELECT o.event_id, e.partition_key, o.changeset_id, e.event_position
      INTO v_event_id, v_partition, v_changeset, v_existing_position
      FROM outbox_message o
      JOIN domain_event e ON e.event_id=o.event_id
     WHERE o.outbox_id=p_outbox_id
     FOR UPDATE OF o;

    IF NOT FOUND THEN RAISE EXCEPTION 'outbox message not found'; END IF;

    SELECT status INTO v_changeset_status FROM changeset WHERE changeset_id=v_changeset;
    IF v_changeset_status <> 'committed' THEN
        RAISE EXCEPTION 'cannot publish event from non-committed changeset %', v_changeset;
    END IF;

    IF v_existing_position IS NOT NULL THEN
        UPDATE outbox_message
           SET status='published', published_at=COALESCE(published_at, now())
         WHERE outbox_id=p_outbox_id;
        RETURN v_existing_position;
    END IF;

    v_position := allocate_event_position(v_partition, v_event_id);
    UPDATE outbox_message
       SET status='published', published_at=now(), attempt_count=attempt_count+1, last_error=NULL
     WHERE outbox_id=p_outbox_id;
    RETURN v_position;
END;
$$;

INSERT INTO schema_migration(migration_key) VALUES ('0004_integrity_guards') ON CONFLICT DO NOTHING;
COMMIT;
