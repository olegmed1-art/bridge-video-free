\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.schema_migration
   WHERE migration_key='0366_autopilot_terminal_dispatch_history') THEN
   RAISE EXCEPTION 'AUTOPILOT_RELIABLE_PROGRESS_REQUIRES_0366';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0367_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL
);
INSERT INTO autopilot.migration_0367_function_backup
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc p WHERE p.oid IN (
 'autopilot.register_parallel_work_manifest(text,text)'::regprocedure,
 'autopilot.reconcile_paused_project_work(uuid,text,text,text,text)'::regprocedure
);
REVOKE ALL ON autopilot.migration_0367_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- Preserve deployed validation and 0366's terminal-history guard. Only
-- identical, proven manifest items may reuse their original provenance.
DO $patch$
DECLARE
 source text;
 revised text;
 anchor text := $anchor$
   IF EXISTS (
     SELECT 1 FROM autopilot.project_work_item AS work
     WHERE work.mailbox_pr=candidate_target_pr
$anchor$;
 reuse text := $reuse$
   SELECT * INTO existing_work FROM autopilot.project_work_item AS work
   WHERE work.work_key=candidate_work_key FOR UPDATE;
   IF FOUND THEN
     IF existing_work.repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
        OR existing_work.created_by IS DISTINCT FROM 'AUTOPILOT_PARALLEL_INTAKE'
        OR COALESCE(existing_work.source,'')!~'^REVIEWED_WORKER_RELEASE:[0-9a-f]{64}$'
        OR NOT EXISTS (
          SELECT 1 FROM autopilot.project_work_manifest_receipt AS receipt
          WHERE receipt.manifest_sha256=split_part(existing_work.source,':',2)
        ) THEN
       RAISE EXCEPTION 'AUTOPILOT_PARALLEL_REUSE_PROVENANCE_INVALID';
     END IF;
     -- Universal registration compares every immutable semantic field and
     -- dependency, but returns without updating timestamps/state/task IDs.
     PERFORM * FROM autopilot.register_universal_work_item(
       candidate_work_key,candidate_role,candidate_task_kind,
       candidate_objective,candidate_target_pr,candidate_priority,spec,NULL,
       'AUTOPILOT_PARALLEL_INTAKE',existing_work.source
     );
     CONTINUE;
   END IF;

$reuse$;
BEGIN
 SELECT definition INTO STRICT source FROM autopilot.migration_0367_function_backup
 WHERE function_key='autopilot.register_parallel_work_manifest(text,text)';
 IF position(anchor in source)=0
    OR position(' existing_receipt autopilot.project_work_manifest_receipt;' in source)=0
    OR position('AND work.state NOT IN (''DONE'',''PAUSED'')' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_RELIABLE_PROGRESS_PATCH_SOURCE_MISMATCH';
 END IF;
 revised:=replace(source,' existing_receipt autopilot.project_work_manifest_receipt;',
   E' existing_receipt autopilot.project_work_manifest_receipt;\n existing_work autopilot.project_work_item;');
 revised:=replace(revised,anchor,reuse||anchor);
 revised:=replace(revised,'AND work.state NOT IN (''DONE'',''PAUSED'')',
   'AND work.state<>''DONE''');
 EXECUTE revised;

 SELECT definition INTO STRICT source FROM autopilot.migration_0367_function_backup
 WHERE function_key='autopilot.reconcile_paused_project_work(uuid,text,text,text,text)';
 IF position('effective_code:=COALESCE(p_result_code,w.result_code);' in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_RELIABLE_PROGRESS_PAUSED_SOURCE_MISMATCH';
 END IF;
 -- Old clients cannot replace the authoritative reason with stale evidence.
 revised:=replace(source,'effective_code:=COALESCE(p_result_code,w.result_code);',
   'effective_code:=w.result_code;');
 EXECUTE revised;
END $patch$;

CREATE FUNCTION autopilot.reconcile_paused_project_work_cas(
 p_work_item_id uuid,p_observed_updated_at timestamptz,p_evidence_token text,
 p_target_disposition text DEFAULT NULL,p_summary text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $f$
DECLARE w autopilot.project_work_item;
BEGIN
 SELECT * INTO w FROM autopilot.project_work_item
 WHERE work_item_id=p_work_item_id FOR UPDATE;
 IF NOT FOUND OR w.state<>'PAUSED'
    OR w.updated_at IS DISTINCT FROM p_observed_updated_at THEN
   RETURN 'NO_CHANGE';
 END IF;
 RETURN autopilot.reconcile_paused_project_work(
   p_work_item_id,p_evidence_token,p_target_disposition,NULL,p_summary);
END $f$;
REVOKE ALL ON FUNCTION autopilot.reconcile_paused_project_work_cas(uuid,timestamptz,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_paused_project_work_cas(uuid,timestamptz,text,text,text) TO bridge_school_worker;

CREATE FUNCTION autopilot.parallel_work_manifest_status(p_manifest_sha256 text)
RETURNS TABLE(manifest_sha256 text,item_count integer,registered_count integer,replayed boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $f$
 SELECT r.manifest_sha256,r.item_count::integer,r.registered_count::integer,true
 FROM autopilot.project_work_manifest_receipt r
 WHERE r.manifest_sha256=p_manifest_sha256
   AND p_manifest_sha256~'^[0-9a-f]{64}$'
$f$;
REVOKE ALL ON FUNCTION autopilot.parallel_work_manifest_status(text) FROM PUBLIC,autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.parallel_work_manifest_status(text)
TO autopilot_runtime,autopilot_runtime_principal;

INSERT INTO public.schema_migration(migration_key)
VALUES('0367_autopilot_reliable_progress');
COMMIT;
