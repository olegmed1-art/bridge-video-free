\set ON_ERROR_STOP on
BEGIN;

-- Independent behavioral regression: real task triggers remain enabled.
-- All fixtures and helper functions are transaction-local and rolled back.
CREATE FUNCTION pg_temp.progress_fixture(p_name text,p_pr integer)
RETURNS uuid LANGUAGE plpgsql AS $f$
DECLARE wid uuid; tid uuid; sid uuid; mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT mailbox FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE';
 INSERT INTO autopilot.project_work_item(work_key,role,target_pr,priority,state,
   created_by,source,task_kind,objective,result_code,mailbox_pr,task_spec_json)
 VALUES('sql-0368-'||p_name,'AUTOPILOT',p_pr,20,'PAUSED','sql-test','SQL_TEST',
   'REPOSITORY_AUDIT','Read-only bounded exact-head audit.',
   'EXACT_HEAD_CHECKS_FAILED',mailbox,
   '{"execution_scope":"REPOSITORY","parallel_safe":true,"production_mutation":false,"repair_policy":"DISABLED","expected_changed_files":[],"required_checks":["exact head"],"forbidden_actions":["canon_mutation","credential_access","deploy","drive_write","force_push","main_write","merge","neon_write","paid_action","production_write","real_media_processing","server_write"]}')
 RETURNING work_item_id INTO wid;
 INSERT INTO autopilot.task(task_key,goal_type,goal_json,status,current_step_key,
   acceptance_contract_json,allowed_capabilities_json,priority,
   terminal_reason_code,safe_summary_json,created_by,source,completed_at)
 VALUES('sql-0368-task-'||p_name,'CHATGPT_ROLE_DISPATCH_V1',
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
   'sql-0368-step-'||p_name,repeat('b',64),'FAILED_CLOSED',1,clock_timestamp())
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
DECLARE wid uuid; parent uuid; child uuid; tid uuid; newer uuid; stamp timestamptz;
 before_row jsonb; after_row jsonb; action text; i integer; code text; materialized record; task_count bigint; origin_spec jsonb; origin_task jsonb;
BEGIN
 IF NOT has_function_privilege('bridge_school_worker',
   'autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz)','EXECUTE')
   OR has_table_privilege('bridge_school_worker','autopilot.project_progress_receipt','INSERT')
   OR has_function_privilege('autopilot_callback',
   'autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz)','EXECUTE') THEN
   RAISE EXCEPTION '0368_ACL_BOUNDARY';
 END IF;
 wid:=pg_temp.progress_fixture('budget',999801);
 SELECT updated_at,last_task_id INTO stamp,tid FROM autopilot.project_work_item WHERE work_item_id=wid;
 SELECT to_jsonb(t) INTO before_row FROM autopilot.task t WHERE task_id=tid;
 -- The legacy entrypoint cannot bypass the new durable retry ledger.
 action:=autopilot.reconcile_paused_project_work(wid,repeat('f',64),NULL,NULL,'CI rerun');
 IF action<>'NO_CHANGE'
    OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'PAUSED' THEN
   RAISE EXCEPTION '0368_LEGACY_BUDGET_BYPASS';
 END IF;
 -- An unknown disposition must not act as a non-null escape around budgets.
 UPDATE autopilot.project_work_item SET result_code='TECHNICAL_TEST_FAILURE' WHERE work_item_id=wid;
 action:=autopilot.reconcile_paused_project_work(wid,repeat('1',64),'CLOSED',NULL,'Closed is not verified success');
 IF action<>'NO_CHANGE' OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'PAUSED' THEN
   RAISE EXCEPTION '0368_CLOSED_DISPOSITION_BUDGET_BYPASS';
 END IF;
 action:=autopilot.reconcile_paused_project_work(wid,repeat('2',64),'UNKNOWN',NULL,'Unknown disposition');
 IF action<>'NO_CHANGE' OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'PAUSED' THEN
   RAISE EXCEPTION '0368_UNKNOWN_DISPOSITION_BUDGET_BYPASS';
 END IF;
 UPDATE autopilot.project_work_item SET result_code='EXACT_HEAD_CHECKS_FAILED' WHERE work_item_id=wid;
 UPDATE autopilot.project_work_item SET task_spec_json=task_spec_json||jsonb_build_object('expected_head_sha',repeat('a',40)) WHERE work_item_id=wid;
 SELECT updated_at INTO stamp FROM autopilot.project_work_item WHERE work_item_id=wid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN
   RAISE EXCEPTION '0368_PINNED_TASK_SCOPE_BYPASSED';
 END IF;
 UPDATE autopilot.project_work_item SET task_spec_json=task_spec_json-'expected_head_sha' WHERE work_item_id=wid;
 SELECT updated_at INTO stamp FROM autopilot.project_work_item WHERE work_item_id=wid;
 action:=autopilot.reconcile_project_progress(wid,stamp-interval '1 second',repeat('b',40));
 IF action<>'NO_CHANGE' THEN
   RAISE EXCEPTION '0368_STALE_CAS_ACCEPTED';
 END IF;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('a',40));
 IF action<>'NO_CHANGE' THEN
   RAISE EXCEPTION '0368_NO_FRESH_EVIDENCE_ACCEPTED';
 END IF;
 BEGIN
   PERFORM autopilot.reconcile_project_progress(wid,stamp,repeat('b',40),clock_timestamp()+interval '1 day');
   RAISE EXCEPTION '0368_FUTURE_EVIDENCE_ACCEPTED';
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'AUTOPILOT_NEXT_STEP_INVALID_EVIDENCE' THEN RAISE; END IF;
 END;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'REAUDIT_READY' THEN
   RAISE EXCEPTION '0368_NEW_HEAD_NOT_READY';
 END IF;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN
   RAISE EXCEPTION '0368_DUPLICATE_SNAPSHOT_ACCEPTED';
 END IF;
 SELECT to_jsonb(t) INTO after_row FROM autopilot.task t WHERE task_id=tid;
 IF after_row IS DISTINCT FROM before_row THEN RAISE EXCEPTION '0368_ORIGIN_TASK_MUTATED'; END IF;
 IF (SELECT count(*) FROM autopilot.project_progress_receipt WHERE work_item_id=wid)<>1
    OR (SELECT last_observed_head_sha FROM autopilot.project_work_item WHERE work_item_id=wid)<>repeat('a',40) THEN
   RAISE EXCEPTION '0368_RECEIPT_OR_HEAD_REWRITTEN';
 END IF;
 -- Recovery authorized B, but GitHub now reports C. Exercise the real
 -- planner materializer with a legitimate lease, not a source-text assertion.
 SELECT count(*) INTO task_count FROM autopilot.project_work_task WHERE work_item_id=wid;
 UPDATE autopilot.project_work_item SET probe_lease_owner='sql-next-step',
   probe_lease_epoch=1,probe_lease_until=now()+interval '1 minute' WHERE work_item_id=wid;
 SELECT * INTO STRICT materialized FROM autopilot.materialize_project_work_probe(
   wid,'sql-next-step',1,true,repeat('c',40));
 IF materialized.resulting_state<>'PAUSED' OR materialized.created IS DISTINCT FROM false
    OR materialized.task_id IS NOT NULL THEN RAISE EXCEPTION '0368_PENDING_HEAD_FENCE_BYPASSED'; END IF;
 IF (SELECT count(*) FROM autopilot.project_work_task WHERE work_item_id=wid)<>task_count
    OR (SELECT hold_reason FROM autopilot.project_work_item WHERE work_item_id=wid)<>'RECOVERY_HEAD_CHANGED'
    OR (SELECT last_task_id FROM autopilot.project_work_item WHERE work_item_id=wid) IS DISTINCT FROM tid
    OR (SELECT last_observed_head_sha FROM autopilot.project_work_item WHERE work_item_id=wid)<>repeat('a',40)
    OR EXISTS(SELECT FROM autopilot.project_work_item WHERE work_item_id=wid AND probe_lease_owner IS NOT NULL) THEN
   RAISE EXCEPTION '0368_PENDING_HEAD_FENCE_MUTATED_LINEAGE';
 END IF;
 UPDATE autopilot.project_work_item SET state='PAUSED',updated_at=clock_timestamp()-interval '1 minute' WHERE work_item_id=wid;
 SELECT updated_at INTO stamp FROM autopilot.project_work_item WHERE work_item_id=wid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40),clock_timestamp());
 IF action<>'RETRY_BUDGET_EXHAUSTED' THEN
   RAISE EXCEPTION '0368_CI_REFRESH_RESET_HEAD_BUDGET';
 END IF;
 FOR i IN 1..2 LOOP
   action:=autopilot.reconcile_project_progress(wid,stamp,repeat((ARRAY['c','d'])[i],40));
 IF action<>'REAUDIT_READY' THEN
     RAISE EXCEPTION '0368_BOUNDED_NEW_HEAD_REJECTED';
   END IF;
   UPDATE autopilot.project_work_item SET state='PAUSED',updated_at=clock_timestamp()-interval '1 minute' WHERE work_item_id=wid;
   SELECT updated_at INTO stamp FROM autopilot.project_work_item WHERE work_item_id=wid;
 END LOOP;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('e',40));
 IF action<>'RETRY_BUDGET_EXHAUSTED'
    OR (SELECT count(*) FROM autopilot.project_progress_receipt WHERE work_item_id=wid)<>3 THEN
   RAISE EXCEPTION '0368_LIFETIME_BUDGET_RESET';
 END IF;

 -- Explicit owner hold is authoritative even with a normally retryable code.
 wid:=pg_temp.progress_fixture('owner',999802);
 UPDATE autopilot.project_work_item SET hold_reason='OWNER_HOLD' WHERE work_item_id=wid;
 SELECT updated_at INTO stamp FROM autopilot.project_work_item WHERE work_item_id=wid;
 action:=autopilot.reconcile_project_progress(wid,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN
   RAISE EXCEPTION '0368_OWNER_HOLD_BYPASSED';
 END IF;
 action:=autopilot.reconcile_paused_project_work(wid,repeat('d',64),'MERGED',NULL,'Fresh merge evidence');
 IF action<>'OWNER_HOLD'
    OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=wid)<>'PAUSED' THEN
   RAISE EXCEPTION '0368_LEGACY_OWNER_HOLD_BYPASSED';
 END IF;

 -- Legacy and new reconciliation must preserve unresolved dependency gates.
 parent:=pg_temp.progress_fixture('parent',999803);
 child:=pg_temp.progress_fixture('child',999804);
 UPDATE autopilot.project_work_item SET depends_on_work_item_id=parent WHERE work_item_id=child;
 UPDATE autopilot.project_work_item SET state='PAUSED',result_code='TECHNICAL_TEST_FAILURE' WHERE work_item_id=child;
 SELECT updated_at INTO stamp FROM autopilot.project_work_item WHERE work_item_id=child;
 action:=autopilot.reconcile_project_progress(child,stamp,repeat('b',40));
 IF action<>'NO_CHANGE' THEN
   RAISE EXCEPTION '0368_DEPENDENCY_BYPASSED';
 END IF;
 UPDATE autopilot.project_work_item SET result_code='BOUNDED_DEFECT' WHERE work_item_id=child;
 action:=autopilot.reconcile_paused_project_work(child,repeat('e',64),NULL,NULL,'Fresh evidence');
 IF action<>'REMEDIATE'
    OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'WAITING_DEPENDENCY' THEN
   RAISE EXCEPTION '0368_LEGACY_DEPENDENCY_BYPASSED';
 END IF;

 -- Real terminal trigger: refused repair cannot become false DONE or wake child.
 UPDATE autopilot.role_registry SET can_repair=false WHERE role_id='AUTOPILOT';
 SELECT last_task_id INTO tid FROM autopilot.project_work_item WHERE work_item_id=parent;
 FOREACH code IN ARRAY ARRAY['REPAIR_REQUIRED','AUDIT_FINDINGS_REPORTED','UNCLASSIFIED_AUDIT_OUTCOME','BOUNDED_DEFECT'] LOOP
   UPDATE autopilot.task SET status='READY',terminal_reason_code=NULL,completed_at=NULL WHERE task_id=tid;
   UPDATE autopilot.project_work_item SET state='ACTIVE',completed_at=NULL WHERE work_item_id=parent;
   UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
     safe_summary_json=jsonb_build_object('status','SUCCEEDED','result_code',code,
       'target_head_sha',repeat('a',40),'summary','Findings need unavailable repair.'),completed_at=now() WHERE task_id=tid;
   IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=parent)='DONE'
      OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'WAITING_DEPENDENCY'
      OR EXISTS(SELECT FROM autopilot.role_dispatch_followup WHERE parent_task_id=tid) THEN
     RAISE EXCEPTION '0368_FALSE_DONE_WITH_FINDINGS: %',code;
   END IF;
 END LOOP;
 -- A verified success must still release the dependent exactly through the trigger.
 UPDATE autopilot.task SET status='READY',terminal_reason_code=NULL,completed_at=NULL WHERE task_id=tid;
 UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
   safe_summary_json=jsonb_build_object('status','SUCCEEDED','result_code','EXACT_HEAD_AUDIT_PASSED',
     'target_head_sha',repeat('a',40),'summary','Audit verified no repair needed.'),completed_at=now() WHERE task_id=tid;
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=parent)<>'DONE'
    OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'READY' THEN
   RAISE EXCEPTION '0368_VERIFIED_SUCCESS_DID_NOT_WAKE';
 END IF;

 -- Old task transition cannot overwrite an already selected newer generation.
 wid:=pg_temp.progress_fixture('stale',999805);
 SELECT last_task_id INTO tid FROM autopilot.project_work_item WHERE work_item_id=wid;
 INSERT INTO autopilot.task(task_key,goal_type,status,terminal_reason_code,completed_at,created_by,source)
 VALUES('sql-0368-newer-placeholder','AUTOPILOT_SMOKE_V1','DONE','SHADOW_DONE',now(),'sql-test','SQL_TEST') RETURNING task_id INTO newer;
 INSERT INTO autopilot.project_work_task(work_item_id,task_id,run_kind) VALUES(wid,newer,'AUDIT');
 UPDATE autopilot.project_work_item SET last_task_id=newer,generation=2 WHERE work_item_id=wid;
 SELECT to_jsonb(w) INTO before_row FROM autopilot.project_work_item w WHERE work_item_id=wid;
 UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
   safe_summary_json=jsonb_build_object('status','SUCCEEDED','result_code','EXACT_HEAD_AUDIT_PASSED',
     'target_head_sha',repeat('a',40),'summary','Old retained completion.'),completed_at=now() WHERE task_id=tid;
 SELECT to_jsonb(w) INTO after_row FROM autopilot.project_work_item w WHERE work_item_id=wid;
 IF after_row IS DISTINCT FROM before_row THEN RAISE EXCEPTION '0368_OLD_TASK_REGRESSED_NEW_GENERATION'; END IF;
 -- A late nonterminal old task must also be fenced before the earlier repair
 -- trigger can insert a successor and replace the work item's last_task_id.
 UPDATE autopilot.role_registry SET can_repair=true WHERE role_id='AUTOPILOT';
 UPDATE autopilot.task SET status='READY',terminal_reason_code=NULL,completed_at=NULL WHERE task_id=tid;
 UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
   safe_summary_json=jsonb_build_object('status','SUCCEEDED','result_code','AUDIT_FINDINGS_REPORTED',
     'target_head_sha',repeat('a',40),'summary','Stale findings must not create repair.'),completed_at=now() WHERE task_id=tid;
 SELECT to_jsonb(w) INTO after_row FROM autopilot.project_work_item w WHERE work_item_id=wid;
 IF after_row IS DISTINCT FROM before_row
    OR EXISTS(SELECT FROM autopilot.role_dispatch_followup WHERE parent_task_id=tid) THEN
   RAISE EXCEPTION '0368_STALE_REPAIR_TRIGGER_REGRESSED_LINEAGE';
 END IF;
 -- Positive full progression: the admitted head must also be executable.
 parent:=pg_temp.progress_fixture('positive-parent',999806);
 child:=pg_temp.progress_fixture('positive-child',999807);
 UPDATE autopilot.project_work_item SET depends_on_work_item_id=parent WHERE work_item_id=child;
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'WAITING_DEPENDENCY' THEN
   RAISE EXCEPTION '0368_POSITIVE_CHILD_NOT_WAITING';
 END IF;
 SELECT updated_at,last_task_id,task_spec_json INTO stamp,tid,origin_spec
 FROM autopilot.project_work_item WHERE work_item_id=parent;
 SELECT to_jsonb(t) INTO origin_task FROM autopilot.task t WHERE task_id=tid;
 action:=autopilot.reconcile_project_progress(parent,stamp,repeat('b',40));
 IF action<>'REAUDIT_READY' THEN RAISE EXCEPTION '0368_POSITIVE_ADMISSION_REJECTED'; END IF;
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=parent)<>'READY' THEN
   RAISE EXCEPTION '0368_POSITIVE_ADMISSION_NOT_READY';
 END IF;
 UPDATE autopilot.project_work_item SET probe_lease_owner='sql-positive-next-step',
   probe_lease_epoch=1,probe_lease_until=now()+interval '1 minute' WHERE work_item_id=parent;
 SELECT * INTO STRICT materialized FROM autopilot.materialize_project_work_probe(
   parent,'sql-positive-next-step',1,true,repeat('b',40));
 IF materialized.created IS DISTINCT FROM true OR materialized.resulting_state<>'ACTIVE'
    OR materialized.task_id IS NULL OR materialized.task_id=tid THEN
   RAISE EXCEPTION '0368_POSITIVE_AUTHORIZED_HEAD_NOT_MATERIALIZED';
 END IF;
 newer:=materialized.task_id;
 IF NOT EXISTS(SELECT FROM autopilot.task t WHERE t.task_id=newer
       AND t.goal_type='CHATGPT_ROLE_DISPATCH_V1' AND t.status='READY'
       AND t.goal_json->>'expected_head_sha'=repeat('b',40)
       AND t.goal_json->>'repository'='olegmed1-art/bridge-video-free'
       AND t.goal_json->>'target_pr'='999806' AND t.goal_json->>'role'='AUTOPILOT'
       AND t.acceptance_contract_json->'production_mutation'='false'::jsonb
       AND t.allowed_capabilities_json='["github.chatgpt.role.dispatch"]'::jsonb)
    OR NOT EXISTS(SELECT FROM autopilot.project_work_task WHERE work_item_id=parent
       AND task_id=newer AND run_kind='AUDIT')
    OR (SELECT task_spec_json FROM autopilot.project_work_item WHERE work_item_id=parent) IS DISTINCT FROM origin_spec
    OR (SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=tid) IS DISTINCT FROM origin_task
    OR (SELECT last_observed_head_sha FROM autopilot.project_work_item WHERE work_item_id=parent)<>repeat('b',40)
    OR (SELECT count(*) FROM autopilot.project_progress_receipt WHERE work_item_id=parent AND head_sha=repeat('b',40))<>1 THEN
   RAISE EXCEPTION '0368_POSITIVE_SCOPE_OR_LINEAGE_CHANGED';
 END IF;
 UPDATE autopilot.task SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
   safe_summary_json=jsonb_build_object('status','SUCCEEDED','result_code','EXACT_HEAD_AUDIT_PASSED',
     'target_head_sha',repeat('b',40),'summary','New-head audit verified no repair needed.'),completed_at=now()
 WHERE task_id=newer;
 IF NOT EXISTS(SELECT FROM autopilot.project_work_item WHERE work_item_id=parent
       AND state='DONE' AND last_task_id=newer AND last_observed_head_sha=repeat('b',40)
       AND result_code='EXACT_HEAD_AUDIT_PASSED' AND completed_at IS NOT NULL)
    OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=child)<>'READY'
    OR EXISTS(SELECT FROM autopilot.role_dispatch_followup WHERE parent_task_id=newer)
    OR (SELECT task_spec_json FROM autopilot.project_work_item WHERE work_item_id=parent) IS DISTINCT FROM origin_spec THEN
   RAISE EXCEPTION '0368_POSITIVE_COMPLETION_OR_DEPENDENCY_WAKE_FAILED';
 END IF;
END $test$;

SELECT '0368 independent trigger, CAS, owner, dependency and budget regressions passed' AS result;
ROLLBACK;
