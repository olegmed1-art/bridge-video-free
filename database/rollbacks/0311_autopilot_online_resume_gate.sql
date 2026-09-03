\set ON_ERROR_STOP on
BEGIN;

DROP FUNCTION IF EXISTS autopilot.register_online_stale_resume_gate(text,text);
DROP TABLE IF EXISTS autopilot.online_resume_gate;
DELETE FROM public.schema_migration
 WHERE migration_key = '0311_autopilot_online_resume_gate';

COMMIT;
