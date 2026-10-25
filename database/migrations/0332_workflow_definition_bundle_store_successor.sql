-- SOURCE ONLY / NOT_QUALIFIED. No GRANT, new role, credential or Canon activation.
-- Installation creates a disabled store and EMPTY authorization-policy bindings.
BEGIN;
SET LOCAL search_path=pg_catalog,public,bidding,pg_temp;
SET LOCAL TimeZone='UTC';
DO $preflight$
DECLARE n integer;
BEGIN
 IF to_regclass('bidding.video_canon_definition_state') IS NULL OR to_regclass('bidding.video_canon_definition_audit') IS NULL THEN
   RAISE EXCEPTION 'DEFINITION_REQUIRES_EXACT_0331_INSTALLATION'; END IF;
 IF to_regclass('bidding.video_definition_store_control') IS NOT NULL OR to_regclass('bidding.video_definition_store_binding') IS NOT NULL THEN
   RAISE EXCEPTION 'DEFINITION_STORE_ALREADY_PRESENT_RECONCILE_NOT_RETRY'; END IF;
 SELECT count(*) INTO n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
 WHERE ns.nspname='bidding' AND p.proname='validate_video_canon_verification_bundle' AND p.pronargs=0
 AND p.prosecdef=true AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex')='531eb789e024d6ada6540f265d1de29eb21135f4dfbf53c8129430c232d2cb90';
 IF n<>1 THEN RAISE EXCEPTION 'DEFINITION_BASE_FUNCTION_PIN_MISMATCH:validate_video_canon_verification_bundle'; END IF;
 SELECT count(*) INTO n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
 WHERE ns.nspname='bidding' AND p.proname='validate_video_canon_verification' AND p.pronargs=0
 AND p.prosecdef=false AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex')='3a9ec52c61d9efb589bfa22470dc115e74091c322e8d7e8e3acfac326bcd6d8b';
 IF n<>1 THEN RAISE EXCEPTION 'DEFINITION_BASE_FUNCTION_PIN_MISMATCH:validate_video_canon_verification'; END IF;
 SELECT count(*) INTO n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
 WHERE ns.nspname='bidding' AND p.proname='guard_definition_contract_state' AND p.pronargs=0 AND p.prosecdef=false
 AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex')='19fbd1579924d93c56bab2a3ab2b7d4dcb8f2611eb37f663c5116a07b985c902';
 IF n<>1 THEN RAISE EXCEPTION 'DEFINITION_0331_FUNCTION_PIN_MISMATCH:guard_definition_contract_state'; END IF;
 SELECT count(*) INTO n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
 WHERE ns.nspname='bidding' AND p.proname='guard_definition_contract_audit' AND p.pronargs=0 AND p.prosecdef=false
 AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex')='c3b65fce98b224ad762fdc2f1f89548d8e5a64f6a167b57a9726a0d9c449dace';
 IF n<>1 THEN RAISE EXCEPTION 'DEFINITION_0331_FUNCTION_PIN_MISMATCH:guard_definition_contract_audit'; END IF;
 SELECT count(*) INTO n FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
 WHERE ns.nspname='bidding' AND p.proname='reject_definition_contract_truncate' AND p.pronargs=0 AND p.prosecdef=false
 AND pg_get_userbyid(p.proowner)=current_user
 AND encode(public.digest(convert_to(replace(p.prosrc,E'\r\n',E'\n'),'UTF8'),'sha256'),'hex')='565eb452f3e54a2aa87f7fa7abda86f812ec5509791d96983b572770382f8e6c';
 IF n<>1 THEN RAISE EXCEPTION 'DEFINITION_0331_FUNCTION_PIN_MISMATCH:reject_definition_contract_truncate'; END IF;
 IF (SELECT count(*) FROM pg_catalog.pg_trigger WHERE
 (tgrelid='bidding.video_canon_definition_state'::regclass AND tgname='definition_contract_state_guard' AND tgfoid='bidding.guard_definition_contract_state()'::regprocedure
 OR tgrelid='bidding.video_canon_definition_audit'::regclass AND tgname='definition_contract_audit_guard' AND tgfoid='bidding.guard_definition_contract_audit()'::regprocedure
 OR tgrelid='bidding.video_canon_definition_state'::regclass AND tgname='definition_contract_state_no_truncate' AND tgfoid='bidding.reject_definition_contract_truncate()'::regprocedure
 OR tgrelid='bidding.video_canon_definition_audit'::regclass AND tgname='definition_contract_audit_no_truncate' AND tgfoid='bidding.reject_definition_contract_truncate()'::regprocedure)
 AND tgenabled IN ('O','A'))<>4 THEN RAISE EXCEPTION 'DEFINITION_0331_TRIGGER_BINDING_REQUIRED'; END IF;
END $preflight$;

CREATE TABLE bidding.video_definition_store_control (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT false,
 generation bigint NOT NULL DEFAULT 1 CHECK(generation>0),
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 changed_by_principal text NOT NULL DEFAULT session_user
);
INSERT INTO bidding.video_definition_store_control(singleton,enabled,generation) VALUES(true,false,1);

CREATE TABLE bidding.video_definition_store_binding (
 binding_id uuid PRIMARY KEY DEFAULT uuidv7(),
 school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
 analysis_candidate_id uuid NOT NULL REFERENCES public.analysis_candidate(analysis_candidate_id) ON DELETE RESTRICT,
 candidate_payload_hash text NOT NULL CHECK(candidate_payload_hash ~ '^[0-9a-f]{64}$'),
 consumer_scope jsonb NOT NULL CHECK(jsonb_typeof(consumer_scope)='object'),
 scope_sha256 text NOT NULL CHECK(scope_sha256 ~ '^[0-9a-f]{64}$'),
 execution_principal text NOT NULL CHECK(btrim(execution_principal)<>''),
 action text NOT NULL CHECK(action IN ('REGISTER_BUNDLE','ATTEST','READ_CURRENT')),
 capability text NOT NULL,
 allowed_check_ids text[] NOT NULL DEFAULT '{}',
 verifier_version text,
 implementation_sha256 text,
 authorization_evidence_sha256 text NOT NULL CHECK(authorization_evidence_sha256 ~ '^[0-9a-f]{64}$'),
 valid_from timestamptz NOT NULL,
 valid_to timestamptz NOT NULL CHECK(valid_to>valid_from),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
 retired_at timestamptz,
 recorded_by_principal text NOT NULL DEFAULT session_user,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((status='active')=(retired_at IS NULL)),
 CHECK((action='REGISTER_BUNDLE' AND capability='bridge_school_canon_verifier') OR
       (action='READ_CURRENT' AND capability IN ('bridge_school_reader','bridge_school_canon_verifier','bridge_school_canon_promoter')) OR
       (action='ATTEST' AND capability IN ('bridge_school_canon_semantic_verifier','bridge_school_canon_bridge_verifier',
          'bridge_school_canon_firewall_verifier','bridge_school_canon_control_verifier'))),
 CHECK((action='ATTEST' AND cardinality(allowed_check_ids)>0 AND verifier_version IS NOT NULL AND btrim(verifier_version)<>''
          AND implementation_sha256 IS NOT NULL AND implementation_sha256 ~ '^[0-9a-f]{64}$') OR
       (action<>'ATTEST' AND cardinality(allowed_check_ids)=0 AND verifier_version IS NULL AND implementation_sha256 IS NULL))
);
CREATE UNIQUE INDEX video_definition_active_binding ON bidding.video_definition_store_binding
 (analysis_candidate_id,candidate_payload_hash,scope_sha256,execution_principal,action,capability) WHERE status='active';

CREATE FUNCTION bidding.video_definition_need(p_ok boolean,p_code text) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
BEGIN IF p_ok IS NOT TRUE THEN RAISE EXCEPTION '%',p_code USING ERRCODE='23514'; END IF; END $$;

CREATE FUNCTION bidding.video_definition_keys(p jsonb,p_keys text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
 SELECT jsonb_typeof(p)='object' AND (SELECT count(*)=cardinality(p_keys) AND bool_and(key=ANY(p_keys)) FROM jsonb_object_keys(p) AS k(key))
$$;

CREATE FUNCTION bidding.video_definition_int(p jsonb,p_min bigint DEFAULT 0) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE n numeric;
BEGIN
 PERFORM bidding.video_definition_need(jsonb_typeof(p)='number' AND p::text ~ '^-?(0|[1-9][0-9]*)$','DEFINITION_INTEGER_REQUIRED');
 n:=p::text::numeric;
 PERFORM bidding.video_definition_need(n>=p_min AND n<=9007199254740991,'DEFINITION_INTEGER_RANGE');
 RETURN n::bigint;
END $$;

CREATE FUNCTION bidding.video_definition_test_context(p_candidate uuid,p_scope jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c public.analysis_candidate%ROWTYPE; native_scope jsonb; policy jsonb; registry jsonb; members jsonb;
 active_roles integer; active_high integer; context jsonb;
BEGIN
 native_scope:=bidding.video_definition_scope(p_candidate,p_scope);
 SELECT * INTO STRICT c FROM public.analysis_candidate WHERE analysis_candidate_id=p_candidate;
 policy:=bidding.video_definition_policy(p_candidate,p_scope);
 SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.database_role COLLATE "C"),'[]'::jsonb) INTO registry
 FROM (SELECT database_role,verifier_family,allowed_check_ids,max_assurance_level,status
       FROM bidding.video_canon_verifier_registry) r;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('binding_id',b.binding_id::text,'principal',b.execution_principal,
   'capability',b.capability,'version',b.verifier_version,'implementation_sha256',b.implementation_sha256,
   'allowed_check_ids',to_jsonb(b.allowed_check_ids),'valid_from',b.valid_from,'valid_to',b.valid_to,
   'status',b.status,'retired_at',b.retired_at,'rolcanlogin',COALESCE(r.rolcanlogin,false),
   'member',CASE WHEN r.oid IS NULL THEN false ELSE pg_has_role(r.oid,b.capability,'MEMBER') END)
   ORDER BY b.binding_id::text COLLATE "C"),'[]'::jsonb) INTO members
 FROM bidding.video_definition_store_binding b LEFT JOIN pg_catalog.pg_roles r ON r.rolname=b.execution_principal
 WHERE b.analysis_candidate_id=p_candidate AND b.candidate_payload_hash=c.payload_hash
   AND b.consumer_scope=p_scope AND b.action='ATTEST';
 SELECT count(DISTINCT b.capability),count(DISTINCT CASE WHEN b.capability<>'bridge_school_canon_control_verifier'
   THEN b.execution_principal END) INTO active_roles,active_high
 FROM bidding.video_definition_store_binding b JOIN pg_catalog.pg_roles r ON r.rolname=b.execution_principal
 JOIN bidding.video_canon_verifier_registry reg ON reg.database_role=b.capability
 WHERE b.analysis_candidate_id=p_candidate AND b.candidate_payload_hash=c.payload_hash AND b.consumer_scope=p_scope
   AND b.action='ATTEST' AND b.status='active' AND b.retired_at IS NULL
   AND b.valid_from<=clock_timestamp() AND clock_timestamp()<b.valid_to AND reg.status='active'
   AND r.rolcanlogin AND pg_has_role(r.oid,b.capability,'MEMBER');
 context:=jsonb_build_object('evidence_mode','OBSERVED',
   'school_snapshot_sha256',bidding.current_school_canon_snapshot_sha256(c.school_id),
   'source_policy_sha256',bidding.video_definition_digest('DEFINITION_SOURCE_POLICY_NATIVE_V1',policy),
   'membership_sha256',bidding.video_definition_digest('DEFINITION_MEMBERSHIP_NATIVE_V1',jsonb_build_object('registry',registry,'bindings',members)),
   'policy_from_us',bidding.video_definition_epoch_us((policy->>'valid_from')::timestamptz),
   'policy_until_us',CASE WHEN policy->>'valid_to' IS NULL THEN NULL ELSE bidding.video_definition_epoch_us((policy->>'valid_to')::timestamptz) END,
   'candidate_active',true,'set_active',EXISTS(SELECT 1 FROM bidding.video_canon_definition_state d
       WHERE d.analysis_candidate_id=p_candidate AND d.candidate_payload_hash=c.payload_hash
       AND d.scope_sha256=bidding.video_definition_digest('DEFINITION_SCOPE_0_1',native_scope)),
   'source_active',true,'attestors_active',active_roles=4 AND active_high>=3);
 RETURN context;
END $$;

CREATE FUNCTION bidding.video_definition_manifest_digest(m jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE c jsonb; sorted_cases jsonb;
BEGIN
 PERFORM bidding.video_definition_need(bidding.video_definition_keys(m,ARRAY['evidence_mode','contract','candidate_hash','scope','set_version',
   'gate_profile_sha256','max_attempt_us','max_age_us','cases']) AND m->>'evidence_mode'='OBSERVED'
   AND m->>'contract'='DEFINITION_TEST_STATE_PROPOSAL_0_1' AND m->>'candidate_hash' ~ '^[0-9a-f]{64}$'
   AND m->>'gate_profile_sha256' ~ '^[0-9a-f]{64}$' AND jsonb_typeof(m->'cases')='array'
   AND jsonb_array_length(m->'cases') BETWEEN 4 AND 4096,'DEFINITION_MANIFEST_FIELDS');
 PERFORM bidding.video_definition_int(m->'set_version',1);
 PERFORM bidding.video_definition_need((m->>'set_version')::bigint<=2147483647,'DEFINITION_MANIFEST_VERSION');
 PERFORM bidding.video_definition_int(m->'max_attempt_us',1); PERFORM bidding.video_definition_int(m->'max_age_us',1);
 PERFORM bidding.video_definition_need((SELECT count(*)=count(DISTINCT value->>'key') FROM jsonb_array_elements(m->'cases'))
   AND (SELECT array_agg(DISTINCT value->>'kind' ORDER BY value->>'kind')=ARRAY['BOUNDARY','INTERFERENCE','NEGATIVE','POSITIVE'] FROM jsonb_array_elements(m->'cases')),
   'DEFINITION_FOUR_UNIQUE_CASE_CLASSES');
 FOR c IN SELECT value FROM jsonb_array_elements(m->'cases') LOOP
   PERFORM bidding.video_definition_need(bidding.video_definition_keys(c,ARRAY['key','version','kind','input_sha256','oracle_sha256','executor_sha256','source_binding_sha256'])
     AND jsonb_typeof(c->'key')='string' AND length(c->>'key') BETWEEN 1 AND 512
     AND c->>'kind' IN ('POSITIVE','NEGATIVE','BOUNDARY','INTERFERENCE')
     AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['input_sha256','oracle_sha256','executor_sha256','source_binding_sha256']) k
       WHERE jsonb_typeof(c->k)<>'string' OR c->>k !~ '^[0-9a-f]{64}$'),'DEFINITION_CASE_BINDING');
   PERFORM bidding.video_definition_int(c->'version',1);
   PERFORM bidding.video_definition_need((c->>'version')::bigint<=2147483647,'DEFINITION_CASE_VERSION');
 END LOOP;
 SELECT jsonb_agg(value ORDER BY value->>'key' COLLATE "C") INTO sorted_cases FROM jsonb_array_elements(m->'cases');
 RETURN bidding.video_definition_digest('DEFINITION_MANIFEST_PROPOSAL_0_1',jsonb_set(m,'{cases}',sorted_cases));
END $$;

CREATE FUNCTION bidding.video_definition_test_state(p_candidate uuid,p_scope jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c public.analysis_candidate%ROWTYPE; d jsonb; stored record; native_scope jsonb; context jsonb; context_hash text;
 m jsonb; set_hash text; x record; a jsonb; o jsonb; case_row jsonb; base jsonb; latest jsonb:='[]'::jsonb;
 n bigint; max_seq bigint; min_seq bigint; distinct_seq bigint; now_us bigint; until_us bigint; end_us bigint; manifest_for_attempt jsonb;
BEGIN
 native_scope:=bidding.video_definition_scope(p_candidate,p_scope);
 SELECT * INTO STRICT c FROM public.analysis_candidate WHERE analysis_candidate_id=p_candidate;
 SELECT canonical_json,document_sha256 INTO stored FROM bidding.video_canon_definition_state
 WHERE school_id=c.school_id AND analysis_candidate_id=p_candidate AND candidate_payload_hash=c.payload_hash
   AND scope_sha256=bidding.video_definition_digest('DEFINITION_SCOPE_0_1',native_scope);
 PERFORM bidding.video_definition_need(FOUND AND octet_length(stored.canonical_json)<=524288
   AND encode(public.digest(convert_to(stored.canonical_json,'UTF8'),'sha256'),'hex')=stored.document_sha256,'DEFINITION_STORED_TEST_STATE_REQUIRED');
 d:=stored.canonical_json::jsonb;
 PERFORM bidding.video_definition_need(stored.canonical_json=bidding.video_definition_canonical_int(d)
   AND bidding.video_definition_keys(d,ARRAY['schema','evidence_mode','revision','candidate_hash','scope','sets','current_set','attempts','outcomes','commands'])
   AND d->>'schema'='definition-contract-store-v0.1' AND d->>'evidence_mode'='OBSERVED' AND d->>'candidate_hash'=c.payload_hash
   AND d->'scope'=native_scope AND jsonb_typeof(d->'sets')='object' AND jsonb_typeof(d->'commands')='object'
   AND jsonb_typeof(d->'attempts')='array' AND jsonb_array_length(d->'attempts')<=4096
   AND jsonb_typeof(d->'outcomes')='array','DEFINITION_STORED_TEST_DOCUMENT_BINDING');
 PERFORM bidding.video_definition_int(d->'revision',1);
 FOR x IN SELECT key,value FROM jsonb_each(d->'sets') LOOP
   PERFORM bidding.video_definition_need(x.key=bidding.video_definition_manifest_digest(x.value)
     AND x.value->>'candidate_hash'=c.payload_hash AND x.value->'scope'=native_scope
     AND x.value->>'gate_profile_sha256'='30b2cb4435a3b0a68b45cffbbf88ee1550557edb140d7483b69a9fe6353f6daf','DEFINITION_STORED_MANIFEST_BINDING');
 END LOOP;
 PERFORM bidding.video_definition_need((SELECT count(*)=count(DISTINCT value->>'set_version') FROM jsonb_each(d->'sets')),'DEFINITION_DUPLICATE_SET_VERSION');
 set_hash:=d->>'current_set'; m:=d->'sets'->set_hash;
 PERFORM bidding.video_definition_need(m IS NOT NULL,'DEFINITION_CURRENT_MANIFEST_REQUIRED');
 now_us:=bidding.video_definition_epoch_us(clock_timestamp());
 context:=bidding.video_definition_test_context(p_candidate,p_scope);
 PERFORM bidding.video_definition_need(context->'candidate_active'='true'::jsonb AND context->'set_active'='true'::jsonb
   AND context->'source_active'='true'::jsonb AND context->'attestors_active'='true'::jsonb
   AND now_us>=(context->>'policy_from_us')::bigint
   AND (context->>'policy_until_us' IS NULL OR now_us<(context->>'policy_until_us')::bigint),'DEFINITION_TEST_CONTEXT_REVOKED_OR_EXPIRED');
 context_hash:=bidding.video_definition_digest('DEFINITION_DEPENDENCY_CONTEXT_0_1',context);
 until_us:=(context->>'policy_until_us')::bigint;
 PERFORM bidding.video_definition_need((SELECT count(*)=count(DISTINCT value->>'attempt_key') FROM jsonb_array_elements(d->'attempts'))
   AND (SELECT count(*)=count(DISTINCT value->>'attempt_key') FROM jsonb_array_elements(d->'outcomes')),'DEFINITION_HISTORY_IDENTITY');
 FOR a IN SELECT value FROM jsonb_array_elements(d->'attempts') LOOP
   PERFORM bidding.video_definition_need(bidding.video_definition_keys(a,ARRAY['attempt_key','case_key','case_version','attempt_seq','candidate_hash',
     'manifest_sha256','context_sha256','started_us','executor_login','reservation_sha256']) AND jsonb_typeof(a->'attempt_key')='string'
     AND length(a->>'attempt_key') BETWEEN 1 AND 512 AND jsonb_typeof(a->'executor_login')='string' AND length(a->>'executor_login') BETWEEN 1 AND 512
     AND a->>'candidate_hash'=c.payload_hash AND d->'sets' ? (a->>'manifest_sha256') AND a->>'context_sha256' ~ '^[0-9a-f]{64}$',
     'DEFINITION_ATTEMPT_FIELDS');
   PERFORM bidding.video_definition_int(a->'attempt_seq',1); PERFORM bidding.video_definition_int(a->'started_us');
   PERFORM bidding.video_definition_int(a->'case_version',1);
   manifest_for_attempt:=d->'sets'->(a->>'manifest_sha256');
   SELECT value INTO case_row FROM jsonb_array_elements(manifest_for_attempt->'cases') WHERE value->>'key'=a->>'case_key';
   PERFORM bidding.video_definition_need(FOUND AND a->'case_version'=case_row->'version' AND (a->>'started_us')::bigint<=now_us
     AND a->>'reservation_sha256'=bidding.video_definition_digest('DEFINITION_RESERVATION_0_1',a-'executor_login'-'reservation_sha256'),
     'DEFINITION_ATTEMPT_BINDING');
 END LOOP;
 FOR o IN SELECT value FROM jsonb_array_elements(d->'outcomes') LOOP
   PERFORM bidding.video_definition_need(bidding.video_definition_keys(o,ARRAY['attempt_key','status','finished_us','observed_sha256','executor_sha256','artifact_sha256'])
     AND o->>'status' IN ('PASS','FAIL','ERROR','CANCELLED') AND o->>'artifact_sha256' ~ '^[0-9a-f]{64}$'
     AND (o->'observed_sha256'='null'::jsonb OR o->>'observed_sha256' ~ '^[0-9a-f]{64}$'),'DEFINITION_OUTCOME_FIELDS');
   SELECT value INTO a FROM jsonb_array_elements(d->'attempts') WHERE value->>'attempt_key'=o->>'attempt_key';
   PERFORM bidding.video_definition_need(FOUND,'DEFINITION_OUTCOME_FOREIGN_ATTEMPT');
   PERFORM bidding.video_definition_int(o->'finished_us');
   SELECT value INTO case_row FROM jsonb_array_elements(d->'sets'->(a->>'manifest_sha256')->'cases') WHERE value->>'key'=a->>'case_key';
   PERFORM bidding.video_definition_need(o->>'executor_sha256'=case_row->>'executor_sha256'
     AND (a->>'started_us')::bigint<=(o->>'finished_us')::bigint AND (o->>'finished_us')::bigint<=now_us
     AND (o->>'status'<>'PASS' OR o->>'observed_sha256'=case_row->>'oracle_sha256'),'DEFINITION_OUTCOME_ORACLE_BINDING');
 END LOOP;
 FOR case_row IN SELECT value FROM jsonb_array_elements(m->'cases') ORDER BY value->>'key' COLLATE "C" LOOP
   SELECT count(*),max((value->>'attempt_seq')::bigint),min((value->>'attempt_seq')::bigint),count(DISTINCT value->>'attempt_seq')
     INTO n,max_seq,min_seq,distinct_seq FROM jsonb_array_elements(d->'attempts')
     WHERE value->>'manifest_sha256'=set_hash AND value->>'case_key'=case_row->>'key';
   PERFORM bidding.video_definition_need(n>0 AND min_seq=1 AND max_seq=n AND distinct_seq=n,'DEFINITION_ATTEMPT_SEQUENCE_OR_MISSING');
   SELECT value INTO a FROM jsonb_array_elements(d->'attempts') WHERE value->>'manifest_sha256'=set_hash
     AND value->>'case_key'=case_row->>'key' ORDER BY (value->>'attempt_seq')::bigint DESC LIMIT 1;
   SELECT value INTO o FROM jsonb_array_elements(d->'outcomes') WHERE value->>'attempt_key'=a->>'attempt_key';
   PERFORM bidding.video_definition_need(FOUND AND o->>'status'='PASS','DEFINITION_LATEST_PENDING_OR_FAILED');
   PERFORM bidding.video_definition_need(a->>'context_sha256'=context_hash,'DEFINITION_STALE_TEST_CONTEXT');
   PERFORM bidding.video_definition_need(EXISTS(SELECT 1 FROM bidding.video_definition_store_binding b JOIN pg_catalog.pg_roles r ON r.rolname=b.execution_principal
     WHERE b.analysis_candidate_id=p_candidate AND b.candidate_payload_hash=c.payload_hash AND b.consumer_scope=p_scope
       AND b.action='ATTEST' AND b.capability='bridge_school_canon_control_verifier' AND b.execution_principal=a->>'executor_login'
       AND b.implementation_sha256=case_row->>'executor_sha256' AND b.status='active' AND b.retired_at IS NULL
       AND b.valid_from<=clock_timestamp() AND clock_timestamp()<b.valid_to AND r.rolcanlogin AND pg_has_role(r.oid,b.capability,'MEMBER')
       AND (case_row->>'kind'||'_TESTS')=ANY(b.allowed_check_ids)),'DEFINITION_CASE_EXECUTOR_REVOKED_OR_WRONG');
   PERFORM bidding.video_definition_need((o->>'finished_us')::bigint-(a->>'started_us')::bigint<=(m->>'max_attempt_us')::bigint,'DEFINITION_TEST_TIMEOUT');
   end_us:=(o->>'finished_us')::bigint+(m->>'max_age_us')::bigint;
   PERFORM bidding.video_definition_need(end_us<=9007199254740991 AND now_us<end_us,'DEFINITION_TEST_STALE_AGE');
   until_us:=CASE WHEN until_us IS NULL THEN end_us ELSE least(until_us,end_us) END;
   base:=a-'executor_login'-'reservation_sha256';
   latest:=latest||jsonb_build_array(jsonb_build_object('case_key',case_row->>'key','attempt',base,'outcome',o));
 END LOOP;
 RETURN jsonb_build_object('state_sha256',bidding.video_definition_digest('DEFINITION_TEST_STATE_PROPOSAL_0_1',
   jsonb_build_object('contract','DEFINITION_TEST_STATE_PROPOSAL_0_1','manifest_sha256',set_hash,'dependency_context_sha256',context_hash,'latest',latest)),
   'context',context,'valid_until_us',until_us);
END $$;

CREATE FUNCTION bidding.video_definition_canonical_int(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE k text; result text;
BEGIN
 k:=jsonb_typeof(p);
 IF k='object' THEN
   SELECT '{'||COALESCE(string_agg(to_jsonb(x.key)::text||':'||bidding.video_definition_canonical_int(x.value),',' ORDER BY x.key COLLATE "C"),'')||'}'
     INTO result FROM jsonb_each(p) x; RETURN result;
 ELSIF k='array' THEN
   SELECT '['||COALESCE(string_agg(bidding.video_definition_canonical_int(x.value),',' ORDER BY x.ord),'')||']'
     INTO result FROM jsonb_array_elements(p) WITH ORDINALITY x(value,ord); RETURN result;
 ELSIF k='number' THEN
   PERFORM bidding.video_definition_need(p::text ~ '^-?(0|[1-9][0-9]*)$' AND abs(p::text::numeric)<=9007199254740991,'DEFINITION_INTEGER_JSON_ONLY');
   RETURN p::text;
 ELSIF k IN ('string','boolean','null') THEN RETURN p::text;
 END IF;
 RAISE EXCEPTION 'DEFINITION_JSON_REQUIRED' USING ERRCODE='23514';
END $$;

CREATE FUNCTION bidding.video_definition_digest(p_domain text,p jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
 SELECT encode(public.digest(convert_to(p_domain,'UTF8')||decode('00','hex')||
 convert_to(bidding.video_definition_canonical_int(p),'UTF8'),'sha256'),'hex')
$$;

CREATE FUNCTION bidding.video_definition_epoch_us(p timestamptz) RETURNS bigint
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$ SELECT (extract(epoch FROM p)*1000000)::bigint $$;

CREATE FUNCTION bidding.video_definition_unique_json(p json,p_depth integer DEFAULT 0) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE x record;
BEGIN
 PERFORM bidding.video_definition_need(p_depth<=64,'DEFINITION_JSON_DEPTH');
 IF json_typeof(p)='object' THEN
   PERFORM bidding.video_definition_need((SELECT count(*)=count(DISTINCT key COLLATE "C") FROM json_each(p)),'DEFINITION_DUPLICATE_JSON_KEY');
   FOR x IN SELECT value FROM json_each(p) LOOP PERFORM bidding.video_definition_unique_json(x.value,p_depth+1); END LOOP;
 ELSIF json_typeof(p)='array' THEN
   FOR x IN SELECT value FROM json_array_elements(p) LOOP PERFORM bidding.video_definition_unique_json(x.value,p_depth+1); END LOOP;
 END IF;
END $$;

CREATE FUNCTION bidding.video_definition_scope(p_candidate uuid,p_scope jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c record; d jsonb; span jsonb;
BEGIN
 PERFORM bidding.video_definition_need(bidding.video_definition_keys(p_scope,ARRAY['school_key','system_profile','learner_level','activation_scope'])
   AND NOT EXISTS(SELECT 1 FROM jsonb_each(p_scope) x WHERE jsonb_typeof(x.value)<>'string' OR length(x.value#>>'{}') NOT BETWEEN 1 AND 512),'DEFINITION_SCOPE_REQUIRED');
 SELECT a.*,s.status source_status,s.school_id source_school,school.stable_name INTO c
 FROM public.analysis_candidate a JOIN public.source s ON s.source_id=a.source_id JOIN public.school school ON school.school_id=a.school_id
 WHERE a.analysis_candidate_id=p_candidate;
 PERFORM bidding.video_definition_need(FOUND AND c.school_id=c.source_school AND c.stable_name=p_scope->>'school_key'
   AND c.candidate_type='video_school_canon_candidate' AND c.status='active' AND c.promotion_status IN ('staging','review_queue')
   AND c.source_status='active' AND c.payload_hash ~ '^[0-9a-f]{64}$'
   AND bidding.video_definition_keys(c.payload,ARRAY['schema','status','knowledge_item','knowledge_version','knowledge_version_source',
       'canon_activation','accepted_readback','db_write_performed'])
   AND c.payload->>'schema'='knowledge-storage-skill-proposal-only-v1'
   AND c.payload->>'status'='PROPOSAL_NOT_INSERTABLE_WITHOUT_REAL_FOREIGN_KEYS'
   AND c.payload->'db_write_performed'='false'::jsonb AND c.payload->'canon_activation'='null'::jsonb
   AND c.payload->'accepted_readback'='null'::jsonb
   AND bidding.video_definition_keys(c.payload->'knowledge_item',ARRAY['knowledge_item_id','school_id','stable_key','knowledge_type','title','status'])
   AND bidding.video_definition_keys(c.payload->'knowledge_version',ARRAY['knowledge_version_id','knowledge_item_id','version_no','content','authority_class',
       'review_status','status','bidding_system_key','agreement_scope','level_scope','effective_from','effective_to','method_version','provenance'])
   AND bidding.video_definition_keys(c.payload->'knowledge_version_source',ARRAY['knowledge_version_id','source_id','relation_type','source_locator'])
   AND c.payload#>>'{knowledge_item,knowledge_type}'='skill' AND c.payload#>>'{knowledge_item,status}'='candidate'
   AND jsonb_typeof(c.payload#>'{knowledge_item,stable_key}')='string'
   AND jsonb_typeof(c.payload#>'{knowledge_item,title}')='string' AND length(c.payload#>>'{knowledge_item,title}')>0
   AND c.payload#>'{knowledge_item,knowledge_item_id}'='null'::jsonb AND c.payload#>'{knowledge_item,school_id}'='null'::jsonb
   AND c.payload#>>'{knowledge_item,stable_key}'=c.stable_key
   AND c.payload#>>'{knowledge_version,authority_class}'='research_candidate'
   AND c.payload#>>'{knowledge_version,review_status}'='unreviewed' AND c.payload#>>'{knowledge_version,status}'='candidate'
   AND c.payload#>'{knowledge_version,knowledge_version_id}'='null'::jsonb
   AND c.payload#>'{knowledge_version,knowledge_item_id}'='null'::jsonb
   AND c.payload#>'{knowledge_version,version_no}'='1'::jsonb
   AND c.payload#>'{knowledge_version,agreement_scope}'='{}'::jsonb AND c.payload#>'{knowledge_version,level_scope}'='{}'::jsonb
   AND c.payload#>'{knowledge_version,effective_from}'='null'::jsonb AND c.payload#>'{knowledge_version,effective_to}'='null'::jsonb
   AND (c.payload#>'{knowledge_version,bidding_system_key}'='null'::jsonb OR
        c.payload#>>'{knowledge_version,bidding_system_key}'=p_scope->>'system_profile')
   AND jsonb_typeof(c.payload#>'{knowledge_version,provenance}')='object'
   AND jsonb_typeof(c.payload#>'{knowledge_version,method_version}')='string' AND length(c.payload#>>'{knowledge_version,method_version}')>0
   AND c.payload#>'{knowledge_version_source,knowledge_version_id}'='null'::jsonb
   AND c.payload#>'{knowledge_version_source,source_id}'='null'::jsonb
   AND c.payload#>>'{knowledge_version_source,relation_type}'='derived_from'
   AND jsonb_typeof(c.payload#>'{knowledge_version_source,source_locator}')='object',
   'DEFINITION_NATIVE_CANDIDATE_SCOPE');
 PERFORM bidding.video_definition_need(bidding.video_definition_int(c.payload#>'{knowledge_version,version_no}',1)=1,'DEFINITION_INITIAL_VERSION_REQUIRED');
 d:=c.payload#>'{knowledge_version,content,definition_evidence}';
 PERFORM bidding.video_definition_need(bidding.video_definition_keys(d,ARRAY['schema','knowledge_domain','knowledge_kind','statement_text',
   'statement_sha256','source_binding','owner_confirmation','scope','requires_52_cards','semantic_confidence','source_authorization','admission'])
   AND d->>'schema'='card-play-definition-proposal-v1' AND d->>'knowledge_domain'='CARD_PLAY'
   AND d->>'knowledge_kind'='DEFINITION' AND d->>'scope'='DEFINITION_RECOGNITION_ONLY' AND d->'requires_52_cards'='false'::jsonb
   AND d->'semantic_confidence'='null'::jsonb AND d->'source_authorization'='null'::jsonb
   AND bidding.video_definition_keys(d->'source_binding',ARRAY['source_candidate_id','source_drive_id','source_sha256','source_segment_id',
       'interval_seconds','raw_text','raw_text_sha256'])
   AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['source_candidate_id','source_drive_id','source_segment_id','raw_text']) AS h(k)
       WHERE jsonb_typeof(d->'source_binding'->k)<>'string' OR length(d->'source_binding'->>k)=0)
   AND d#>>'{source_binding,source_sha256}' ~ '^[0-9a-f]{64}$'
   AND jsonb_typeof(d#>'{source_binding,source_sha256}')='string' AND jsonb_typeof(d#>'{source_binding,raw_text_sha256}')='string'
   AND jsonb_typeof(d->'statement_sha256')='string'
   AND jsonb_typeof(d->'statement_text')='string' AND length(d->>'statement_text')>0
   AND bidding.video_definition_keys(d->'owner_confirmation',ARRAY['status','message_ref','record_sha256','confirmed_role','named_person','independent_audio_review'])
   AND d#>>'{owner_confirmation,status}'='OWNER_CONFIRMED' AND d#>>'{owner_confirmation,confirmed_role}'='teacher'
   AND d#>'{owner_confirmation,named_person}'='null'::jsonb AND d#>'{owner_confirmation,independent_audio_review}'='false'::jsonb
   AND jsonb_typeof(d#>'{owner_confirmation,message_ref}')='string' AND length(d#>>'{owner_confirmation,message_ref}')>0
   AND d#>>'{owner_confirmation,record_sha256}' ~ '^[0-9a-f]{64}$'
   AND jsonb_typeof(d#>'{owner_confirmation,record_sha256}')='string'
   AND bidding.video_definition_keys(d->'admission',ARRAY['status','required_checks','verification_receipts'])
   AND d#>>'{admission,status}'='BLOCKED' AND d#>'{admission,verification_receipts}'='[]'::jsonb
   AND d#>'{admission,required_checks}'='["BOUNDARY_TESTS","BRIDGE_LOGIC","CANON_CONFLICT_SCAN","CANON_INTEGRITY","CANON_REGRESSION","EXPLANATION_COMPLETENESS","HIDDEN_INFORMATION_FIREWALL","INTERFERENCE_TESTS","NEGATIVE_TESTS","POSITIVE_TESTS","ROLLBACK_RESTORE","SEMANTIC_PARSE","SOURCE_AUTHORITY","SOURCE_BINDING","SPEAKER_IDENTITY","TRANSCRIPT_BINDING"]'::jsonb
   AND encode(public.digest(convert_to(d->>'statement_text','UTF8'),'sha256'),'hex')=d->>'statement_sha256'
   AND encode(public.digest(convert_to(d#>>'{source_binding,raw_text}','UTF8'),'sha256'),'hex')=d#>>'{source_binding,raw_text_sha256}',
   'DEFINITION_TYPED_SOURCE_REQUIRED');
 span:=d#>'{source_binding,interval_seconds}';
 PERFORM bidding.video_definition_need(jsonb_typeof(span)='array' AND jsonb_array_length(span)=2
   AND jsonb_typeof(span->0)='number' AND jsonb_typeof(span->1)='number'
   AND (span->>0)::numeric>=0 AND (span->>0)::numeric<(span->>1)::numeric,'DEFINITION_SOURCE_INTERVAL');
 RETURN jsonb_build_object('school',c.school_id::text,'profile',p_scope->>'system_profile',
   'level',p_scope->>'learner_level','scope',p_scope->>'activation_scope');
END $$;

CREATE FUNCTION bidding.video_definition_bounded_root() RETURNS void
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public,bidding,pg_temp AS $$
BEGIN
 PERFORM bidding.video_definition_need(current_setting('statement_timeout')::interval>interval '0'
   AND current_setting('statement_timeout')::interval<=interval '3 seconds'
   AND current_setting('lock_timeout')::interval>interval '0'
   AND current_setting('lock_timeout')::interval<=interval '1 second','DEFINITION_BOUNDED_TIMEOUTS_REQUIRED');
END $$;

CREATE FUNCTION bidding.video_definition_metadata_fence() RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp AS $$
BEGIN
 PERFORM bidding.video_definition_need(current_setting('transaction_read_only')='off','DEFINITION_RW_ROOT_REQUIRED');
 PERFORM bidding.video_definition_bounded_root();
 -- Coarse source-only fence, intentionally not advertised as production-qualified.
 -- Locks last through the CALLER root transaction, not just function return.
 -- Pin the ENTIRE consumed assignment readset, including phantom inserts.
 -- This precedes candidate/FK locks and every binding-row lock in the RPC.
 LOCK TABLE bidding.video_definition_store_binding IN SHARE MODE;
 LOCK TABLE public.analysis_candidate IN EXCLUSIVE MODE;
 LOCK TABLE public.source,bidding.video_canon_source_policy,bidding.video_canon_verifier_registry,
 bidding.video_canon_definition_state,public.knowledge_item,public.knowledge_version,public.knowledge_version_source,
 public.canon_activation,public.knowledge_relation,bidding.rule,bidding.runtime_activation,bidding.rule_conflict,
 bidding.rule_test,bidding.rule_test_run IN SHARE MODE;
END $$;

CREATE FUNCTION bidding.video_definition_require_binding(p_id uuid,p_action text)
RETURNS bidding.video_definition_store_binding LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE b bidding.video_definition_store_binding%ROWTYPE; enabled boolean;
BEGIN
 PERFORM bidding.video_definition_need(current_setting('transaction_read_only')='off','DEFINITION_RW_ROOT_REQUIRED');
 PERFORM bidding.video_definition_bounded_root();
 -- Cheap native precheck is not authority through commit: repeat under fences.
 SELECT c.enabled INTO enabled FROM bidding.video_definition_store_control c WHERE singleton;
 PERFORM bidding.video_definition_need(enabled IS TRUE,'DEFINITION_STORE_DISABLED');
 SELECT * INTO b FROM bidding.video_definition_store_binding WHERE binding_id=p_id;
 PERFORM bidding.video_definition_need(FOUND AND b.status='active' AND b.retired_at IS NULL
   AND b.action=p_action AND b.execution_principal=session_user::text
   AND b.valid_from<=clock_timestamp() AND clock_timestamp()<b.valid_to
   AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user AND r.rolcanlogin
       AND pg_has_role(r.oid,b.capability,'MEMBER')),'DEFINITION_NATIVE_SCOPE_BINDING_REQUIRED');
 PERFORM bidding.video_definition_metadata_fence();
 SELECT c.enabled INTO enabled FROM bidding.video_definition_store_control c WHERE singleton FOR SHARE;
 PERFORM bidding.video_definition_need(enabled IS TRUE,'DEFINITION_STORE_DISABLED');
 SELECT * INTO b FROM bidding.video_definition_store_binding WHERE binding_id=p_id FOR SHARE;
 PERFORM bidding.video_definition_need(FOUND AND b.status='active' AND b.retired_at IS NULL
   AND b.action=p_action AND b.execution_principal=session_user::text
   AND b.valid_from<=clock_timestamp() AND clock_timestamp()<b.valid_to
   AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user AND r.rolcanlogin
       AND pg_has_role(r.oid,b.capability,'MEMBER')),'DEFINITION_NATIVE_SCOPE_BINDING_REQUIRED');
 PERFORM bidding.video_definition_need(b.scope_sha256=bidding.video_definition_digest('DEFINITION_CONSUMER_SCOPE_V1',b.consumer_scope)
   AND b.school_id::text=bidding.video_definition_scope(b.analysis_candidate_id,b.consumer_scope)->>'school'
   AND EXISTS(SELECT 1 FROM public.analysis_candidate c WHERE c.analysis_candidate_id=b.analysis_candidate_id
       AND c.payload_hash=b.candidate_payload_hash),'DEFINITION_BINDING_STALE_OR_FOREIGN');
 RETURN b;
END $$;

CREATE FUNCTION bidding.video_definition_policy(p_candidate uuid,p_scope jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c public.analysis_candidate%ROWTYPE; d jsonb; p bidding.video_canon_source_policy%ROWTYPE; n integer;
BEGIN
 PERFORM bidding.video_definition_scope(p_candidate,p_scope);
 SELECT * INTO STRICT c FROM public.analysis_candidate WHERE analysis_candidate_id=p_candidate;
 d:=c.payload#>'{knowledge_version,content,definition_evidence,source_binding}';
 SELECT count(*) INTO n FROM bidding.video_canon_source_policy WHERE school_id=c.school_id AND source_id=c.source_id
   AND source_sha256=d->>'source_sha256' AND video_file_id=d->>'source_drive_id'
   AND system_profile=p_scope->>'system_profile' AND learner_level=p_scope->>'learner_level'
   AND policy_version='school-video-auto-canon-v1';
 PERFORM bidding.video_definition_need(n=1,'DEFINITION_EXACT_SOURCE_POLICY_REQUIRED');
 SELECT * INTO STRICT p FROM bidding.video_canon_source_policy WHERE school_id=c.school_id AND source_id=c.source_id
   AND source_sha256=d->>'source_sha256' AND video_file_id=d->>'source_drive_id'
   AND system_profile=p_scope->>'system_profile' AND learner_level=p_scope->>'learner_level'
   AND policy_version='school-video-auto-canon-v1';
 PERFORM bidding.video_definition_need(p.status='active' AND p.retired_at IS NULL AND p.valid_from<=clock_timestamp()
   AND (p.valid_to IS NULL OR clock_timestamp()<p.valid_to),'DEFINITION_SOURCE_POLICY_REVOKED_OR_EXPIRED');
 RETURN jsonb_build_object('school_key',p_scope->>'school_key','source_sha256',p.source_sha256,'video_file_id',p.video_file_id,
   'teacher_ids',to_jsonb(p.teacher_ids),'semantic_scopes',to_jsonb(p.semantic_scopes),'system_profile',p.system_profile,
   'learner_level',p.learner_level,'policy_version',p.policy_version,'authorization_evidence_sha256',p.authorization_evidence_sha256,
   'status',p.status,'valid_from',p.valid_from,'valid_to',p.valid_to,'retired_at',p.retired_at);
END $$;

CREATE FUNCTION bidding.video_definition_descriptor(p_candidate uuid,p jsonb,p_hash text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c public.analysis_candidate%ROWTYPE; native_scope jsonb; policy jsonb; state jsonb; r jsonb; reg record; n integer;
BEGIN
 PERFORM bidding.video_definition_need(bidding.video_definition_keys(p,ARRAY['schema','candidate_payload','candidate_payload_hash','scope',
   'canon_snapshot_sha256','gate_profile_sha256','definition_test_state_sha256','definition_restore_binding_sha256',
   'checks','source_binding','speaker_id','source_class','semantic_scope','semantic_confidence'])
   AND p->>'schema'='video-canon-definition-admission-v1' AND p_hash ~ '^[0-9a-f]{64}$','DEFINITION_BUNDLE_SCHEMA');
 native_scope:=bidding.video_definition_scope(p_candidate,p->'scope');
 SELECT * INTO STRICT c FROM public.analysis_candidate WHERE analysis_candidate_id=p_candidate;
 PERFORM bidding.video_definition_need(p->>'candidate_payload_hash'=c.payload_hash
   AND (p->'candidate_payload')::text=c.payload::text
   AND (p->'source_binding')::text=(c.payload#>'{knowledge_version,content,definition_evidence,source_binding}')::text
   AND p->>'gate_profile_sha256'='30b2cb4435a3b0a68b45cffbbf88ee1550557edb140d7483b69a9fe6353f6daf'
   AND p->>'canon_snapshot_sha256' ~ '^[0-9a-f]{64}$' AND p->>'definition_test_state_sha256' ~ '^[0-9a-f]{64}$'
   AND p->>'definition_restore_binding_sha256' ~ '^[0-9a-f]{64}$'
   AND p->>'source_class'='SCHOOL_PRIMARY_EVIDENCE' AND p->>'semantic_scope'='DEFINITION_RECOGNITION_ONLY'
   AND jsonb_typeof(p->'semantic_confidence')='number' AND (p->>'semantic_confidence')::numeric BETWEEN 0.95 AND 1,
   'DEFINITION_CANDIDATE_SOURCE_BINDING');
 policy:=bidding.video_definition_policy(p_candidate,p->'scope');
 PERFORM bidding.video_definition_need(jsonb_typeof(p->'speaker_id')='string' AND policy->'teacher_ids' ? (p->>'speaker_id')
   AND policy->'semantic_scopes' ? (p->>'semantic_scope'),'DEFINITION_AUTHORIZED_TEACHER_SCOPE');
 state:=bidding.video_definition_test_state(p_candidate,p->'scope');
 PERFORM bidding.video_definition_need(p->>'definition_test_state_sha256'=state->>'state_sha256'
   AND p->>'canon_snapshot_sha256'=state#>>'{context,school_snapshot_sha256}','DEFINITION_CURRENT_TEST_OR_CANON_SNAPSHOT');
 PERFORM bidding.video_definition_need(jsonb_typeof(p->'checks')='array' AND jsonb_array_length(p->'checks')=16
   AND (SELECT count(DISTINCT value->>'check_id')=16 FROM jsonb_array_elements(p->'checks')),'DEFINITION_ALL16_ASSIGNMENTS_REQUIRED');
 FOR r IN SELECT value FROM jsonb_array_elements(p->'checks') LOOP
   PERFORM bidding.video_definition_need(bidding.video_definition_keys(r,ARRAY['check_id','result','verifier_family','verifier_version',
     'execution_principal','assurance_level','evidence_sha256','canon_snapshot_sha256'])
     AND r->>'check_id'=ANY(ARRAY['SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING','SEMANTIC_PARSE',
       'EXPLANATION_COMPLETENESS','BRIDGE_LOGIC','HIDDEN_INFORMATION_FIREWALL','POSITIVE_TESTS','NEGATIVE_TESTS','BOUNDARY_TESTS',
       'INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'])
     AND r->>'result'='PASS' AND r->>'evidence_sha256' ~ '^[0-9a-f]{64}$'
     AND jsonb_typeof(r->'verifier_version')='string' AND length(r->>'verifier_version') BETWEEN 1 AND 512
     AND jsonb_typeof(r->'execution_principal')='string' AND length(r->>'execution_principal') BETWEEN 1 AND 512,
     'DEFINITION_CHECK_ASSIGNMENT_BINDING');
   SELECT * INTO reg FROM bidding.video_canon_verifier_registry WHERE verifier_family=r->>'verifier_family' AND status='active';
   PERFORM bidding.video_definition_need(FOUND AND r->>'check_id'=ANY(reg.allowed_check_ids)
     AND r->>'assurance_level' IN ('I0','I1','I2','I3') AND reg.max_assurance_level IN ('I1','I2','I3')
     AND substring(r->>'assurance_level' FROM 2)::integer<=substring(reg.max_assurance_level FROM 2)::integer,
     'DEFINITION_REGISTRY_OR_ASSURANCE');
   SELECT count(*) INTO n FROM bidding.video_definition_store_binding b JOIN pg_catalog.pg_roles login ON login.rolname=b.execution_principal
   WHERE b.analysis_candidate_id=p_candidate AND b.candidate_payload_hash=c.payload_hash AND b.consumer_scope=p->'scope'
     AND b.action='ATTEST' AND b.capability=reg.database_role AND b.execution_principal=r->>'execution_principal'
     AND b.verifier_version=r->>'verifier_version' AND r->>'check_id'=ANY(b.allowed_check_ids)
     AND b.status='active' AND b.retired_at IS NULL AND b.valid_from<=clock_timestamp() AND clock_timestamp()<b.valid_to
     AND login.rolcanlogin AND pg_has_role(login.oid,b.capability,'MEMBER');
   PERFORM bidding.video_definition_need(n=1,'DEFINITION_CURRENT_NATIVE_EXECUTOR_BINDING');
   IF r->>'check_id'=ANY(ARRAY['SEMANTIC_PARSE','BRIDGE_LOGIC','HIDDEN_INFORMATION_FIREWALL']) THEN
     PERFORM bidding.video_definition_need(r->>'assurance_level' IN ('I2','I3'),'DEFINITION_HIGH_ASSURANCE_REQUIRED');
   END IF;
   IF r->>'check_id'=ANY(ARRAY['CANON_REGRESSION','CANON_INTEGRITY','CANON_CONFLICT_SCAN','ROLLBACK_RESTORE']) THEN
     PERFORM bidding.video_definition_need(r->>'canon_snapshot_sha256'=p->>'canon_snapshot_sha256','DEFINITION_STATEFUL_SNAPSHOT');
   ELSE
     PERFORM bidding.video_definition_need(r->'canon_snapshot_sha256'='null'::jsonb,'DEFINITION_STATELESS_SNAPSHOT_CLAIM');
   END IF;
 END LOOP;
 PERFORM bidding.video_definition_need((SELECT count(DISTINCT value->>'execution_principal')=3 FROM jsonb_array_elements(p->'checks')
     WHERE value->>'check_id'=ANY(ARRAY['SEMANTIC_PARSE','BRIDGE_LOGIC','HIDDEN_INFORMATION_FIREWALL']))
   AND (SELECT value->>'verifier_family' FROM jsonb_array_elements(p->'checks') WHERE value->>'check_id'='SEMANTIC_PARSE')
       IS DISTINCT FROM (SELECT value->>'verifier_family' FROM jsonb_array_elements(p->'checks') WHERE value->>'check_id'='BRIDGE_LOGIC'),
   'DEFINITION_INDEPENDENT_VERIFIERS_REQUIRED');
 RETURN state;
END $$;

CREATE FUNCTION bidding.video_definition_control_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,bidding,pg_temp AS $$
BEGIN
 IF TG_OP<>'UPDATE' OR NEW.singleton IS DISTINCT FROM OLD.singleton OR NEW.generation<>OLD.generation+1 THEN
   RAISE EXCEPTION 'DEFINITION_CONTROL_IMMUTABLE_HISTORY' USING ERRCODE='55000';
 END IF;
 NEW.changed_at:=clock_timestamp(); NEW.changed_by_principal:=session_user;
 RETURN NEW;
END $$;
CREATE TRIGGER video_definition_control_guard BEFORE UPDATE OR DELETE ON bidding.video_definition_store_control
 FOR EACH ROW EXECUTE FUNCTION bidding.video_definition_control_guard();

CREATE FUNCTION bidding.video_definition_binding_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE c public.analysis_candidate%ROWTYPE; reg bidding.video_canon_verifier_registry%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'DEFINITION_BINDING_HISTORY_DELETE_FORBIDDEN' USING ERRCODE='55000'; END IF;
 IF TG_OP='UPDATE' THEN
   PERFORM bidding.video_definition_need(OLD.status='active' AND NEW.status='revoked'
     AND (to_jsonb(NEW)-ARRAY['status','retired_at','valid_to'])=(to_jsonb(OLD)-ARRAY['status','retired_at','valid_to']),
     'DEFINITION_BINDING_IMMUTABLE_ONLY_REVOKE');
   NEW.retired_at:=clock_timestamp();
   NEW.valid_to:=CASE WHEN OLD.valid_from<NEW.retired_at THEN least(OLD.valid_to,NEW.retired_at) ELSE OLD.valid_to END;
   RETURN NEW;
 END IF;
 PERFORM bidding.video_definition_scope(NEW.analysis_candidate_id,NEW.consumer_scope);
 SELECT * INTO STRICT c FROM public.analysis_candidate WHERE analysis_candidate_id=NEW.analysis_candidate_id;
 PERFORM bidding.video_definition_need(NEW.recorded_by_principal=session_user AND NEW.status='active' AND NEW.retired_at IS NULL
   AND c.school_id=NEW.school_id AND c.payload_hash=NEW.candidate_payload_hash
   AND NEW.scope_sha256=bidding.video_definition_digest('DEFINITION_CONSUMER_SCOPE_V1',NEW.consumer_scope)
   AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles login WHERE login.rolname=NEW.execution_principal AND login.rolcanlogin
       AND pg_has_role(login.oid,NEW.capability,'MEMBER')),'DEFINITION_POLICY_NATIVE_IDENTITY_REQUIRED');
 IF NEW.action='ATTEST' THEN
   SELECT * INTO reg FROM bidding.video_canon_verifier_registry WHERE database_role=NEW.capability AND status='active';
   PERFORM bidding.video_definition_need(FOUND AND NEW.allowed_check_ids<@reg.allowed_check_ids
     AND array_position(NEW.allowed_check_ids,NULL) IS NULL
     AND cardinality(NEW.allowed_check_ids)=(SELECT count(DISTINCT x) FROM unnest(NEW.allowed_check_ids) x)
     AND NEW.verifier_version IS NOT NULL AND btrim(NEW.verifier_version)<>''
     AND NEW.implementation_sha256 ~ '^[0-9a-f]{64}$','DEFINITION_POLICY_EXISTING_VERIFIER_REQUIRED');
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER video_definition_binding_guard BEFORE INSERT OR UPDATE OR DELETE ON bidding.video_definition_store_binding
 FOR EACH ROW EXECUTE FUNCTION bidding.video_definition_binding_guard();

CREATE FUNCTION bidding.video_definition_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'DEFINITION_STORE_HISTORY_TRUNCATE_FORBIDDEN' USING ERRCODE='55000'; END $$;
CREATE TRIGGER video_definition_binding_no_truncate BEFORE TRUNCATE ON bidding.video_definition_store_binding
 FOR EACH STATEMENT EXECUTE FUNCTION bidding.video_definition_no_truncate();
CREATE TRIGGER video_definition_control_no_truncate BEFORE TRUNCATE ON bidding.video_definition_store_control
 FOR EACH STATEMENT EXECUTE FUNCTION bidding.video_definition_no_truncate();

CREATE FUNCTION bidding.video_definition_binding_for(p_candidate uuid,p_hash text,p_scope jsonb,p_action text,p_capability text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE result uuid; n integer;
BEGIN
 SELECT count(*),min(binding_id::text)::uuid INTO n,result FROM bidding.video_definition_store_binding
 WHERE analysis_candidate_id=p_candidate AND candidate_payload_hash=p_hash AND consumer_scope=p_scope
   AND action=p_action AND execution_principal=session_user AND status='active' AND retired_at IS NULL
   AND valid_from<=clock_timestamp() AND clock_timestamp()<valid_to AND (p_capability IS NULL OR capability=p_capability);
 PERFORM bidding.video_definition_need(n=1,'DEFINITION_UNIQUE_SCOPED_BINDING_REQUIRED');
 PERFORM bidding.video_definition_require_binding(result,p_action);
 RETURN result;
END $$;

CREATE FUNCTION bidding.video_definition_validate_bundle_row(n bidding.video_canon_ai_verification_bundle) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE b bidding.video_definition_store_binding%ROWTYPE; raw json; expected uuid;
BEGIN
 PERFORM bidding.video_definition_need(octet_length(n.bundle_canonical_json) BETWEEN 1 AND 1048576
   AND encode(public.digest(convert_to(n.bundle_canonical_json,'UTF8'),'sha256'),'hex')=n.verification_bundle_sha256,
   'DEFINITION_RAW_BUNDLE_HASH_REQUIRED');
 -- Byte commitment is verified BEFORE parsing; duplicate keys are never normalized away.
 raw:=n.bundle_canonical_json::json; PERFORM bidding.video_definition_unique_json(raw);
 PERFORM bidding.video_definition_need((raw::jsonb)::text=n.bundle_payload::text,'DEFINITION_RAW_PARSED_BINDING');
 expected:=bidding.video_definition_binding_for(n.analysis_candidate_id,n.candidate_payload_hash,n.bundle_payload->'scope','REGISTER_BUNDLE');
 b:=bidding.video_definition_require_binding(expected,'REGISTER_BUNDLE');
 PERFORM bidding.video_definition_need(b.school_id=n.school_id,'DEFINITION_BUNDLE_FOREIGN_SCHOOL');
 PERFORM bidding.video_definition_descriptor(n.analysis_candidate_id,n.bundle_payload,n.verification_bundle_sha256);
END $$;

CREATE FUNCTION bidding.video_definition_validate_check_row(n bidding.video_canon_ai_verification) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE bundle bidding.video_canon_ai_verification_bundle%ROWTYPE; b bidding.video_definition_store_binding%ROWTYPE;
 reg bidding.video_canon_verifier_registry%ROWTYPE; assignment jsonb; expected uuid;
BEGIN
 SELECT * INTO STRICT bundle FROM bidding.video_canon_ai_verification_bundle
 WHERE video_canon_ai_verification_bundle_id=n.video_canon_ai_verification_bundle_id;
 PERFORM bidding.video_definition_need(bundle.bundle_payload->>'schema'='video-canon-definition-admission-v1'
   AND n.analysis_candidate_id=bundle.analysis_candidate_id AND n.school_id=bundle.school_id
   AND n.candidate_payload_hash=bundle.candidate_payload_hash AND n.verification_bundle_sha256=bundle.verification_bundle_sha256
   AND n.execution_principal=session_user::text,'DEFINITION_NATIVE_RECEIPT_ACTOR_BINDING');
 SELECT * INTO reg FROM bidding.video_canon_verifier_registry WHERE verifier_family=n.verifier_family AND status='active';
 PERFORM bidding.video_definition_need(FOUND,'DEFINITION_VERIFIER_FAMILY_REVOKED');
 expected:=bidding.video_definition_binding_for(n.analysis_candidate_id,n.candidate_payload_hash,bundle.bundle_payload->'scope','ATTEST',reg.database_role);
 b:=bidding.video_definition_require_binding(expected,'ATTEST');
 PERFORM bidding.video_definition_need(n.verifier_version=b.verifier_version AND n.check_id=ANY(b.allowed_check_ids),
   'DEFINITION_RECEIPT_IMPLEMENTATION_OR_CHECK');
 PERFORM bidding.video_definition_descriptor(n.analysis_candidate_id,bundle.bundle_payload,n.verification_bundle_sha256);
 SELECT value INTO assignment FROM jsonb_array_elements(bundle.bundle_payload->'checks')
 WHERE value->>'check_id'=n.check_id AND value->>'execution_principal'=session_user::text;
 PERFORM bidding.video_definition_need(FOUND AND assignment->>'result'=n.result AND assignment->>'verifier_family'=n.verifier_family
   AND assignment->>'verifier_version'=n.verifier_version AND assignment->>'assurance_level'=n.assurance_level
   AND assignment->>'evidence_sha256'=n.evidence_sha256
   AND (assignment->>'canon_snapshot_sha256') IS NOT DISTINCT FROM n.canon_snapshot_sha256,
   'DEFINITION_GENUINE_ASSIGNED_RECEIPT_REQUIRED');
END $$;

-- This trigger remains installed on rollback, even after old BIDDING bodies return.
CREATE FUNCTION bidding.video_definition_denial_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp AS $$
DECLARE definition boolean:=false; enabled boolean;
BEGIN
 IF TG_TABLE_NAME='video_canon_ai_verification_bundle' THEN
   IF TG_OP<>'DELETE' THEN definition:=NEW.bundle_payload->>'schema'='video-canon-definition-admission-v1'; END IF;
   IF TG_OP<>'INSERT' THEN definition:=definition OR OLD.bundle_payload->>'schema'='video-canon-definition-admission-v1'; END IF;
 ELSE
   IF TG_OP<>'DELETE' THEN SELECT EXISTS(SELECT 1 FROM bidding.video_canon_ai_verification_bundle b
       WHERE b.video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id
       AND b.bundle_payload->>'schema'='video-canon-definition-admission-v1') INTO definition; END IF;
   IF TG_OP<>'INSERT' THEN definition:=definition OR EXISTS(SELECT 1 FROM bidding.video_canon_ai_verification_bundle b
       WHERE b.video_canon_ai_verification_bundle_id=OLD.video_canon_ai_verification_bundle_id
       AND b.bundle_payload->>'schema'='video-canon-definition-admission-v1'); END IF;
 END IF;
 IF definition IS TRUE THEN
   PERFORM bidding.video_definition_bounded_root();
   SELECT c.enabled INTO enabled FROM bidding.video_definition_store_control c WHERE singleton FOR SHARE;
   PERFORM bidding.video_definition_need(enabled IS TRUE,'DEFINITION_STORE_DISABLED');
   PERFORM bidding.video_definition_need(TG_OP='INSERT','DEFINITION_APPEND_ONLY');
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
CREATE TRIGGER video_definition_bundle_denial BEFORE INSERT OR UPDATE OR DELETE ON bidding.video_canon_ai_verification_bundle
 FOR EACH ROW EXECUTE FUNCTION bidding.video_definition_denial_guard();
CREATE TRIGGER video_definition_check_denial BEFORE INSERT OR UPDATE OR DELETE ON bidding.video_canon_ai_verification
 FOR EACH ROW EXECUTE FUNCTION bidding.video_definition_denial_guard();

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification_bundle()
RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,public,bidding AS $$
DECLARE v_candidate public.analysis_candidate%ROWTYPE; v_decoded jsonb; v_computed text;
BEGIN

    -- DEFINITION successor branch. Non-DEFINITION body below is exact0329 source.
    IF NEW.bundle_payload->>'schema'='video-canon-definition-admission-v1' THEN
        PERFORM bidding.video_definition_validate_bundle_row(NEW);
        RETURN NEW;
    END IF;
    SELECT * INTO v_candidate FROM public.analysis_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id;
    IF NOT FOUND OR v_candidate.school_id<>NEW.school_id
       OR v_candidate.payload_hash<>NEW.candidate_payload_hash
       OR v_candidate.candidate_type<>'video_school_canon_candidate' THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CANDIDATE_MISMATCH' USING ERRCODE='23514';
    END IF;
    BEGIN
        v_decoded := NEW.bundle_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CANONICAL_JSON_INVALID' USING ERRCODE='23514';
    END;
    v_computed := encode(public.digest(convert_to(NEW.bundle_canonical_json,'UTF8'),'sha256'),'hex');
    IF v_decoded<>NEW.bundle_payload OR v_computed<>NEW.verification_bundle_sha256
       OR NEW.bundle_payload->>'schema'<>'video-canon-ai-promotion-v1'
       OR NEW.bundle_payload->>'policy_version'<>'school-video-auto-canon-v1'
       OR NEW.bundle_payload->>'candidate_payload_hash'<>NEW.candidate_payload_hash
       OR NEW.bundle_payload->'candidate_payload'<>v_candidate.payload
       OR NOT ((NEW.bundle_payload->>'canon_snapshot_sha256') ~ '^[0-9a-f]{64}$')
       OR NOT ((NEW.bundle_payload->>'rule_test_state_sha256') ~ '^[0-9a-f]{64}$')
       OR jsonb_typeof(NEW.bundle_payload->'checks')<>'array'
       OR jsonb_array_length(NEW.bundle_payload->'checks')<>16
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(NEW.bundle_payload->'checks') AS c(value)
          WHERE c.value->>'result' IS DISTINCT FROM 'PASS'
             OR c.value->>'check_id' NOT IN (
               'SOURCE_AUTHORITY','SOURCE_BINDING','SPEAKER_IDENTITY','TRANSCRIPT_BINDING',
               'SEMANTIC_PARSE','EXPLANATION_COMPLETENESS','BRIDGE_LOGIC',
               'HIDDEN_INFORMATION_FIREWALL','POSITIVE_TESTS','NEGATIVE_TESTS',
               'BOUNDARY_TESTS','INTERFERENCE_TESTS','CANON_REGRESSION','CANON_INTEGRITY',
               'CANON_CONFLICT_SCAN','ROLLBACK_RESTORE'
             )
       )
       OR (SELECT count(DISTINCT c.value->>'check_id')
             FROM jsonb_array_elements(NEW.bundle_payload->'checks') AS c(value))<>16
       OR jsonb_typeof(NEW.bundle_payload->'effective_period')<>'object'
       OR jsonb_typeof(NEW.bundle_payload->'rollback')<>'object' THEN
        RAISE EXCEPTION 'VIDEO_CANON_BUNDLE_CONTENT_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION bidding.validate_video_canon_verification()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_bound record;
    v_principal bidding.video_canon_verifier_registry%ROWTYPE;
BEGIN

    -- Only a native retained DEFINITION bundle selects this branch.
    IF EXISTS(SELECT 1 FROM bidding.video_canon_ai_verification_bundle b
        WHERE b.video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id
          AND b.bundle_payload->>'schema'='video-canon-definition-admission-v1') THEN
        PERFORM bidding.video_definition_validate_check_row(NEW);
        RETURN NEW;
    END IF;
    SELECT * INTO v_bound FROM bidding.video_canon_bound_candidate
     WHERE analysis_candidate_id=NEW.analysis_candidate_id
       AND video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id;
    IF NOT FOUND OR v_bound.school_id<>NEW.school_id
       OR v_bound.payload_hash<>NEW.candidate_payload_hash
       OR v_bound.bound_candidate_payload_hash<>NEW.candidate_payload_hash
       OR v_bound.verification_bundle_sha256<>NEW.verification_bundle_sha256 THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BINDING_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF v_bound.verification_set_closed THEN
        RAISE EXCEPTION 'VIDEO_CANON_PROMOTED_VERIFICATION_SET_CLOSED' USING ERRCODE='55000';
    END IF;
    SELECT * INTO v_principal FROM bidding.video_canon_verifier_registry
     WHERE database_role=current_user AND status='active';
    IF NOT FOUND OR NEW.verifier_family<>v_principal.verifier_family
       OR NEW.execution_principal<>session_user
       OR NOT (NEW.check_id=ANY(v_principal.allowed_check_ids))
       OR (v_principal.max_assurance_level='I1' AND NEW.assurance_level NOT IN ('I0','I1'))
       OR (v_principal.max_assurance_level='I2' AND NEW.assurance_level NOT IN ('I0','I1','I2'))
       OR v_principal.max_assurance_level NOT IN ('I1','I2','I3') THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFIER_PRINCIPAL_MISMATCH' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_bound.assigned_checks) AS c(value)
          WHERE c.value->>'check_id'=NEW.check_id
            AND c.value->>'result'=NEW.result
            AND c.value->>'verifier_family'=NEW.verifier_family
            AND c.value->>'verifier_version'=NEW.verifier_version
            AND c.value->>'execution_principal'=NEW.execution_principal
            AND c.value->>'assurance_level'=NEW.assurance_level
            AND c.value->>'evidence_sha256'=NEW.evidence_sha256
            AND (c.value->>'canon_snapshot_sha256') IS NOT DISTINCT FROM NEW.canon_snapshot_sha256
       ) THEN
        RAISE EXCEPTION 'VIDEO_CANON_VERIFICATION_BUNDLE_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;


CREATE FUNCTION bidding.register_video_definition_bundle(p_binding_id uuid,p_candidate_id uuid,p_canonical_json text,p_bundle_sha256 text)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE b bidding.video_definition_store_binding%ROWTYPE; result uuid; prior bidding.video_canon_ai_verification_bundle%ROWTYPE; payload jsonb;
BEGIN
 b:=bidding.video_definition_require_binding(p_binding_id,'REGISTER_BUNDLE');
 PERFORM bidding.video_definition_need(b.analysis_candidate_id=p_candidate_id,'DEFINITION_FOREIGN_CANDIDATE');
 PERFORM bidding.video_definition_need(octet_length(p_canonical_json) BETWEEN 1 AND 1048576 AND p_bundle_sha256 ~ '^[0-9a-f]{64}$'
   AND encode(public.digest(convert_to(p_canonical_json,'UTF8'),'sha256'),'hex')=p_bundle_sha256,'DEFINITION_RAW_BUNDLE_HASH_REQUIRED');
 PERFORM bidding.video_definition_unique_json(p_canonical_json::json); payload:=p_canonical_json::jsonb;
 PERFORM bidding.video_definition_need(payload->'scope'=b.consumer_scope,'DEFINITION_FOREIGN_SCOPE');
 PERFORM bidding.video_definition_descriptor(p_candidate_id,payload,p_bundle_sha256);
 INSERT INTO bidding.video_canon_ai_verification_bundle(school_id,analysis_candidate_id,candidate_payload_hash,
   verification_bundle_sha256,bundle_canonical_json,bundle_payload)
 VALUES(b.school_id,p_candidate_id,b.candidate_payload_hash,p_bundle_sha256,p_canonical_json,payload)
 ON CONFLICT(analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256) DO NOTHING
 RETURNING video_canon_ai_verification_bundle_id INTO result;
 IF result IS NULL THEN
   SELECT * INTO STRICT prior FROM bidding.video_canon_ai_verification_bundle WHERE analysis_candidate_id=p_candidate_id
     AND candidate_payload_hash=b.candidate_payload_hash AND verification_bundle_sha256=p_bundle_sha256;
   PERFORM bidding.video_definition_need(prior.school_id=b.school_id AND prior.bundle_canonical_json=p_canonical_json
     AND prior.bundle_payload::text=payload::text,'DEFINITION_BUNDLE_IDEMPOTENCY_CONFLICT');
   result:=prior.video_canon_ai_verification_bundle_id;
 END IF;
 PERFORM bidding.video_definition_require_binding(p_binding_id,'REGISTER_BUNDLE');
 PERFORM bidding.video_definition_descriptor(p_candidate_id,payload,p_bundle_sha256);
 -- Merely registered claims/assignments. This is NOT16genuine rows or admission.
 RETURN result;
END $$;

CREATE FUNCTION bidding.record_video_definition_verification(p_binding_id uuid,p_bundle_id uuid,p_check_id text,p_result text,
 p_verifier_version text,p_assurance_level text,p_evidence_sha256 text,p_snapshot_sha256 text)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE b bidding.video_definition_store_binding%ROWTYPE; bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
 reg bidding.video_canon_verifier_registry%ROWTYPE; result uuid; prior bidding.video_canon_ai_verification%ROWTYPE;
BEGIN
 b:=bidding.video_definition_require_binding(p_binding_id,'ATTEST');
 SELECT * INTO STRICT bundle FROM bidding.video_canon_ai_verification_bundle WHERE video_canon_ai_verification_bundle_id=p_bundle_id;
 PERFORM bidding.video_definition_need(bundle.analysis_candidate_id=b.analysis_candidate_id AND bundle.candidate_payload_hash=b.candidate_payload_hash
   AND bundle.school_id=b.school_id AND bundle.bundle_payload->'scope'=b.consumer_scope,'DEFINITION_FOREIGN_BUNDLE');
 SELECT * INTO STRICT reg FROM bidding.video_canon_verifier_registry WHERE database_role=b.capability AND status='active';
 PERFORM bidding.video_definition_need(p_check_id=ANY(b.allowed_check_ids) AND p_verifier_version=b.verifier_version,
   'DEFINITION_ATTESTATION_ASSIGNMENT');
 INSERT INTO bidding.video_canon_ai_verification(school_id,analysis_candidate_id,video_canon_ai_verification_bundle_id,candidate_payload_hash,
   verification_bundle_sha256,check_id,result,verifier_family,verifier_version,execution_principal,assurance_level,evidence_sha256,canon_snapshot_sha256)
 VALUES(b.school_id,b.analysis_candidate_id,p_bundle_id,b.candidate_payload_hash,bundle.verification_bundle_sha256,p_check_id,p_result,
   reg.verifier_family,p_verifier_version,session_user,p_assurance_level,p_evidence_sha256,p_snapshot_sha256)
 ON CONFLICT(analysis_candidate_id,candidate_payload_hash,verification_bundle_sha256,check_id,verifier_family,verifier_version,execution_principal,evidence_sha256)
 DO NOTHING RETURNING video_canon_ai_verification_id INTO result;
 IF result IS NULL THEN
   SELECT * INTO STRICT prior FROM bidding.video_canon_ai_verification WHERE analysis_candidate_id=b.analysis_candidate_id
     AND candidate_payload_hash=b.candidate_payload_hash AND verification_bundle_sha256=bundle.verification_bundle_sha256
     AND check_id=p_check_id AND verifier_family=reg.verifier_family AND verifier_version=p_verifier_version
     AND execution_principal=session_user AND evidence_sha256=p_evidence_sha256;
   PERFORM bidding.video_definition_need(prior.result=p_result AND prior.assurance_level=p_assurance_level
     AND prior.canon_snapshot_sha256 IS NOT DISTINCT FROM p_snapshot_sha256,'DEFINITION_ATTESTATION_IDEMPOTENCY_CONFLICT');
   result:=prior.video_canon_ai_verification_id;
 END IF;
 PERFORM bidding.video_definition_require_binding(p_binding_id,'ATTEST');
 PERFORM bidding.video_definition_descriptor(b.analysis_candidate_id,bundle.bundle_payload,bundle.verification_bundle_sha256);
 RETURN result;
END $$;

CREATE FUNCTION bidding.read_current_video_definition_bundle(p_binding_id uuid,p_bundle_id uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE b bidding.video_definition_store_binding%ROWTYPE; bundle bidding.video_canon_ai_verification_bundle%ROWTYPE;
 check_rows jsonb; registry jsonb; members jsonb; policy jsonb; state jsonb; result jsonb;
BEGIN
 b:=bidding.video_definition_require_binding(p_binding_id,'READ_CURRENT');
 SELECT * INTO STRICT bundle FROM bidding.video_canon_ai_verification_bundle WHERE video_canon_ai_verification_bundle_id=p_bundle_id;
 PERFORM bidding.video_definition_need(bundle.analysis_candidate_id=b.analysis_candidate_id AND bundle.candidate_payload_hash=b.candidate_payload_hash
   AND bundle.school_id=b.school_id AND bundle.bundle_payload->'scope'=b.consumer_scope,'DEFINITION_FOREIGN_BUNDLE');
 state:=bidding.video_definition_descriptor(b.analysis_candidate_id,bundle.bundle_payload,bundle.verification_bundle_sha256);
 SELECT COALESCE(jsonb_agg(jsonb_build_object('check_id',v.check_id,'result',v.result,'verifier_family',v.verifier_family,
   'verifier_version',v.verifier_version,'execution_principal',v.execution_principal,'assurance_level',v.assurance_level,
   'evidence_sha256',v.evidence_sha256,'canon_snapshot_sha256',v.canon_snapshot_sha256,'candidate_payload_hash',v.candidate_payload_hash,
   'verification_bundle_sha256',v.verification_bundle_sha256,'school_key',b.consumer_scope->>'school_key') ORDER BY v.check_id COLLATE "C"),'[]'::jsonb)
 INTO check_rows FROM bidding.video_canon_ai_verification v WHERE v.video_canon_ai_verification_bundle_id=p_bundle_id
   AND v.analysis_candidate_id=b.analysis_candidate_id AND v.school_id=b.school_id AND v.candidate_payload_hash=b.candidate_payload_hash
   AND v.verification_bundle_sha256=bundle.verification_bundle_sha256;
 PERFORM bidding.video_definition_need(jsonb_array_length(check_rows)=16 AND
   (SELECT jsonb_agg(value||jsonb_build_object('candidate_payload_hash',b.candidate_payload_hash,
       'verification_bundle_sha256',bundle.verification_bundle_sha256,'school_key',b.consumer_scope->>'school_key')
       ORDER BY value->>'check_id' COLLATE "C") FROM jsonb_array_elements(bundle.bundle_payload->'checks'))::text=check_rows::text,
   'DEFINITION_ALL16_GENUINE_NATIVE_ROWS_REQUIRED');
 SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY r.database_role COLLATE "C"),'[]'::jsonb) INTO registry
 FROM (SELECT database_role,verifier_family,allowed_check_ids,max_assurance_level,status FROM bidding.video_canon_verifier_registry) r;
 SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.execution_principal COLLATE "C",x.database_role COLLATE "C"),'[]'::jsonb) INTO members
 FROM (SELECT DISTINCT v.execution_principal,reg.database_role,login.rolcanlogin,pg_has_role(login.oid,reg.database_role,'MEMBER') is_member
   FROM bidding.video_canon_ai_verification v JOIN bidding.video_canon_verifier_registry reg USING(verifier_family)
   JOIN pg_catalog.pg_roles login ON login.rolname=v.execution_principal WHERE v.video_canon_ai_verification_bundle_id=p_bundle_id) x;
 policy:=bidding.video_definition_policy(b.analysis_candidate_id,b.consumer_scope);
 result:=jsonb_build_object('candidate_payload_hash',b.candidate_payload_hash,'verification_bundle_sha256',bundle.verification_bundle_sha256,
   'scope',b.consumer_scope,'source_binding',bundle.bundle_payload->'source_binding','source_policy',policy,
   'speaker_id',bundle.bundle_payload->'speaker_id','source_class',bundle.bundle_payload->'source_class',
   'semantic_scope',bundle.bundle_payload->'semantic_scope','semantic_confidence',bundle.bundle_payload->'semantic_confidence',
   'canon_snapshot_sha256',bundle.bundle_payload->'canon_snapshot_sha256','checks',check_rows,'registry',registry,
   'memberships',members,'source_status','active');
 PERFORM bidding.video_definition_require_binding(p_binding_id,'READ_CURRENT');
 PERFORM bidding.video_definition_need(state->>'state_sha256'=(bidding.video_definition_descriptor(b.analysis_candidate_id,bundle.bundle_payload,
   bundle.verification_bundle_sha256))->>'state_sha256','DEFINITION_CURRENT_READ_CHANGED');
 RETURN result;
END $$;

CREATE FUNCTION bidding.read_video_definition_test_context(p_binding_id uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp SET TimeZone='UTC' AS $$
DECLARE b bidding.video_definition_store_binding%ROWTYPE; op text; result jsonb;
BEGIN
 SELECT action INTO op FROM bidding.video_definition_store_binding WHERE binding_id=p_binding_id;
 PERFORM bidding.video_definition_need(op IN ('REGISTER_BUNDLE','ATTEST','READ_CURRENT'),'DEFINITION_BINDING_REQUIRED');
 b:=bidding.video_definition_require_binding(p_binding_id,op);
 result:=bidding.video_definition_test_context(b.analysis_candidate_id,b.consumer_scope);
 PERFORM bidding.video_definition_require_binding(p_binding_id,op);
 RETURN result;
END $$;

CREATE FUNCTION bidding.video_definition_commit_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bidding,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='video_canon_ai_verification_bundle' THEN
   IF NEW.bundle_payload->>'schema'='video-canon-definition-admission-v1' THEN
     PERFORM bidding.video_definition_validate_bundle_row(NEW);
   END IF;
 ELSIF EXISTS(SELECT 1 FROM bidding.video_canon_ai_verification_bundle b
   WHERE b.video_canon_ai_verification_bundle_id=NEW.video_canon_ai_verification_bundle_id
   AND b.bundle_payload->>'schema'='video-canon-definition-admission-v1') THEN
   PERFORM bidding.video_definition_validate_check_row(NEW);
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER video_definition_bundle_commit_guard AFTER INSERT ON bidding.video_canon_ai_verification_bundle
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bidding.video_definition_commit_guard();
CREATE CONSTRAINT TRIGGER video_definition_check_commit_guard AFTER INSERT ON bidding.video_canon_ai_verification
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bidding.video_definition_commit_guard();

REVOKE ALL ON FUNCTION bidding.video_definition_need(pg_catalog.bool,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_keys(jsonb,text[]) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_int(jsonb,pg_catalog.int8) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_test_context(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_manifest_digest(jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_test_state(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_canonical_int(jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_digest(text,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_epoch_us(timestamptz) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_unique_json(pg_catalog.json,pg_catalog.int4) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_scope(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_bounded_root() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_metadata_fence() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_require_binding(uuid,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_policy(uuid,jsonb) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_descriptor(uuid,jsonb,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_control_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_binding_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_no_truncate() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_binding_for(uuid,text,jsonb,text,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_validate_bundle_row(bidding.video_canon_ai_verification_bundle) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_validate_check_row(bidding.video_canon_ai_verification) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_denial_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.register_video_definition_bundle(uuid,uuid,text,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.record_video_definition_verification(uuid,uuid,text,text,text,text,text,text) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.read_current_video_definition_bundle(uuid,uuid) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.read_video_definition_test_context(uuid) FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON FUNCTION bidding.video_definition_commit_guard() FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
REVOKE ALL ON bidding.video_definition_store_control,bidding.video_definition_store_binding FROM PUBLIC,bridge_school_reader,bridge_school_worker,bridge_school_app,bridge_school_canon_verifier,bridge_school_canon_promoter,bridge_school_canon_restorer,bridge_school_canon_semantic_verifier,bridge_school_canon_bridge_verifier,bridge_school_canon_firewall_verifier,bridge_school_canon_control_verifier;
DO $new_acl_postflight$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_class t CROSS JOIN LATERAL pg_catalog.aclexplode(
     COALESCE(t.relacl,pg_catalog.acldefault('r',t.relowner))) a
   WHERE t.oid IN ('bidding.video_definition_store_control'::regclass,'bidding.video_definition_store_binding'::regclass)
     AND (pg_catalog.pg_get_userbyid(t.relowner)<>current_user OR a.grantee<>t.relowner)) THEN
   RAISE EXCEPTION 'DEFINITION_UNEXPECTED_NEW_TABLE_ACL'; END IF;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(
     COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
   WHERE p.oid IN ('bidding.video_definition_need(pg_catalog.bool,text)'::regprocedure,'bidding.video_definition_keys(jsonb,text[])'::regprocedure,'bidding.video_definition_int(jsonb,pg_catalog.int8)'::regprocedure,'bidding.video_definition_test_context(uuid,jsonb)'::regprocedure,'bidding.video_definition_manifest_digest(jsonb)'::regprocedure,'bidding.video_definition_test_state(uuid,jsonb)'::regprocedure,'bidding.video_definition_canonical_int(jsonb)'::regprocedure,'bidding.video_definition_digest(text,jsonb)'::regprocedure,'bidding.video_definition_epoch_us(timestamptz)'::regprocedure,'bidding.video_definition_unique_json(pg_catalog.json,pg_catalog.int4)'::regprocedure,'bidding.video_definition_scope(uuid,jsonb)'::regprocedure,'bidding.video_definition_bounded_root()'::regprocedure,'bidding.video_definition_metadata_fence()'::regprocedure,'bidding.video_definition_require_binding(uuid,text)'::regprocedure,'bidding.video_definition_policy(uuid,jsonb)'::regprocedure,'bidding.video_definition_descriptor(uuid,jsonb,text)'::regprocedure,'bidding.video_definition_control_guard()'::regprocedure,'bidding.video_definition_binding_guard()'::regprocedure,'bidding.video_definition_no_truncate()'::regprocedure,'bidding.video_definition_binding_for(uuid,text,jsonb,text,text)'::regprocedure,'bidding.video_definition_validate_bundle_row(bidding.video_canon_ai_verification_bundle)'::regprocedure,'bidding.video_definition_validate_check_row(bidding.video_canon_ai_verification)'::regprocedure,'bidding.video_definition_denial_guard()'::regprocedure,'bidding.register_video_definition_bundle(uuid,uuid,text,text)'::regprocedure,'bidding.record_video_definition_verification(uuid,uuid,text,text,text,text,text,text)'::regprocedure,'bidding.read_current_video_definition_bundle(uuid,uuid)'::regprocedure,'bidding.read_video_definition_test_context(uuid)'::regprocedure,'bidding.video_definition_commit_guard()'::regprocedure) AND (pg_catalog.pg_get_userbyid(p.proowner)<>current_user OR a.grantee<>p.proowner)) THEN
   RAISE EXCEPTION 'DEFINITION_UNEXPECTED_NEW_FUNCTION_ACL'; END IF;
 IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolcanlogin AND NOT r.rolsuper
   AND r.rolname<>current_user AND r.rolname<>session_user AND pg_catalog.pg_has_role(r.oid,current_user,'MEMBER')) THEN
   RAISE EXCEPTION 'DEFINITION_UNREVIEWED_LOGIN_INHERITS_INSTALLATION_OWNER'; END IF;
END $new_acl_postflight$;
INSERT INTO public.schema_migration(migration_key) VALUES('0332_workflow_definition_bundle_store_successor') ON CONFLICT DO NOTHING;
COMMIT;
