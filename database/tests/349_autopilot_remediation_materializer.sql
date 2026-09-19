\set ON_ERROR_STOP on
BEGIN;
DO $t$
DECLARE origin_id uuid; child uuid; c record;
BEGIN
 SELECT task_id INTO origin_id FROM autopilot.create_chatgpt_role_dispatch_task(
  'sql-0349-origin',jsonb_build_object(
   'repository','olegmed1-art/bridge-video-free','mailbox_pr',1637,'role','AUTOPILOT',
   'target_pr',1682,'expected_head_sha',repeat('a',40),'dispatch_epoch',1,
   'successor_task_key',NULL,'successor_role',NULL,'successor_target_pr',NULL,'successor_expected_head_sha',NULL
  ),0,'database-test','SQL_TEST');

 child:=autopilot.materialize_blocker_remediation(origin_id,'SERVER_EVIDENCE_INCOMPLETE','Evidence missing.');
 IF child IS NULL OR (SELECT followup_kind FROM autopilot.role_dispatch_followup WHERE followup_task_id=child)<>'REPAIR'
 OR autopilot.blocker_remediation_action((SELECT goal_json->>'blocked_result_code' FROM autopilot.task WHERE task_id=child))<>'EVIDENCE_REMEDIATION'
 OR (SELECT goal_json->>'mode' FROM autopilot.task WHERE task_id=child)<>'REPAIR'
 OR (SELECT goal_json->>'repair_attempt' FROM autopilot.task WHERE task_id=child)<>'1' THEN
  RAISE EXCEPTION 'AUTOPILOT_0349_EVIDENCE_ROUTE_INVALID';
 END IF;

 DELETE FROM autopilot.role_dispatch_followup WHERE parent_task_id=origin_id;
 DELETE FROM autopilot.task WHERE task_id=child;
 child:=autopilot.materialize_blocker_remediation(origin_id,'TARGET_SUPERSEDED_BY_CURRENT_MAIN','Target stale.');
 IF child IS NULL OR (SELECT followup_kind FROM autopilot.role_dispatch_followup WHERE followup_task_id=child)<>'REPAIR'
 OR autopilot.blocker_remediation_action((SELECT goal_json->>'blocked_result_code' FROM autopilot.task WHERE task_id=child))<>'RECONCILE_TARGET'
 OR (SELECT goal_json->>'mode' FROM autopilot.task WHERE task_id=child)<>'REPAIR' THEN
  RAISE EXCEPTION 'AUTOPILOT_0349_RECONCILE_ROUTE_INVALID';
 END IF;

 IF autopilot.materialize_blocker_remediation(origin_id,'CODEX_ACK_DEADLINE_EXCEEDED','Provider down.') IS NOT NULL
 OR autopilot.materialize_blocker_remediation(origin_id,'OWNER_INPUT_REQUIRED','Owner needed.') IS NOT NULL
 OR autopilot.materialize_blocker_remediation(origin_id,'UNKNOWN_NEW_BLOCKER','Unknown.') IS NOT NULL THEN
  RAISE EXCEPTION 'AUTOPILOT_0349_HOLD_ROUTE_CREATED_CHILD';
 END IF;
END $t$;
ROLLBACK;
