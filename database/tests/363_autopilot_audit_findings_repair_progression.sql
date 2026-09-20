\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
 item_id uuid;
 origin_id uuid;
 repair_id uuid;
 replayed_repair_id uuid;
 probe record;
 materialized record;
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0363_autopilot_audit_findings_repair_progression'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_MIGRATION_MISSING';
 END IF;
 IF strpos(
      pg_get_functiondef(
        'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
      ),
      'AUDIT_FINDINGS_REPAIR_SUCCESSOR_V1'
    )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_REPAIR_ADMISSION_NOT_INSTALLED';
 END IF;
 IF strpos(
      pg_get_functiondef('autopilot.on_role_task_terminal()'::regprocedure),
      'AUDIT_FINDINGS_REPAIR_TERMINAL_V1'
    )=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_TERMINAL_ROUTER_NOT_INSTALLED';
 END IF;

 SELECT work_item_id INTO item_id
 FROM autopilot.register_project_work_item(
  'sql-0363-audit-findings-successor',
  'VIDEO_QUEUE',1698,0,NULL,'database-test','SQL_TEST'
 );
 SELECT * INTO probe
 FROM autopilot.claim_project_work_probe('sql-0363-probe',60);
 IF NOT FOUND OR probe.work_item_id<>item_id THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_WORK_NOT_CLAIMED';
 END IF;
 SELECT * INTO materialized
 FROM autopilot.materialize_project_work_probe(
   item_id,'sql-0363-probe',probe.lease_epoch,true,repeat('f',40)
 );
 origin_id:=materialized.task_id;
 IF origin_id IS NULL OR materialized.resulting_state<>'ACTIVE' THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_ORIGIN_NOT_MATERIALIZED';
 END IF;

 UPDATE autopilot.task
 SET status='DONE',
     terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
     safe_summary_json=jsonb_build_object(
       'status','SUCCEEDED','result_code','AUDIT_FINDINGS_REPORTED',
       'target_head_sha',repeat('f',40),
       'summary','Task completed. See execution details above.'
     ),
     completed_at=clock_timestamp()
 WHERE task_id=origin_id;

 SELECT followup_task_id INTO repair_id
 FROM autopilot.role_dispatch_followup
 WHERE parent_task_id=origin_id AND followup_kind='REPAIR';
 IF repair_id IS NULL
    OR (SELECT status FROM autopilot.task WHERE task_id=repair_id)<>'READY'
    OR (SELECT state FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>'ACTIVE'
    OR (SELECT completed_at FROM autopilot.project_work_item
        WHERE work_item_id=item_id) IS NOT NULL
    OR (SELECT last_task_id FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>repair_id
    OR (SELECT count(*) FROM autopilot.project_work_task
        WHERE work_item_id=item_id
          AND task_id=repair_id
          AND run_kind='REPAIR')<>1
    OR EXISTS (
      SELECT 1 FROM autopilot.role_dispatch_followup
      WHERE parent_task_id=origin_id AND followup_kind='CONTINUATION'
    ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_REPAIR_SUCCESSOR_INVALID';
 END IF;

 SELECT autopilot.materialize_role_repair(
   origin_id,'AUDIT_FINDINGS_REPORTED',
   'Task completed. See execution details above.'
 ) INTO replayed_repair_id;
 IF replayed_repair_id IS DISTINCT FROM repair_id
    OR (SELECT count(*) FROM autopilot.role_dispatch_followup
     WHERE parent_task_id=origin_id AND followup_kind='REPAIR')<>1
    OR (SELECT count(*) FROM autopilot.project_work_task
        WHERE work_item_id=item_id AND run_kind='REPAIR')<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_REPAIR_NOT_IDEMPOTENT';
 END IF;

 IF autopilot.materialize_role_repair(
      origin_id,'UNCLASSIFIED_AUDIT_OUTCOME','Must stay fail closed.'
    ) IS NOT NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0363_UNKNOWN_RESULT_NOT_FAIL_CLOSED';
 END IF;
END $test$;

SELECT 3 AS cases,0 AS failures;
ROLLBACK;
