\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_item IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.project_work_task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_followup IN SHARE ROW EXCLUSIVE MODE;

DO $pre$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0362_autopilot_target_pr_codex_context'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_REQUIRES_0362';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0363_autopilot_audit_findings_repair_progression'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_ALREADY_APPLIED';
 END IF;
 IF to_regprocedure('autopilot.materialize_role_repair(uuid,text,text)') IS NULL
    OR to_regprocedure('autopilot.on_role_task_terminal()') IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_PREREQUISITE_MISSING';
 END IF;
 IF EXISTS (
   SELECT 1 FROM autopilot.role_dispatch_outbox
   WHERE status NOT IN ('CALLBACK_ACCEPTED','FAILED_CLOSED')
 ) OR EXISTS (
   SELECT 1 FROM autopilot.task
   WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
     AND status NOT IN (
       'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
     )
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0363_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL CHECK (length(definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0363_function_backup(function_key,definition)
SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid=p.pronamespace
WHERE p.oid IN (
 'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure,
 'autopilot.on_role_task_terminal()'::regprocedure
);

DO $patch$
DECLARE
 original text;
 patched text;
BEGIN
 IF (SELECT count(*) FROM autopilot.migration_0363_function_backup)<>2 THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_BACKUP_INCOMPLETE';
 END IF;

 SELECT definition INTO STRICT original
 FROM autopilot.migration_0363_function_backup
 WHERE function_key='autopilot.materialize_role_repair(uuid,text,text)';
 IF strpos(original,'AUDIT_FINDINGS_REPAIR_SUCCESSOR_V1')>0
    OR strpos(original,$$p_result_code <> 'REPAIR_REQUIRED'$$)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_ADMISSION_SOURCE_DRIFT';
 END IF;
 patched:=replace(
   original,
   $$p_result_code <> 'REPAIR_REQUIRED'$$,
   $$p_result_code NOT IN ('REPAIR_REQUIRED','AUDIT_FINDINGS_REPORTED')$$
 );
 patched:=replace(
   patched,
   '           -- REPAIR_REQUIRED_DIRECT_ADMISSION_V1: explicit retained defect.',
   $marker$           -- AUDIT_FINDINGS_REPAIR_SUCCESSOR_V1: retained audit findings require one bounded repair.
           -- REPAIR_REQUIRED_DIRECT_ADMISSION_V1: explicit retained defect.$marker$
 );
 patched:=replace(
   patched,
   $old$    SELECT created.task_id INTO followup_id
      FROM autopilot.create_chatgpt_role_followup_task($old$,
   $new$    -- AUDIT_FINDINGS_REPAIR_REPLAY_V1: lineage is the durable
    -- idempotency boundary even if the successor goal has advanced.
    SELECT existing_followup.followup_task_id INTO followup_id
      FROM autopilot.role_dispatch_followup AS existing_followup
     WHERE existing_followup.parent_task_id = origin_row.task_id
       AND existing_followup.followup_kind = 'REPAIR'
       AND existing_followup.trigger_result_code = p_result_code;
    IF FOUND THEN
        RETURN followup_id;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM autopilot.role_dispatch_followup AS existing_followup
         WHERE existing_followup.parent_task_id = origin_row.task_id
           AND existing_followup.followup_kind = 'REPAIR'
    ) THEN
        RETURN NULL;
    END IF;

    SELECT created.task_id INTO followup_id
      FROM autopilot.create_chatgpt_role_followup_task($new$
 );
 IF patched=original
    OR strpos(patched,'AUDIT_FINDINGS_REPAIR_SUCCESSOR_V1')=0
    OR strpos(patched,'AUDIT_FINDINGS_REPAIR_REPLAY_V1')=0
    OR strpos(patched, 'p_result_code <> ''REPAIR_REQUIRED''')>0 THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_ADMISSION_PATCH_FAILED';
 END IF;
 EXECUTE patched;

 SELECT definition INTO STRICT original
 FROM autopilot.migration_0363_function_backup
 WHERE function_key='autopilot.on_role_task_terminal()';
 IF strpos(original,'AUDIT_FINDINGS_REPAIR_TERMINAL_V1')>0
    OR strpos(original,$$IF result_code = 'REPAIR_REQUIRED' THEN$$)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_TERMINAL_SOURCE_DRIFT';
 END IF;
 patched:=replace(
   original,
   $$IF result_code = 'REPAIR_REQUIRED' THEN$$,
   $$-- AUDIT_FINDINGS_REPAIR_TERMINAL_V1: a successful audit with retained
            -- findings must progress to the same bounded repair controller.
            IF result_code IN ('REPAIR_REQUIRED','AUDIT_FINDINGS_REPORTED') THEN$$
 );
 IF patched=original
    OR strpos(patched,'AUDIT_FINDINGS_REPAIR_TERMINAL_V1')=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_AUDIT_FINDINGS_REPAIR_TERMINAL_PATCH_FAILED';
 END IF;
 EXECUTE patched;
END $patch$;

INSERT INTO public.schema_migration(migration_key)
VALUES('0363_autopilot_audit_findings_repair_progression');

COMMIT;
