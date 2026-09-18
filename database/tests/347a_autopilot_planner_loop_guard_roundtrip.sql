\set ON_ERROR_STOP on
BEGIN;

DO $roundtrip$
DECLARE
  before_def text;
  backup_def text;
  installed_def text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.schema_migration
    WHERE migration_key='0347_autopilot_planner_loop_guard'
  ) THEN RAISE EXCEPTION 'AUTOPILOT_0347_ROUNDTRIP_MIGRATION_MISSING'; END IF;

  SELECT function_definition INTO backup_def
    FROM autopilot.migration_0347_function_backup
   WHERE function_key='materialize_project_work_probe';
  installed_def := pg_get_functiondef(
    'autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure
  );
  IF backup_def IS NULL OR strpos(installed_def,'PLANNER_LOOP_GUARD_V2')=0
     OR strpos(backup_def,'PLANNER_LOOP_GUARD_V2')>0 THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_ROUNDTRIP_BACKUP_PROVENANCE_INVALID';
  END IF;

  -- Exact-function rollback rehearsal inside this transaction.
  EXECUTE backup_def;
  before_def := pg_get_functiondef(
    'autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure
  );
  IF before_def IS DISTINCT FROM backup_def
     OR strpos(before_def,'PLANNER_LOOP_GUARD_V2')>0 THEN
    RAISE EXCEPTION 'AUTOPILOT_0347_ROUNDTRIP_EXACT_RESTORE_FAILED';
  END IF;

  -- The transaction rollback below restores the installed 0347 definition,
  -- proving the rollback statement itself without mutating the CI database.
END $roundtrip$;

ROLLBACK;

DO $post$
BEGIN
 IF strpos(pg_get_functiondef(
   'autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure
 ),'PLANNER_LOOP_GUARD_V2')=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0347_ROUNDTRIP_TRANSACTION_RESTORE_FAILED';
 END IF;
END $post$;
