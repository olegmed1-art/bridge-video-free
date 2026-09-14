\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE goal_type = 'CHATGPT_ROLE_FOLLOWUP_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_FAILURE_CONTINUATION_ROLLBACK_REQUIRES_DRAIN';
    END IF;
END $$;

DROP TRIGGER IF EXISTS autopilot_role_task_terminal_followup ON autopilot.task;
DROP TRIGGER IF EXISTS autopilot_role_outbox_prepare
    ON autopilot.role_dispatch_outbox;

DO $rollback$
DECLARE
    function_sql text;
    new_prepare_guard text := $new$IF NOT FOUND OR task_row.goal_type NOT IN (
        'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
    ) THEN$new$;
    old_prepare_guard text := $old$IF NOT FOUND OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1' THEN$old$;
    new_callback_guard text := $new$OR task_row.goal_type NOT IN (
           'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
       )$new$;
    old_callback_guard text := $old$OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'$old$;
    new_head_guard text := $new$OR (
           outbox_row.mode IN ('READ_ONLY', 'VERIFY')
           AND outbox_row.expected_head_sha <> p_body->>'target_head_sha'
       )$new$;
    old_head_guard text := $old$OR outbox_row.expected_head_sha <> p_body->>'target_head_sha'$old$;
BEGIN
    SELECT pg_get_functiondef(
        'autopilot.prepare_role_dispatch(uuid,text,bigint)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL OR strpos(function_sql, new_prepare_guard) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_PREPARE_0323_DEFINITION_UNEXPECTED';
    END IF;
    EXECUTE replace(function_sql, new_prepare_guard, old_prepare_guard);

    SELECT pg_get_functiondef(
        'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL
       OR strpos(function_sql, new_callback_guard) = 0
       OR strpos(function_sql, new_head_guard) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_0323_DEFINITION_UNEXPECTED';
    END IF;
    function_sql := replace(function_sql, new_callback_guard, old_callback_guard);
    function_sql := replace(function_sql, new_head_guard, old_head_guard);
    EXECUTE function_sql;
END
$rollback$;

DROP FUNCTION IF EXISTS autopilot.on_role_task_terminal();
DROP FUNCTION IF EXISTS autopilot.materialize_role_verification(uuid,text);
DROP FUNCTION IF EXISTS autopilot.materialize_role_repair(uuid,text,text);
DROP FUNCTION IF EXISTS autopilot.materialize_role_continuation(uuid,text);
DROP FUNCTION IF EXISTS autopilot.claim_role_dispatch_outbox_v2(text,integer);
DROP FUNCTION IF EXISTS autopilot.on_role_outbox_prepare();
DROP FUNCTION IF EXISTS autopilot.create_chatgpt_role_followup_task(
    text,jsonb,integer,text,text
);
DROP FUNCTION IF EXISTS autopilot.role_blocker_requires_owner(text);

DROP TABLE IF EXISTS autopilot.role_dispatch_followup;

ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK (
    goal_type IN (
        'AUTOPILOT_SMOKE_V1',
        'EXTERNAL_WAIT_SHADOW_V1',
        'OWNER_BOUNDARY_V1',
        'GITHUB_PR_READ_ONLY_V1',
        'GITHUB_CI_READ_ONLY_V1',
        'GITHUB_DRAFT_REPAIR_V1',
        'IBF_READ_ONLY_ANALYSIS',
        'CHATGPT_ROLE_DISPATCH_V1'
    )
);

ALTER TABLE autopilot.role_dispatch_outbox
    DROP CONSTRAINT IF EXISTS role_dispatch_outbox_followup_shape,
    DROP CONSTRAINT IF EXISTS role_dispatch_outbox_mode_check,
    DROP COLUMN IF EXISTS blocked_summary,
    DROP COLUMN IF EXISTS blocked_result_code,
    DROP COLUMN IF EXISTS prior_task_id,
    DROP COLUMN IF EXISTS origin_task_id,
    DROP COLUMN IF EXISTS repair_attempt,
    DROP COLUMN IF EXISTS mode;

DELETE FROM public.schema_migration
 WHERE migration_key = '0323_autopilot_failure_continuation';

COMMIT;
