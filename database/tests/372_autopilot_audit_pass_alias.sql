\set ON_ERROR_STOP on
BEGIN;

-- Independent behavioral regression: real task triggers remain enabled.
-- All fixtures and helper functions are transaction-local and rolled back.
CREATE FUNCTION pg_temp.audit_alias_fixture(p_name text,p_pr integer)
RETURNS uuid LANGUAGE plpgsql AS $f$
DECLARE wid uuid; tid uuid; sid uuid; mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT mailbox FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 INSERT INTO autopilot.project_work_item(work_key,role,target_pr,priority,state,
   created_by,source,task_kind,objective,result_code,mailbox_pr,task_spec_json)
 VALUES('sql-0372-'||p_name,'AUTOPILOT',p_pr,20,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','Read-only bounded exact-head audit.',
   'EXACT_HEAD_CHECKS_FAILED',mailbox,
   '{"execution_scope":"REPOSITORY","parallel_safe":true,"production_mutation":false,"repair_policy":"DISABLED","expected_changed_files":[],"required_checks":["exact head"],"forbidden_actions":["canon_mutation","credential_access","deploy","drive_write","force_push","main_write","merge","neon_write","paid_action","production_write","real_media_processing","server_write"]}')
 RETURNING work_item_id INTO wid;
 INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,
   acceptance_contract_json,allowed_capabilities_json,priority,
   terminal_reason_code,safe_summary_json,created_by,source,completed_at)
 VALUES('sql-0372-task-'||p_name,'CHATGPT_ROLE_DISPATCH_V1',
   jsonb_build_object('repository','olegmed1-art/bridge-video-free','mailbox_pr',mailbox,
     'role','AUTOPILOT','target_pr',p_pr,'expected_head_sha',repeat('a',40),
     'dispatch_epoch',1,'successor_task_key',NULL,'successor_role',NULL,
     'successor_target_pr',NULL,'successor_expected_head_sha',NULL),
   'FAILED_CLOSED','github.chatgpt.role.dispatch',
   '{"retained_evidence_required":true,"production_mutation":false,"exact_head_required":true}',
   '["github.chatgpt.role.dispatch"]',20,'EXACT_HEAD_CHECKS_FAILED',
   jsonb_build_object('status','BLOCKED','result_code','EXACT_HEAD_CHECKS_FAILED',
     'target_head_sha',repeat('a',40),'summary','Retained exact-head checks failed.'),
   'sql-test','SQL_TEST',clock_timestamp()) RETURNING task_id INTO tid;
 INSERT INTO autopilot.project_work_task(work_item_id,task_id,run_kind) VALUES(wid,tid,'AUDIT');
 INSERT INTO autopilot.step_attempt(task_id,step_key,attempt_no,capability_name,
   idempotency_key,input_fingerprint,status,lease_epoch,completed_at)
 VALUES(tid,'github.chatgpt.role.dispatch',1,'github.chatgpt.role.dispatch',
   'sql-0372-step-'||p_name,repeat('b',64),'FAILED_CLOSED',1,clock_timestamp())
 RETURNING step_attempt_id INTO sid;
 INSERT INTO autopilot.role_dispatch_outbox(task_id,step_attempt_id,repository,mailbox_pr,
   role,target_pr,expected_head_sha,dispatch_epoch,task_fingerprint,status,
   prepared_by,completed_at,mode,delivery_contract_version)
 VALUES(tid,sid,'olegmed1-art/bridge-video-free',mailbox,'AUTOPILOT',p_pr,
   repeat('a',40),1,repeat('c',64),'FAILED_CLOSED','sql-test',clock_timestamp(),'READ_ONLY',3);
 UPDATE autopilot.project_work_item SET last_task_id=tid,last_observed_head_sha=repeat('a',40),
   generation=1,updated_at=clock_timestamp()-interval '1 minute' WHERE work_item_id=wid;
 RETURN wid;
END $f$;

DO $test$
DECLARE parent uuid; child uuid; tid uuid; newer uuid; before_row jsonb; result_status text; code text;
BEGIN
 UPDATE autopilot.role_registry SET can_repair=false WHERE role_id='AUTOPILOT';
 parent:=pg_temp.audit_alias_fixture('parent',999872);
 child:=pg_temp.audit_alias_fixture('child',999873);
 UPDATE autopilot.project_work_item SET depends_on_work_item_id=parent WHERE work_item_id=child;
 SELECT last_task_id INTO tid FROM autopilot.project_work_item WHERE work_item_id=parent;
 FOREACH result_status IN ARRAY ARRAY['BLOCKED','SUCCEEDED'] LOOP
   FOREACH code IN ARRAY ARRAY['UNKNOWN_AUDIT_PASS','AUDIT_FINDINGS_REPORTED','AUDIT_PASS'] LOOP
     UPDATE autopilot.task SET status='READY',terminal_reason_code=NULL,completed_at=NULL WHERE task_id=tid;
     UPDATE autopilot.project_work_item SET state='ACTIVE',completed_at=NULL WHERE work_item_id=parent;
     UPDATE autopilot.project_work_item SET state='WAITING_DEPENDENCY' WHERE work_item_id=child;
     UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
       safe_summary_json=jsonb_build_object('status',result_status,'result_code',code,
         'target_head_sha',repeat('a',40),'summary','Bounded audit result.'),completed_at=now() WHERE task_id=tid;
     IF result_status='SUCCEEDED' AND code='AUDIT_PASS' THEN
       IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=parent)<>'DONE'
          OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'READY' THEN
         RAISE EXCEPTION 'AUDIT_PASS_ALIAS_DID_NOT_COMPLETE';
       END IF;
     ELSIF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=parent)='DONE'
          OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'WAITING_DEPENDENCY' THEN
       RAISE EXCEPTION 'AUDIT_PASS_ALIAS_FALSE_COMPLETION';
     END IF;
     IF EXISTS(SELECT FROM autopilot.role_dispatch_followup WHERE parent_task_id=tid)
        OR EXISTS(SELECT FROM autopilot.project_progress_receipt WHERE work_item_id=parent) THEN
       RAISE EXCEPTION 'AUDIT_PASS_ALIAS_CREATED_RETRY';
     END IF;
   END LOOP;
 END LOOP;
 -- The new alias must remain subject to the existing generation fence.
 INSERT INTO autopilot.task(task_key,goal_type,status,terminal_reason_code,completed_at,created_by,source)
 VALUES('sql-0372-newer','AUTOPILOT_SMOKE_V1','DONE','SHADOW_DONE',now(),'sql-test','SQL_TEST') RETURNING task_id INTO newer;
 INSERT INTO autopilot.project_work_task(work_item_id,task_id,run_kind) VALUES(parent,newer,'AUDIT');
 UPDATE autopilot.project_work_item SET last_task_id=newer,generation=2 WHERE work_item_id=parent;
 SELECT to_jsonb(w) INTO before_row FROM autopilot.project_work_item w WHERE work_item_id=parent;
 UPDATE autopilot.task SET status='READY',terminal_reason_code=NULL,completed_at=NULL WHERE task_id=tid;
 UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
   safe_summary_json=jsonb_build_object('status','SUCCEEDED','result_code','AUDIT_PASS',
     'target_head_sha',repeat('a',40),'summary','Stale completion.'),completed_at=now() WHERE task_id=tid;
 IF (SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=parent) IS DISTINCT FROM before_row THEN
   RAISE EXCEPTION 'AUDIT_PASS_ALIAS_STALE_GENERATION';
 END IF;
END $test$;
ROLLBACK;
