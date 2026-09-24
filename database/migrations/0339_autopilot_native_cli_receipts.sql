\set ON_ERROR_STOP on
BEGIN;

DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration WHERE migration_key='0338_autopilot_bounded_publication_permit') THEN
  RAISE EXCEPTION 'NATIVE_REQUIRES_0338';
 END IF;
END $$;

CREATE TABLE autopilot.native_cli_config (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT false,
 cutover_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO autopilot.native_cli_config DEFAULT VALUES;
CREATE TABLE autopilot.native_cli_receipt (
 dispatch_id uuid PRIMARY KEY REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
 owner_name name NOT NULL DEFAULT SESSION_USER,
 request jsonb NOT NULL CHECK(jsonb_typeof(request)='object'),
 state text NOT NULL DEFAULT 'RESERVED' CHECK(state IN ('RESERVED','SUBMITTED','TERMINAL')),
 provider_task_id text UNIQUE CHECK(provider_task_id ~ '^task_[A-Za-z0-9_]{1,120}$'),
 prompt_sha256 text CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
 terminal jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 submission_started_at timestamptz,
 submitted_at timestamptz,
 completed_at timestamptz,
 CHECK(state='RESERVED' OR (provider_task_id IS NOT NULL AND prompt_sha256 IS NOT NULL AND submitted_at IS NOT NULL)),
 CHECK((state='TERMINAL')=(terminal IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE TABLE autopilot.migration_0339_function_backup(function_key text PRIMARY KEY,definition text NOT NULL);
INSERT INTO autopilot.migration_0339_function_backup
 SELECT 'reconcile',pg_get_functiondef('autopilot.reconcile_role_dispatch_callbacks()'::regprocedure)
 UNION ALL SELECT 'followup',pg_get_functiondef('autopilot.on_role_task_terminal()'::regprocedure)
 UNION ALL SELECT 'project_terminal',pg_get_functiondef('autopilot.on_project_work_task_terminal()'::regprocedure);
ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_delivery_contract_version_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_delivery_contract_version_check
 CHECK(delivery_contract_version IN (1,2,3,4));

-- A lost create response is not proof that the provider stopped. Native slots
-- are released only by a retained terminal receipt, never a legacy timeout.
DO $$ DECLARE source text; revised text; BEGIN
 SELECT definition INTO STRICT source FROM autopilot.migration_0339_function_backup WHERE function_key='reconcile';
 IF position('WHERE (status=''PUBLISHED'' AND delivery_deadline_at<=now())' in source)=0
 OR position('OR (status=''SENT'' AND callback_deadline_at<=now())' in source)=0 THEN
  RAISE EXCEPTION 'NATIVE_RECONCILER_SHAPE_CHANGED';
 END IF;
 revised:=replace(source,'WHERE (status=''PUBLISHED'' AND delivery_deadline_at<=now())',
  'WHERE (delivery_contract_version<>4 AND status=''PUBLISHED'' AND delivery_deadline_at<=now())');
 revised:=replace(revised,'OR (status=''SENT'' AND callback_deadline_at<=now())',
  'OR (delivery_contract_version<>4 AND status=''SENT'' AND callback_deadline_at<=now())');
 EXECUTE revised;
END $$;

-- Private authority lock. Outer mutations lock outbox, task, and step first.
CREATE FUNCTION autopilot.native_cli_authority_locked(p_dispatch_id uuid,p_assignment jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE o autopilot.role_dispatch_outbox; a jsonb; w autopilot.project_work_item;
BEGIN
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=p_dispatch_id;
 PERFORM 1 FROM autopilot.role_registry WHERE role_id=o.role FOR SHARE;
 PERFORM 1 FROM autopilot.project_work_task WHERE task_id=o.task_id FOR SHARE;
 SELECT work.* INTO w FROM autopilot.project_work_task m
 JOIN autopilot.project_work_item work USING(work_item_id)
 WHERE m.task_id=o.task_id FOR SHARE OF work;
 SELECT to_jsonb(x) INTO a FROM autopilot.get_dispatch_assignment(p_dispatch_id) x;
 RETURN COALESCE(a IS NOT NULL AND a=p_assignment AND w.state='ACTIVE' AND w.last_task_id=o.task_id,false);
END $$;

CREATE FUNCTION autopilot.native_cli_snapshot(p_dispatch_id uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE r autopilot.native_cli_receipt;
BEGIN
 SELECT * INTO r FROM autopilot.native_cli_receipt WHERE dispatch_id=p_dispatch_id AND owner_name=SESSION_USER;
 IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_RESERVATION_NOT_OWNED'; END IF;
 RETURN jsonb_build_object('state',r.state,'request',r.request,'provider_task_id',r.provider_task_id,'terminal',r.terminal);
END $$;

CREATE FUNCTION autopilot.native_cli_reserve(p_dispatch_id uuid,p_assignment jsonb,p_branch text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE o autopilot.role_dispatch_outbox; r autopilot.native_cli_receipt; t autopilot.task; request jsonb;
BEGIN
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 SELECT * INTO r FROM autopilot.native_cli_receipt WHERE dispatch_id=p_dispatch_id;
 IF FOUND THEN
  IF r.owner_name<>SESSION_USER OR r.request->'assignment' IS DISTINCT FROM p_assignment
     OR r.request->>'branch' IS DISTINCT FROM p_branch THEN RAISE EXCEPTION 'NATIVE_RESERVATION_CONFLICT'; END IF;
  RETURN autopilot.native_cli_snapshot(p_dispatch_id);
 END IF;
 SELECT * INTO t FROM autopilot.task WHERE task_id=o.task_id FOR UPDATE;
 PERFORM 1 FROM autopilot.step_attempt WHERE step_attempt_id=o.step_attempt_id FOR UPDATE;
 IF o.dispatch_id IS NULL OR o.status IS DISTINCT FROM 'PUBLISHED'
 OR o.delivery_contract_version IS DISTINCT FROM 3 OR t.status IS DISTINCT FROM 'WAITING_EXTERNAL'
 OR o.delivery_deadline_at IS NULL OR o.delivery_deadline_at<=clock_timestamp()
 OR o.github_dispatch_comment_id IS NULL
 OR COALESCE(p_branch,'') !~ '^(codex|autopilot|fix)/[A-Za-z0-9_./-]{1,180}$'
 OR position('..' in p_branch)>0 OR p_branch LIKE 'autopilot/dispatch/%'
 OR NOT EXISTS(SELECT FROM autopilot.native_cli_config WHERE enabled AND o.published_at>=cutover_at)
 OR NOT autopilot.native_cli_authority_locked(p_dispatch_id,p_assignment) THEN
  RAISE EXCEPTION 'NATIVE_RESERVATION_INVALID';
 END IF;
 request:=jsonb_build_object('dispatch_id',p_dispatch_id,'reservation_id',gen_random_uuid(),
  'expected_head_sha',o.expected_head_sha,'branch',p_branch,'mode',o.mode,'assignment',p_assignment,
  'target_pr',o.target_pr,'task_fingerprint',o.task_fingerprint);
 INSERT INTO autopilot.native_cli_receipt(dispatch_id,request) VALUES(p_dispatch_id,request);
 UPDATE autopilot.role_dispatch_outbox SET delivery_contract_version=4,updated_at=now() WHERE dispatch_id=p_dispatch_id;
 RETURN autopilot.native_cli_snapshot(p_dispatch_id);
END $$;

CREATE FUNCTION autopilot.native_cli_current(p_request jsonb)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
 SELECT EXISTS(
  SELECT FROM autopilot.native_cli_receipt r
  JOIN autopilot.role_dispatch_outbox o USING(dispatch_id)
  JOIN autopilot.task t USING(task_id)
  JOIN autopilot.project_work_task m USING(task_id)
  JOIN autopilot.project_work_item w USING(work_item_id)
  CROSS JOIN LATERAL autopilot.get_dispatch_assignment(r.dispatch_id) a
  WHERE r.owner_name=SESSION_USER AND r.request=p_request AND r.state IN ('RESERVED','SUBMITTED')
   AND t.status='WAITING_EXTERNAL' AND w.state='ACTIVE' AND w.last_task_id=o.task_id
   AND to_jsonb(a)=p_request->'assignment'
   -- Disablement stops new creation; already acknowledged tasks may drain.
   AND (r.state='SUBMITTED' OR EXISTS(SELECT FROM autopilot.native_cli_config WHERE enabled))
 )
$$;

CREATE FUNCTION autopilot.native_cli_ack(p_request jsonb,p_task_id text,p_prompt_sha256 text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE id uuid; o autopilot.role_dispatch_outbox; r autopilot.native_cli_receipt;
BEGIN
 id:=(p_request->>'dispatch_id')::uuid;
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=id FOR UPDATE;
 SELECT * INTO r FROM autopilot.native_cli_receipt WHERE dispatch_id=id FOR UPDATE;
 IF r.dispatch_id IS NULL OR r.owner_name<>SESSION_USER OR r.request IS DISTINCT FROM p_request
 OR COALESCE(p_task_id,'') !~ '^task_[A-Za-z0-9_]{1,120}$'
 OR COALESCE(p_prompt_sha256,'') !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'NATIVE_ACK_INVALID'; END IF;
 IF r.state<>'RESERVED' THEN
  IF r.provider_task_id IS DISTINCT FROM p_task_id OR r.prompt_sha256 IS DISTINCT FROM p_prompt_sha256 THEN
   RAISE EXCEPTION 'NATIVE_ACK_CONFLICT';
  END IF;
  RETURN autopilot.native_cli_snapshot(id);
 END IF;
 IF o.status IS DISTINCT FROM 'PUBLISHED' OR o.delivery_contract_version IS DISTINCT FROM 4
 OR r.submission_started_at IS NULL THEN
  RAISE EXCEPTION 'NATIVE_ACK_STATE_INVALID';
 END IF;
 -- This records a provider task that ALREADY exists. A pause must not prevent
 -- recovery of its lost ACK; authority for new creation is a separate check.
 UPDATE autopilot.native_cli_receipt SET state='SUBMITTED',provider_task_id=p_task_id,
  prompt_sha256=p_prompt_sha256,submitted_at=clock_timestamp() WHERE dispatch_id=id RETURNING * INTO r;
 UPDATE autopilot.role_dispatch_outbox SET status='SENT',sent_at=r.submitted_at,delivered_at=r.submitted_at,
  callback_deadline_at=r.submitted_at+interval '2 hours',updated_at=now() WHERE dispatch_id=id;
 RETURN autopilot.native_cli_snapshot(id);
END $$;

CREATE FUNCTION autopilot.native_cli_begin(p_request jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE id uuid; r autopilot.native_cli_receipt; o autopilot.role_dispatch_outbox;
BEGIN
 id:=(p_request->>'dispatch_id')::uuid;
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=id FOR UPDATE;
 SELECT * INTO r FROM autopilot.native_cli_receipt WHERE dispatch_id=id FOR UPDATE;
 IF r.dispatch_id IS NULL OR r.owner_name<>SESSION_USER OR r.request IS DISTINCT FROM p_request THEN
  RAISE EXCEPTION 'NATIVE_BEGIN_NOT_OWNED';
 END IF;
 IF r.state<>'RESERVED' OR r.submission_started_at IS NOT NULL THEN RETURN false; END IF;
 PERFORM 1 FROM autopilot.task WHERE task_id=o.task_id FOR UPDATE;
 IF NOT autopilot.native_cli_current(p_request)
 OR NOT autopilot.native_cli_authority_locked(id,p_request->'assignment') THEN RETURN false; END IF;
 UPDATE autopilot.native_cli_receipt SET submission_started_at=clock_timestamp() WHERE dispatch_id=id;
 RETURN true;
END $$;

CREATE FUNCTION autopilot.native_cli_finish(p_request jsonb,p_task_id text,p_terminal jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE id uuid; o autopilot.role_dispatch_outbox; r autopilot.native_cli_receipt;
 t autopilot.task; s autopilot.step_attempt; success boolean; next_state text; keys text[];
BEGIN
 id:=(p_request->>'dispatch_id')::uuid;
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=id FOR UPDATE;
 SELECT * INTO r FROM autopilot.native_cli_receipt WHERE dispatch_id=id FOR UPDATE;
 IF r.dispatch_id IS NULL OR r.owner_name<>SESSION_USER OR r.request IS DISTINCT FROM p_request
 OR r.provider_task_id IS DISTINCT FROM p_task_id OR r.provider_task_id IS NULL
 OR jsonb_typeof(p_terminal) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'NATIVE_TERMINAL_INVALID'; END IF;
 SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(p_terminal) key;
 IF keys IS DISTINCT FROM ARRAY['provider_evidence_sha256','result_code','status','summary','target_head_sha']
 OR EXISTS(SELECT FROM jsonb_each(p_terminal) WHERE jsonb_typeof(value)<>'string')
 OR COALESCE(p_terminal->>'status','') NOT IN ('SUCCEEDED','BLOCKED')
 OR COALESCE(p_terminal->>'result_code','') !~ '^[A-Z][A-Z0-9_]{0,63}$'
 OR COALESCE(p_terminal->>'provider_evidence_sha256','') !~ '^[0-9a-f]{64}$'
 OR COALESCE(p_terminal->>'target_head_sha','') !~ '^[0-9a-f]{40}$'
 OR COALESCE(length(p_terminal->>'summary'),0) NOT BETWEEN 1 AND 160
 OR p_terminal->>'summary' ~ '[[:cntrl:]]' THEN RAISE EXCEPTION 'NATIVE_TERMINAL_INVALID'; END IF;
 IF r.state='TERMINAL' THEN
  IF r.terminal IS DISTINCT FROM p_terminal THEN RAISE EXCEPTION 'NATIVE_TERMINAL_CONFLICT'; END IF;
  RETURN autopilot.native_cli_snapshot(id);
 END IF;
 SELECT * INTO t FROM autopilot.task WHERE task_id=o.task_id FOR UPDATE;
 SELECT * INTO s FROM autopilot.step_attempt WHERE step_attempt_id=o.step_attempt_id FOR UPDATE;
 success:=p_terminal->>'status'='SUCCEEDED';
 IF r.state IS DISTINCT FROM 'SUBMITTED' OR o.status IS DISTINCT FROM 'SENT'
 OR o.delivery_contract_version IS DISTINCT FROM 4 OR t.status IS DISTINCT FROM 'WAITING_EXTERNAL'
 OR s.status IS DISTINCT FROM 'WAITING_EXTERNAL'
 OR (success AND (o.mode NOT IN ('READ_ONLY','VERIFY')
  OR p_terminal->>'target_head_sha' IS DISTINCT FROM o.expected_head_sha
  OR NOT autopilot.native_cli_authority_locked(id,r.request->'assignment'))) THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_AUTHORITY_INVALID';
 END IF;
 INSERT INTO autopilot.evidence(task_id,step_attempt_id,evidence_class,provider,external_ref,content_sha256,metadata_json)
 VALUES(o.task_id,o.step_attempt_id,'CHATGPT_ROLE_DISPATCH_RESULT','ORACLE_RESIDENT',
  'codex-cli:'||p_task_id,p_terminal->>'provider_evidence_sha256',jsonb_build_object(
   'dispatch_id',id,'reservation_id',r.request->>'reservation_id','provider_task_id',p_task_id,
   'prompt_sha256',r.prompt_sha256,'terminal',p_terminal));
 next_state:=CASE WHEN success THEN 'DONE' ELSE 'FAILED_CLOSED' END;
 UPDATE autopilot.step_attempt SET status=CASE WHEN success THEN 'COMPLETED' ELSE 'FAILED_CLOSED' END,
  error_code=CASE WHEN success THEN NULL ELSE p_terminal->>'result_code' END,
  result_summary_json=p_terminal,completed_at=now() WHERE step_attempt_id=o.step_attempt_id;
 UPDATE autopilot.task SET status=next_state,terminal_reason_code=p_terminal->>'result_code',
  safe_summary_json=p_terminal,completed_at=now() WHERE task_id=o.task_id;
 -- The native inactive-authority trigger guard preserves explicit PAUSED
 -- work and newer attempts. Retire a still-current ACTIVE item if that guard
 -- suppressed continuation (for example, its role was disabled).
 IF NOT success THEN
  UPDATE autopilot.project_work_item w SET state='BLOCKED',
   result_code=p_terminal->>'result_code',result_summary=p_terminal->>'summary',
   not_before=now()+interval '15 minutes',updated_at=now(),completed_at=NULL
  FROM autopilot.project_work_task m
  WHERE m.task_id=o.task_id AND w.work_item_id=m.work_item_id
    AND w.state='ACTIVE' AND w.last_task_id=o.task_id;
 END IF;
 UPDATE autopilot.role_dispatch_outbox SET status='CALLBACK_ACCEPTED',completed_at=now(),updated_at=now()
 WHERE dispatch_id=id;
 UPDATE autopilot.native_cli_receipt SET state='TERMINAL',terminal=p_terminal,completed_at=now() WHERE dispatch_id=id;
 PERFORM autopilot.record_event(o.task_id,CASE WHEN success THEN 'TASK_DONE' ELSE 'TASK_FAILED_CLOSED' END,
  'WAITING_EXTERNAL',next_state,jsonb_build_object('dispatch_id',id,'provider_task_id',p_task_id),
  'EXTERNAL_EVENT','SLAVIK_CLI','native-cli-terminal:'||id::text);
 PERFORM pg_notify('autopilot_ready','native-cli-terminal');
 RETURN autopilot.native_cli_snapshot(id);
END $$;

-- Preserve legacy followups. A native task whose role/work was paused can be
-- closed, but must not create a repair task that overrides that pause.
DO $$ DECLARE source text; revised text; BEGIN
 FOR source IN SELECT definition FROM autopilot.migration_0339_function_backup
 WHERE function_key IN ('followup','project_terminal') LOOP
 IF position(E'BEGIN\n' in source)=0 THEN RAISE EXCEPTION 'NATIVE_FOLLOWUP_SHAPE_CHANGED'; END IF;
 revised:=regexp_replace(source,E'BEGIN\n',E'BEGIN\n'
  ||' IF NEW.status IN (''DONE'',''FAILED_CLOSED'',''OWNER_REQUIRED'',''BUDGET_STOP'',''CANCELLED'') AND EXISTS('
  ||' SELECT FROM autopilot.native_cli_receipt r JOIN autopilot.role_dispatch_outbox o USING(dispatch_id)'
  ||' WHERE o.task_id=NEW.task_id AND NOT autopilot.native_cli_authority_locked(r.dispatch_id,r.request->''assignment''))'
  ||E' THEN RETURN NEW; END IF;\n');
 EXECUTE revised;
 END LOOP;
END $$;

REVOKE ALL ON TABLE autopilot.native_cli_config,autopilot.native_cli_receipt,autopilot.migration_0339_function_backup
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.native_cli_authority_locked(uuid,jsonb),autopilot.native_cli_snapshot(uuid),
 autopilot.native_cli_current(jsonb),autopilot.native_cli_begin(jsonb),
 autopilot.native_cli_reserve(uuid,jsonb,text),autopilot.native_cli_ack(jsonb,text,text),
 autopilot.native_cli_finish(jsonb,text,jsonb) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
-- Owner-only draft: runtime privilege activation requires its reviewed native
-- provider installation and authority adapter. No broad table access is added.
INSERT INTO public.schema_migration(migration_key) VALUES('0339_autopilot_native_cli_receipts');
COMMIT;
