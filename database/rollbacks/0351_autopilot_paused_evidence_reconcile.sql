\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM autopilot.paused_work_reconcile_receipt) THEN RAISE EXCEPTION 'AUTOPILOT_0351_ROLLBACK_HAS_EVIDENCE'; END IF;
END $$;
DROP FUNCTION autopilot.reconcile_paused_project_work(uuid,text,text,text,text);
DROP TABLE autopilot.paused_work_reconcile_receipt;
DELETE FROM public.schema_migration WHERE migration_key='0351_autopilot_paused_evidence_reconcile';
COMMIT;
