\set ON_ERROR_STOP on
BEGIN;

-- Follow migration 0050 by enforcing non-empty deal stable keys without
-- rewriting any existing non-empty identity.
ALTER TABLE deal
    ADD COLUMN IF NOT EXISTS stable_key text;

WITH ranked AS (
    SELECT
        deal_id,
        COALESCE(
            NULLIF(btrim(deal_fingerprint), ''),
            'deal:' || deal_id::text
        ) AS base_key,
        row_number() OVER (
            PARTITION BY
                school_id,
                COALESCE(
                    NULLIF(btrim(deal_fingerprint), ''),
                    'deal:' || deal_id::text
                )
            ORDER BY deal_id
        ) AS duplicate_no
    FROM deal
    WHERE stable_key IS NULL OR btrim(stable_key) = ''
)
UPDATE deal d
   SET stable_key = CASE
       WHEN ranked.duplicate_no = 1 THEN ranked.base_key
       ELSE ranked.base_key || '#' || ranked.duplicate_no::text
   END
  FROM ranked
 WHERE d.deal_id = ranked.deal_id;

ALTER TABLE deal
    ALTER COLUMN stable_key SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'deal'::regclass
           AND conname = 'deal_school_id_stable_key_key'
    ) THEN
        ALTER TABLE deal
            ADD CONSTRAINT deal_school_id_stable_key_key
            UNIQUE (school_id, stable_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'deal'::regclass
           AND conname = 'deal_stable_key_nonempty'
    ) THEN
        ALTER TABLE deal
            ADD CONSTRAINT deal_stable_key_nonempty
            CHECK (btrim(stable_key) <> '');
    END IF;
END $$;

INSERT INTO schema_migration(migration_key)
VALUES ('0052_deal_stable_key_nonempty_constraint')
ON CONFLICT DO NOTHING;

COMMIT;
