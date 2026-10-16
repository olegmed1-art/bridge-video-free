-- Synthetic capacity rows, never external work. The caller is restricted to
-- a disposable localhost CI database. All production guards remain enabled.
CREATE OR REPLACE FUNCTION pg_temp.active_queue_fixture(p_name text,p_status text DEFAULT 'PENDING')
RETURNS uuid LANGUAGE plpgsql AS $f$
DECLARE tid uuid; sid uuid; did uuid; mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT mailbox FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,completed_at,terminal_reason_code,created_by,source)
 VALUES('sql-0403-'||p_name,'CHATGPT_ROLE_DISPATCH_V1',
        jsonb_build_object('mailbox_pr',mailbox,'role','AUTOPILOT'),
        'DONE','github.chatgpt.role.dispatch',clock_timestamp(),'SQL_FIXTURE_TERMINAL','sql-test','SQL_TEST') RETURNING task_id INTO tid;
 INSERT INTO autopilot.step_attempt(task_id,step_key,attempt_no,capability_name,idempotency_key,input_fingerprint,status,lease_epoch)
 VALUES(tid,'github.chatgpt.role.dispatch',1,'github.chatgpt.role.dispatch','sql-0403-'||p_name,
        repeat('b',64),'COMPLETED',1) RETURNING step_attempt_id INTO sid;
 INSERT INTO autopilot.role_dispatch_outbox(task_id,step_attempt_id,repository,mailbox_pr,role,target_pr,
        expected_head_sha,dispatch_epoch,task_fingerprint,status,prepared_by,completed_at)
 VALUES(tid,sid,'olegmed1-art/bridge-video-free',mailbox,'AUTOPILOT',999973,repeat('a',40),1,
        repeat('c',64),p_status,'sql-test',CASE WHEN p_status='FAILED_CLOSED' THEN clock_timestamp() END)
 RETURNING dispatch_id INTO did;
 RETURN did;
END $f$;
