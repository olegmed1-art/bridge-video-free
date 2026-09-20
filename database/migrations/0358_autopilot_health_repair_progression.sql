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
    OR to_regprocedure('autopilot.blocker_remediation_action(text)') IS NULL
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
 'autopilot.blocker_remediation_action(text)'::regprocedure,
 'autopilot.on_role_task_terminal()'::regprocedure
);

CREATE OR REPLACE FUNCTION autopilot.blocker_remediation_action(
 p_result_code text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
SELECT CASE
 WHEN p_result_code IS NULL THEN 'HOLD_UNKNOWN'
 -- Explicit retained defect results precede the unknown-code owner fallback.
 WHEN p_result_code='REPAIR_REQUIRED' THEN 'REPOSITORY_REPAIR'
 WHEN autopilot.role_blocker_requires_owner(p_result_code) THEN 'OWNER_HOLD'
 WHEN p_result_code IN (
   'CODEX_PROVIDER_GENERIC_FAILURE',
   'CODEX_ACK_DEADLINE_EXCEEDED',
   'CODEX_RESULT_DEADLINE_EXCEEDED'
 ) THEN 'PROVIDER_HOLD'
 WHEN p_result_code IN (
   'TARGET_SUPERSEDED_BY_CURRENT_MAIN',
   'CURRENT_MAIN_SUBSUMES_TARGET',
   'TARGET_PR_NOT_UPDATED'
 ) THEN 'RECONCILE_TARGET'
 WHEN p_result_code IN (
   'SERVER_EVIDENCE_INCOMPLETE',
   'SERVER_PARITY_EVIDENCE_INCOMPLETE',
   'PRODUCTION_ACCEPTANCE_PLAN_INCOMPLETE',
   'WORLD_EVIDENCE_NOT_VERSION_BOUND'
 ) THEN 'EVIDENCE_REMEDIATION'
 WHEN p_result_code IN (
   'QA_GATES_FAILED',
   'FINDINGS_REQUIRE_REPAIR',
   'VIRTUALENV_INTERPRETER_REQUIRED',
   'HISTORICAL_RULE_CONTENT_MUTABLE',
   'FINDING_STALE_BLOCK_LEDGER',
   'RECOGNIZER_READINESS_GAP',
   'TECHNICAL_TEST_FAILURE',
   'BOUNDED_DEFECT',
   'BOUNDED_REPOSITORY_DEFECT'
 ) THEN 'REPOSITORY_REPAIR'
 WHEN p_result_code IN (
   'PROJECT_HEAD_BROKER_TRANSIENT_ERROR',
   'ROLE_DISPATCH_RESPONSE_INVALID',
   'GITHUB_API_TRANSIENT_ERROR',
   'AUTOPILOT_TRANSIENT_DATABASE_ERROR'
 ) THEN 'TRANSPORT_REMEDIATION'
 WHEN p_result_code IN (
   'ROOT_CAUSE_IDENTIFIED',
   'BOUNDED_REPAIR_UNIT_SELECTED',
   'NEXT_REPAIR_UNIT_CONFIRMED'
 ) THEN 'REPOSITORY_REPAIR'
 ELSE 'HOLD_UNKNOWN'
END
$$;

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
            IF autopilot.blocker_remediation_action(result_code) = 'REPOSITORY_REPAIR' THEN
                result_summary := COALESCE(
                    NULLIF(NEW.safe_summary_json->>'summary', ''),
                    'Role result requires bounded repository repair.'
                );
                PERFORM autopilot.materialize_blocker_remediation(
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
