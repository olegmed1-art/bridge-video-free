\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('autopilot.codex_command_send_intent.v1',0));
-- Pause/quiesce the sender first. NEVER restore an unguarded POST path.
DROP FUNCTION autopilot.claim_codex_command_send(uuid,uuid,jsonb,text);
DROP FUNCTION autopilot.codex_command_send_binding(uuid);
-- Deliberately retain codex_command_send_intent, including every consumed
-- attempt. Reapplying 0370 must not regrant old send rights.
DELETE FROM public.schema_migration
WHERE migration_key='0370_autopilot_codex_send_intent';
COMMIT;
