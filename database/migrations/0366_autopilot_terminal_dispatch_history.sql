\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0365_autopilot_parallel_work_intake'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_HISTORY_REQUIRES_0365';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0366_autopilot_terminal_dispatch_history'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_HISTORY_ALREADY_APPLIED';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0366_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL CHECK (length(definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0366_function_backup(function_key,definition)
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid=p.pronamespace
WHERE p.oid='autopilot.register_parallel_work_manifest(text,text)'::regprocedure;

REVOKE ALL ON TABLE autopilot.migration_0366_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

DO $patch$
DECLARE
 source text;
 revised text;
 old_guard text := $old$
     SELECT 1 FROM autopilot.role_dispatch_outbox AS outbox
     WHERE outbox.mailbox_pr=candidate_target_pr
        OR outbox.github_dispatch_comment_id=candidate_target_pr::bigint
        OR outbox.codex_command_pr=candidate_target_pr
$old$;
 new_guard text := $new$
     SELECT 1 FROM autopilot.role_dispatch_outbox AS outbox
     WHERE outbox.status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
       AND (
         outbox.mailbox_pr=candidate_target_pr
         OR outbox.github_dispatch_comment_id=candidate_target_pr::bigint
         OR outbox.codex_command_pr=candidate_target_pr
       )
$new$;
BEGIN
 SELECT definition INTO STRICT source
 FROM autopilot.migration_0366_function_backup
 WHERE function_key='autopilot.register_parallel_work_manifest(text,text)';
 IF position(old_guard in source)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_HISTORY_PATCH_SOURCE_MISMATCH';
 END IF;
 revised:=replace(source,old_guard,new_guard);
 IF revised=source OR position(old_guard in revised)<>0
    OR position(new_guard in revised)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_HISTORY_PATCH_INVALID';
 END IF;
 EXECUTE revised;
END $patch$;

INSERT INTO public.schema_migration(migration_key)
VALUES('0366_autopilot_terminal_dispatch_history');

COMMIT;
