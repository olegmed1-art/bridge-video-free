-- REHEARSAL ONLY: hard-fenced to an expiring Neon child. Never run on production.
-- All row changes, publication binding and synthetic send intent are rolled back
-- by the inner subtransaction. This does not call GitHub or the broker.
DO $rehearsal$
DECLARE
 tid constant uuid := 'f05c605f-f664-4ff7-9927-a039f000a929';
 did constant uuid := '322dd440-30b9-49d2-8e1a-f5ecc1d2b99b';
 wid constant uuid := '714feeda-d4c4-48b1-8d07-26427d343a7b';
 sid constant uuid := '7e45dcdd-ee49-4c38-930c-14e36a70a255';
 before_task jsonb; before_outbox jsonb; before_step jsonb; before_work jsonb;
 binding jsonb; receipt_count integer;
BEGIN
 IF current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-holy-term-b1k8s9qe' THEN
  RAISE EXCEPTION 'RECOVERY_REHEARSAL_WRONG_BRANCH';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('light-dispatch-1867-rehearsal',0));
 PERFORM 1 FROM autopilot.role_dispatch_outbox WHERE dispatch_id=did FOR UPDATE;
 PERFORM 1 FROM autopilot.task WHERE task_id=tid FOR UPDATE;
 PERFORM 1 FROM autopilot.project_work_item WHERE work_item_id=wid FOR UPDATE;
 PERFORM 1 FROM autopilot.step_attempt WHERE step_attempt_id=sid FOR UPDATE;
 SELECT to_jsonb(t) INTO before_task FROM autopilot.task t WHERE task_id=tid;
 SELECT to_jsonb(o) INTO before_outbox FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=did;
 SELECT to_jsonb(s) INTO before_step FROM autopilot.step_attempt s WHERE step_attempt_id=sid;
 SELECT to_jsonb(w) INTO before_work FROM autopilot.project_work_item w WHERE work_item_id=wid;
 IF before_task IS NULL OR before_outbox IS NULL OR before_step IS NULL OR before_work IS NULL
 OR before_task->>'status' IS DISTINCT FROM 'FAILED_CLOSED'
 OR before_task->>'attempts' IS DISTINCT FROM '1'
 OR before_task->'goal_json'->>'expected_head_sha' IS DISTINCT FROM '2586929313ab40326d64353b513ff86e5ae3350c'
 OR before_outbox->>'status' IS DISTINCT FROM 'FAILED_CLOSED'
 OR before_outbox->>'attempts' IS DISTINCT FROM '5'
 OR before_outbox->>'claim_epoch' IS DISTINCT FROM '5'
 OR before_outbox->>'mailbox_pr' IS DISTINCT FROM '1703'
 OR before_outbox->>'last_error_code' IS DISTINCT FROM 'ROLE_DISPATCH_RESPONSE_INVALID'
 OR before_outbox->>'github_dispatch_comment_id' IS NOT NULL
 OR before_outbox->>'sent_at' IS NOT NULL
 OR before_outbox->>'codex_command_comment_id' IS NOT NULL
 OR before_outbox->>'codex_ack_at' IS NOT NULL
 OR before_work->>'state' IS DISTINCT FROM 'BLOCKED'
 OR before_work->>'last_task_id' IS DISTINCT FROM tid::text
 OR before_work->>'result_code' IS DISTINCT FROM 'ROLE_DISPATCH_RESPONSE_INVALID'
 OR before_work->>'hold_reason' IS NOT NULL THEN
  RAISE EXCEPTION 'RECOVERY_REHEARSAL_INPUT_DRIFT';
 END IF;
 SELECT count(*) INTO receipt_count FROM (
  SELECT dispatch_id FROM autopilot.role_dispatch_delivery_proof
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_codex_delivery_proof
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_codex_terminal_receipt
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_callback_receipt
  UNION ALL SELECT dispatch_id FROM autopilot.codex_command_send_intent
  UNION ALL SELECT dispatch_id FROM autopilot.codex_publication_permit
 ) r WHERE dispatch_id=did;
 IF receipt_count<>0 THEN RAISE EXCEPTION 'RECOVERY_REHEARSAL_RECEIPT_EXISTS'; END IF;
 BEGIN
  -- A production implementation must durably archive these four before-images
  -- and record an administrative recovery event before performing transitions.
  UPDATE autopilot.project_work_item SET state='ACTIVE',result_code=NULL,
   result_summary=NULL,not_before=now(),updated_at=now() WHERE work_item_id=wid;
  UPDATE autopilot.task SET status='WAITING_EXTERNAL',terminal_reason_code=NULL,
   completed_at=NULL WHERE task_id=tid;
  UPDATE autopilot.step_attempt SET status='WAITING_EXTERNAL',error_code=NULL,
   completed_at=NULL WHERE step_attempt_id=sid;
  -- Preserve attempts=5. A temporary claim is used only to exercise the existing
  -- publication RPC for the already-created PR; no publishing call is made.
  UPDATE autopilot.role_dispatch_outbox SET status='CLAIMED',
   claim_owner='isolated-recovery-rehearsal',claim_epoch=6,
   claim_until=now()+interval '1 minute',completed_at=NULL WHERE dispatch_id=did;
  IF NOT autopilot.mark_role_dispatch_published(did,'isolated-recovery-rehearsal',6,1867,
    '331fcde86d72d5195064dd1e9263596147419980b7ef9035a7ebcf27da09cc5f') THEN
   RAISE EXCEPTION 'RECOVERY_REHEARSAL_PUBLICATION_REJECTED';
  END IF;
  binding:=autopilot.codex_command_send_binding(did);
  IF binding IS NULL OR binding->>'dispatch_pr' IS DISTINCT FROM '1867'
   OR binding->>'task_id' IS DISTINCT FROM tid::text
   OR binding->>'expected_head_sha' IS DISTINCT FROM '2586929313ab40326d64353b513ff86e5ae3350c'
   OR binding->>'mode' IS DISTINCT FROM 'READ_ONLY'
   OR (SELECT attempts FROM autopilot.role_dispatch_outbox WHERE dispatch_id=did)<>5 THEN
   RAISE EXCEPTION 'RECOVERY_REHEARSAL_BINDING_INVALID';
  END IF;
  IF NOT autopilot.claim_codex_command_send(did,'0f000000-0000-4000-8000-000000001867',binding,repeat('0',64))
   OR autopilot.claim_codex_command_send(did,'0f000000-0000-4000-8000-000000001867',binding,repeat('0',64))
   OR autopilot.claim_codex_command_send(did,'0f000000-0000-4000-8000-000000001868',binding,repeat('0',64)) THEN
   RAISE EXCEPTION 'RECOVERY_REHEARSAL_SINGLE_SEND_FAILED';
  END IF;
  IF (SELECT sent_at IS NOT NULL OR codex_ack_at IS NOT NULL
      FROM autopilot.role_dispatch_outbox WHERE dispatch_id=did) THEN
   RAISE EXCEPTION 'RECOVERY_REHEARSAL_SYNTHETIC_DELIVERY';
  END IF;
  RAISE EXCEPTION USING ERRCODE='Z1867',MESSAGE='ROLLBACK_SUCCESSFUL_REHEARSAL';
 EXCEPTION WHEN SQLSTATE 'Z1867' THEN NULL;
 END;
 IF (SELECT to_jsonb(t) FROM autopilot.task t WHERE task_id=tid) IS DISTINCT FROM before_task
 OR (SELECT to_jsonb(o) FROM autopilot.role_dispatch_outbox o WHERE dispatch_id=did) IS DISTINCT FROM before_outbox
 OR (SELECT to_jsonb(s) FROM autopilot.step_attempt s WHERE step_attempt_id=sid) IS DISTINCT FROM before_step
 OR (SELECT to_jsonb(w) FROM autopilot.project_work_item w WHERE work_item_id=wid) IS DISTINCT FROM before_work
 OR EXISTS(SELECT FROM autopilot.codex_command_send_intent WHERE dispatch_id=did) THEN
  RAISE EXCEPTION 'RECOVERY_REHEARSAL_ROLLBACK_MISMATCH';
 END IF;
END $rehearsal$;
