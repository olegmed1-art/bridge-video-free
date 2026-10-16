\set ON_ERROR_STOP on
-- One reviewed owner case; not a migration and not a worker RPC.
-- Required psql variables bind one private case from a fresh reviewed owner snapshot.
-- Never publish these variables, private identifiers or private task URLs.
-- External project/endpoint identity and exclusive DDL window remain owner gates.
-- No summaries, requests, task payloads or message bodies are read.
-- Retain the execution result, including any compensating reversal, externally.
BEGIN ISOLATION LEVEL READ COMMITTED;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
-- Fail before reading if an enclosing transaction already fixed a higher-isolation snapshot.
-- Override a REPEATABLE READ session default before any statement snapshot.
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '10s';
SET LOCAL search_path = pg_catalog;
SELECT set_config('bridge.pr1994.database', :'expected_database', true) AS discard_0,
       set_config('bridge.pr1994.branch', :'expected_branch', true) AS discard_1,
       set_config('bridge.pr1994.owner', :'expected_owner', true) AS discard_2,
       set_config('bridge.pr1994.updated_at', :'expected_updated_at', true) AS discard_3,
       set_config('bridge.pr1994.catalog', :'expected_catalog_sha256', true) AS discard_4,
       set_config('bridge.pr1994.action', :'action', true) AS discard_5,
       set_config('bridge.pr1994.work_item_id', :'expected_work_item_id', true) AS discard_6,
       set_config('bridge.pr1994.work_key', :'expected_work_key', true) AS discard_7,
       set_config('bridge.pr1994.task_id', :'expected_task_id', true) AS discard_8,
       set_config('bridge.pr1994.dispatch_id', :'expected_dispatch_id', true) AS discard_9,
       set_config('bridge.pr1994.provider_task_id', :'expected_provider_task_id', true) AS discard_10,
       set_config('bridge.pr1994.prompt_sha256', :'expected_prompt_sha256', true) AS discard_11,
       set_config('bridge.pr1994.original_evidence_sha256', :'expected_original_evidence_sha256', true) AS discard_12,
       set_config('bridge.pr1994.original_completed_at', :'expected_original_completed_at', true) AS discard_13
\gset

DO $capacity$
BEGIN
 IF NOT pg_try_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0)) THEN
   RAISE EXCEPTION 'PR1994_CAPACITY_WRITER_PRESENT';
 END IF;
END $capacity$;
-- Block incoming dependencies and every writer to the protected metadata.
LOCK TABLE autopilot.project_work_item,autopilot.paused_work_reconcile_receipt,
 autopilot.project_planner_state,autopilot.role_dispatch_outbox,autopilot.task,
 autopilot.native_cli_receipt,autopilot.native_cli_config,autopilot.role_registry,
 autopilot.project_work_task
 IN SHARE ROW EXCLUSIVE MODE;

DO $case$
DECLARE
 wid uuid := current_setting('bridge.pr1994.work_item_id')::uuid;
 tid uuid := current_setting('bridge.pr1994.task_id')::uuid;
 did uuid := current_setting('bridge.pr1994.dispatch_id')::uuid;
 packet_sha constant text := 'f1926a98fc7ffa69a04d5c9c4a77166c2f0325687bc9377905a680d79136adc1';
 original_head constant text := '4c26b6eda7617563a6372d1fc0eb82beadd4e910';
 original_code constant text := 'AUDIT_FINDINGS_REPORTED';
 protected_relations constant oid[] := ARRAY[
 'autopilot.project_work_item'::regclass,'autopilot.paused_work_reconcile_receipt'::regclass,
 'autopilot.project_planner_state'::regclass,'autopilot.role_dispatch_outbox'::regclass,
 'autopilot.task'::regclass,'autopilot.native_cli_receipt'::regclass,
 'autopilot.native_cli_config'::regclass,'autopilot.role_registry'::regclass,
 'autopilot.project_work_task'::regclass];
 closed_code constant text := 'TARGET_SUPERSEDED_BY_CURRENT_MAIN';
 direction text := current_setting('bridge.pr1994.action');
 stamp timestamptz := clock_timestamp();
 changed integer;
 w record;
 after_w record;
 before_meta jsonb;
 after_meta jsonb;
 catalog_before text;
 catalog_after text;
 receipt_before jsonb;
 receipt_after jsonb;
 fp text;
 catalog_sql constant text := $catalog_query$SELECT encode(sha256(convert_to(jsonb_build_object(
 'relations',(SELECT jsonb_agg(jsonb_build_array(c.oid::text,n.nspname,c.relname,
   pg_get_userbyid(c.relowner),c.relkind,c.relrowsecurity,c.relforcerowsecurity,
   c.relacl::text,c.reloptions,
   (SELECT jsonb_agg(jsonb_build_array(a.attnum,a.attname,a.atttypid::text,
      a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid))
      ORDER BY a.attnum) FROM pg_attribute a LEFT JOIN pg_attrdef d
      ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
   (SELECT jsonb_agg(jsonb_build_array(k.conname,k.contype,k.convalidated,
      k.condeferrable,k.condeferred,pg_get_constraintdef(k.oid,true))
      ORDER BY k.conname) FROM pg_constraint k WHERE k.conrelid=c.oid)
   ) ORDER BY n.nspname,c.relname)
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='autopilot' AND c.relname IN (
 'project_work_item','paused_work_reconcile_receipt','project_planner_state',
 'role_dispatch_outbox','task','native_cli_receipt','native_cli_config','role_registry','project_work_task')),
 'owner_gate',(SELECT jsonb_build_array(p.oid::regprocedure::text,pg_get_userbyid(p.proowner),
    p.provolatile,p.prosecdef,p.proconfig,p.proacl::text,pg_get_functiondef(p.oid))
    FROM pg_proc p WHERE p.oid='autopilot.role_blocker_requires_owner(text)'::regprocedure),
 'triggers',(SELECT jsonb_agg(jsonb_build_array(t.tgrelid::regclass::text,
    t.tgname,t.tgenabled,t.tgisinternal,pg_get_triggerdef(t.oid,true),
    p.oid::regprocedure::text,pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,
    p.proacl::text,pg_get_functiondef(p.oid)) ORDER BY t.tgrelid::regclass::text,t.tgname)
 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
 WHERE t.tgrelid IN ('autopilot.project_work_item'::regclass,
                    'autopilot.paused_work_reconcile_receipt'::regclass))
 )::text,'UTF8')),'hex')$catalog_query$;
 target_sql constant text := $target_query$SELECT work_item_id,work_key,repository,role,task_kind,target_pr,mailbox_pr,
 priority,state,depends_on_work_item_id,generation,last_observed_head_sha,last_task_id,
 result_code,not_before,probe_lease_owner,probe_lease_epoch,probe_lease_until,
 created_at,updated_at,completed_at,hold_reason,hold_until,progress_token,blocker_fingerprint
 FROM ONLY autopilot.project_work_item
 WHERE work_item_id=current_setting('bridge.pr1994.work_item_id')::uuid$target_query$;
 metadata_sql constant text := $metadata_query$SELECT jsonb_build_object(
 'other_work',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.work_item_id) FROM (
 SELECT work_item_id,work_key,repository,role,task_kind,target_pr,mailbox_pr,priority,
 state,depends_on_work_item_id,generation,last_observed_head_sha,last_task_id,
 result_code,not_before,probe_lease_owner,probe_lease_epoch,probe_lease_until,
 created_at,updated_at,completed_at,hold_reason,hold_until,progress_token,blocker_fingerprint
 FROM autopilot.project_work_item
 WHERE work_item_id<>current_setting('bridge.pr1994.work_item_id')::uuid) x),
 'planner',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.singleton) FROM (
 SELECT singleton,enabled,decision_count,last_decision_code,last_work_item_id,last_decision_at
 FROM autopilot.project_planner_state) x),
 'outbox',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.dispatch_id) FROM (
 SELECT dispatch_id,task_id,role,target_pr,mailbox_pr,status,expected_head_sha,
 mode,github_dispatch_comment_id,delivery_contract_version,updated_at,completed_at
 FROM autopilot.role_dispatch_outbox) x),
 'work_task_lineage',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.work_item_id,x.task_id) FROM (
 SELECT work_item_id,task_id,run_kind,created_at FROM autopilot.project_work_task) x),
 'tasks',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.task_id) FROM (
 SELECT task_id,goal_type,status,terminal_reason_code,updated_at,completed_at FROM autopilot.task) x),
 'native_receipts',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.dispatch_id) FROM (
 SELECT dispatch_id,state,provider_task_id,prompt_sha256,created_at,submission_started_at,
 submitted_at,completed_at,terminal->>'status' AS terminal_status,
 terminal->>'result_code' AS terminal_code,terminal->>'target_head_sha' AS terminal_head,
 terminal->>'provider_evidence_sha256' AS terminal_evidence
 FROM autopilot.native_cli_receipt) x),
 'native_control',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.singleton) FROM (
 SELECT singleton,enabled,cutover_at FROM autopilot.native_cli_config) x),
 'role_control',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.role_id) FROM (
 SELECT role_id,enabled,execution_scope FROM autopilot.role_registry) x),
 'other_closure_receipts',(SELECT jsonb_agg(to_jsonb(x) ORDER BY x.receipt_id) FROM (
 SELECT receipt_id,work_item_id,evidence_token,action,blocker_fingerprint,
 followup_task_id,result_code,created_at FROM autopilot.paused_work_reconcile_receipt
 WHERE work_item_id<>current_setting('bridge.pr1994.work_item_id')::uuid) x)
 )$metadata_query$;
BEGIN
 IF direction NOT IN ('close','reverse')
    OR current_setting('bridge.pr1994.database')=''
    OR current_setting('bridge.pr1994.branch')=''
    OR current_database() IS DISTINCT FROM current_setting('bridge.pr1994.database')
    OR current_setting('neon.branch_id',true) IS DISTINCT FROM current_setting('bridge.pr1994.branch')
    OR current_user IS DISTINCT FROM current_setting('bridge.pr1994.owner') THEN
   RAISE EXCEPTION 'PR1994_TARGET_DRIFT';
 END IF;
 -- Statistics views may otherwise reuse the earlier transaction snapshot.
 PERFORM pg_stat_clear_snapshot();
 IF EXISTS (SELECT FROM pg_stat_activity
    WHERE datname=current_database() AND pid<>pg_backend_pid()
      AND backend_type='client backend' AND state IS DISTINCT FROM 'idle')
    OR EXISTS (SELECT FROM pg_prepared_xacts WHERE database=current_database()) THEN
   RAISE EXCEPTION 'PR1994_UNSERIALIZED_WRITER';
 END IF;
 IF EXISTS (SELECT FROM pg_class WHERE oid=ANY(protected_relations)
       AND (relkind<>'r' OR relrowsecurity OR relforcerowsecurity))
    OR EXISTS (SELECT FROM pg_class c
    WHERE c.oid IN ('autopilot.project_work_item'::regclass,
                    'autopilot.paused_work_reconcile_receipt'::regclass)
      AND (c.relkind<>'r' OR c.relrowsecurity OR c.relforcerowsecurity
           OR pg_get_userbyid(c.relowner) IS DISTINCT FROM current_setting('bridge.pr1994.owner')))
    OR EXISTS (SELECT FROM pg_rewrite
       WHERE ev_class=ANY(protected_relations))
    OR EXISTS (SELECT FROM pg_inherits
       WHERE inhrelid=ANY(protected_relations) OR inhparent=ANY(protected_relations))
    OR EXISTS (SELECT FROM pg_event_trigger WHERE evtenabled<>'D')
    OR EXISTS (SELECT FROM pg_trigger
       WHERE NOT tgisinternal AND (
          tgrelid='autopilot.paused_work_reconcile_receipt'::regclass
          OR (tgrelid='autopilot.project_work_item'::regclass AND tgname NOT IN (
           'project_work_item_enabled_role','autopilot_project_work_dependency_state',
           'autopilot_project_work_dependency_release')))) THEN
   RAISE EXCEPTION 'PR1994_UNREVIEWED_RELATION_OR_TRIGGER';
 END IF;
 EXECUTE catalog_sql INTO catalog_before;
 IF catalog_before IS DISTINCT FROM current_setting('bridge.pr1994.catalog')
    OR current_setting('bridge.pr1994.catalog') !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'PR1994_CATALOG_DRIFT';
 END IF;
 EXECUTE target_sql INTO w;
 IF w.work_item_id IS DISTINCT FROM wid OR w.work_key IS DISTINCT FROM current_setting('bridge.pr1994.work_key')
    OR w.repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free' OR w.role IS DISTINCT FROM 'AUTOPILOT'
    OR NOT COALESCE(w.task_kind IN ('REPOSITORY_AUDIT','REPOSITORY_SECURITY_AUDIT'),false)
    OR w.target_pr IS DISTINCT FROM 1994 OR w.mailbox_pr IS DISTINCT FROM 1703 OR w.generation IS DISTINCT FROM 1
    OR w.last_task_id IS DISTINCT FROM tid
    OR w.last_observed_head_sha IS DISTINCT FROM original_head
    OR w.updated_at IS DISTINCT FROM current_setting('bridge.pr1994.updated_at')::timestamptz
    OR w.depends_on_work_item_id IS NOT NULL OR w.hold_reason IS NOT NULL
    OR w.hold_until IS NOT NULL OR w.probe_lease_owner IS NOT NULL
    OR w.probe_lease_until IS NOT NULL THEN
   RAISE EXCEPTION 'PR1994_WORK_DRIFT';
 END IF;
 IF EXISTS (SELECT FROM autopilot.project_work_item WHERE depends_on_work_item_id=wid)
    OR EXISTS (SELECT FROM autopilot.project_work_item
       WHERE state='ACTIVE' OR probe_lease_until>=clock_timestamp())
    OR EXISTS (SELECT FROM autopilot.task
       WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
         AND status NOT IN ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'))
    OR EXISTS (SELECT FROM autopilot.role_dispatch_outbox
       WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED'))
    OR EXISTS (SELECT FROM autopilot.native_cli_receipt WHERE state<>'TERMINAL')
    OR (SELECT count(*) FROM autopilot.native_cli_config WHERE singleton AND NOT enabled)<>1
    OR autopilot.role_blocker_requires_owner(original_code) IS DISTINCT FROM false
    OR NOT EXISTS (SELECT FROM autopilot.role_registry
       WHERE role_id='AUTOPILOT' AND enabled AND execution_scope='REPOSITORY') THEN
   RAISE EXCEPTION 'PR1994_LIVE_WORK_OR_CONTROL_DRIFT';
 END IF;
 IF NOT EXISTS (SELECT FROM autopilot.task
       WHERE task_id=tid AND status='DONE' AND terminal_reason_code=original_code)
    OR (SELECT count(*) FROM autopilot.project_work_task
       WHERE work_item_id=wid AND task_id=tid AND run_kind='AUDIT')<>1
    OR NOT EXISTS (SELECT FROM autopilot.role_dispatch_outbox
       WHERE dispatch_id=did AND task_id=tid AND target_pr=1994 AND mailbox_pr=1703
         AND repository='olegmed1-art/bridge-video-free' AND role='AUTOPILOT'
         AND expected_head_sha=original_head AND mode='READ_ONLY'
         AND status='CALLBACK_ACCEPTED')
    OR NOT EXISTS (SELECT FROM autopilot.native_cli_receipt
       WHERE dispatch_id=did AND state='TERMINAL'
         AND provider_task_id=current_setting('bridge.pr1994.provider_task_id')
         AND prompt_sha256=current_setting('bridge.pr1994.prompt_sha256')
         AND completed_at=current_setting('bridge.pr1994.original_completed_at')::timestamptz
         AND terminal->>'status'='SUCCEEDED' AND terminal->>'result_code'=original_code
         AND terminal->>'target_head_sha'=original_head
         AND terminal->>'provider_evidence_sha256'=
             current_setting('bridge.pr1994.original_evidence_sha256')) THEN
   RAISE EXCEPTION 'PR1994_AUDIT_LINEAGE_DRIFT';
 END IF;
 EXECUTE metadata_sql INTO before_meta;
 SELECT to_jsonb(r) INTO receipt_before FROM autopilot.paused_work_reconcile_receipt r
 WHERE work_item_id=wid AND evidence_token=packet_sha;
 fp:=encode(sha256(convert_to(original_code||'|'||packet_sha,'UTF8')),'hex');
 IF direction='close' THEN
   IF w.state IS DISTINCT FROM 'BLOCKED' OR w.result_code IS DISTINCT FROM original_code
      OR w.completed_at IS NOT NULL
      OR EXISTS (SELECT FROM autopilot.paused_work_reconcile_receipt WHERE work_item_id=wid) THEN
     RAISE EXCEPTION 'PR1994_CLOSE_PRECONDITION_DRIFT';
   END IF;
   UPDATE ONLY autopilot.project_work_item
   SET state='DONE',result_code=closed_code,completed_at=stamp,updated_at=stamp
   WHERE work_item_id=wid AND state='BLOCKED' AND result_code=original_code
     AND updated_at=w.updated_at AND last_task_id=tid AND generation=1;
   GET DIAGNOSTICS changed=ROW_COUNT;
   IF changed<>1 THEN RAISE EXCEPTION 'PR1994_CHANGED_ROW_COUNT'; END IF;
   INSERT INTO autopilot.paused_work_reconcile_receipt(
     work_item_id,evidence_token,action,blocker_fingerprint,followup_task_id,result_code,created_at)
   VALUES(wid,packet_sha,'CLOSE_SUPERSEDED',fp,NULL,original_code,stamp);
 ELSE
   -- Compensation restores only four fields. It retains the original receipt.
   -- The external owner execution receipt must record this reversal explicitly.
   IF w.state IS DISTINCT FROM 'DONE' OR w.result_code IS DISTINCT FROM closed_code
      OR w.completed_at IS DISTINCT FROM w.updated_at
      OR (SELECT count(*) FROM autopilot.paused_work_reconcile_receipt WHERE work_item_id=wid)<>1
      OR receipt_before IS NULL
      OR receipt_before->>'action' IS DISTINCT FROM 'CLOSE_SUPERSEDED'
      OR receipt_before->>'result_code' IS DISTINCT FROM original_code
      OR receipt_before->>'blocker_fingerprint' IS DISTINCT FROM fp
      OR receipt_before->>'followup_task_id' IS NOT NULL
      OR (receipt_before->>'created_at')::timestamptz IS DISTINCT FROM w.completed_at THEN
     RAISE EXCEPTION 'PR1994_REVERSE_PRECONDITION_DRIFT';
   END IF;
   UPDATE ONLY autopilot.project_work_item
   SET state='BLOCKED',result_code=original_code,completed_at=NULL,updated_at=stamp
   WHERE work_item_id=wid AND state='DONE' AND result_code=closed_code
     AND updated_at=w.updated_at AND completed_at=w.completed_at AND generation=1;
   GET DIAGNOSTICS changed=ROW_COUNT;
   IF changed<>1 THEN RAISE EXCEPTION 'PR1994_CHANGED_ROW_COUNT'; END IF;
 END IF;
 EXECUTE target_sql INTO after_w;
 EXECUTE metadata_sql INTO after_meta;
 EXECUTE catalog_sql INTO catalog_after;
 SELECT to_jsonb(r) INTO receipt_after FROM autopilot.paused_work_reconcile_receipt r
 WHERE work_item_id=wid AND evidence_token=packet_sha;
 -- Refresh backend activity again immediately before the postcheck.
 PERFORM pg_stat_clear_snapshot();
 IF after_meta IS DISTINCT FROM before_meta OR catalog_after IS DISTINCT FROM catalog_before
    OR (to_jsonb(after_w)-ARRAY['state','result_code','completed_at','updated_at'])
       IS DISTINCT FROM (to_jsonb(w)-ARRAY['state','result_code','completed_at','updated_at'])
    OR after_w.updated_at IS DISTINCT FROM stamp
    OR (SELECT count(*) FROM autopilot.paused_work_reconcile_receipt WHERE work_item_id=wid)<>1
    OR EXISTS (SELECT FROM pg_prepared_xacts WHERE database=current_database())
    OR EXISTS (SELECT FROM pg_stat_activity WHERE datname=current_database()
       AND pid<>pg_backend_pid() AND backend_type='client backend' AND state IS DISTINCT FROM 'idle') THEN
   RAISE EXCEPTION 'PR1994_POSTCHECK_SIDE_EFFECT';
 END IF;
 IF direction='close' THEN
   IF after_w.state IS DISTINCT FROM 'DONE' OR after_w.result_code IS DISTINCT FROM closed_code
      OR after_w.completed_at IS DISTINCT FROM stamp
      OR receipt_after->>'action' IS DISTINCT FROM 'CLOSE_SUPERSEDED'
      OR receipt_after->>'result_code' IS DISTINCT FROM original_code
      OR receipt_after->>'blocker_fingerprint' IS DISTINCT FROM fp
      OR receipt_after->>'followup_task_id' IS NOT NULL
      OR (receipt_after->>'created_at')::timestamptz IS DISTINCT FROM stamp THEN
     RAISE EXCEPTION 'PR1994_CLOSE_POSTCHECK';
   END IF;
 ELSE
   IF after_w.state IS DISTINCT FROM 'BLOCKED' OR after_w.result_code IS DISTINCT FROM original_code
      OR after_w.completed_at IS NOT NULL OR receipt_after IS DISTINCT FROM receipt_before THEN
     RAISE EXCEPTION 'PR1994_REVERSE_POSTCHECK';
   END IF;
 END IF;
 RAISE NOTICE 'PR1994_VERIFIED action=% changed_items=1 retained_receipts=1',direction;
END $case$;
COMMIT;
-- Safe execution receipt fields; capture after COMMIT and independently re-read.
SELECT work_item_id,state,result_code,updated_at,completed_at
 FROM autopilot.project_work_item
 WHERE work_item_id=:'expected_work_item_id'::uuid;
SELECT evidence_token,action,result_code,created_at
 FROM autopilot.paused_work_reconcile_receipt
 WHERE work_item_id=:'expected_work_item_id'::uuid;
