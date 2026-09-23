-- Read-only structural gate for the existing canary. This does not authorize
-- deployment, recovery, publication or activation. Missing rows fail closed.
WITH candidate AS (
 SELECT t.status AS task_status, t.attempts AS task_attempts,
        t.goal_json->>'expected_head_sha' AS task_head, t.terminal_reason_code,
        o.*, s.status AS step_status, s.error_code AS step_error
 FROM autopilot.task t
 JOIN autopilot.role_dispatch_outbox o USING(task_id)
 JOIN autopilot.step_attempt s USING(step_attempt_id)
 WHERE t.task_id='f05c605f-f664-4ff7-9927-a039f000a929'
), receipts AS (
 SELECT count(*) AS n FROM (
  SELECT dispatch_id FROM autopilot.role_dispatch_delivery_proof
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_codex_delivery_proof
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_codex_terminal_receipt
  UNION ALL SELECT dispatch_id FROM autopilot.role_dispatch_callback_receipt
  UNION ALL SELECT dispatch_id FROM autopilot.codex_command_send_intent
  UNION ALL SELECT dispatch_id FROM autopilot.codex_publication_permit
 ) r WHERE dispatch_id='322dd440-30b9-49d2-8e1a-f5ecc1d2b99b'
), conflicts AS (
 SELECT count(*) AS n FROM autopilot.role_dispatch_outbox
 WHERE repository='olegmed1-art/bridge-video-free' AND target_pr=1769
   AND dispatch_id<>'322dd440-30b9-49d2-8e1a-f5ecc1d2b99b'
   AND status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
)
SELECT COALESCE((SELECT
 task_status='FAILED_CLOSED' AND task_attempts=1
 AND task_head='2586929313ab40326d64353b513ff86e5ae3350c'
 AND terminal_reason_code='ROLE_DISPATCH_DELIVERY_EXHAUSTED'
 AND dispatch_id='322dd440-30b9-49d2-8e1a-f5ecc1d2b99b'
 AND status='FAILED_CLOSED' AND attempts=5 AND max_attempts=5
 AND claim_epoch=5 AND claim_owner IS NULL AND claim_until IS NULL
 AND mailbox_pr=1703 AND delivery_contract_version=3
 AND repository='olegmed1-art/bridge-video-free' AND role='AUTOPILOT'
 AND target_pr=1769 AND mode='READ_ONLY' AND dispatch_epoch=1
 AND expected_head_sha=task_head
 AND task_fingerprint='6c67171ad333f5ca6b19bc7faac712fbf98440aaec639191a4c46a9f2182add0'
 AND last_error_code='ROLE_DISPATCH_RESPONSE_INVALID'
 AND step_status='FAILED_CLOSED' AND step_error='ROLE_DISPATCH_DELIVERY_EXHAUSTED'
 AND sent_at IS NULL AND published_at IS NULL AND delivered_at IS NULL
 AND github_dispatch_comment_id IS NULL AND dispatch_body_sha256 IS NULL
 AND codex_command_pr IS NULL AND codex_command_comment_id IS NULL
 AND codex_ack_reaction_id IS NULL AND codex_ack_at IS NULL
 AND callback_deadline_at IS NULL AND delivery_deadline_at IS NULL
 FROM candidate),false) AND (SELECT n=0 FROM receipts)
 AND (SELECT n=0 FROM conflicts) AS structural_preflight_pass,
 (SELECT count(*) FROM candidate) AS candidate_count,
 (SELECT n FROM receipts) AS receipt_count,
 (SELECT n FROM conflicts) AS conflicting_active_dispatches,
 false AS activation_authorized;
