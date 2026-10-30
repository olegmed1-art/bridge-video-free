-- Pre-publication recovery only. Service stopped; HOLD already restored.
-- If any dispatch was published, preserve all evidence and investigate.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
SELECT pg_advisory_xact_lock(hashtext(current_database()),
                             hashtext('bridge_school_schema_migrations'));
LOCK TABLE autopilot.task, autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $restore$
DECLARE
    original text;
    current_definition text;
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
       OR to_regclass('autopilot.migration_0355_function_backup') IS NULL
       OR (SELECT count(*) FROM autopilot.task
           WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')) <> 1
       OR NOT EXISTS (SELECT FROM autopilot.task
           WHERE task_id='f05c605f-f664-4ff7-9927-a039f000a929'::uuid
             AND status='READY' AND attempts=0 AND lease_owner IS NULL
             AND lease_until IS NULL AND lease_epoch=0)
       OR EXISTS (SELECT FROM autopilot.role_dispatch_outbox
           WHERE task_id='f05c605f-f664-4ff7-9927-a039f000a929'::uuid)
       OR EXISTS (SELECT FROM autopilot.role_dispatch_outbox
           WHERE status='CLAIMED' OR claim_until IS NOT NULL) THEN
        RAISE EXCEPTION 'LIGHT_0355_RECOVERY_REQUIRES_UNPUBLISHED_CANARY';
    END IF;
    SELECT function_definition INTO original
      FROM autopilot.migration_0355_function_backup
     WHERE function_key='claim_next_task';
    current_definition := pg_get_functiondef(
        'autopilot.claim_next_task(text,integer)'::regprocedure);
    IF original IS NULL OR current_definition IS DISTINCT FROM original
       OR (length(original)-length(replace(original,anchor,'')))/length(anchor)<>1 THEN
        RAISE EXCEPTION 'LIGHT_0355_RECOVERY_FUNCTION_DRIFT';
    END IF;
    EXECUTE replace(original,anchor,patch);
    IF pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)
       IS DISTINCT FROM replace(original,anchor,patch) THEN
        RAISE EXCEPTION 'LIGHT_0355_RECOVERY_VERIFY_FAILED';
    END IF;
END $restore$;
COMMIT;
