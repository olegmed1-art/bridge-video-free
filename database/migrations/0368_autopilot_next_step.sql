\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (SELECT FROM public.schema_migration
   WHERE migration_key='0367_autopilot_reliable_progress') THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_REQUIRES_0367';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0368_function_backup (
 function_key text PRIMARY KEY, definition text NOT NULL
);
INSERT INTO autopilot.migration_0368_function_backup
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc p WHERE p.oid IN (
 'autopilot.on_project_work_task_terminal()'::regprocedure,
 'autopilot.on_role_task_terminal()'::regprocedure,
 'autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)'::regprocedure,
 'autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'::regprocedure
);
REVOKE ALL ON autopilot.migration_0368_function_backup FROM PUBLIC;

-- Durable budgets survive worker restarts, newer CI timestamps and rollback.
CREATE TABLE IF NOT EXISTS autopilot.project_progress_receipt (
 work_item_id uuid NOT NULL REFERENCES autopilot.project_work_item,
 head_sha text NOT NULL CHECK(head_sha ~ '^[0-9a-f]{40}$'),
 source_task_id uuid NOT NULL REFERENCES autopilot.task,
 reason text NOT NULL CHECK(reason IN ('HEAD_CHANGED','CI_COMPLETED','TRANSPORT_RECOVERED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(work_item_id,head_sha)
);
REVOKE ALL ON autopilot.project_progress_receipt FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;

DO $patch$
DECLARE source text; patched text;
BEGIN
 SELECT definition INTO STRICT source FROM autopilot.migration_0368_function_backup
 WHERE function_key='autopilot.on_role_task_terminal()';
 IF position('    result_code := COALESCE(' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_ROLE_SOURCE_DRIFT';
 END IF;
 patched:=replace(source,'    result_code := COALESCE(',
 $guard$    -- Fence before the repair controller can attach an older task's child.
    IF EXISTS(SELECT FROM autopilot.project_work_task m WHERE m.task_id=NEW.task_id)
       AND NOT EXISTS(SELECT FROM autopilot.project_work_task m
         JOIN autopilot.project_work_item w USING(work_item_id)
         WHERE m.task_id=NEW.task_id AND w.last_task_id=NEW.task_id) THEN
      RETURN NEW;
    END IF;
    result_code := COALESCE($guard$);
 EXECUTE patched;

 SELECT definition INTO STRICT source FROM autopilot.migration_0368_function_backup
 WHERE function_key='autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)';
 IF position('    IF item.generation > 333331 THEN' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_PLANNER_SOURCE_DRIFT';
 END IF;
 patched:=replace(source,'    IF item.generation > 333331 THEN',
 $guard$    -- NEXT_STEP_PENDING_HEAD_FENCE: admission for B cannot dispatch C.
    IF item.state='READY' AND EXISTS(SELECT FROM autopilot.project_progress_receipt r
        WHERE r.work_item_id=item.work_item_id AND r.source_task_id=item.last_task_id)
       AND p_observed_head_sha IS DISTINCT FROM (
         SELECT r.head_sha FROM autopilot.project_progress_receipt r
         WHERE r.work_item_id=item.work_item_id AND r.source_task_id=item.last_task_id
         ORDER BY r.created_at DESC LIMIT 1) THEN
      UPDATE autopilot.project_work_item SET state='PAUSED',
        hold_reason='RECOVERY_HEAD_CHANGED',probe_lease_owner=NULL,probe_lease_until=NULL,
        updated_at=clock_timestamp() WHERE work_item_id=item.work_item_id;
      RETURN QUERY SELECT NULL::uuid,'PAUSED'::text,false;
      RETURN;
    END IF;
    IF item.generation > 333331 THEN$guard$);
 EXECUTE patched;

 SELECT definition INTO STRICT source FROM autopilot.migration_0368_function_backup
 WHERE function_key='autopilot.on_project_work_task_terminal()';
 IF position('    -- The 0323 trigger runs first' in source)=0
    OR position('NEW.status = ''DONE'' AND mapping.run_kind IN (''AUDIT'', ''VERIFY'')' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_TERMINAL_SOURCE_DRIFT';
 END IF;
 patched:=replace(source,'    -- The 0323 trigger runs first',
 $guard$    -- NEXT_STEP_TERMINAL_FENCE: a retained older task cannot overwrite
    -- a later generation or release its dependents.
    PERFORM 1 FROM autopilot.project_work_item
     WHERE work_item_id=mapping.work_item_id AND last_task_id=NEW.task_id
     FOR UPDATE;
    IF NOT FOUND THEN RETURN NEW; END IF;

    -- The 0323 trigger runs first$guard$);
 patched:=replace(patched,
 'NEW.status = ''DONE'' AND mapping.run_kind IN (''AUDIT'', ''VERIFY'')',
 $guard$NEW.status = 'DONE' AND mapping.run_kind IN ('AUDIT', 'VERIFY')
       AND NOT COALESCE(autopilot.role_blocker_requires_owner(terminal_code),true)
       AND terminal_code NOT IN ('REPAIR_REQUIRED','AUDIT_FINDINGS_REPORTED',
         'ROOT_CAUSE_IDENTIFIED','BOUNDED_REPAIR_UNIT_SELECTED','NEXT_REPAIR_UNIT_CONFIRMED')
       AND (NOT EXISTS(SELECT FROM autopilot.project_work_item w
              WHERE w.work_item_id=mapping.work_item_id
                AND w.task_kind IN ('REPOSITORY_AUDIT','REPOSITORY_SECURITY_AUDIT'))
            OR (NEW.safe_summary_json->>'status'='SUCCEEDED' AND terminal_code IN (
              'AUDIT_VERIFIED_NO_REPAIR','EXACT_HEAD_AUDIT_PASSED','AUDIT_PASSED',
              'REPAIR_VERIFIED','REPAIR_VERIFIED_GREEN','CONSUMER_CONTRACT_VERIFIED_GREEN',
              'KNOWLEDGE_READY','VIDEO_READY')))$guard$);
 EXECUTE patched;

 SELECT definition INTO STRICT source FROM autopilot.migration_0368_function_backup
 WHERE function_key='autopilot.reconcile_paused_project_work(uuid,text,text,text,text)';
 IF position(' IF autopilot.role_blocker_requires_owner(effective_code)' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_RECONCILE_SOURCE_DRIFT';
 END IF;
 patched:=replace(source,' IF autopilot.role_blocker_requires_owner(effective_code)',
 ' IF w.hold_reason=''OWNER_HOLD'' OR autopilot.role_blocker_requires_owner(effective_code)');
 -- Do not leave a legacy RPC path around the new durable budget. Disposition
 -- closure stays available; repair chains remain under their own controller.
 IF position(' effective_code:=w.result_code;' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_LEGACY_SOURCE_DRIFT';
 END IF;
 patched:=replace(patched,' effective_code:=w.result_code;',
 $guard$ IF COALESCE(p_target_disposition NOT IN ('MERGED','SUPERSEDED','SUBSUMED_BY_MAIN'),true) AND (
   (w.task_kind IN ('REPOSITORY_AUDIT','REPOSITORY_SECURITY_AUDIT')
    AND w.result_code IN ('EXACT_HEAD_CHECKS_FAILED','LIVE_HEAD_AND_CHECKS_UNVERIFIED',
      'QA_GATES_FAILED','TECHNICAL_TEST_FAILURE','ROLE_DISPATCH_RESPONSE_INVALID'))
   OR EXISTS(SELECT FROM autopilot.project_work_task m
     WHERE m.work_item_id=w.work_item_id AND m.run_kind IN ('REPAIR','VERIFY'))
 ) AND w.hold_reason IS DISTINCT FROM 'OWNER_HOLD'
   AND NOT COALESCE(autopilot.role_blocker_requires_owner(w.result_code),true) THEN
   RETURN 'NO_CHANGE';
 END IF;
 effective_code:=w.result_code;$guard$);
 -- Old clients also cannot rearm an unresolved dependency.
 IF position('SET state=''READY'',' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_DEPENDENCY_SOURCE_DRIFT';
 END IF;
 patched:=replace(patched,'SET state=''READY'',',
 $guard$SET state=CASE WHEN w.depends_on_work_item_id IS NOT NULL AND NOT EXISTS(
              SELECT FROM autopilot.project_work_item parent
              WHERE parent.work_item_id=w.depends_on_work_item_id AND parent.state='DONE')
              THEN 'WAITING_DEPENDENCY' ELSE 'READY' END,$guard$);
 EXECUTE patched;
END $patch$;

CREATE FUNCTION autopilot.project_progress_candidates(p_limit integer DEFAULT 50)
RETURNS SETOF jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $f$
 SELECT jsonb_build_object('work_item_id',w.work_item_id,'target_pr',w.target_pr,
   'updated_at',w.updated_at,'last_observed_head_sha',w.last_observed_head_sha,
   'hold_reason',w.hold_reason,'result_code',w.result_code)
 FROM autopilot.project_work_item w
 WHERE w.repository='olegmed1-art/bridge-video-free' AND w.state='PAUSED'
   AND w.task_kind IN ('REPOSITORY_AUDIT','REPOSITORY_SECURITY_AUDIT')
   AND w.hold_reason IS DISTINCT FROM 'OWNER_HOLD'
   AND NOT COALESCE(autopilot.role_blocker_requires_owner(w.result_code),true)
   AND w.result_code IN ('EXACT_HEAD_CHECKS_FAILED','LIVE_HEAD_AND_CHECKS_UNVERIFIED',
     'QA_GATES_FAILED','TECHNICAL_TEST_FAILURE','ROLE_DISPATCH_RESPONSE_INVALID')
   AND EXISTS(SELECT FROM autopilot.role_registry r
              WHERE r.role_id=w.role AND r.enabled AND r.execution_scope='REPOSITORY')
 ORDER BY (SELECT max(r.created_at) FROM autopilot.project_progress_receipt r
            WHERE r.work_item_id=w.work_item_id) NULLS FIRST,w.updated_at,w.work_item_id
 LIMIT LEAST(GREATEST(p_limit,0),50)
$f$;
REVOKE ALL ON FUNCTION autopilot.project_progress_candidates(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.project_progress_candidates(integer) TO bridge_school_worker;

CREATE FUNCTION autopilot.reconcile_project_progress(
 p_work_item_id uuid,p_observed_updated_at timestamptz,p_head_sha text,
 p_checks_completed_at timestamptz DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $f$
DECLARE w autopilot.project_work_item; t autopilot.task; o autopilot.role_dispatch_outbox;
 reason text;
BEGIN
 IF p_head_sha IS NULL OR p_head_sha !~ '^[0-9a-f]{40}$'
    OR p_checks_completed_at>clock_timestamp() THEN
   RAISE EXCEPTION 'AUTOPILOT_NEXT_STEP_INVALID_EVIDENCE';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
 SELECT * INTO w FROM autopilot.project_work_item WHERE work_item_id=p_work_item_id FOR UPDATE;
 IF NOT FOUND OR w.state<>'PAUSED' OR w.updated_at IS DISTINCT FROM p_observed_updated_at
    OR w.repository<>'olegmed1-art/bridge-video-free'
    OR w.task_kind NOT IN ('REPOSITORY_AUDIT','REPOSITORY_SECURITY_AUDIT')
    OR w.task_spec_json->>'execution_scope' IS DISTINCT FROM 'REPOSITORY'
    OR w.task_spec_json->'production_mutation' IS DISTINCT FROM 'false'::jsonb
    OR COALESCE(w.task_spec_json->>'repair_policy','') NOT IN ('DISABLED','BOUNDED')
    OR jsonb_typeof(w.task_spec_json->'required_checks') IS DISTINCT FROM 'array'
    OR w.task_spec_json->'required_checks'='[]'::jsonb
    OR NOT COALESCE(w.task_spec_json->'forbidden_actions' @>
      '["canon_mutation","credential_access","deploy","drive_write","force_push",
        "main_write","merge","neon_write","paid_action","production_write",
        "real_media_processing","server_write"]'::jsonb,false)
    OR (w.task_spec_json->>'repair_policy'='BOUNDED' AND (
       jsonb_typeof(w.task_spec_json->'expected_changed_files') IS DISTINCT FROM 'array'
       OR w.task_spec_json->'expected_changed_files'='[]'::jsonb))
    OR w.hold_reason='OWNER_HOLD'
    OR (w.task_spec_json ? 'expected_head_sha'
        AND w.task_spec_json->>'expected_head_sha' IS DISTINCT FROM p_head_sha)
    OR COALESCE(autopilot.role_blocker_requires_owner(w.result_code),true)
    OR NOT EXISTS(SELECT FROM autopilot.role_registry r
       WHERE r.role_id=w.role AND r.enabled AND r.execution_scope='REPOSITORY')
    OR w.result_code NOT IN ('EXACT_HEAD_CHECKS_FAILED','LIVE_HEAD_AND_CHECKS_UNVERIFIED',
      'QA_GATES_FAILED','TECHNICAL_TEST_FAILURE','ROLE_DISPATCH_RESPONSE_INVALID') THEN
   RETURN 'NO_CHANGE';
 END IF;
 -- A repair/verification chain must use its own controller and retry budget.
 SELECT * INTO t FROM autopilot.task WHERE task_id=w.last_task_id;
 IF NOT FOUND OR t.goal_type<>'CHATGPT_ROLE_DISPATCH_V1'
    OR t.status NOT IN ('DONE','FAILED_CLOSED')
    OR NOT EXISTS(SELECT FROM autopilot.project_work_task m
         WHERE m.work_item_id=w.work_item_id AND m.task_id=t.task_id AND m.run_kind='AUDIT')
    OR EXISTS(SELECT FROM autopilot.role_dispatch_followup f
         JOIN autopilot.project_work_task m ON m.task_id=f.parent_task_id
         WHERE m.work_item_id=w.work_item_id)
    OR t.goal_json->>'repository' IS DISTINCT FROM w.repository
    OR t.goal_json->>'role' IS DISTINCT FROM w.role
    OR t.goal_json->>'target_pr' IS DISTINCT FROM w.target_pr::text
    OR t.goal_json->>'expected_head_sha' IS DISTINCT FROM w.last_observed_head_sha THEN
   RETURN 'NO_CHANGE';
 END IF;
 IF (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE task_id=t.task_id)<>1 THEN
   RETURN 'NO_CHANGE';
 END IF;
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE task_id=t.task_id;
 IF o.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED') OR o.delivery_contract_version IS DISTINCT FROM 3
    OR o.mode IS DISTINCT FROM 'READ_ONLY' OR o.repository IS DISTINCT FROM w.repository
    OR o.role IS DISTINCT FROM w.role OR o.target_pr IS DISTINCT FROM w.target_pr
    OR o.expected_head_sha IS DISTINCT FROM w.last_observed_head_sha THEN RETURN 'NO_CHANGE'; END IF;
 IF EXISTS(SELECT FROM autopilot.project_work_item other
     WHERE other.repository=w.repository AND other.target_pr=w.target_pr
       AND (other.state='ACTIVE' OR other.probe_lease_until>=now()))
    OR EXISTS(SELECT FROM autopilot.task active
       WHERE active.goal_json->>'repository'=w.repository
         AND active.goal_json->>'target_pr'=w.target_pr::text
         AND active.status NOT IN ('DONE','FAILED_CLOSED','OWNER_REQUIRED','BUDGET_STOP','CANCELLED'))
    OR EXISTS(SELECT FROM autopilot.role_dispatch_outbox active
       WHERE active.repository=w.repository AND active.target_pr=w.target_pr
         AND active.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED'))
    OR EXISTS(SELECT FROM autopilot.role_dispatch_mailbox_registry m WHERE m.mailbox_pr=w.target_pr)
    OR EXISTS(SELECT FROM autopilot.role_dispatch_outbox d WHERE d.repository=w.repository
              AND d.github_dispatch_comment_id=w.target_pr)
    OR (w.depends_on_work_item_id IS NOT NULL AND NOT EXISTS(SELECT FROM autopilot.project_work_item p
              WHERE p.work_item_id=w.depends_on_work_item_id AND p.state='DONE')) THEN
   RETURN 'NO_CHANGE';
 END IF;
 IF EXISTS(SELECT FROM autopilot.project_progress_receipt r
       WHERE r.work_item_id=w.work_item_id AND r.head_sha=p_head_sha)
    OR (SELECT count(*) FROM autopilot.project_progress_receipt r WHERE r.work_item_id=w.work_item_id)>=3 THEN
   RETURN 'RETRY_BUDGET_EXHAUSTED';
 END IF;
 IF w.result_code='ROLE_DISPATCH_RESPONSE_INVALID' THEN
   -- Contract failures need evidence of successful delivery on the same route,
   -- not merely another commit or another green CI run.
   IF NOT EXISTS(SELECT FROM autopilot.role_dispatch_outbox success
       WHERE success.repository=o.repository AND success.mailbox_pr=o.mailbox_pr
         AND success.delivery_contract_version=o.delivery_contract_version
         AND success.executor_id IS NOT DISTINCT FROM o.executor_id
         AND success.target_chat_id IS NOT DISTINCT FROM o.target_chat_id
         AND success.mode='READ_ONLY' AND success.status='CALLBACK_ACCEPTED'
         AND success.sent_at>w.updated_at AND success.completed_at>w.updated_at) THEN
     RETURN 'NO_CHANGE';
   END IF;
   reason:='TRANSPORT_RECOVERED';
 ELSIF p_head_sha IS DISTINCT FROM w.last_observed_head_sha THEN
   reason:='HEAD_CHANGED';
 ELSIF p_checks_completed_at>w.updated_at THEN reason:='CI_COMPLETED';
 ELSE RETURN 'NO_CHANGE';
 END IF;
 INSERT INTO autopilot.project_progress_receipt(work_item_id,head_sha,source_task_id,reason)
 VALUES(w.work_item_id,p_head_sha,t.task_id,reason);
 -- Preserve original task, scope, result and head until the planner binds its
 -- new READ_ONLY audit. Existing repair policy is neither reset nor expanded.
 UPDATE autopilot.project_work_item SET state='READY',not_before=now(),
   hold_reason=NULL,hold_until=NULL,probe_lease_owner=NULL,probe_lease_until=NULL,
   completed_at=NULL,updated_at=clock_timestamp()
 WHERE work_item_id=w.work_item_id;
 PERFORM pg_notify('autopilot_ready','verified-next-step');
 RETURN 'REAUDIT_READY';
END $f$;
REVOKE ALL ON FUNCTION autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz)
 TO bridge_school_worker;

INSERT INTO public.schema_migration(migration_key) VALUES('0368_autopilot_next_step');
COMMIT;
