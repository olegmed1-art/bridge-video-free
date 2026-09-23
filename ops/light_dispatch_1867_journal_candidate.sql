-- Candidate administrative procedure, intentionally restricted to the disposable
-- rehearsal branch. This is NOT the production deployment/activation command.
-- Install once, as database owner. No runtime grants or network calls.
DO $$ BEGIN
 IF current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-holy-term-b1k8s9qe' THEN
  RAISE EXCEPTION 'RECOVERY_CANDIDATE_REHEARSAL_BRANCH_REQUIRED';
 END IF;
END $$;

CREATE TABLE autopilot.light_dispatch_1867_journal (
 action text PRIMARY KEY CHECK(action IN ('APPLY','ROLLBACK')),
 before_image jsonb NOT NULL CHECK(jsonb_typeof(before_image)='object'),
 after_image jsonb NOT NULL CHECK(jsonb_typeof(after_image)='object'),
 evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON autopilot.light_dispatch_1867_journal FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;
CREATE TRIGGER light_dispatch_1867_journal_immutable
 BEFORE UPDATE OR DELETE ON autopilot.light_dispatch_1867_journal
 FOR EACH ROW EXECUTE FUNCTION autopilot.prevent_immutable_change();

CREATE FUNCTION autopilot.light_dispatch_1867_snapshot() RETURNS jsonb
 LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('task',to_jsonb(t),'outbox',to_jsonb(o),
  'step',to_jsonb(s),'work',to_jsonb(w))
 FROM autopilot.task t JOIN autopilot.role_dispatch_outbox o USING(task_id)
 JOIN autopilot.step_attempt s USING(step_attempt_id)
 JOIN autopilot.project_work_task m ON m.task_id=t.task_id
 JOIN autopilot.project_work_item w USING(work_item_id)
 WHERE t.task_id='f05c605f-f664-4ff7-9927-a039f000a929'
 AND o.dispatch_id='322dd440-30b9-49d2-8e1a-f5ecc1d2b99b'
 AND s.step_attempt_id='7e45dcdd-ee49-4c38-930c-14e36a70a255'
 AND w.work_item_id='714feeda-d4c4-48b1-8d07-26427d343a7b'
$$;

-- Pure predicate: explicit JSON nulls must be present; numbers cannot be strings.
-- The predicate is also exercised on JSON copies by the read-only regression.
CREATE FUNCTION autopilot.light_dispatch_1867_input_valid(v jsonb) RETURNS boolean
 LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
 SELECT COALESCE(v @> '{
  "task": {
   "task_id":"f05c605f-f664-4ff7-9927-a039f000a929",
   "status":"FAILED_CLOSED","attempts":1,
   "terminal_reason_code":"ROLE_DISPATCH_DELIVERY_EXHAUSTED",
   "goal_json":{"expected_head_sha":"2586929313ab40326d64353b513ff86e5ae3350c"},
   "safe_summary_json":{"result_code":"ROLE_DISPATCH_RESPONSE_INVALID"}
  },
  "outbox": {
   "dispatch_id":"322dd440-30b9-49d2-8e1a-f5ecc1d2b99b",
   "task_id":"f05c605f-f664-4ff7-9927-a039f000a929",
   "step_attempt_id":"7e45dcdd-ee49-4c38-930c-14e36a70a255",
   "status":"FAILED_CLOSED","attempts":5,"max_attempts":5,
   "claim_epoch":5,"claim_owner":null,"claim_until":null,
   "mailbox_pr":1703,"delivery_contract_version":3,
   "repository":"olegmed1-art/bridge-video-free","role":"AUTOPILOT",
   "target_pr":1769,"mode":"READ_ONLY","dispatch_epoch":1,
   "expected_head_sha":"2586929313ab40326d64353b513ff86e5ae3350c",
   "task_fingerprint":"6c67171ad333f5ca6b19bc7faac712fbf98440aaec639191a4c46a9f2182add0",
   "last_error_code":"ROLE_DISPATCH_RESPONSE_INVALID",
   "prepared_by":"oracle-autopilot-light-1","repair_attempt":0,
   "executor_id":null,"prior_task_id":null,"origin_task_id":null,
   "target_chat_id":null,"target_chat_name":null,
   "blocked_result_code":null,"blocked_summary":null,
   "sent_at":null,"published_at":null,"delivered_at":null,
   "github_dispatch_comment_id":null,"dispatch_body_sha256":null,
   "codex_command_pr":null,"codex_command_comment_id":null,
   "codex_ack_reaction_id":null,"codex_ack_at":null,
   "callback_deadline_at":null,"delivery_deadline_at":null
  },
  "step": {
   "step_attempt_id":"7e45dcdd-ee49-4c38-930c-14e36a70a255",
   "status":"FAILED_CLOSED","error_code":"ROLE_DISPATCH_DELIVERY_EXHAUSTED"
  },
  "work": {
   "work_item_id":"714feeda-d4c4-48b1-8d07-26427d343a7b",
   "state":"BLOCKED","last_task_id":"f05c605f-f664-4ff7-9927-a039f000a929",
   "hold_reason":null,"result_code":"ROLE_DISPATCH_RESPONSE_INVALID"
  }
 }'::jsonb AND v#>>'{task,safe_summary_json,status}' IS NULL,false)
$$;
REVOKE ALL ON FUNCTION autopilot.light_dispatch_1867_input_valid(jsonb) FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;

CREATE FUNCTION autopilot.light_dispatch_1867_recover(p_action text,p_evidence jsonb)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE
 tid constant uuid := 'f05c605f-f664-4ff7-9927-a039f000a929';
 did constant uuid := '322dd440-30b9-49d2-8e1a-f5ecc1d2b99b';
 wid constant uuid := '714feeda-d4c4-48b1-8d07-26427d343a7b';
 sid constant uuid := '7e45dcdd-ee49-4c38-930c-14e36a70a255';
 original jsonb; current_image jsonb; after_image jsonb; previous_after jsonb;
 receipt_count integer; binding jsonb;
BEGIN
 IF current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-holy-term-b1k8s9qe'
 OR current_user IS DISTINCT FROM pg_get_userbyid((SELECT relowner FROM pg_class
    WHERE oid='autopilot.light_dispatch_1867_journal'::regclass)) THEN
  RAISE EXCEPTION 'RECOVERY_CANDIDATE_REHEARSAL_OWNER_REQUIRED';
 END IF;
 IF p_action IS NULL OR p_action NOT IN ('APPLY','ROLLBACK') OR p_evidence IS NULL
 OR jsonb_typeof(p_evidence) IS DISTINCT FROM 'object'
 OR p_evidence->>'scope' IS DISTINCT FROM 'ISOLATED_REHEARSAL'
 OR octet_length(p_evidence::text)>8192 THEN
  RAISE EXCEPTION 'RECOVERY_CANDIDATE_INPUT_INVALID';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('light-dispatch-1867-journal',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
 PERFORM 1 FROM autopilot.role_dispatch_outbox WHERE dispatch_id=did FOR UPDATE;
 PERFORM 1 FROM autopilot.task WHERE task_id=tid FOR UPDATE;
 PERFORM 1 FROM autopilot.project_work_item WHERE work_item_id=wid FOR UPDATE;
 PERFORM 1 FROM autopilot.step_attempt WHERE step_attempt_id=sid FOR UPDATE;
 IF EXISTS(SELECT FROM autopilot.light_dispatch_1867_journal WHERE action=p_action) THEN
  RAISE EXCEPTION 'RECOVERY_CANDIDATE_ACTION_ALREADY_USED';
 END IF;
 SELECT count(*) INTO receipt_count FROM (
  SELECT dispatch_id FROM autopilot.role_dispatch_delivery_proof
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_codex_delivery_proof
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_codex_terminal_receipt
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_callback_receipt
  UNION ALL SELECT dispatch_id FROM autopilot.codex_command_send_intent
  UNION ALL SELECT dispatch_id FROM autopilot.codex_publication_permit
  UNION ALL SELECT dispatch_id FROM autopilot.native_cli_receipt
 ) r WHERE dispatch_id=did;
 IF receipt_count<>0 THEN RAISE EXCEPTION 'RECOVERY_CANDIDATE_RECEIPT_EXISTS'; END IF;
 current_image:=autopilot.light_dispatch_1867_snapshot();
 IF current_image IS NULL THEN RAISE EXCEPTION 'RECOVERY_CANDIDATE_LINEAGE_MISSING'; END IF;
 IF p_action='APPLY' THEN
  IF NOT autopilot.light_dispatch_1867_input_valid(current_image) THEN
   RAISE EXCEPTION 'RECOVERY_CANDIDATE_STATE_DRIFT';
  END IF;
  IF EXISTS(SELECT FROM autopilot.role_dispatch_outbox
    WHERE repository='olegmed1-art/bridge-video-free' AND target_pr=1769
    AND dispatch_id<>did AND status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')) THEN
   RAISE EXCEPTION 'RECOVERY_CANDIDATE_CONFLICTING_DISPATCH';
  END IF;
  UPDATE autopilot.project_work_item SET state='ACTIVE',result_code=NULL,
   result_summary=NULL,not_before=now(),updated_at=now() WHERE work_item_id=wid;
  UPDATE autopilot.task SET status='WAITING_EXTERNAL',terminal_reason_code=NULL,
   completed_at=NULL WHERE task_id=tid;
  UPDATE autopilot.step_attempt SET status='WAITING_EXTERNAL',error_code=NULL,
   completed_at=NULL WHERE step_attempt_id=sid;
  UPDATE autopilot.role_dispatch_outbox SET status='CLAIMED',claim_epoch=6,
   claim_owner='journal-recovery-rehearsal',claim_until=now()+interval '1 minute',
   completed_at=NULL WHERE dispatch_id=did;
  IF NOT autopilot.mark_role_dispatch_published(did,'journal-recovery-rehearsal',6,1867,
    '331fcde86d72d5195064dd1e9263596147419980b7ef9035a7ebcf27da09cc5f') THEN
   RAISE EXCEPTION 'RECOVERY_CANDIDATE_PUBLICATION_REJECTED';
  END IF;
  binding:=autopilot.codex_command_send_binding(did);
  IF binding IS NULL OR binding->>'dispatch_pr' IS DISTINCT FROM '1867'
   OR binding->>'expected_head_sha' IS DISTINCT FROM '2586929313ab40326d64353b513ff86e5ae3350c' THEN
   RAISE EXCEPTION 'RECOVERY_CANDIDATE_BINDING_REJECTED';
  END IF;
 ELSE
  SELECT j.before_image,j.after_image INTO original,previous_after
   FROM autopilot.light_dispatch_1867_journal j WHERE action='APPLY';
  IF original IS NULL OR current_image IS DISTINCT FROM previous_after THEN
   RAISE EXCEPTION 'RECOVERY_CANDIDATE_ROLLBACK_DRIFT';
  END IF;
  UPDATE autopilot.task t SET status=b.status,terminal_reason_code=b.terminal_reason_code,
   completed_at=b.completed_at FROM jsonb_populate_record(NULL::autopilot.task,original->'task') b
   WHERE t.task_id=tid;
  UPDATE autopilot.step_attempt s SET status=b.status,error_code=b.error_code,completed_at=b.completed_at
   FROM jsonb_populate_record(NULL::autopilot.step_attempt,original->'step') b WHERE s.step_attempt_id=sid;
  UPDATE autopilot.role_dispatch_outbox o SET status=b.status,claim_epoch=b.claim_epoch,
   claim_owner=b.claim_owner,claim_until=b.claim_until,completed_at=b.completed_at,
   github_dispatch_comment_id=b.github_dispatch_comment_id,dispatch_body_sha256=b.dispatch_body_sha256,
   published_at=b.published_at,delivery_deadline_at=b.delivery_deadline_at,
   last_error_code=b.last_error_code,updated_at=now()
   FROM jsonb_populate_record(NULL::autopilot.role_dispatch_outbox,original->'outbox') b
   WHERE o.dispatch_id=did;
  UPDATE autopilot.project_work_item w SET state=b.state,result_code=b.result_code,
   result_summary=b.result_summary,not_before=b.not_before,updated_at=now()
   FROM jsonb_populate_record(NULL::autopilot.project_work_item,original->'work') b
   WHERE w.work_item_id=wid;
 END IF;
 after_image:=autopilot.light_dispatch_1867_snapshot();
 INSERT INTO autopilot.light_dispatch_1867_journal(action,before_image,after_image,evidence)
 VALUES(p_action,current_image,after_image,p_evidence);
 PERFORM autopilot.record_event(tid,'ADMIN_RECOVERY_'||p_action,
  current_image#>>'{task,status}',after_image#>>'{task,status}',
  jsonb_build_object('dispatch_id',did,'dispatch_pr',1867,'journal','light_dispatch_1867_journal'),
  'SYSTEM','isolated-recovery-rehearsal','light-dispatch-1867-journal:'||p_action);
 RETURN jsonb_build_object('action',p_action,'task_state',after_image#>>'{task,status}',
  'outbox_state',after_image#>>'{outbox,status}','attempts',after_image#>>'{outbox,attempts}');
END $$;
REVOKE ALL ON FUNCTION autopilot.light_dispatch_1867_snapshot() FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;
REVOKE ALL ON FUNCTION autopilot.light_dispatch_1867_recover(text,jsonb) FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;
