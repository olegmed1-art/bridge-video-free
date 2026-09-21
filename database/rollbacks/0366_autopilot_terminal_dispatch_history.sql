\set ON_ERROR_STOP on
BEGIN;

DO $rollback$
DECLARE
 source text;
BEGIN
 SELECT definition INTO STRICT source
 FROM autopilot.migration_0366_function_backup
 WHERE function_key='autopilot.register_parallel_work_manifest(text,text)';
 EXECUTE source;
END $rollback$;

DROP TABLE autopilot.migration_0366_function_backup;

DELETE FROM public.schema_migration
WHERE migration_key='0366_autopilot_terminal_dispatch_history';

COMMIT;
