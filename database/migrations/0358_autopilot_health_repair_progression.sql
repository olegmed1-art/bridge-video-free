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
   WHERE migration_key='0357_autopilot_mailbox_v4_rotation'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_REQUIRES_0357';
 END IF;
 IF EXISTS (
   SELECT 1 FROM public.schema_migration
   WHERE migration_key='0358_autopilot_health_repair_progression'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_ALREADY_APPLIED';
 END IF;
 IF to_regprocedure('autopilot.mailbox_rotation_readiness()') IS NULL
    OR to_regprocedure('autopilot.materialize_role_repair(uuid,text,text)') IS NULL
    OR to_regprocedure('autopilot.on_role_task_terminal()') IS NULL THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_PREREQUISITE_MISSING';
 END IF;
 IF NOT pg_has_role(
   'bridge_school_health_principal','bridge_school_health','MEMBER'
 ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_ROLE_MEMBERSHIP_MISSING';
 END IF;
 IF has_function_privilege(
      'bridge_school_health',
      'autopilot.mailbox_rotation_readiness()','EXECUTE'
    )
    OR has_function_privilege(
      'bridge_school_health_principal',
      'autopilot.mailbox_rotation_readiness()','EXECUTE'
    ) THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_EXECUTE_ALREADY_PRESENT';
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
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_ACTIVE_ROLE_WORK_PRESENT';
 END IF;
END $pre$;

CREATE TABLE autopilot.migration_0358_function_backup (
 function_key text PRIMARY KEY,
 definition text NOT NULL CHECK (length(definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0358_function_backup(function_key,definition)
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
 old_terminal text;
 new_terminal text;
BEGIN
 IF (SELECT count(*) FROM autopilot.migration_0358_function_backup)<>2 THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_BACKUP_INCOMPLETE';
 END IF;

 SELECT definition INTO STRICT original
 FROM autopilot.migration_0358_function_backup
 WHERE function_key='autopilot.materialize_role_repair(uuid,text,text)';
 patched:=replace(
   original,
   $old$    IF NOT autopilot.blocker_repository_repair_allowed(p_result_code) THEN
        RETURN NULL;
    END IF;
$old$,
   $new$    -- REPAIR_REQUIRED_DIRECT_ADMISSION_V1: an explicit retained defect
    -- bypasses only the unknown-code fallback; all other codes keep the gate.
    IF p_result_code <> 'REPAIR_REQUIRED'
       AND NOT autopilot.blocker_repository_repair_allowed(p_result_code) THEN
        RETURN NULL;
    END IF;
$new$
 );
 IF patched=original
    OR position('REPAIR_REQUIRED_DIRECT_ADMISSION_V1' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_ADMISSION_SOURCE_DRIFT';
 END IF;
 EXECUTE patched;

 SELECT definition INTO STRICT original
 FROM autopilot.migration_0358_function_backup
 WHERE function_key='autopilot.on_role_task_terminal()';
 old_terminal := $old$        ELSIF NEW.status = 'DONE' THEN
            -- The 0322 callback also creates this successor.  The task-key
            -- contract makes the call idempotent while this trigger records
            -- explicit continuation lineage.
            PERFORM autopilot.materialize_role_continuation(
                NEW.task_id, result_code
            );
$old$;
 new_terminal := $new$        ELSIF NEW.status = 'DONE' THEN
            -- REPAIR_REQUIRED_SUCCESSOR_V1: a syntactically successful callback
            -- may still retain a bounded repository defect. Route it through the
            -- repair controller before project completion is evaluated.
            IF result_code = 'REPAIR_REQUIRED' THEN
                result_summary := COALESCE(
                    NULLIF(NEW.safe_summary_json->>'summary', ''),
                    'Role result requires bounded repository repair.'
                );
                PERFORM autopilot.materialize_role_repair(
                    NEW.task_id, result_code, result_summary
                );
            ELSE
                -- The 0322 callback also creates this successor.  The task-key
                -- contract makes the call idempotent while this trigger records
                -- explicit continuation lineage.
                PERFORM autopilot.materialize_role_continuation(
                    NEW.task_id, result_code
                );
            END IF;
$new$;
 patched:=replace(original,old_terminal,new_terminal);
 IF patched=original
    OR position('REPAIR_REQUIRED_SUCCESSOR_V1' in patched)=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_HEALTH_REPAIR_TERMINAL_SOURCE_DRIFT';
 END IF;
 EXECUTE patched;
END $patch$;

REVOKE ALL ON FUNCTION autopilot.mailbox_rotation_readiness() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mailbox_rotation_readiness()
 TO bridge_school_health;

INSERT INTO public.schema_migration(migration_key)
VALUES('0358_autopilot_health_repair_progression');

COMMIT;
