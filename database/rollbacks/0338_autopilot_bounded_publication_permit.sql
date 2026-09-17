\set ON_ERROR_STOP on
BEGIN;
-- Stop the publisher/feature flag first. Never erase retained permits/evidence.
LOCK TABLE autopilot.codex_publication_permit IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.codex_publication_permit) THEN
        RAISE EXCEPTION 'PUBLICATION_ROLLBACK_REQUIRES_RETAINED_EVIDENCE_PLAN';
    END IF;
END $$;
DROP FUNCTION autopilot.authorize_codex_publication(jsonb,bigint,text);
DROP TABLE autopilot.codex_publication_permit;
DELETE FROM public.schema_migration
WHERE migration_key='0338_autopilot_bounded_publication_permit';
COMMIT;
