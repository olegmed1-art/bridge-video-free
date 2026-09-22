\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
DO $pre$
BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration WHERE migration_key='0370_autopilot_codex_send_intent') THEN
  RAISE EXCEPTION 'AUTOPILOT_PROVIDER_BUDGET_REQUIRES_0370';
 END IF;
END $pre$;
CREATE TABLE autopilot.migration_0371_function_backup(function_key text PRIMARY KEY,definition text NOT NULL);
REVOKE ALL ON autopilot.migration_0371_function_backup FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;
INSERT INTO autopilot.migration_0371_function_backup
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid) FROM pg_proc p
WHERE p.oid IN (
 'autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'::regprocedure,
 'autopilot.project_progress_candidates(integer)'::regprocedure,
 'autopilot.reconcile_project_progress(uuid,timestamptz,text,timestamptz)'::regprocedure);

-- Provider health is evidence, not fresh retry authority. Audit retries use
-- the existing atomic per-head/lifetime receipt budget and exact route proof.
DO $patch$
DECLARE saved record; source text; anchor text; replacement text;
BEGIN
 FOR saved IN SELECT * FROM autopilot.migration_0371_function_backup LOOP
  source:=saved.definition;
  anchor:=$a$'ROLE_DISPATCH_RESPONSE_INVALID')$a$;
  replacement:=$a$'ROLE_DISPATCH_RESPONSE_INVALID','CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE')$a$;
  IF (length(source)-length(replace(source,anchor,'')))/length(anchor)<>1 THEN
   RAISE EXCEPTION 'AUTOPILOT_PROVIDER_BUDGET_SOURCE_DRIFT';
  END IF;
  source:=replace(source,anchor,replacement);
  IF saved.function_key='autopilot.reconcile_project_progress(uuid,timestamp with time zone,text,timestamp with time zone)' THEN
   anchor:=$a$IF w.result_code='ROLE_DISPATCH_RESPONSE_INVALID' THEN$a$;
   IF strpos(source,anchor)=0 THEN RAISE EXCEPTION 'AUTOPILOT_PROVIDER_BUDGET_TRANSPORT_DRIFT'; END IF;
   source:=replace(source,anchor,
    $a$IF w.result_code IN ('ROLE_DISPATCH_RESPONSE_INVALID','CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE') THEN$a$);
   anchor:=$a$AND success.mode='READ_ONLY' AND success.status='CALLBACK_ACCEPTED'$a$;
   IF strpos(source,anchor)=0 THEN RAISE EXCEPTION 'AUTOPILOT_PROVIDER_BUDGET_SUCCESS_DRIFT'; END IF;
   source:=replace(source,anchor,anchor||$guard$
         AND EXISTS(SELECT FROM autopilot.task completed WHERE completed.task_id=success.task_id
           AND completed.safe_summary_json->>'status' IN ('SUCCEEDED','BLOCKED')
           AND COALESCE(NULLIF(completed.safe_summary_json->>'result_code',''),completed.terminal_reason_code)
             NOT IN ('CODEX_ACK_DEADLINE_EXCEEDED','CODEX_RESULT_DEADLINE_EXCEEDED','CODEX_PROVIDER_GENERIC_FAILURE'))$guard$);
  END IF;
  EXECUTE source;
 END LOOP;
END $patch$;
INSERT INTO public.schema_migration(migration_key) VALUES('0371_autopilot_provider_retry_budget');
COMMIT;
