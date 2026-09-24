\set ON_ERROR_STOP on
BEGIN;
DROP FUNCTION autopilot_reconcile.apply_progress(uuid,timestamptz,text,timestamptz);
DROP FUNCTION autopilot_reconcile.apply_paused(uuid,timestamptz,text,text,text);
DROP FUNCTION autopilot_reconcile.progress_candidates(integer);
DROP FUNCTION autopilot_reconcile.paused_candidates(integer);
-- No CASCADE: unexpected dependencies must block rollback.
DROP SCHEMA autopilot_reconcile;
DELETE FROM public.schema_migration WHERE migration_key='0369_autopilot_reconcile_gateway';
COMMIT;
