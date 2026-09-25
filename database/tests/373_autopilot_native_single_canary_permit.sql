\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.canary_fixture(label text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE work_id uuid; probe record; materialized record; claimed record;
 dispatch record; published record; assignment jsonb; mailbox integer;
BEGIN
 SELECT mailbox_pr INTO STRICT mailbox FROM autopilot.role_dispatch_mailbox_registry
 WHERE lifecycle='ACTIVE';
 SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
  label,'AUTOPILOT','REPOSITORY_REPAIR','Native canary fixture.',mailbox,0,
  '{}'::jsonb,NULL,'database-test','SQL_TEST');
 SELECT * INTO probe FROM autopilot.claim_project_work_probe(label,60);
 SELECT * INTO materialized FROM autopilot.materialize_project_work_probe(
  work_id,label,probe.lease_epoch,true,repeat('a',40));
 SELECT * INTO claimed FROM autopilot.claim_next_task(label,60);
 IF claimed.task_id IS DISTINCT FROM materialized.task_id THEN RAISE EXCEPTION 'CANARY_TASK_CLAIM_INVALID'; END IF;
 SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(claimed.task_id,label,claimed.lease_epoch);
 SELECT * INTO published FROM autopilot.claim_role_dispatch_outbox_v2(label,60);
 PERFORM autopilot.mark_role_dispatch_published(dispatch.dispatch_id,label,
  published.claim_epoch,9900373,repeat('b',64));
 SELECT to_jsonb(x) INTO assignment FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id) x;
 RETURN jsonb_build_object('dispatch_id',dispatch.dispatch_id,'assignment',assignment);
END $$;

DO $$ DECLARE fixture jsonb; other jsonb; id uuid; reservation jsonb; request jsonb;
 terminal jsonb; conflict boolean;
BEGIN
 IF EXISTS(SELECT FROM autopilot.native_cli_single_canary_permit)
 OR (SELECT enabled FROM autopilot.native_cli_config) THEN
  RAISE EXCEPTION 'CANARY_NOT_DISABLED_BY_DEFAULT';
 END IF;
 IF has_table_privilege('autopilot_runtime','autopilot.native_cli_single_canary_permit','INSERT')
 OR has_function_privilege('autopilot_runtime','autopilot.native_cli_begin_canary(jsonb)','EXECUTE')
 OR has_function_privilege('autopilot_runtime','autopilot.native_cli_reserve_canary(uuid,jsonb,text)','EXECUTE') THEN
  RAISE EXCEPTION 'CANARY_ACL_TOO_BROAD';
 END IF;

 fixture:=pg_temp.canary_fixture('sql-0373-one-shot');
 id:=(fixture->>'dispatch_id')::uuid;
 INSERT INTO autopilot.native_cli_single_canary_permit(
  dispatch_id,target_pr,expected_head_sha,expires_at)
 SELECT dispatch_id,target_pr,expected_head_sha,clock_timestamp()+interval '10 minutes'
 FROM autopilot.role_dispatch_outbox WHERE dispatch_id=id;

 -- The permit cannot override the disabled 0339 runtime switch.
 BEGIN
  PERFORM autopilot.native_cli_reserve_canary(id,fixture->'assignment','codex/sql-0373');
  RAISE EXCEPTION 'CANARY_DISABLED_CONFIG_ACCEPTED';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM IS DISTINCT FROM 'NATIVE_RESERVATION_INVALID' THEN RAISE; END IF;
 END;
 UPDATE autopilot.native_cli_config SET enabled=true,
  cutover_at=clock_timestamp()-interval '1 hour';
 reservation:=autopilot.native_cli_reserve_canary(id,fixture->'assignment','codex/sql-0373');
 IF reservation IS DISTINCT FROM autopilot.native_cli_reserve_canary(
     id,fixture->'assignment','codex/sql-0373') THEN
  RAISE EXCEPTION 'CANARY_RESERVATION_REPLAY_CHANGED';
 END IF;
 request:=reservation->'request';
 UPDATE autopilot.native_cli_single_canary_permit SET revoked=true WHERE dispatch_id=id;
 IF autopilot.native_cli_canary_current(request) THEN
  RAISE EXCEPTION 'CANARY_REVOKED_BEFORE_SUBMIT_RETAINED_AUTHORITY';
 END IF;
 conflict:=false;
 BEGIN
  PERFORM autopilot.native_cli_begin_canary(request);
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM IS DISTINCT FROM 'NATIVE_CANARY_PERMIT_INVALID' THEN RAISE; END IF;
  conflict:=true;
 END;
 IF NOT conflict THEN RAISE EXCEPTION 'CANARY_REVOKED_BEFORE_BEGIN_ACCEPTED'; END IF;
 UPDATE autopilot.native_cli_single_canary_permit SET revoked=false WHERE dispatch_id=id;
 IF NOT autopilot.native_cli_canary_current(request)
 OR NOT autopilot.native_cli_begin_canary(request) THEN
  RAISE EXCEPTION 'CANARY_BEGIN_DENIED';
 END IF;
 conflict:=false;
 BEGIN
  PERFORM autopilot.native_cli_begin_canary(request);
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM IS DISTINCT FROM 'NATIVE_CANARY_PERMIT_INVALID' THEN RAISE; END IF;
  conflict:=true;
 END;
 IF NOT conflict THEN RAISE EXCEPTION 'CANARY_SECOND_BEGIN_ALLOWED'; END IF;
 PERFORM autopilot.native_cli_ack(request,'task_e_sql373',repeat('c',64));
 UPDATE autopilot.native_cli_single_canary_permit SET revoked=true WHERE dispatch_id=id;
 IF NOT autopilot.native_cli_canary_current(request) THEN
  RAISE EXCEPTION 'CANARY_SUBMITTED_CANNOT_DRAIN';
 END IF;

 -- Revocation never replenishes the one-attempt budget.
 other:=pg_temp.canary_fixture('sql-0373-revoked-budget');
 conflict:=false;
 BEGIN
  INSERT INTO autopilot.native_cli_single_canary_permit(
   dispatch_id,target_pr,expected_head_sha,expires_at)
  SELECT dispatch_id,target_pr,expected_head_sha,clock_timestamp()+interval '10 minutes'
  FROM autopilot.role_dispatch_outbox WHERE dispatch_id=(other->>'dispatch_id')::uuid;
 EXCEPTION WHEN unique_violation THEN conflict:=true;
 END;
 IF NOT conflict THEN RAISE EXCEPTION 'CANARY_REISSUED_AFTER_REVOKE'; END IF;

 terminal:=jsonb_build_object('status','SUCCEEDED','result_code','VERIFIED',
  'summary','Scoped native SQL canary.','target_head_sha',repeat('a',40),
  'provider_evidence_sha256',repeat('d',64));
 IF autopilot.native_cli_finish(request,'task_e_sql373',terminal)->>'state'<>'TERMINAL' THEN
  RAISE EXCEPTION 'CANARY_TERMINAL_NOT_RETAINED';
 END IF;
END $$;
ROLLBACK;
