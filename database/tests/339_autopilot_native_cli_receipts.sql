\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.native_fixture(label text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE work_id uuid; probe record; materialized record; claimed record; dispatch record; published record; a jsonb;
BEGIN
 SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
  label,'AUTOPILOT','REPOSITORY_REPAIR','Native SQL fixture.',1150,0,'{}'::jsonb,NULL,'database-test','SQL_TEST');
 SELECT * INTO probe FROM autopilot.claim_project_work_probe(label,60);
 SELECT * INTO materialized FROM autopilot.materialize_project_work_probe(work_id,label,probe.lease_epoch,true,repeat('a',40));
 SELECT * INTO claimed FROM autopilot.claim_next_task(label,60);
 IF claimed.task_id IS DISTINCT FROM materialized.task_id THEN RAISE EXCEPTION 'TEST_NATIVE_TASK_NOT_CLAIMED'; END IF;
 SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(claimed.task_id,label,claimed.lease_epoch);
 SELECT * INTO published FROM autopilot.claim_role_dispatch_outbox_v2(label,60);
 PERFORM autopilot.mark_role_dispatch_published(dispatch.dispatch_id,label,published.claim_epoch,9900339,repeat('b',64));
 SELECT to_jsonb(x) INTO a FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id) x;
 RETURN jsonb_build_object('id',dispatch.dispatch_id,'assignment',a,'work_id',work_id,'task_id',claimed.task_id);
END $$;

CREATE FUNCTION pg_temp.native_reject(sql text, expected text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 BEGIN
  EXECUTE sql;
 EXCEPTION WHEN OTHERS THEN
  IF SQLERRM<>expected THEN RAISE; END IF;
  RETURN;
 END;
 RAISE EXCEPTION 'TEST_NATIVE_REJECTION_MISSING: %',expected;
END $$;

DO $$ DECLARE f jsonb; r jsonb; req jsonb; result jsonb; after_result jsonb; id uuid; work_id uuid; evidence_count integer; key text;
BEGIN
 IF (SELECT enabled FROM autopilot.native_cli_config) THEN RAISE EXCEPTION 'TEST_NATIVE_ENABLED_BY_DEFAULT'; END IF;
 f:=pg_temp.native_fixture('native-sql-success-339'); id:=(f->>'id')::uuid; work_id:=(f->>'work_id')::uuid;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_reserve(%L,%L,%L)',id,f->'assignment','codex/native-test'),
  'NATIVE_RESERVATION_INVALID');
 UPDATE autopilot.native_cli_config SET enabled=true,cutover_at=clock_timestamp()+interval '1 hour';
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_reserve(%L,%L,%L)',id,f->'assignment','codex/native-test'),
  'NATIVE_RESERVATION_INVALID');
 UPDATE autopilot.native_cli_config SET cutover_at=clock_timestamp()-interval '1 hour';
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_reserve(%L,NULL,%L)',id,'codex/native-test'),
  'NATIVE_RESERVATION_INVALID');
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_reserve(%L,%L,NULL)',id,f->'assignment'),
  'NATIVE_RESERVATION_INVALID');
 UPDATE autopilot.project_work_item SET state='PAUSED' WHERE work_item_id=work_id;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_reserve(%L,%L,%L)',id,f->'assignment','codex/native-test'),
  'NATIVE_RESERVATION_INVALID');
 UPDATE autopilot.project_work_item SET state='ACTIVE' WHERE work_item_id=work_id;
 r:=autopilot.native_cli_reserve(id,f->'assignment','codex/native-test'); req:=r->'request';
 UPDATE autopilot.native_cli_config SET enabled=false;
 IF autopilot.native_cli_begin(req) THEN RAISE EXCEPTION 'TEST_NATIVE_DISABLED_SUBMIT_ALLOWED'; END IF;
 UPDATE autopilot.native_cli_config SET enabled=true;
 UPDATE autopilot.native_cli_receipt SET owner_name='different_session_owner' WHERE dispatch_id=id;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_snapshot(%L)',id),'NATIVE_RESERVATION_NOT_OWNED');
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_begin(%L)',req),'NATIVE_BEGIN_NOT_OWNED');
 UPDATE autopilot.native_cli_receipt SET owner_name=SESSION_USER WHERE dispatch_id=id;
 IF r IS DISTINCT FROM autopilot.native_cli_reserve(id,f->'assignment','codex/native-test') THEN
  RAISE EXCEPTION 'TEST_NATIVE_RESERVE_REPLAY';
 END IF;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_reserve(%L,%L,%L)',id,f->'assignment','codex/other'),
  'NATIVE_RESERVATION_CONFLICT');
 UPDATE autopilot.role_dispatch_outbox SET delivery_deadline_at=clock_timestamp()-interval '1 hour' WHERE dispatch_id=id;
 PERFORM autopilot.reconcile_role_dispatch_callbacks();
 IF (SELECT status FROM autopilot.role_dispatch_outbox WHERE dispatch_id=id)<>'PUBLISHED' THEN
  RAISE EXCEPTION 'TEST_NATIVE_UNKNOWN_RELEASED';
 END IF;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_ack(%L,NULL,%L)',req,repeat('c',64)),'NATIVE_ACK_INVALID');
 IF NOT autopilot.native_cli_begin(req) OR autopilot.native_cli_begin(req) THEN RAISE EXCEPTION 'TEST_NATIVE_BEGIN_NOT_ONESHOT'; END IF;
 r:=autopilot.native_cli_ack(req,'task_e_sql339_success',repeat('c',64));
 IF r IS DISTINCT FROM autopilot.native_cli_ack(req,'task_e_sql339_success',repeat('c',64)) THEN
  RAISE EXCEPTION 'TEST_NATIVE_ACK_REPLAY';
 END IF;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_ack(%L,%L,%L)',req,'task_e_other',repeat('c',64)),
  'NATIVE_ACK_CONFLICT');
 UPDATE autopilot.role_dispatch_outbox SET callback_deadline_at=clock_timestamp()-interval '1 hour' WHERE dispatch_id=id;
 PERFORM autopilot.reconcile_role_dispatch_callbacks();
 IF (SELECT status FROM autopilot.role_dispatch_outbox WHERE dispatch_id=id)<>'SENT' THEN
  RAISE EXCEPTION 'TEST_NATIVE_RUNNING_RELEASED';
 END IF;
 result:=jsonb_build_object('status','SUCCEEDED','result_code','VERIFIED','summary','SQL fixture verified.',
  'target_head_sha',repeat('a',40),'provider_evidence_sha256',repeat('d',64));
 FOR key IN SELECT jsonb_object_keys(result) LOOP
  PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_finish(%L,%L,%L)',req,'task_e_sql339_success',result-key),
   'NATIVE_TERMINAL_INVALID');
  PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_finish(%L,%L,%L)',req,'task_e_sql339_success',
   jsonb_set(result,ARRAY[key],'null'::jsonb)),'NATIVE_TERMINAL_INVALID');
 END LOOP;
 UPDATE autopilot.project_work_item SET state='PAUSED' WHERE work_item_id=work_id;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_finish(%L,%L,%L)',req,'task_e_sql339_success',result),
  'NATIVE_TERMINAL_AUTHORITY_INVALID');
 UPDATE autopilot.project_work_item SET state='ACTIVE' WHERE work_item_id=work_id;
 UPDATE autopilot.role_registry SET enabled=false WHERE role_id='AUTOPILOT';
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_finish(%L,%L,%L)',req,'task_e_sql339_success',result),
  'NATIVE_TERMINAL_AUTHORITY_INVALID');
 UPDATE autopilot.role_registry SET enabled=true WHERE role_id='AUTOPILOT';
 UPDATE autopilot.native_cli_config SET enabled=false;
 IF NOT autopilot.native_cli_current(req) THEN RAISE EXCEPTION 'TEST_NATIVE_DRAIN_DISABLED'; END IF;
 after_result:=autopilot.native_cli_finish(req,'task_e_sql339_success',result);
 IF after_result->>'state'<>'TERMINAL' OR after_result->'terminal' IS DISTINCT FROM result THEN
  RAISE EXCEPTION 'TEST_NATIVE_TERMINAL_READBACK';
 END IF;
 IF (SELECT status FROM autopilot.task WHERE task_id=(f->>'task_id')::uuid)<>'DONE' THEN
  RAISE EXCEPTION 'TEST_NATIVE_SLOT_NOT_RELEASED';
 END IF;
 PERFORM autopilot.native_cli_finish(req,'task_e_sql339_success',result);
 SELECT count(*) INTO evidence_count FROM autopilot.evidence WHERE external_ref='codex-cli:task_e_sql339_success';
 IF evidence_count<>1 THEN RAISE EXCEPTION 'TEST_NATIVE_DUPLICATE_EVIDENCE'; END IF;
 PERFORM pg_temp.native_reject(format('SELECT autopilot.native_cli_finish(%L,%L,%L)',req,'task_e_sql339_success',
  jsonb_set(result,'{provider_evidence_sha256}',to_jsonb(repeat('e',64)))),'NATIVE_TERMINAL_CONFLICT');

 UPDATE autopilot.native_cli_config SET enabled=true;
 f:=pg_temp.native_fixture('native-sql-paused-339'); id:=(f->>'id')::uuid; work_id:=(f->>'work_id')::uuid;
 r:=autopilot.native_cli_reserve(id,f->'assignment','codex/native-paused'); req:=r->'request';
 IF NOT autopilot.native_cli_begin(req) THEN RAISE EXCEPTION 'TEST_NATIVE_BEGIN_DENIED'; END IF;
 UPDATE autopilot.project_work_item SET state='PAUSED' WHERE work_item_id=work_id;
 UPDATE autopilot.role_registry SET enabled=false WHERE role_id='AUTOPILOT';
 -- An already-created provider task can be acknowledged after authority pause.
 PERFORM autopilot.native_cli_ack(req,'task_e_sql339_paused',repeat('c',64));
 result:=result||'{"status":"BLOCKED","result_code":"TARGET_AUTHORITY_CHANGED"}'::jsonb;
 PERFORM autopilot.native_cli_finish(req,'task_e_sql339_paused',result);
 IF (SELECT status FROM autopilot.task WHERE task_id=(f->>'task_id')::uuid)<>'FAILED_CLOSED'
 OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=work_id)<>'PAUSED' THEN
  RAISE EXCEPTION 'TEST_NATIVE_PAUSED_CLOSURE';
 END IF;
 UPDATE autopilot.role_registry SET enabled=true WHERE role_id='AUTOPILOT';
 f:=pg_temp.native_fixture('native-sql-disabled-role-339'); id:=(f->>'id')::uuid; work_id:=(f->>'work_id')::uuid;
 r:=autopilot.native_cli_reserve(id,f->'assignment','codex/native-disabled-role'); req:=r->'request';
 IF NOT autopilot.native_cli_begin(req) THEN RAISE EXCEPTION 'TEST_NATIVE_BEGIN_DENIED'; END IF;
 PERFORM autopilot.native_cli_ack(req,'task_e_sql339_disabled',repeat('c',64));
 UPDATE autopilot.role_registry SET enabled=false WHERE role_id='AUTOPILOT';
 PERFORM autopilot.native_cli_finish(req,'task_e_sql339_disabled',result);
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=work_id)<>'BLOCKED' THEN
  RAISE EXCEPTION 'TEST_NATIVE_DISABLED_ROLE_STRANDED_ACTIVE';
 END IF;
 UPDATE autopilot.role_registry SET enabled=true WHERE role_id='AUTOPILOT';
 f:=pg_temp.native_fixture('native-sql-changed-scope-339'); id:=(f->>'id')::uuid; work_id:=(f->>'work_id')::uuid;
 r:=autopilot.native_cli_reserve(id,f->'assignment','codex/native-changed-scope'); req:=r->'request';
 PERFORM autopilot.native_cli_begin(req);
 PERFORM autopilot.native_cli_ack(req,'task_e_sql339_changed',repeat('c',64));
 UPDATE autopilot.project_work_item SET task_spec_json='{"focus_path":"changed.py"}'::jsonb WHERE work_item_id=work_id;
 IF autopilot.native_cli_current(req) THEN RAISE EXCEPTION 'TEST_NATIVE_CHANGED_ASSIGNMENT_ALLOWED'; END IF;
 PERFORM autopilot.native_cli_finish(req,'task_e_sql339_changed',result);
 IF (SELECT state FROM autopilot.project_work_item WHERE work_item_id=work_id)<>'BLOCKED' THEN
  RAISE EXCEPTION 'TEST_NATIVE_CHANGED_ASSIGNMENT_STRANDED_ACTIVE';
 END IF;
 f:=pg_temp.native_fixture('native-sql-normal-blocked-339'); id:=(f->>'id')::uuid; work_id:=(f->>'work_id')::uuid;
 r:=autopilot.native_cli_reserve(id,f->'assignment','codex/native-normal-blocked'); req:=r->'request';
 IF NOT autopilot.native_cli_begin(req) THEN RAISE EXCEPTION 'TEST_NATIVE_BEGIN_DENIED'; END IF;
 PERFORM autopilot.native_cli_ack(req,'task_e_sql339_blocked',repeat('c',64));
 result:=result||'{"result_code":"BOUNDED_DEFECT","summary":"Synthetic bounded defect."}'::jsonb;
 PERFORM autopilot.native_cli_finish(req,'task_e_sql339_blocked',result);
 IF NOT EXISTS(SELECT FROM autopilot.role_dispatch_followup WHERE parent_task_id=(f->>'task_id')::uuid AND followup_kind='REPAIR')
 OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=work_id)<>'ACTIVE'
 OR (SELECT last_task_id FROM autopilot.project_work_item WHERE work_item_id=work_id)=(f->>'task_id')::uuid THEN
  RAISE EXCEPTION 'TEST_NATIVE_NORMAL_REPAIR_CONTINUATION_LOST';
 END IF;
 IF has_function_privilege('autopilot_runtime','autopilot.native_cli_finish(jsonb,text,jsonb)','EXECUTE')
 OR has_function_privilege('autopilot_callback','autopilot.native_cli_ack(jsonb,text,text)','EXECUTE')
 OR has_table_privilege('autopilot_runtime','autopilot.native_cli_receipt','INSERT') THEN
  RAISE EXCEPTION 'TEST_NATIVE_PRIVILEGES_WIDENED';
 END IF;
END $$;
ROLLBACK;
