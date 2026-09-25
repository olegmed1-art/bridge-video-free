\set ON_ERROR_STOP on
-- Disposable PostgreSQL CI only. NOT a production apply package.
BEGIN;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='1s';
DO $$ BEGIN
 IF current_database()<>'bridge_school_ci' OR session_user<>'postgres'
 OR current_user<>'postgres' THEN
  RAISE EXCEPTION 'DISPOSABLE_CI_REQUIRED';
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname IN ('native_acl_login','native_acl_parent')) THEN
  RAISE EXCEPTION 'FIXTURE_ROLE_EXISTS';
 END IF;
END $$;
CREATE ROLE native_acl_parent NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE native_acl_login NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT;
GRANT native_acl_parent TO native_acl_login;
GRANT USAGE ON SCHEMA autopilot TO native_acl_parent;

CREATE TEMP TABLE native_acl_target(signature text PRIMARY KEY, id oid UNIQUE);
INSERT INTO native_acl_target VALUES
 ('autopilot.native_cli_reserve(uuid,jsonb,text)','autopilot.native_cli_reserve(uuid,jsonb,text)'::regprocedure),
 ('autopilot.native_cli_snapshot(uuid)','autopilot.native_cli_snapshot(uuid)'::regprocedure),
 ('autopilot.native_cli_current(jsonb)','autopilot.native_cli_current(jsonb)'::regprocedure),
 ('autopilot.native_cli_begin(jsonb)','autopilot.native_cli_begin(jsonb)'::regprocedure),
 ('autopilot.native_cli_ack(jsonb,text,text)','autopilot.native_cli_ack(jsonb,text,text)'::regprocedure),
 ('autopilot.native_cli_finish(jsonb,text,jsonb)','autopilot.native_cli_finish(jsonb,text,jsonb)'::regprocedure);

CREATE FUNCTION pg_temp.native_acl_state() RETURNS jsonb LANGUAGE sql AS $$
 SELECT jsonb_build_object(
  'functions',(SELECT jsonb_agg(jsonb_build_object('id',p.oid,'owner',p.proowner,
    'acl',p.proacl::text,'definition',pg_get_functiondef(p.oid)) ORDER BY p.oid)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='autopilot' AND left(p.proname,11)='native_cli_'),
  'tables',(SELECT jsonb_agg(jsonb_build_object('id',c.oid,'owner',c.relowner,
    'acl',c.relacl::text) ORDER BY c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='autopilot' AND c.relname IN ('native_cli_config','native_cli_receipt')),
  'schema',(SELECT jsonb_build_object('owner',nspowner,'acl',nspacl::text)
    FROM pg_namespace WHERE nspname='autopilot'),
  'membership',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.roleid,m.member)
    FROM pg_auth_members m WHERE m.member='native_acl_login'::regrole),
  'config',(SELECT jsonb_agg(to_jsonb(c)) FROM autopilot.native_cli_config c),
  'receipts',(SELECT count(*) FROM autopilot.native_cli_receipt))
$$;
CREATE TEMP TABLE native_acl_manifest AS SELECT pg_temp.native_acl_state() AS before_state,
 NULL::jsonb AS after_state;

CREATE FUNCTION pg_temp.native_acl_check_context() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF current_database()<>'bridge_school_ci' OR session_user<>'postgres' OR current_user<>'postgres' THEN
  RAISE EXCEPTION 'CONTEXT_DRIFT';
 END IF;
 IF (SELECT count(*) FROM autopilot.native_cli_config)<>1
 OR EXISTS(SELECT FROM autopilot.native_cli_config WHERE enabled IS DISTINCT FROM false)
 OR EXISTS(SELECT FROM autopilot.native_cli_receipt) THEN RAISE EXCEPTION 'STATE_DRIFT'; END IF;
END $$;

CREATE FUNCTION pg_temp.native_acl_apply() RETURNS void LANGUAGE plpgsql AS $$
DECLARE f record;
BEGIN
 PERFORM pg_temp.native_acl_check_context();
 IF pg_temp.native_acl_state() IS DISTINCT FROM (SELECT before_state FROM native_acl_manifest)
 THEN RAISE EXCEPTION 'BASELINE_DRIFT'; END IF;
 IF EXISTS(SELECT FROM native_acl_target WHERE has_function_privilege('native_acl_login',id,'EXECUTE'))
 THEN RAISE EXCEPTION 'PREEXISTING_EXECUTE'; END IF;
 FOR f IN SELECT signature FROM native_acl_target ORDER BY signature LOOP
  EXECUTE 'GRANT EXECUTE ON FUNCTION '||f.signature||' TO native_acl_login';
 END LOOP;
 IF EXISTS(SELECT FROM native_acl_target WHERE NOT has_function_privilege('native_acl_login',id,'EXECUTE'))
 OR has_function_privilege('native_acl_login','autopilot.native_cli_authority_locked(uuid,jsonb)','EXECUTE')
 OR has_table_privilege('native_acl_login','autopilot.native_cli_config','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR has_table_privilege('native_acl_login','autopilot.native_cli_receipt','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
 OR NOT has_schema_privilege('native_acl_login','autopilot','USAGE') THEN
  RAISE EXCEPTION 'POSTCHECK_FAILED';
 END IF;
 UPDATE native_acl_manifest SET after_state=pg_temp.native_acl_state();
END $$;

CREATE FUNCTION pg_temp.native_acl_rollback() RETURNS void LANGUAGE plpgsql AS $$
DECLARE f record;
BEGIN
 PERFORM pg_temp.native_acl_check_context();
 IF (SELECT after_state FROM native_acl_manifest) IS NULL
 OR pg_temp.native_acl_state() IS DISTINCT FROM (SELECT after_state FROM native_acl_manifest)
 THEN RAISE EXCEPTION 'ROLLBACK_DRIFT'; END IF;
 FOR f IN SELECT signature FROM native_acl_target ORDER BY signature LOOP
  EXECUTE 'REVOKE EXECUTE ON FUNCTION '||f.signature||' FROM native_acl_login RESTRICT';
 END LOOP;
 IF pg_temp.native_acl_state() IS DISTINCT FROM (SELECT before_state FROM native_acl_manifest)
 THEN RAISE EXCEPTION 'ROLLBACK_MISMATCH'; END IF;
END $$;

-- Each fault subtransaction must roll itself back; the final equality check
-- catches accidental survival of a grant, role membership, or config edit.
DO $$ BEGIN
 BEGIN
  UPDATE autopilot.native_cli_config SET enabled=true;
  PERFORM pg_temp.native_acl_apply();
  RAISE EXCEPTION 'TEST_ACCEPTED_ENABLED';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'STATE_DRIFT' THEN RAISE; END IF; END;
 BEGIN
  GRANT EXECUTE ON FUNCTION autopilot.native_cli_begin(jsonb) TO native_acl_login;
  PERFORM pg_temp.native_acl_apply();
  RAISE EXCEPTION 'TEST_ACCEPTED_PREEXISTING_GRANT';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'BASELINE_DRIFT' THEN RAISE; END IF; END;
 BEGIN
  ALTER FUNCTION autopilot.native_cli_begin(jsonb) COST 199;
  PERFORM pg_temp.native_acl_apply();
  RAISE EXCEPTION 'TEST_ACCEPTED_DEFINITION_DRIFT';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'BASELINE_DRIFT' THEN RAISE; END IF; END;
 BEGIN
  REVOKE native_acl_parent FROM native_acl_login;
  PERFORM pg_temp.native_acl_apply();
  RAISE EXCEPTION 'TEST_ACCEPTED_MEMBERSHIP_DRIFT';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'BASELINE_DRIFT' THEN RAISE; END IF; END;
 BEGIN
  GRANT EXECUTE ON FUNCTION autopilot.native_cli_begin(jsonb) TO native_acl_parent;
  PERFORM pg_temp.native_acl_apply();
  RAISE EXCEPTION 'TEST_ACCEPTED_INHERITED_GRANT';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'BASELINE_DRIFT' THEN RAISE; END IF; END;
 BEGIN
  PERFORM pg_temp.native_acl_apply();
  RAISE EXCEPTION 'SIMULATED_POSTCHECK_FAILURE';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'SIMULATED_POSTCHECK_FAILURE' THEN RAISE; END IF; END;
 IF pg_temp.native_acl_state() IS DISTINCT FROM (SELECT before_state FROM native_acl_manifest)
 THEN RAISE EXCEPTION 'FAULT_ROLLBACK_LEAK'; END IF;
END $$;

SELECT pg_temp.native_acl_apply();
DO $$ BEGIN
 BEGIN
  GRANT EXECUTE ON FUNCTION autopilot.native_cli_snapshot(uuid) TO native_acl_parent;
  PERFORM pg_temp.native_acl_rollback();
  RAISE EXCEPTION 'TEST_ROLLBACK_ACCEPTED_DRIFT';
 EXCEPTION WHEN raise_exception THEN IF SQLERRM<>'ROLLBACK_DRIFT' THEN RAISE; END IF; END;
END $$;
SELECT pg_temp.native_acl_rollback();

-- Pure metadata output for comparison with the live owner's observed hashes.
SELECT 'NATIVE_DEFINITION_SHA256' AS evidence, p.proname,
 encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex') AS definition_sha256
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='autopilot' AND left(p.proname,11)='native_cli_' ORDER BY p.proname;
ROLLBACK;
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname IN ('native_acl_login','native_acl_parent'))
 THEN RAISE EXCEPTION 'FIXTURE_ROLLBACK_LEAK'; END IF;
END $$;
\echo NATIVE_GRANT_ROLLBACK_REHEARSAL_PASS
