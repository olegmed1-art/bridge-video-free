\set ON_ERROR_STOP on
BEGIN;
\ir fixtures/codex_send_fixture.sql

DO $test$
DECLARE active_id uuid; expired_id uuid; incompatible_id uuid;
 result record; n integer; terminal jsonb; delivery text:='github-codex-result:9372001';
 first_diag boolean; duplicate_diag boolean;
BEGIN
 IF NOT has_function_privilege('autopilot_callback',
    'autopilot.codex_terminal_readback_candidates()','EXECUTE')
 OR has_function_privilege('public',
    'autopilot.codex_terminal_readback_candidates()','EXECUTE')
 OR has_function_privilege('autopilot_runtime',
    'autopilot.codex_terminal_readback_candidates()','EXECUTE')
 OR NOT has_function_privilege('autopilot_callback',
    'autopilot.accept_role_dispatch_codex_rest_readback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)','EXECUTE')
 OR has_function_privilege('autopilot_callback',
    'autopilot.codex_rest_readback_terminal_core(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)','EXECUTE')
 OR has_function_privilege('autopilot_callback',
    'autopilot.codex_rest_readback_core_parity()','EXECUTE')
 OR has_function_privilege('public',
    'autopilot.accept_role_dispatch_codex_rest_readback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)','EXECUTE')
 OR NOT has_function_privilege('autopilot_callback',
    'autopilot.record_codex_rest_readback_diagnostic(uuid,text,bigint)','EXECUTE')
 OR has_table_privilege('autopilot_callback',
    'autopilot.codex_rest_readback_diagnostic','INSERT')
 OR has_table_privilege('autopilot_callback',
    'autopilot.role_dispatch_outbox','SELECT') THEN
  RAISE EXCEPTION '0372_ACL_BOUNDARY_INVALID';
 END IF;
 IF autopilot.codex_rest_readback_core_parity() IS DISTINCT FROM true THEN
  RAISE EXCEPTION '0372_EVENT_REST_CORE_PARITY_INVALID';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM information_schema.columns
   WHERE table_schema='autopilot' AND table_name='role_dispatch_codex_terminal_receipt'
     AND column_name='ingress_provenance') THEN
  RAISE EXCEPTION '0372_PROVENANCE_COLUMN_MISSING';
 END IF;
 active_id:=pg_temp.codex_send_fixture('readback-active');
 expired_id:=pg_temp.codex_send_fixture('readback-expired');
 incompatible_id:=pg_temp.codex_send_fixture('readback-incompatible');
 UPDATE autopilot.role_dispatch_outbox
    SET delivery_deadline_at=clock_timestamp()+interval '30 seconds'
  WHERE dispatch_id=expired_id;
 UPDATE autopilot.role_dispatch_outbox
    SET delivery_contract_version=4 WHERE dispatch_id=incompatible_id;
 SELECT count(*) INTO n FROM autopilot.codex_terminal_readback_candidates()
  WHERE dispatch_id=active_id;
 IF n<>1 OR EXISTS(SELECT 1 FROM autopilot.codex_terminal_readback_candidates()
   WHERE dispatch_id IN (expired_id,incompatible_id)) THEN
  RAISE EXCEPTION '0372_CANDIDATE_SCOPE_INVALID';
 END IF;
 SELECT * INTO result FROM autopilot.codex_terminal_readback_candidates()
  WHERE dispatch_id=active_id;
 IF result.target_pr<>999970 OR result.dispatch_epoch<>1
 OR result.role<>'AUTOPILOT' OR result.mode<>'READ_ONLY'
 OR result.task_fingerprint<>repeat('c',64)
 OR result.since_at IS NULL OR result.deadline_at<=clock_timestamp()+interval '60 seconds' THEN
  RAISE EXCEPTION '0372_CANDIDATE_BINDING_INVALID';
 END IF;
 first_diag:=autopilot.record_codex_rest_readback_diagnostic(
    active_id,'CODEX_REPAIR_HEAD_NOT_APPLIED',9372001);
 duplicate_diag:=autopilot.record_codex_rest_readback_diagnostic(
    active_id,'CODEX_REPAIR_HEAD_NOT_APPLIED',9372001);
 IF NOT first_diag OR duplicate_diag
 OR (SELECT count(*) FROM autopilot.codex_rest_readback_diagnostic
      WHERE dispatch_id=active_id AND reason_code='CODEX_REPAIR_HEAD_NOT_APPLIED')<>1 THEN
  RAISE EXCEPTION '0372_DIAGNOSTIC_NOT_DURABLE_OR_DUPLICATE';
 END IF;
 IF (SELECT count(*) FROM autopilot.codex_terminal_readback_candidates())>7 THEN
  RAISE EXCEPTION '0372_CAPACITY_UNBOUNDED';
 END IF;
 SELECT jsonb_build_object('dispatch_id',o.dispatch_id::text,
   'dispatch_epoch',o.dispatch_epoch,'role',o.role,
   'task_fingerprint',o.task_fingerprint,'target_pr',o.target_pr,
   'status','BLOCKED','result_code','READBACK_TEST_BLOCKED',
   'target_head_sha',o.expected_head_sha,
   'summary','Original terminal comment returned by authenticated GitHub API.')
 INTO terminal FROM autopilot.role_dispatch_outbox o WHERE o.dispatch_id=active_id;
 BEGIN
  PERFORM * FROM autopilot.accept_role_dispatch_codex_rest_readback(
   delivery,repeat('8',64),false,'olegmed1-art/bridge-video-free',999970,
   'chatgpt-codex-connector[bot]',199175422,'NONE',
   'chatgpt-codex-connector',1144995,terminal);
  RAISE EXCEPTION '0372_UNVERIFIED_REST_ACCEPTED';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'AUTOPILOT_CODEX_TERMINAL_IDENTITY_INVALID' THEN RAISE; END IF;
 END;
 SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_rest_readback(
  delivery,repeat('8',64),true,'olegmed1-art/bridge-video-free',999970,
  'chatgpt-codex-connector[bot]',199175422,'NONE',
  'chatgpt-codex-connector',1144995,terminal);
 IF NOT result.accepted OR result.duplicate OR result.resulting_state<>'FAILED_CLOSED'
 OR (SELECT ingress_provenance FROM autopilot.role_dispatch_codex_terminal_receipt
      WHERE delivery_id=delivery)<>'GITHUB_REST_DOUBLE_READ' THEN
  RAISE EXCEPTION '0372_REST_PROVENANCE_NOT_RETAINED';
 END IF;
 SELECT * INTO result FROM autopilot.accept_role_dispatch_codex_rest_readback(
  delivery,repeat('8',64),true,'olegmed1-art/bridge-video-free',999970,
  'chatgpt-codex-connector[bot]',199175422,'NONE',
  'chatgpt-codex-connector',1144995,terminal);
 IF result.accepted OR NOT result.duplicate
 OR (SELECT count(*) FROM autopilot.role_dispatch_codex_terminal_receipt
      WHERE delivery_id=delivery)<>1 THEN
  RAISE EXCEPTION '0372_REST_DUPLICATE_NOT_IDEMPOTENT';
 END IF;
END $test$;

ROLLBACK;
