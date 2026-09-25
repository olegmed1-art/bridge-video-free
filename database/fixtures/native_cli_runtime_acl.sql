\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF current_database()<>'bridge_school_ci' OR session_user<>'postgres' THEN
  RAISE EXCEPTION 'DISPOSABLE_CI_DATABASE_REQUIRED';
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname IN ('native_ci_runtime','native_ci_other')) THEN
  RAISE EXCEPTION 'FIXTURE_ROLE_ALREADY_EXISTS';
 END IF;
END $$;
CREATE ROLE native_ci_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE native_ci_other NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT USAGE ON SCHEMA autopilot TO native_ci_runtime,native_ci_other;
GRANT EXECUTE ON FUNCTION
 autopilot.native_cli_reserve(uuid,jsonb,text),
 autopilot.native_cli_snapshot(uuid),autopilot.native_cli_current(jsonb),
 autopilot.native_cli_begin(jsonb),autopilot.native_cli_ack(jsonb,text,text),
 autopilot.native_cli_finish(jsonb,text,jsonb)
 TO native_ci_runtime,native_ci_other;
SET LOCAL ROLE bridge_ci_owner;
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


SELECT set_config('native_ci.fixture',pg_temp.native_fixture('native-runtime-acl-339')::text,true);
RESET ROLE;
SET LOCAL SESSION AUTHORIZATION native_ci_runtime;
DO $$ DECLARE f jsonb:=current_setting('native_ci.fixture')::jsonb; BEGIN
 IF current_user<>'native_ci_runtime' OR session_user<>'native_ci_runtime' THEN
  RAISE EXCEPTION 'TEST_NOT_REAL_SESSION_IDENTITY';
 END IF;
 IF has_table_privilege(current_user,'autopilot.native_cli_config','SELECT,INSERT,UPDATE,DELETE')
 OR has_table_privilege(current_user,'autopilot.native_cli_receipt','SELECT,INSERT,UPDATE,DELETE')
 OR has_function_privilege(current_user,'autopilot.native_cli_authority_locked(uuid,jsonb)','EXECUTE') THEN
  RAISE EXCEPTION 'TEST_RUNTIME_OVERPRIVILEGED';
 END IF;
 BEGIN
  PERFORM * FROM autopilot.native_cli_receipt;
  RAISE EXCEPTION 'TEST_DIRECT_TABLE_READ_ALLOWED';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 BEGIN
  PERFORM autopilot.native_cli_authority_locked((f->>'id')::uuid,f->'assignment');
  RAISE EXCEPTION 'TEST_PRIVATE_HELPER_ALLOWED';
 EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 BEGIN
  PERFORM autopilot.native_cli_reserve((f->>'id')::uuid,f->'assignment','codex/native-runtime-acl');
  RAISE EXCEPTION 'TEST_DISABLED_RESERVE_ALLOWED';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'NATIVE_RESERVATION_INVALID' THEN RAISE; END IF;
 END;
END $$;
RESET SESSION AUTHORIZATION;
UPDATE autopilot.native_cli_config SET enabled=true,cutover_at=clock_timestamp()-interval '1 hour';
SET LOCAL SESSION AUTHORIZATION native_ci_runtime;
DO $$ DECLARE f jsonb:=current_setting('native_ci.fixture')::jsonb;
 r jsonb; req jsonb; result jsonb; terminal jsonb;
BEGIN
 r:=autopilot.native_cli_reserve((f->>'id')::uuid,f->'assignment','codex/native-runtime-acl');
 req:=r->'request';
 IF r IS DISTINCT FROM autopilot.native_cli_reserve((f->>'id')::uuid,f->'assignment','codex/native-runtime-acl') THEN
  RAISE EXCEPTION 'TEST_RESERVE_NOT_IDEMPOTENT';
 END IF;
 IF NOT autopilot.native_cli_current(req) THEN RAISE EXCEPTION 'TEST_CURRENT_DENIED'; END IF;
 IF NOT autopilot.native_cli_begin(req) OR autopilot.native_cli_begin(req) THEN
  RAISE EXCEPTION 'TEST_INTENT_NOT_ONESHOT';
 END IF;
 r:=autopilot.native_cli_ack(req,'task_e_runtime_acl339',repeat('c',64));
 IF r IS DISTINCT FROM autopilot.native_cli_ack(req,'task_e_runtime_acl339',repeat('c',64)) THEN
  RAISE EXCEPTION 'TEST_ACK_REPLAY';
 END IF;
 result:=jsonb_build_object('status','SUCCEEDED','result_code','VERIFIED','summary','Runtime ACL fixture passed.',
  'target_head_sha',repeat('a',40),'provider_evidence_sha256',repeat('d',64));
 terminal:=autopilot.native_cli_finish(req,'task_e_runtime_acl339',result);
 IF terminal->>'state'<>'TERMINAL' OR terminal->'terminal' IS DISTINCT FROM result
 OR terminal IS DISTINCT FROM autopilot.native_cli_finish(req,'task_e_runtime_acl339',result)
 OR terminal IS DISTINCT FROM autopilot.native_cli_snapshot((f->>'id')::uuid) THEN
  RAISE EXCEPTION 'TEST_TERMINAL_NOT_RETAINED';
 END IF;
END $$;
RESET SESSION AUTHORIZATION;
SET LOCAL SESSION AUTHORIZATION native_ci_other;
DO $$ DECLARE f jsonb:=current_setting('native_ci.fixture')::jsonb; BEGIN
 BEGIN
  PERFORM autopilot.native_cli_snapshot((f->>'id')::uuid);
  RAISE EXCEPTION 'TEST_OTHER_SESSION_READ_ALLOWED';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'NATIVE_RESERVATION_NOT_OWNED' THEN RAISE; END IF;
 END;
END $$;
RESET SESSION AUTHORIZATION;
DO $$ DECLARE f jsonb:=current_setting('native_ci.fixture')::jsonb; BEGIN
 IF (SELECT owner_name FROM autopilot.native_cli_receipt WHERE dispatch_id=(f->>'id')::uuid)<>'native_ci_runtime'
 OR (SELECT status FROM autopilot.task WHERE task_id=(f->>'task_id')::uuid)<>'DONE'
 OR (SELECT count(*) FROM autopilot.evidence WHERE external_ref='codex-cli:task_e_runtime_acl339')<>1 THEN
  RAISE EXCEPTION 'TEST_RUNTIME_LIFECYCLE_NOT_ATOMIC';
 END IF;
END $$;
ROLLBACK;
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname IN ('native_ci_runtime','native_ci_other'))
 OR EXISTS(SELECT FROM autopilot.native_cli_receipt WHERE provider_task_id='task_e_runtime_acl339')
 OR EXISTS(SELECT FROM autopilot.project_work_item WHERE work_key='native-runtime-acl-339') THEN
  RAISE EXCEPTION 'TEST_FIXTURE_ROLLBACK_LEAK';
 END IF;
END $$;
\echo NATIVE_RUNTIME_SIX_RPC_REHEARSAL_PASS
