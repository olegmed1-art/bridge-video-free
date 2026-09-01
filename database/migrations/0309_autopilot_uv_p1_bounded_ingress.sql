\set ON_ERROR_STOP on
BEGIN;

-- Temporary-branch-only, director-approved ingress for the three Universal Video
-- P1 CI snapshots authorized on 2026-09-01. The caller can select only one of
-- three immutable task keys; repository, PR, head SHA, capability, cost, origin,
-- priority and retry policy are fixed inside this SECURITY DEFINER boundary.
CREATE OR REPLACE FUNCTION autopilot.register_approved_uv_p1_ci(
    p_task_key text
)
RETURNS TABLE(task_id uuid, status text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.task;
    inserted autopilot.task;
    goal_payload jsonb;
    approved_pr integer;
    approved_head text;
    active_count integer;
BEGIN
    CASE p_task_key
        WHEN 'uv-p1-runtime-pr997-545ef013-20260901' THEN
            approved_pr := 997;
            approved_head := '545ef0135e3cfe436b918c3ec26f5e2b77500977';
        WHEN 'uv-p1-intake-pr1000-5af0675a-20260901' THEN
            approved_pr := 1000;
            approved_head := '5af0675a9e13a9725348661be297abc5f52ff0e4';
        WHEN 'uv-p1-idle-pr1047-621ab073-20260901' THEN
            approved_pr := 1047;
            approved_head := '621ab073418b3f3d1b75cb6abb074dba4ea305cb';
        ELSE
            RAISE EXCEPTION 'AUTOPILOT_UV_P1_TASK_NOT_APPROVED';
    END CASE;

    goal_payload := jsonb_build_object(
        'repository', 'olegmed1-art/bridge-video-free',
        'pr_number', approved_pr,
        'expected_head_sha', approved_head,
        'require_draft', true
    );

    -- Exact replay remains idempotent, including after terminal completion.
    SELECT t.* INTO existing
      FROM autopilot.task AS t
     WHERE t.task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> 'GITHUB_CI_READ_ONLY_V1'
           OR existing.goal_json <> goal_payload
           OR existing.current_step_key <> 'github.ci.snapshot'
           OR existing.allowed_capabilities_json <> '["github.ci.snapshot"]'::jsonb
           OR existing.priority <> 10
           OR existing.max_attempts <> 1
           OR existing.model_turn_cap <> 0
           OR existing.cost_cap_microusd <> 0
           OR existing.created_by <> 'DIRECTOR_CHAT_GO_20260901'
           OR existing.source <> 'BOUNDED_UV_P1_INGRESS_V1' THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    -- Serialize all calls so concurrent requests cannot bypass one-active-task.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot:uv_p1_read_only_ingress', 20260901)
    );

    -- Recheck after taking the lock to preserve exact replay under concurrency.
    SELECT t.* INTO existing
      FROM autopilot.task AS t
     WHERE t.task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> 'GITHUB_CI_READ_ONLY_V1'
           OR existing.goal_json <> goal_payload
           OR existing.current_step_key <> 'github.ci.snapshot'
           OR existing.allowed_capabilities_json <> '["github.ci.snapshot"]'::jsonb
           OR existing.priority <> 10
           OR existing.max_attempts <> 1
           OR existing.model_turn_cap <> 0
           OR existing.cost_cap_microusd <> 0
           OR existing.created_by <> 'DIRECTOR_CHAT_GO_20260901'
           OR existing.source <> 'BOUNDED_UV_P1_INGRESS_V1' THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    SELECT count(*) INTO active_count
      FROM autopilot.task AS t
     WHERE t.status NOT IN (
         'OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP',
         'DONE', 'CANCELLED'
     );
    IF active_count <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_ACTIVE_LIMIT';
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json,
        priority, max_attempts, model_turn_cap,
        cost_cap_microusd, created_by, source
    ) VALUES (
        p_task_key,
        'GITHUB_CI_READ_ONLY_V1',
        goal_payload,
        'READY',
        'github.ci.snapshot',
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'exact_head_required', true,
            'model_calls', 0,
            'cost_actual_microusd', 0,
            'approval_ref', 'DIRECTOR_CHAT_GO_20260901'
        ),
        '["github.ci.snapshot"]'::jsonb,
        10,
        1,
        0,
        0,
        'DIRECTOR_CHAT_GO_20260901',
        'BOUNDED_UV_P1_INGRESS_V1'
    )
    RETURNING * INTO inserted;

    PERFORM autopilot.record_event(
        inserted.task_id,
        'TASK_READY',
        'NEW',
        'READY',
        jsonb_build_object(
            'goal_type', inserted.goal_type,
            'approval_ref', 'DIRECTOR_CHAT_GO_20260901',
            'ingress', 'BOUNDED_UV_P1_INGRESS_V1'
        ),
        'SYSTEM',
        'bounded-uv-p1-ingress-v1',
        'uv-p1-ingress:' || p_task_key
    );

    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.register_approved_uv_p1_ci(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.register_approved_uv_p1_ci(text)
    TO autopilot_runtime;

COMMENT ON FUNCTION autopilot.register_approved_uv_p1_ci(text) IS
'Registers only the three director-approved, zero-cost, exact-head UV P1 GitHub CI snapshots, one active task at a time.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0309_autopilot_uv_p1_bounded_ingress')
ON CONFLICT DO NOTHING;

COMMIT;
