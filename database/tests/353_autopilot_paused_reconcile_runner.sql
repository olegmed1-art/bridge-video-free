\set ON_ERROR_STOP on
BEGIN;

DO $t$
DECLARE
 wid uuid;
 wid2 uuid;
 wid3 uuid;
 wid4 uuid;
 result_action text;
 candidate record;
 token1 text:=repeat('a',64);
 token2 text:=repeat('b',64);
 active_mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT active_mailbox
 FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 IF NOT has_function_privilege(
   'bridge_school_worker',
   'autopilot.paused_reconcile_candidates(integer)',
   'EXECUTE'
 ) OR NOT has_function_privilege(
   'bridge_school_worker',
   'autopilot.reconcile_paused_project_work(uuid,text,text,text,text)',
   'EXECUTE'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_WORKER_PRIVILEGE_MISSING';
 END IF;

 INSERT INTO autopilot.project_work_item(
   work_key,role,target_pr,priority,state,created_by,source,
   task_kind,objective,result_code,mailbox_pr
 ) VALUES(
   'test-0353-remediate','AUTOPILOT',1150,0,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_REPAIR','0353 bounded remediation test','BOUNDED_DEFECT',active_mailbox
 )
 RETURNING work_item_id INTO wid;

 result_action:=autopilot.reconcile_paused_project_work(
   wid,token1,NULL,'BOUNDED_DEFECT','Fresh repository evidence permits one bounded attempt.'
 );
 IF result_action<>'REMEDIATE' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REMEDIATE_ACTION_INVALID';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM autopilot.project_work_item
   WHERE work_item_id=wid
     AND state='READY'
     AND progress_token=token1
     AND blocker_fingerprint ~ '^[0-9a-f]{64}$'
     AND hold_reason IS NULL
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REMEDIATE_STATE_INVALID';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM autopilot.paused_work_reconcile_receipt r
   WHERE r.work_item_id=wid AND r.evidence_token=token1
     AND r.action='REMEDIATE' AND r.followup_task_id IS NULL
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REMEDIATE_RECEIPT_INVALID';
 END IF;

 UPDATE autopilot.project_work_item
 SET state='PAUSED',result_code='AUTOPILOT_UNCLASSIFIED_FAILURE',
     completed_at=NULL,updated_at=now()
 WHERE work_item_id=wid;

 result_action:=autopilot.reconcile_paused_project_work(
   wid,token2,NULL,'AUTOPILOT_UNCLASSIFIED_FAILURE','Unclassified failure requires owner review.'
 );
 IF result_action<>'OWNER_HOLD' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_OWNER_HOLD_ACTION_INVALID';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM autopilot.project_work_item
   WHERE work_item_id=wid
     AND state='PAUSED'
     AND hold_reason='OWNER_HOLD'
     AND progress_token=token2
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_OWNER_HOLD_STATE_INVALID';
 END IF;
 IF autopilot.reconcile_paused_project_work(
   wid,token2,NULL,'AUTOPILOT_UNCLASSIFIED_FAILURE','Unclassified failure requires owner review.'
 )<>'NO_CHANGE' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REPLAY_INVALID';
 END IF;
 IF (SELECT count(*) FROM autopilot.paused_work_reconcile_receipt r
     WHERE r.work_item_id=wid AND r.evidence_token=token2)<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REPLAY_RECEIPT_INVALID';
 END IF;


 INSERT INTO autopilot.project_work_item(
   work_key,role,target_pr,priority,state,created_by,source,
   task_kind,objective,result_code,mailbox_pr
 ) VALUES(
   'test-0353-owner-gated-role','SERVER',1106,0,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','0353 owner-gated role preservation','SERVER_EVIDENCE_INCOMPLETE',active_mailbox
 )
 RETURNING work_item_id INTO wid2;

 result_action:=autopilot.reconcile_paused_project_work(
   wid2,repeat('c',64),NULL,'SERVER_EVIDENCE_INCOMPLETE','Fresh evidence must not bypass owner-gated SERVER role.'
 );
 IF result_action<>'OWNER_HOLD' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_OWNER_GATED_ROLE_BYPASSED';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM autopilot.project_work_item
   WHERE work_item_id=wid2
     AND state='PAUSED'
     AND hold_reason='OWNER_HOLD'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_OWNER_GATED_ROLE_STATE_INVALID';
 END IF;

 INSERT INTO autopilot.project_work_item(
   work_key,role,target_pr,priority,state,created_by,source,
   task_kind,objective,result_code,mailbox_pr
 ) VALUES(
   'test-0353-provider-recovery','VIDEO',1599,0,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','0353 provider recovery evidence','CODEX_PROVIDER_GENERIC_FAILURE',active_mailbox
 )
 RETURNING work_item_id INTO wid3;

 UPDATE autopilot.project_work_item
 SET updated_at=now()-interval '1 minute'
 WHERE work_item_id=wid3;
 UPDATE autopilot.provider_circuit_state
 SET state='CLOSED',last_success_at=now(),updated_at=now()
 WHERE provider_id='CODEX';

 result_action:=autopilot.reconcile_paused_project_work(
   wid3,repeat('d',64),NULL,'CODEX_PROVIDER_GENERIC_FAILURE','Provider recovery verified after paused item.'
 );
 -- 0371 retires global-health-only rearm; recovery must use durable budgets.
 IF result_action<>'NO_CHANGE' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_PROVIDER_RECOVERY_BYPASSED_BUDGET';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM autopilot.project_work_item
   WHERE work_item_id=wid3
     AND state='PAUSED'
     AND progress_token IS NULL
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_PROVIDER_RECOVERY_STATE_INVALID';
 END IF;

 INSERT INTO autopilot.project_work_item(
   work_key,role,target_pr,priority,state,created_by,source,
   task_kind,objective,result_code,mailbox_pr
 ) VALUES(
   'test-0353-closed-not-success','AUTOPILOT',1150,0,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','0353 closed target is not success','TARGET_PR_NOT_UPDATED',active_mailbox
 )
 RETURNING work_item_id INTO wid4;

 result_action:=autopilot.reconcile_paused_project_work(
   wid4,repeat('e',64),'CLOSED','TARGET_PR_NOT_UPDATED','Closed target alone is not evidence of goal completion.'
 );
 IF result_action<>'REMEDIATE' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_CLOSED_TARGET_WAS_TREATED_AS_DONE';
 END IF;
 IF EXISTS(
   SELECT 1 FROM autopilot.project_work_item
   WHERE work_item_id=wid4 AND state='DONE'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_CLOSED_TARGET_FALSE_DONE';
 END IF;

 SELECT * INTO candidate
 FROM autopilot.paused_reconcile_candidates(50)
 WHERE work_item_id=wid;
 IF candidate.work_item_id IS NULL
    OR candidate.blocker_action<>'OWNER_HOLD' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_CANDIDATE_INVALID';
 END IF;
END $t$;

ROLLBACK;
