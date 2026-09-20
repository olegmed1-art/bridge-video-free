\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_followup IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0358_autopilot_health_repair_progression'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_REQUIRES_0358';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0359_autopilot_historical_repair_reopen'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_ALREADY_APPLIED';
 END IF;
 IF to_regprocedure('autopilot.on_project_work_followup()') IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_PREREQUISITE_MISSING';
 END IF;
 IF to_regclass('autopilot.migration_0359_function_backup') IS NOT NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_BACKUP_ALREADY_PRESENT';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
 ) OR EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN (
       'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
     )
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0359_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL CHECK (length(definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0359_function_backup(function_key,definition)
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid=p.pronamespace
WHERE p.oid='autopilot.on_project_work_followup()'::regprocedure;

DO $patch$
DECLARE
 original text;
 patched text;
BEGIN
 IF (SELECT count(*) FROM autopilot.migration_0359_function_backup)<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_BACKUP_INCOMPLETE';
 END IF;

 SELECT definition INTO STRICT original
 FROM autopilot.migration_0359_function_backup
 WHERE function_key='autopilot.on_project_work_followup()';

 patched:=replace(
   original,
   $old$       SET state = 'ACTIVE',
           last_task_id = NEW.followup_task_id,$old$,
   $new$       SET state = 'ACTIVE',
           -- HISTORICAL_REPAIR_REOPEN_V1: clear terminal shape before reactivation.
           completed_at = NULL,
           last_task_id = NEW.followup_task_id,$new$
 );
 IF patched=original
    OR position('HISTORICAL_REPAIR_REOPEN_V1' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_HISTORICAL_REPAIR_REOPEN_SOURCE_DRIFT';
 END IF;
 EXECUTE patched;
END $patch$;

INSERT INTO public.schema_migration(migration_key)
VALUES('0359_autopilot_historical_repair_reopen');

COMMIT;
