\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
 item_id uuid;
 origin_id uuid;
 repair_id uuid;
 second_repair_id uuid;
 probe record;
 materialized record;
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0359_autopilot_historical_repair_reopen'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_MIGRATION_MISSING';
 END IF;
 IF strpos(
      pg_get_functiondef('autopilot.on_project_work_followup()'::regprocedure),
      'HISTORICAL_REPAIR_REOPEN_V1'
    )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_REOPEN_PATCH_NOT_INSTALLED';
 END IF;

 SELECT work_item_id INTO item_id
 FROM autopilot.register_project_work_item(
  'sql-0359-historical-repair-reopen',
  'RECOGNIZER',1698,0,NULL,'database-test','SQL_TEST'
 );
 SELECT * INTO probe
 FROM autopilot.claim_project_work_probe('sql-0359-probe',60);
 IF NOT FOUND OR probe.work_item_id<>item_id THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_WORK_NOT_CLAIMED';
 END IF;
 SELECT * INTO materialized
 FROM autopilot.materialize_project_work_probe(
   item_id,'sql-0359-probe',probe.lease_epoch,true,repeat('f',40)
 );
 origin_id:=materialized.task_id;
 IF origin_id IS NULL OR materialized.resulting_state<>'ACTIVE' THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_ORIGIN_NOT_MATERIALIZED';
 END IF;

 UPDATE autopilot.project_work_item
 SET state='DONE',
     result_code='REPAIR_REQUIRED',
     result_summary='Historical retained defect still requires repair.',
     completed_at=clock_timestamp(),
     updated_at=clock_timestamp()
 WHERE work_item_id=item_id;

 SELECT autopilot.materialize_role_repair(
   origin_id,'REPAIR_REQUIRED',
   'Historical retained defect still requires repair.'
 ) INTO repair_id;

 IF repair_id IS NULL
    OR (SELECT state FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>'ACTIVE'
    OR (SELECT completed_at FROM autopilot.project_work_item
        WHERE work_item_id=item_id) IS NOT NULL
    OR (SELECT result_code FROM autopilot.project_work_item
        WHERE work_item_id=item_id) IS NOT NULL
    OR (SELECT result_summary FROM autopilot.project_work_item
        WHERE work_item_id=item_id) IS NOT NULL
    OR (SELECT last_task_id FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>repair_id
    OR (SELECT status FROM autopilot.task
        WHERE task_id=repair_id)<>'READY'
    OR (SELECT count(*) FROM autopilot.project_work_task
        WHERE work_item_id=item_id
          AND task_id=repair_id
          AND run_kind='REPAIR')<>1
    OR (SELECT count(*) FROM autopilot.role_dispatch_followup
        WHERE parent_task_id=origin_id
          AND followup_kind='REPAIR'
          AND followup_task_id=repair_id)<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_HISTORICAL_REPAIR_REOPEN_INVALID';
 END IF;

 SELECT autopilot.materialize_role_repair(
   origin_id,'REPAIR_REQUIRED',
   'Historical retained defect still requires repair.'
 ) INTO second_repair_id;
 IF second_repair_id<>repair_id
    OR (SELECT count(*) FROM autopilot.role_dispatch_followup
        WHERE parent_task_id=origin_id AND followup_kind='REPAIR')<>1
    OR (SELECT count(*) FROM autopilot.project_work_task
        WHERE work_item_id=item_id AND run_kind='REPAIR')<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0359_HISTORICAL_REPAIR_NOT_IDEMPOTENT';
 END IF;
END $test$;

ROLLBACK;
