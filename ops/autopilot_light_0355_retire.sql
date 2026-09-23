-- Explicit operator action. Never run from an automatic migration sweep.
-- Prerequisite: reviewed live HOLD preflight on the installed release, then
-- STOP the Light worker and independently recheck its inactive state.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
SELECT pg_advisory_xact_lock(hashtext(current_database()),
                             hashtext('bridge_school_schema_migrations'));
LOCK TABLE autopilot.task, autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
DECLARE
    fenced text;
    original text;
    anchor text := $a$         WHERE t.status = 'READY' AND t.not_before <= now()
           AND t.attempts < t.max_attempts
$a$;
    patch text := $p$         WHERE t.status = 'READY' AND t.not_before <= now()
           AND NOT ( -- LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1
               p_worker_id = 'oracle-autopilot-light-1'
               AND t.goal_type IN (
                   'CHATGPT_ROLE_DISPATCH_V1',
                   'CHATGPT_ROLE_FOLLOWUP_V1'
               )
           )
           AND t.attempts < t.max_attempts
$p$;
BEGIN
    IF NOT EXISTS (SELECT FROM public.schema_migration
                   WHERE migration_key='0355_autopilot_light_v3_compatibility_fence')
       OR to_regclass('autopilot.migration_0355_function_backup') IS NULL THEN
        RAISE EXCEPTION 'LIGHT_0355_BASELINE_MISSING';
    END IF;
    IF (SELECT count(*) FROM autopilot.task
        WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')) <> 1
       OR NOT EXISTS (SELECT FROM autopilot.task
           WHERE task_id='f05c605f-f664-4ff7-9927-a039f000a929'::uuid
             AND status='READY' AND attempts=0 AND lease_owner IS NULL
             AND lease_until IS NULL AND lease_epoch=0
             AND goal_type='CHATGPT_ROLE_DISPATCH_V1'
             AND goal_json->>'mailbox_pr'='1703'
             AND goal_json->>'target_pr'='1769'
             AND goal_json->>'expected_head_sha'='2586929313ab40326d64353b513ff86e5ae3350c')
       OR EXISTS (SELECT FROM autopilot.role_dispatch_outbox
                  WHERE task_id='f05c605f-f664-4ff7-9927-a039f000a929'::uuid)
    THEN
        RAISE EXCEPTION 'LIGHT_0355_QUEUE_DRIFT';
    END IF;
    SELECT function_definition INTO original
      FROM autopilot.migration_0355_function_backup
     WHERE function_key='claim_next_task';
    fenced := pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure);
    IF original IS NULL OR fenced IS NULL
       OR (length(original)-length(replace(original,anchor,'')))/length(anchor)<>1
       OR fenced IS DISTINCT FROM replace(original,anchor,patch) THEN
        RAISE EXCEPTION 'LIGHT_0355_FUNCTION_DRIFT';
    END IF;
    -- CREATE OR REPLACE preserves the function identity, grants and owner.
    EXECUTE original;
    IF pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)
       IS DISTINCT FROM original THEN
        RAISE EXCEPTION 'LIGHT_0355_RETIRE_VERIFICATION_FAILED';
    END IF;
END $guard$;
COMMIT;
