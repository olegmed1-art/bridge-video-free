-- Test-only helper: pg_temp lifetime; call only in the disposable CI database.
CREATE FUNCTION pg_temp.codex_send_fixture(p_name text)
RETURNS uuid LANGUAGE plpgsql AS $f$
DECLARE wid uuid; tid uuid; sid uuid; did uuid; mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT mailbox FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 INSERT INTO autopilot.project_work_item(work_key,role,target_pr,priority,state,
   created_by,source,task_kind,objective,mailbox_pr,task_spec_json)
 VALUES('sql-0370-'||p_name,'AUTOPILOT',999970,20,'ACTIVE','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','Read-only bounded exact-head audit.',mailbox,'{}')
 RETURNING work_item_id INTO wid;
 INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,
   acceptance_contract_json,allowed_capabilities_json,priority,created_by,source)
 VALUES('sql-0370-task-'||p_name,'CHATGPT_ROLE_DISPATCH_V1',
   jsonb_build_object('repository','olegmed1-art/bridge-video-free','mailbox_pr',mailbox,
     'role','AUTOPILOT','target_pr',999970,'expected_head_sha',repeat('a',40),
     'dispatch_epoch',1,'successor_task_key',NULL,'successor_role',NULL,
     'successor_target_pr',NULL,'successor_expected_head_sha',NULL),
   'WAITING_EXTERNAL','github.chatgpt.role.dispatch',
   '{"retained_evidence_required":true,"production_mutation":false,"exact_head_required":true}',
   '["github.chatgpt.role.dispatch"]',20,'sql-test','SQL_TEST') RETURNING task_id INTO tid;
 INSERT INTO autopilot.project_work_task(work_item_id,task_id,run_kind) VALUES(wid,tid,'AUDIT');
 INSERT INTO autopilot.step_attempt(task_id,step_key,attempt_no,capability_name,
   idempotency_key,input_fingerprint,status,lease_epoch)
 VALUES(tid,'github.chatgpt.role.dispatch',1,'github.chatgpt.role.dispatch',
   'sql-0370-step-'||p_name,repeat('b',64),'WAITING_EXTERNAL',1)
 RETURNING step_attempt_id INTO sid;
 INSERT INTO autopilot.role_dispatch_outbox(task_id,step_attempt_id,repository,mailbox_pr,
   role,target_pr,expected_head_sha,dispatch_epoch,task_fingerprint,status,
   prepared_by,mode,delivery_contract_version,github_dispatch_comment_id,
   dispatch_body_sha256,published_at,delivery_deadline_at)
 VALUES(tid,sid,'olegmed1-art/bridge-video-free',mailbox,'AUTOPILOT',999970,
   repeat('a',40),1,repeat('c',64),'PUBLISHED','sql-test','READ_ONLY',3,
   999971,repeat('d',64),clock_timestamp(),clock_timestamp()+interval '30 minutes')
 RETURNING dispatch_id INTO did;
 UPDATE autopilot.project_work_item SET last_task_id=tid,last_observed_head_sha=repeat('a',40),
   generation=1 WHERE work_item_id=wid;
 RETURN did;
END $f$;
