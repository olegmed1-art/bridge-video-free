\set ON_ERROR_STOP on
BEGIN;

DO $rt$
DECLARE
 before_def text;
 backup_def text;
BEGIN
 SELECT function_definition INTO backup_def
 FROM autopilot.migration_0353_function_backup
 WHERE function_key='reconcile_paused_project_work';

 IF backup_def IS NULL
    OR position('PAUSED_EVIDENCE_REMEDIATION_READY' in backup_def)>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_BACKUP_PROVENANCE_INVALID';
 END IF;

 EXECUTE backup_def;
 before_def:=pg_get_functiondef(
   'autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'::regprocedure
 );

 IF before_def IS DISTINCT FROM backup_def
    OR position('PAUSED_EVIDENCE_REMEDIATION_READY' in before_def)>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_EXACT_RESTORE_FAILED';
 END IF;
END $rt$;

ROLLBACK;

DO $post$
BEGIN
 IF position(
   'PAUSED_EVIDENCE_REMEDIATION_READY' in
   pg_get_functiondef(
     'autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'::regprocedure
   )
 )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_TRANSACTION_RESTORE_FAILED';
 END IF;
END $post$;
