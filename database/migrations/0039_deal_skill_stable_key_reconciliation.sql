\set ON_ERROR_STOP on

BEGIN;

-- Reconcile audited production drift after 0019. Existing keys are preserved.
-- Databases without the drift receive deterministic keys from immutable UUIDs.
ALTER TABLE deal ADD COLUMN IF NOT EXISTS stable_key text;
ALTER TABLE skill ADD COLUMN IF NOT EXISTS stable_key text;

UPDATE deal
SET stable_key = 'DEAL-' || upper(substr(replace(deal_id::text, '-', ''), 1, 16))
WHERE stable_key IS NULL OR btrim(stable_key) = '';

UPDATE skill
SET stable_key = 'SKILL-' || upper(substr(replace(skill_id::text, '-', ''), 1, 16))
WHERE stable_key IS NULL OR btrim(stable_key) = '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'deal'::regclass
          AND conname = 'deal_school_id_stable_key_key'
    ) THEN
        ALTER TABLE deal
            ADD CONSTRAINT deal_school_id_stable_key_key
            UNIQUE (school_id, stable_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'skill'::regclass
          AND conname = 'skill_school_id_stable_key_key'
    ) THEN
        ALTER TABLE skill
            ADD CONSTRAINT skill_school_id_stable_key_key
            UNIQUE (school_id, stable_key);
    END IF;
END
$$;

ALTER TABLE deal ALTER COLUMN stable_key SET NOT NULL;
ALTER TABLE skill ALTER COLUMN stable_key SET NOT NULL;

COMMENT ON COLUMN deal.stable_key IS
    'Stable school-scoped deal identity; reconciled into migration history by 0039.';
COMMENT ON COLUMN skill.stable_key IS
    'Stable school-scoped skill identity; reconciled into migration history by 0039.';

INSERT INTO schema_migration(migration_key)
VALUES ('0039_deal_skill_stable_key_reconciliation')
ON CONFLICT DO NOTHING;

COMMIT;
