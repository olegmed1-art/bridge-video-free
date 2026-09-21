\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;

DO $prerequisite$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0354_autopilot_mailbox_v3_final_head'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_REQUIRES_0354';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0355_autopilot_light_v3_compatibility_fence'
    ) OR to_regclass('autopilot.migration_0355_function_backup') IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_ALREADY_PRESENT';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
           AND status IN ('READY','RUNNING')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_REQUIRES_QUIESCENT_ROLE_QUEUE';
    END IF;
END $prerequisite$;

CREATE TABLE autopilot.migration_0355_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);

INSERT INTO autopilot.migration_0355_function_backup(
    function_key,function_definition
)
VALUES (
    'claim_next_task',
    pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)
);

REVOKE ALL ON TABLE autopilot.migration_0355_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

DO $patch$
DECLARE
    original text;
    anchor text := $anchor$         WHERE t.status = 'READY' AND t.not_before <= now()
           AND t.attempts < t.max_attempts
$anchor$;
    replacement text := $replacement$         WHERE t.status = 'READY' AND t.not_before <= now()
           AND NOT ( -- LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1
               p_worker_id = 'oracle-autopilot-light-1'
               AND t.goal_type IN (
                   'CHATGPT_ROLE_DISPATCH_V1',
                   'CHATGPT_ROLE_FOLLOWUP_V1'
               )
           )
           AND t.attempts < t.max_attempts
$replacement$;
BEGIN
    original := pg_get_functiondef(
        'autopilot.claim_next_task(text,integer)'::regprocedure
    );
    IF original IS NULL
       OR position('LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1' IN original)>0
       OR (length(original)-length(replace(original,anchor,'')))
          /length(anchor)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_SOURCE_DRIFT';
    END IF;
    EXECUTE replace(original,anchor,replacement);
END $patch$;

DO $verify$
BEGIN
    IF position(
        'LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1' IN
        pg_get_functiondef('autopilot.claim_next_task(text,integer)'::regprocedure)
    )=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_LIGHT_V3_FENCE_INSTALL_FAILED';
    END IF;
END $verify$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0355_autopilot_light_v3_compatibility_fence');

COMMIT;
