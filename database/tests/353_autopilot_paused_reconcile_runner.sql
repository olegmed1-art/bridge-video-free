\set ON_ERROR_STOP on
BEGIN;

DO $t$
DECLARE
 wid uuid;
 wid2 uuid;
 action text;
 candidate record;
 token1 text:=repeat('a',64);
 token2 text:=repeat('b',64);
BEGIN
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
   'REPOSITORY_REPAIR','0353 bounded remediation test','BOUNDED_DEFECT',1685
 )
 RETURNING work_item_id INTO wid;

 action:=autopilot.reconcile_paused_project_work(
   wid,token1,NULL,'BOUNDED_DEFECT','Fresh repository evidence permits one bounded attempt.'
 );
 IF action<>'REMEDIATE' THEN
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
   SELECT 1 FROM autopilot.paused_work_reconcile_receipt
   WHERE work_item_id=wid AND evidence_token=token1
     AND action='REMEDIATE' AND followup_task_id IS NULL
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REMEDIATE_RECEIPT_INVALID';
 END IF;

 UPDATE autopilot.project_work_item
 SET state='PAUSED',result_code='AUTOPILOT_UNCLASSIFIED_FAILURE',
     completed_at=NULL,updated_at=now()
 WHERE work_item_id=wid;

 action:=autopilot.reconcile_paused_project_work(
   wid,token2,NULL,'AUTOPILOT_UNCLASSIFIED_FAILURE','Unclassified failure requires owner review.'
 );
 IF action<>'OWNER_HOLD' THEN
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
 IF (SELECT count(*) FROM autopilot.paused_work_reconcile_receipt
     WHERE work_item_id=wid AND evidence_token=token2)<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_REPLAY_RECEIPT_INVALID';
 END IF;


 INSERT INTO autopilot.project_work_item(
   work_key,role,target_pr,priority,state,created_by,source,
   task_kind,objective,result_code,mailbox_pr
 ) VALUES(
   'test-0353-owner-gated-role','SERVER',1106,0,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','0353 owner-gated role preservation','SERVER_EVIDENCE_INCOMPLETE',1685
 )
 RETURNING work_item_id INTO wid2;

 action:=autopilot.reconcile_paused_project_work(
   wid2,repeat('c',64),NULL,'SERVER_EVIDENCE_INCOMPLETE','Fresh evidence must not bypass owner-gated SERVER role.'
 );
 IF action<>'OWNER_HOLD' THEN
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

 SELECT * INTO candidate
 FROM autopilot.paused_reconcile_candidates(50)
 WHERE work_item_id=wid;
 IF candidate.work_item_id IS NULL
    OR candidate.blocker_action<>'OWNER_HOLD' THEN
   RAISE EXCEPTION 'AUTOPILOT_0353_CANDIDATE_INVALID';
 END IF;
END $t$;

ROLLBACK;
