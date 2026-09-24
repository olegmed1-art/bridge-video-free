\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
 item_id uuid;
 origin_id uuid;
 repair_id uuid;
 replayed_repair_id uuid;
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0364_autopilot_repair_head_rebind'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_MIGRATION_MISSING';
 END IF;
 IF to_regprocedure(
      'autopilot.materialize_role_repair_rebound(uuid,text,text,text)'
    ) IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_FUNCTION_MISSING';
 END IF;
 IF has_function_privilege(
      'autopilot_runtime',
      'autopilot.materialize_role_repair_rebound(uuid,text,text,text)',
      'EXECUTE'
    ) OR has_function_privilege(
      'autopilot_callback',
      'autopilot.materialize_role_repair_rebound(uuid,text,text,text)',
      'EXECUTE'
    ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_OWNER_ONLY_ACL_INVALID';
 END IF;

 SELECT work_item_id INTO item_id
 FROM autopilot.register_project_work_item(
   'sql-0364-repair-head-rebind',
   'VIDEO_QUEUE',1698,0,NULL,'database-test','SQL_TEST'
 );
 INSERT INTO autopilot.task(
   task_key,goal_type,goal_json,status,current_step_key,
   acceptance_contract_json,allowed_capabilities_json,priority,
   model_turn_cap,cost_cap_microusd,terminal_reason_code,
   safe_summary_json,created_by,source,completed_at
 ) VALUES (
   'sql-0364-lost-audit-origin','CHATGPT_ROLE_DISPATCH_V1',
   jsonb_build_object(
     'repository','olegmed1-art/bridge-video-free',
     'mailbox_pr',1703,
     'role','VIDEO_QUEUE',
     'target_pr',1698,
     'expected_head_sha',repeat('a',40),
     'dispatch_epoch',1,
     'successor_task_key',NULL,
     'successor_role',NULL,
     'successor_target_pr',NULL,
     'successor_expected_head_sha',NULL
   ),
   'DONE','github.chatgpt.role.dispatch',
   jsonb_build_object(
     'retained_evidence_required',true,
     'production_mutation',false,
     'exact_head_required',true,
     'mailbox_pr',1703,
     'cost_actual_microusd',0
   ),
   '["github.chatgpt.role.dispatch"]'::jsonb,0,0,0,
   'CODEX_CLOUD_RESULT_RETAINED',
   jsonb_build_object(
     'status','SUCCEEDED','result_code','AUDIT_FINDINGS_REPORTED',
     'target_head_sha',repeat('a',40),
     'summary','Task completed. See execution details above.'
   ),
   'database-test','SQL_TEST',clock_timestamp()
 ) RETURNING task_id INTO origin_id;
 INSERT INTO autopilot.project_work_task(work_item_id,task_id,run_kind)
 VALUES(item_id,origin_id,'AUDIT');
 UPDATE autopilot.project_work_item
 SET state='DONE',
     generation=1,
     last_observed_head_sha=repeat('a',40),
     last_task_id=origin_id,
     result_code='AUDIT_FINDINGS_REPORTED',
     result_summary='Task completed. See execution details above.',
     completed_at=clock_timestamp(),
     updated_at=clock_timestamp()
 WHERE work_item_id=item_id;

 IF autopilot.materialize_role_repair_rebound(
      origin_id,'AUDIT_FINDINGS_REPORTED',
      'Different retained summary.',repeat('b',40)
    ) IS NOT NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_SUMMARY_MISMATCH_ALLOWED';
 END IF;

 SELECT autopilot.materialize_role_repair_rebound(
   origin_id,'AUDIT_FINDINGS_REPORTED',
   'Task completed. See execution details above.',repeat('b',40)
 ) INTO repair_id;
 IF repair_id IS NULL
    OR (SELECT status FROM autopilot.task WHERE task_id=repair_id)<>'READY'
    OR (SELECT goal_json->>'expected_head_sha' FROM autopilot.task
        WHERE task_id=repair_id)<>repeat('b',40)
    OR (SELECT goal_json->>'expected_head_sha' FROM autopilot.task
        WHERE task_id=origin_id)<>repeat('a',40)
    OR (SELECT source FROM autopilot.task
        WHERE task_id=repair_id)<>'AUTOPILOT_REPAIR_REBOUND'
    OR (SELECT state FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>'ACTIVE'
    OR (SELECT last_observed_head_sha FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>repeat('b',40)
    OR (SELECT last_task_id FROM autopilot.project_work_item
        WHERE work_item_id=item_id)<>repair_id
    OR (SELECT completed_at FROM autopilot.project_work_item
        WHERE work_item_id=item_id) IS NOT NULL
    OR (SELECT count(*) FROM autopilot.project_work_task
        WHERE work_item_id=item_id AND task_id=repair_id
          AND run_kind='REPAIR')<>1
    OR (SELECT count(*) FROM autopilot.role_dispatch_followup
        WHERE parent_task_id=origin_id AND followup_kind='REPAIR')<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_REBOUND_REPAIR_INVALID';
 END IF;

 SELECT autopilot.materialize_role_repair_rebound(
   origin_id,'AUDIT_FINDINGS_REPORTED',
   'Task completed. See execution details above.',repeat('b',40)
 ) INTO replayed_repair_id;
 IF replayed_repair_id IS DISTINCT FROM repair_id THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_REBOUND_NOT_IDEMPOTENT';
 END IF;

 IF autopilot.materialize_role_repair_rebound(
      origin_id,'AUDIT_FINDINGS_REPORTED',
      'Task completed. See execution details above.',repeat('a',40)
    ) IS NOT NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_ORIGINAL_HEAD_REBOUND_ALLOWED';
 END IF;
 IF autopilot.materialize_role_repair_rebound(
      origin_id,'UNCLASSIFIED_AUDIT_OUTCOME','Must stay fail closed.',repeat('c',40)
    ) IS NOT NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_UNKNOWN_RESULT_NOT_FAIL_CLOSED';
 END IF;
 IF autopilot.materialize_role_repair(
      origin_id,'AUDIT_FINDINGS_REPORTED',
      'Task completed. See execution details above.'
    ) IS DISTINCT FROM repair_id THEN
   RAISE EXCEPTION 'AUTOPILOT_0364_STANDARD_REPLAY_DIVERGED';
 END IF;
END $test$;

SELECT 6 AS cases,0 AS failures;
ROLLBACK;
