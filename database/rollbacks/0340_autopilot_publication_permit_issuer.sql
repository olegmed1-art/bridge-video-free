\set ON_ERROR_STOP on
BEGIN;
-- Drain/serialize any in-flight owner issuer before deciding that the ledger
-- is empty. The issuer takes the same transaction-scoped fence first.
SELECT pg_advisory_xact_lock(hashtextextended(
    'autopilot.codex_publication_permit.issuer_rollback_fence.v1',0));
LOCK TABLE autopilot.codex_publication_permit IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.codex_publication_permit) THEN
        RAISE EXCEPTION 'PUBLICATION_ISSUER_ROLLBACK_REQUIRES_RETAINED_EVIDENCE_PLAN';
    END IF;
END $$;
DROP FUNCTION autopilot.issue_codex_publication_permit(jsonb,integer);
DELETE FROM public.schema_migration
WHERE migration_key='0340_autopilot_publication_permit_issuer';
COMMIT;
