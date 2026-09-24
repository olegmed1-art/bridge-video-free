\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('autopilot.role-worker-capacity-v1',0));
DO $pre$
BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration WHERE migration_key='0371_autopilot_provider_retry_budget') THEN
   RAISE EXCEPTION 'AUDIT_PASS_ALIAS_REQUIRES_0371';
 END IF;
END $pre$;
CREATE TABLE autopilot.migration_0372_function_backup (
 function_key text PRIMARY KEY, definition text NOT NULL, patched_definition text NOT NULL
);
REVOKE ALL ON autopilot.migration_0372_function_backup FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;
DO $patch$
DECLARE source text; patched text;
 anchor text := '''AUDIT_VERIFIED_NO_REPAIR'',''EXACT_HEAD_AUDIT_PASSED'',''AUDIT_PASSED''';
BEGIN
 source:=pg_get_functiondef('autopilot.on_project_work_task_terminal()'::regprocedure);
 IF (length(source)-length(replace(source,anchor,'')))/length(anchor) <> 1
    OR position('NEXT_STEP_TERMINAL_FENCE' in source)=0
    OR position('NEW.safe_summary_json->>''status''=''SUCCEEDED''' in source)=0 THEN
   RAISE EXCEPTION 'AUDIT_PASS_ALIAS_SOURCE_DRIFT';
 END IF;
 patched:=replace(source,anchor,anchor||',''AUDIT_PASS''');
 EXECUTE patched;
 INSERT INTO autopilot.migration_0372_function_backup
 VALUES ('autopilot.on_project_work_task_terminal()',source,
         pg_get_functiondef('autopilot.on_project_work_task_terminal()'::regprocedure));
END $patch$;
-- Do not replay old terminal triggers or rewrite already accepted receipts.
INSERT INTO public.schema_migration(migration_key) VALUES('0372_autopilot_audit_pass_alias');
COMMIT;
