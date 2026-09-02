\set ON_ERROR_STOP on
BEGIN;

-- event_position is a publication/replay cursor, not an insert-time sequence.
-- It remains NULL while the event is committed but not yet published by the outbox dispatcher.
ALTER TABLE domain_event ALTER COLUMN event_position DROP DEFAULT;
ALTER TABLE domain_event ALTER COLUMN event_position DROP NOT NULL;

CREATE TABLE IF NOT EXISTS event_position_allocator (
    partition_key text PRIMARY KEY,
    last_position bigint NOT NULL DEFAULT 0 CHECK (last_position >= 0)
);

CREATE OR REPLACE FUNCTION allocate_event_position(p_partition_key text, p_event_id uuid)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_position bigint;
BEGIN
    IF p_partition_key IS NULL OR p_partition_key = '' THEN
        RAISE EXCEPTION 'partition key is required';
    END IF;

    INSERT INTO event_position_allocator(partition_key, last_position)
    VALUES (p_partition_key, 1)
    ON CONFLICT (partition_key)
    DO UPDATE SET last_position = event_position_allocator.last_position + 1
    RETURNING last_position INTO v_position;

    UPDATE domain_event
       SET event_position = v_position
     WHERE event_id = p_event_id
       AND partition_key = p_partition_key
       AND event_position IS NULL;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'event % not found, wrong partition, or already positioned', p_event_id;
    END IF;

    RETURN v_position;
END;
$$;

-- Global uniqueness of event_position from the first migration is too strong once
-- each partition has its own monotonic cursor. Replace it with partition-local uniqueness.
DO $$
DECLARE
    cname text;
BEGIN
    SELECT c.conname INTO cname
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
     WHERE t.relname = 'domain_event'
       AND c.contype = 'u'
       AND pg_get_constraintdef(c.oid) = 'UNIQUE (event_position)'
     LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE domain_event DROP CONSTRAINT %I', cname);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS domain_event_partition_position_uk
    ON domain_event(partition_key, event_position)
    WHERE event_position IS NOT NULL;

INSERT INTO schema_migration(migration_key) VALUES ('0003_event_publication_order') ON CONFLICT DO NOTHING;
COMMIT;
