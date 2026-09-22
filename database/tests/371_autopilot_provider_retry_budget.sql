\set ON_ERROR_STOP on
BEGIN;
CREATE FUNCTION pg_temp.provider_budget_fixture(p_name text,p_pr integer)
RETURNS uuid LANGUAGE plpgsql AS $f$
DECLARE wid uuid; tid uuid; sid uuid; mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT mailbox FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 INSERT INTO autopilot.project_work_item(work_key,role,target_pr,priority,state,
   created_by,source,task_kind,objective,result_code,mailbox_pr,task_spec_json)
 VALUES('sql-0371-'||p_name,'AUTOPILOT',p_pr,20,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','Read-only bounded exact-head audit.',
   'EXACT_HEAD_CHECKS_FAILED',mailbox,
   '{"execution_scope":"REPOSITORY","parallel_safe":true,"production_mutation":false,"repair_policy":"DISABLED","expected_changed_files":[],"required_checks":["exact head"],"forbidden_actions":["canon_mutation","credential_access","deploy","drive_write","force_push","main_write","merge","neon_write","paid_action","production_write","real_media_processing","server_write"]}')
 RETURNING work_item_id INTO wid;
 INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,
   acceptance_contract_json,allowed_capabilities_json,priority,
   terminal_reason_code,safe_summary_json,created_by,source,completed_at)
 VALUES('sql-0371-task-'||p_name,'CHATGPT_ROLE_DISPATCH_V1',
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
   'sql-0371-step-'||p_name,repeat('b',64),'FAILED_CLOSED',1,clock_timestamp())
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
DECLARE wid uuid; proof uuid; other_kind uuid; tid uuid; stamp timestamptz; action text;
 code text; n integer:=0; before_row jsonb;
BEGIN
 -- A fresh global provider timestamp alone must never renew retry authority.
 UPDATE autopilot.provider_circuit_state SET state='CLOSED',last_success_at=clock_timestamp()
 WHERE provider_id='CODEX';
 FOREACH code IN ARRAY ARRAY['CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE'] LOOP
  n:=n+1;
  wid:=pg_temp.provider_budget_fixture('provider-'||n,999870+n);
  UPDATE autopilot.project_work_item SET result_code=code,hold_reason='PROVIDER_HOLD' WHERE work_item_id=wid;
  SELECT updated_at,last_task_id,to_jsonb(w) INTO stamp,tid,before_row FROM autopilot.project_work_item w WHERE work_item_id=wid;
  IF NOT EXISTS(SELECT FROM autopilot.project_progress_candidates(50) c WHERE c->>'work_item_id'=wid::text) THEN
   RAISE EXCEPTION '0371_PROVIDER_NOT_ROUTED_TO_BUDGET';
  END IF;
  action:=autopilot.reconcile_paused_project_work(wid,repeat(n::text,64),NULL,NULL,'Fresh provider health');
  IF action<>'NO_CHANGE' OR (SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=wid) IS DISTINCT FROM before_row THEN
   RAISE EXCEPTION '0371_LEGACY_PROVIDER_BYPASS';
  END IF;
  action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40),clock_timestamp());
  IF action<>'NO_CHANGE' THEN RAISE EXCEPTION '0371_GLOBAL_HEALTH_OR_GREEN_CI_NOT_ROUTE_PROOF'; END IF;
  -- A previously consumed same-head receipt is immutable, even with fresh CI.
  INSERT INTO autopilot.project_progress_receipt(work_item_id,head_sha,source_task_id,reason)
   VALUES(wid,repeat('a',40),tid,'TRANSPORT_RECOVERED');
  action:=autopilot.reconcile_project_progress(wid,stamp,repeat('a',40),clock_timestamp());
  IF action<>'RETRY_BUDGET_EXHAUSTED' OR (SELECT count(*) FROM autopilot.project_progress_receipt WHERE work_item_id=wid)<>1 THEN
   RAISE EXCEPTION '0371_PROVIDER_HEAD_BUDGET_RESET';
  END IF;
 END LOOP;

 -- Global health must not rearm other task kinds with AUDIT-only lineage.
 other_kind:=pg_temp.provider_budget_fixture('non-audit-kind',999874);
 UPDATE autopilot.project_work_item SET task_kind='GITHUB_EVENT_RUNTIME_E2E_CANARY',
   result_code='CODEX_ACK_DEADLINE_EXCEEDED',hold_reason='PROVIDER_HOLD'
   WHERE work_item_id=other_kind;
 SELECT to_jsonb(w) INTO before_row FROM autopilot.project_work_item w WHERE work_item_id=other_kind;
 action:=autopilot.reconcile_paused_project_work(other_kind,repeat('d',64),NULL,NULL,'Fresh provider health');
 IF action<>'NO_CHANGE' OR (SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=other_kind) IS DISTINCT FROM before_row THEN
  RAISE EXCEPTION '0371_NON_AUDIT_PROVIDER_BYPASS';
 END IF;
 action:=autopilot.reconcile_paused_project_work(other_kind,repeat('e',64),'MERGED',NULL,'Verified target merged');
 IF action<>'CLOSE_SUPERSEDED' OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=other_kind)<>'DONE' THEN
  RAISE EXCEPTION '0371_TARGET_DISPOSITION_CLOSURE_BLOCKED';
 END IF;

 -- Build independent real task/outbox proof on the same delivery route.
 -- Fixture mutations stay inside this rolled-back test transaction.
 proof:=pg_temp.provider_budget_fixture('proof',999875);
 SELECT last_task_id INTO tid FROM autopilot.project_work_item WHERE work_item_id=proof;
 UPDATE autopilot.task SET safe_summary_json=jsonb_build_object('status','BLOCKED',
   'result_code','CODEX_PROVIDER_GENERIC_FAILURE','summary','Task blocked. See execution details above.') WHERE task_id=tid;
 UPDATE autopilot.role_dispatch_outbox SET status='CALLBACK_ACCEPTED',
   github_dispatch_comment_id=999875,dispatch_body_sha256=repeat('f',64),
   sent_at=clock_timestamp(),callback_deadline_at=clock_timestamp()+interval '2 hours',
   completed_at=clock_timestamp() WHERE task_id=tid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN RAISE EXCEPTION '0371_PROVIDER_FAILURE_FALSE_RECOVERY'; END IF;
 UPDATE autopilot.task SET safe_summary_json=jsonb_build_object('status','BLOCKED',
   'result_code','EXACT_HEAD_CHECKS_FAILED','summary','Task blocked. See execution details above.') WHERE task_id=tid;
 UPDATE autopilot.role_dispatch_outbox SET target_chat_id='wrong-route' WHERE task_id=tid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN RAISE EXCEPTION '0371_WRONG_ROUTE_RECOVERY'; END IF;
 UPDATE autopilot.role_dispatch_outbox SET target_chat_id=NULL WHERE task_id=tid;
 UPDATE autopilot.project_work_item SET hold_reason='OWNER_HOLD' WHERE work_item_id=wid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN RAISE EXCEPTION '0371_OWNER_HOLD_RECOVERY'; END IF;
 UPDATE autopilot.project_work_item SET hold_reason='PROVIDER_HOLD' WHERE work_item_id=wid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'REAUDIT_READY' THEN RAISE EXCEPTION '0371_VERIFIED_ROUTE_NOT_ADMITTED: %',action; END IF;
 IF (SELECT count(*) FROM autopilot.project_progress_receipt WHERE work_item_id=wid)<>2
  OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'READY' THEN
  RAISE EXCEPTION '0371_RECOVERY_RECEIPT_MISSING';
 END IF;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN RAISE EXCEPTION '0371_REPLAY_ACCEPTED'; END IF;
END $test$;
SELECT '0371 provider route, failure classification, legacy fencing and durable retry budget passed' AS result;
ROLLBACK;
