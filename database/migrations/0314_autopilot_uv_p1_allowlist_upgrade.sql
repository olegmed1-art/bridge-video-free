\set ON_ERROR_STOP on
BEGIN;

-- Forward-only replacement of the UV P1 ingress allowlist. Migration 0309 is
-- immutable because it has already been recorded on the temporary branch.
DO $migration_guard$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0309_autopilot_uv_p1_bounded_ingress'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_0309_REQUIRED';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.schema_migration
         WHERE migration_key = '0313_autopilot_uv_p1_allowlist_upgrade'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_UV_P1_0313_REQUIRED';
    END IF;
END
$migration_guard$;

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
        WHEN 'uv-p1-runtime-pr997-c1515c5af4a4-20260902' THEN
            approved_pr := 997;
            approved_head := 'c1515c5af4a47c7468d7c4769e91082f7afd163c';
        WHEN 'uv-p1-canary-pr1062-79aec3f732fd-20260902' THEN
            approved_pr := 1062;
            approved_head := '79aec3f732fdcd8ca9f5f8a4a6ba5a88f4bba8d4';
        WHEN 'uv-p1-idle-pr1061-8ab8d74c2a0f-20260902' THEN
            approved_pr := 1061;
            approved_head := '8ab8d74c2a0ffd281ae4ccea9e5c8e55eea2ab45';
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
'Registers only the three director-approved, zero-cost, exact-head UV P1 GitHub CI snapshots, one active task at a time; allowlist upgraded by migration 0314.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0314_autopilot_uv_p1_allowlist_upgrade')
ON CONFLICT DO NOTHING;

COMMIT;
