\set ON_ERROR_STOP on
DO $$
DECLARE wid uuid; tid uuid; action text; token text:=repeat('a',64); active_mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT active_mailbox
 FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 INSERT INTO autopilot.project_work_item(work_key,role,target_pr,priority,state,created_by,source,task_kind,objective,result_code,mailbox_pr)
 VALUES('test-0351-close','AUTOPILOT',1150,0,'PAUSED','sql-test','SQL_TEST','REPOSITORY_AUDIT','0351 close test','TARGET_SUPERSEDED_BY_CURRENT_MAIN',active_mailbox)
 RETURNING work_item_id INTO wid;
 action:=autopilot.reconcile_paused_project_work(wid,token,'SUPERSEDED','TARGET_SUPERSEDED_BY_CURRENT_MAIN','verified');
 IF action<>'CLOSE_SUPERSEDED' THEN RAISE EXCEPTION 'AUTOPILOT_0351_CLOSE_FAILED'; END IF;
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'DONE' THEN RAISE EXCEPTION 'AUTOPILOT_0351_NOT_DONE'; END IF;
 IF autopilot.reconcile_paused_project_work(wid,token,'SUPERSEDED','TARGET_SUPERSEDED_BY_CURRENT_MAIN','verified')<>'NO_CHANGE' THEN RAISE EXCEPTION 'AUTOPILOT_0351_REPLAY_NOT_CLOSED'; END IF;
 IF (SELECT count(*) FROM autopilot.paused_work_reconcile_receipt WHERE work_item_id=wid)<>1 THEN RAISE EXCEPTION 'AUTOPILOT_0351_RECEIPT_INVALID'; END IF;
END $$;
