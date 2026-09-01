\set ON_ERROR_STOP on
BEGIN;

-- School Autopilot Controller v1.6 — bounded, shadow-only real-task ingress.
-- The only newly admitted task kind is a read-only IBF analysis. The caller
-- cannot supply an arbitrary URL, capability, source, cost budget or executor.

ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK (goal_type IN (
    'AUTOPILOT_SMOKE_V1',
    'EXTERNAL_WAIT_SHADOW_V1',
    'OWNER_BOUNDARY_V1',
    'GITHUB_PR_READ_ONLY_V1',
    'GITHUB_CI_READ_ONLY_V1',
    'GITHUB_DRAFT_REPAIR_V1',
    'IBF_READ_ONLY_ANALYSIS'
));

ALTER TABLE autopilot.step_attempt DROP CONSTRAINT step_attempt_capability_name_check;
ALTER TABLE autopilot.step_attempt ADD CONSTRAINT step_attempt_capability_name_check CHECK (
    capability_name IN (
        'shadow.noop', 'shadow.wait', 'policy.owner_boundary',
        'github.pr.snapshot', 'github.ci.snapshot', 'github.draft_repair',
        'ibf.read_only_analysis'
    )
);

ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_evidence_class_check;
ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_evidence_class_check CHECK (
    evidence_class IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'OWNER_BOUNDARY_PROOF',
        'GITHUB_PR_READ_ONLY_SNAPSHOT',
        'GITHUB_CI_READ_ONLY_SNAPSHOT',
        'GITHUB_DRAFT_REPAIR_EVIDENCE',
        'IBF_READ_ONLY_ANALYSIS_EVIDENCE'
    )
);

CREATE OR REPLACE FUNCTION autopilot.register_approved_ibf_analysis(
    p_task_key text,
    p_ibf_player_id text,
    p_approval_ref text,
    p_priority integer DEFAULT 10
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
    active_count integer;
    recent_count integer;
BEGIN
    IF p_task_key IS NULL OR p_task_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_INVALID';
    END IF;
    IF p_ibf_player_id IS NULL OR p_ibf_player_id !~ '^[1-9][0-9]{0,9}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_IBF_PLAYER_ID_INVALID';
    END IF;
    IF p_approval_ref IS NULL
       OR p_approval_ref !~ '^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_APPROVAL_REF_INVALID';
    END IF;
    IF p_priority NOT IN (0, 10, 20, 30) THEN
        RAISE EXCEPTION 'AUTOPILOT_PRIORITY_INVALID';
    END IF;

    goal_payload := jsonb_build_object(
        'approval_ref', p_approval_ref,
        'ibf_player_id', p_ibf_player_id,
        'source_authority', 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS'
    );

    -- Exact replay is always allowed and returns the canonical task identity.
    SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> 'IBF_READ_ONLY_ANALYSIS'
           OR existing.goal_json <> goal_payload
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> 0
           OR existing.created_by <> 'DIRECTOR_APPROVED_INGRESS'
           OR existing.source <> 'BOUNDED_TASK_INGRESS_V1' THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    -- Serialize the narrow ingress so concurrency cannot bypass the limits.
    PERFORM pg_advisory_xact_lock(hashtextextended('autopilot:ibf_read_only_ingress', 1015));

    SELECT count(*) INTO active_count
      FROM autopilot.task
     WHERE goal_type = 'IBF_READ_ONLY_ANALYSIS'
       AND status NOT IN ('OWNER_REQUIRED', 'FAILED_CLOSED', 'BUDGET_STOP', 'DONE', 'CANCELLED');
    IF active_count >= 3 THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_ACTIVE_LIMIT';
    END IF;

    SELECT count(*) INTO recent_count
      FROM autopilot.task
     WHERE goal_type = 'IBF_READ_ONLY_ANALYSIS'
       AND created_at > now() - interval '1 hour';
    IF recent_count >= 12 THEN
        RAISE EXCEPTION 'AUTOPILOT_INGRESS_RATE_LIMIT';
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json, priority,
        cost_cap_microusd, created_by, source
    ) VALUES (
        p_task_key,
        'IBF_READ_ONLY_ANALYSIS',
        goal_payload,
        'READY',
        'ibf.read_only_analysis',
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'manual_director_url_forbidden', true,
            'source_authority', 'ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS'
        ),
        '["ibf.read_only_analysis"]'::jsonb,
        p_priority::smallint,
        0,
        'DIRECTOR_APPROVED_INGRESS',
        'BOUNDED_TASK_INGRESS_V1'
    ) ON CONFLICT (task_key) DO NOTHING
    RETURNING * INTO inserted;

    IF inserted.task_id IS NULL THEN
        SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
        IF NOT FOUND
           OR existing.goal_type <> 'IBF_READ_ONLY_ANALYSIS'
           OR existing.goal_json <> goal_payload
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> 0
           OR existing.created_by <> 'DIRECTOR_APPROVED_INGRESS'
           OR existing.source <> 'BOUNDED_TASK_INGRESS_V1' THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    PERFORM autopilot.record_event(
        inserted.task_id,
        'TASK_READY',
        'NEW',
        'READY',
        jsonb_build_object(
            'goal_type', inserted.goal_type,
            'approval_ref', p_approval_ref,
            'ingress', 'BOUNDED_TASK_INGRESS_V1'
        ),
        'SYSTEM',
        'bounded-task-ingress-v1',
        'ingress:' || p_task_key
    );

    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.register_approved_ibf_analysis(text,text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.register_approved_ibf_analysis(text,text,text,integer)
    TO autopilot_runtime;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0306_autopilot_bounded_task_ingress')
ON CONFLICT DO NOTHING;

COMMIT;
