\set ON_ERROR_STOP on
BEGIN;

DO $rollback$
DECLARE
 original text;
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0365_autopilot_parallel_work_intake'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_INTAKE_NOT_APPLIED';
 END IF;
 SELECT definition INTO STRICT original
 FROM autopilot.migration_0365_function_backup
 WHERE function_key='autopilot.claim_project_work_probe(text,integer)';
 IF EXISTS (
   SELECT 1 FROM autopilot.project_work_item
   WHERE created_by='AUTOPILOT_PARALLEL_INTAKE'
     AND source LIKE 'REVIEWED_WORKER_RELEASE:%'
     AND (state<>'READY' OR last_task_id IS NOT NULL)
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_PARALLEL_INTAKE_ROLLBACK_REQUIRES_RECONCILIATION';
 END IF;
 EXECUTE original;
END $rollback$;

UPDATE autopilot.project_planner_state AS planner
SET last_work_item_id=NULL,
    last_decision_code='IDLE_NO_ELIGIBLE_TASK',
    last_decision_at=now()
WHERE planner.last_work_item_id IN (
  SELECT work_item_id
  FROM autopilot.project_work_item
  WHERE created_by='AUTOPILOT_PARALLEL_INTAKE'
    AND source LIKE 'REVIEWED_WORKER_RELEASE:%'
    AND state='READY'
    AND last_task_id IS NULL
);

DELETE FROM autopilot.project_work_item
WHERE created_by='AUTOPILOT_PARALLEL_INTAKE'
  AND source LIKE 'REVIEWED_WORKER_RELEASE:%'
  AND state='READY'
  AND last_task_id IS NULL;

REVOKE ALL ON FUNCTION autopilot.register_parallel_work_manifest(text,text)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
DROP FUNCTION autopilot.register_parallel_work_manifest(text,text);
DROP TABLE autopilot.project_work_manifest_receipt;
DROP TABLE autopilot.migration_0365_function_backup;

DELETE FROM public.schema_migration
WHERE migration_key='0365_autopilot_parallel_work_intake';

COMMIT;
