\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
 source text;
BEGIN
 SELECT pg_get_functiondef(
   'autopilot.register_parallel_work_manifest(text,text)'::regprocedure
 ) INTO STRICT source;
 IF position(
   'outbox.status NOT IN (''CALLBACK_ACCEPTED'',''FAILED_CLOSED'')'
   in source
 )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0366_TERMINAL_FILTER_MISSING';
 END IF;
 IF position(
   'outbox.codex_command_pr=candidate_target_pr'
   in source
 )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0366_ACTIVE_CONTROL_GUARD_MISSING';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM autopilot.migration_0366_function_backup
   WHERE function_key='autopilot.register_parallel_work_manifest(text,text)'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0366_ROLLBACK_BACKUP_MISSING';
 END IF;
END;
$test$;

ROLLBACK;
