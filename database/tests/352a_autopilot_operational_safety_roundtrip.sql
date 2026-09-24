\set ON_ERROR_STOP on
BEGIN;
DO $rt$
DECLARE d text;
BEGIN
 SELECT function_definition INTO d FROM autopilot.migration_0352_function_backup WHERE function_key='claim_project_work_probe';
 IF d IS NULL OR position('PROJECT_DONE_WITH_PAUSED_BACKLOG' in d)>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_PLANNER_BACKUP_PROVENANCE_INVALID';
 END IF;
 EXECUTE d;
 IF position('PROJECT_DONE_WITH_PAUSED_BACKLOG' in pg_get_functiondef('autopilot.claim_project_work_probe(text,integer)'::regprocedure))>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_PLANNER_EXACT_RESTORE_FAILED';
 END IF;
 SELECT function_definition INTO d FROM autopilot.migration_0352_function_backup WHERE function_key='role_blocker_requires_owner';
 IF d IS NULL OR position('AUTOPILOT_UNCLASSIFIED_FAILURE' in d)>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_BLOCKER_BACKUP_PROVENANCE_INVALID';
 END IF;
 EXECUTE d;
 SELECT function_definition INTO d FROM autopilot.migration_0352_function_backup WHERE function_key='enforce_role_dispatch_mailbox_capacity';
 IF d IS NULL OR position('mailbox_rotation_signal' in d)>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_CAPACITY_BACKUP_PROVENANCE_INVALID';
 END IF;
 EXECUTE d;
END $rt$;
ROLLBACK;
DO $post$
BEGIN
 IF position('PROJECT_DONE_WITH_PAUSED_BACKLOG' in pg_get_functiondef('autopilot.claim_project_work_probe(text,integer)'::regprocedure))=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_TRANSACTION_RESTORE_FAILED';
 END IF;
 IF autopilot.role_blocker_requires_owner('AUTOPILOT_UNCLASSIFIED_FAILURE') IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'AUTOPILOT_0352_BLOCKER_TRANSACTION_RESTORE_FAILED';
 END IF;
END $post$;
