\set ON_ERROR_STOP on
BEGIN;

-- School Autopilot Controller v1.2 — first real, read-only external capability.
-- The Oracle worker may inspect one exact public draft PR. It receives no
-- GitHub credential and cannot create, edit, merge, comment, rerun or dispatch.

ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK (goal_type IN (
    'AUTOPILOT_SMOKE_V1',
    'EXTERNAL_WAIT_SHADOW_V1',
    'OWNER_BOUNDARY_V1',
    'GITHUB_PR_READ_ONLY_V1'
));

ALTER TABLE autopilot.step_attempt DROP CONSTRAINT step_attempt_capability_name_check;
ALTER TABLE autopilot.step_attempt ADD CONSTRAINT step_attempt_capability_name_check CHECK (
    capability_name IN ('shadow.noop', 'shadow.wait', 'policy.owner_boundary', 'github.pr.snapshot')
);

ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_evidence_class_check;
ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_evidence_class_check CHECK (
    evidence_class IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'OWNER_BOUNDARY_PROOF',
        'GITHUB_PR_READ_ONLY_SNAPSHOT'
    )
);

CREATE OR REPLACE FUNCTION autopilot.create_shadow_task(
    p_task_key text,
    p_goal_type text,
    p_goal_json jsonb DEFAULT '{}'::jsonb,
    p_priority integer DEFAULT 20,
    p_cost_cap_microusd bigint DEFAULT 0,
    p_created_by text DEFAULT 'DIRECTOR',
    p_source text DEFAULT 'CHATGPT'
)
RETURNS TABLE(task_id uuid, status text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.task;
    inserted autopilot.task;
    github_keys text[];
BEGIN
    IF p_task_key IS NULL OR p_task_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_INVALID';
    END IF;
    IF p_goal_type NOT IN (
        'AUTOPILOT_SMOKE_V1', 'EXTERNAL_WAIT_SHADOW_V1',
        'OWNER_BOUNDARY_V1', 'GITHUB_PR_READ_ONLY_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CAPABILITY_UNKNOWN';
    END IF;
    IF p_priority NOT IN (0, 10, 20, 30) THEN
        RAISE EXCEPTION 'AUTOPILOT_PRIORITY_INVALID';
    END IF;
    IF jsonb_typeof(COALESCE(p_goal_json, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_goal_json, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_GOAL_INVALID';
    END IF;
    IF p_goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
       AND COALESCE(p_goal_json->>'correlation_id', '') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_CORRELATION_INVALID';
    END IF;
    IF p_goal_type = 'GITHUB_PR_READ_ONLY_V1' THEN
        SELECT array_agg(key ORDER BY key) INTO github_keys
          FROM jsonb_object_keys(p_goal_json) AS keys(key);
        IF github_keys <> ARRAY['expected_head_sha', 'pr_number', 'repository', 'require_draft']
           OR p_goal_json->>'repository' <> 'olegmed1-art/bridge-video-free'
           OR COALESCE(p_goal_json->>'expected_head_sha', '') !~ '^[0-9a-f]{40}$'
           OR jsonb_typeof(p_goal_json->'pr_number') <> 'number'
           OR (p_goal_json->>'pr_number') !~ '^[1-9][0-9]{0,6}$'
           OR (p_goal_json->>'pr_number')::integer > 1000000
           OR p_goal_json->'require_draft' <> 'true'::jsonb
           OR p_cost_cap_microusd <> 0 THEN
            RAISE EXCEPTION 'AUTOPILOT_GITHUB_CONTRACT_INVALID';
        END IF;
    ELSIF (p_goal_type = 'EXTERNAL_WAIT_SHADOW_V1'
           AND (SELECT count(*) FROM jsonb_object_keys(COALESCE(p_goal_json, '{}'::jsonb))) <> 1)
       OR (p_goal_type <> 'EXTERNAL_WAIT_SHADOW_V1'
           AND COALESCE(p_goal_json, '{}'::jsonb) <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'AUTOPILOT_GOAL_FIELDS_INVALID';
    END IF;
    IF p_cost_cap_microusd NOT BETWEEN 0 AND 20000000 THEN
        RAISE EXCEPTION 'AUTOPILOT_COST_CAP_INVALID';
    END IF;
    IF length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_ORIGIN_INVALID';
    END IF;

    SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> p_goal_type OR existing.goal_json <> COALESCE(p_goal_json, '{}'::jsonb)
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> p_cost_cap_microusd
           OR existing.created_by <> p_created_by OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json, priority,
        cost_cap_microusd, created_by, source
    ) VALUES (
        p_task_key, p_goal_type, COALESCE(p_goal_json, '{}'::jsonb), 'READY',
        CASE p_goal_type
            WHEN 'AUTOPILOT_SMOKE_V1' THEN 'shadow.noop'
            WHEN 'EXTERNAL_WAIT_SHADOW_V1' THEN 'shadow.wait'
            WHEN 'GITHUB_PR_READ_ONLY_V1' THEN 'github.pr.snapshot'
            ELSE 'policy.owner_boundary'
        END,
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'exact_head_required', p_goal_type = 'GITHUB_PR_READ_ONLY_V1'
        ),
        CASE p_goal_type
            WHEN 'AUTOPILOT_SMOKE_V1' THEN '["shadow.noop"]'::jsonb
            WHEN 'EXTERNAL_WAIT_SHADOW_V1' THEN '["shadow.wait"]'::jsonb
            WHEN 'GITHUB_PR_READ_ONLY_V1' THEN '["github.pr.snapshot"]'::jsonb
            ELSE '["policy.owner_boundary"]'::jsonb
        END,
        p_priority::smallint, p_cost_cap_microusd, p_created_by, p_source
    ) ON CONFLICT (task_key) DO NOTHING
    RETURNING * INTO inserted;

    IF inserted.task_id IS NULL THEN
        SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
        IF NOT FOUND OR existing.goal_type <> p_goal_type
           OR existing.goal_json <> COALESCE(p_goal_json, '{}'::jsonb)
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> p_cost_cap_microusd
           OR existing.created_by <> p_created_by OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    PERFORM autopilot.record_event(
        inserted.task_id, 'TASK_READY', 'NEW', 'READY',
        jsonb_build_object('goal_type', inserted.goal_type),
        'DIRECTOR', left(p_created_by, 256), 'create:' || p_task_key
    );
    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.complete_task(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint,
    p_evidence_class text, p_content_sha256 text, p_summary jsonb DEFAULT '{}'::jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    step_id uuid;
    old_state text;
BEGIN
    IF p_evidence_class NOT IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'GITHUB_PR_READ_ONLY_SNAPSHOT'
    ) OR p_content_sha256 !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_summary, '{}'::jsonb)) <> 'object'
       OR octet_length(COALESCE(p_summary, '{}'::jsonb)::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_INVALID';
    END IF;
    SELECT status INTO old_state FROM autopilot.task
     WHERE task_id = p_task_id AND status = 'RUNNING'
       AND lease_owner = p_worker_id AND lease_epoch = p_lease_epoch
     FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    SELECT step_attempt_id INTO step_id
      FROM autopilot.step_attempt
     WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch AND status = 'RUNNING'
     ORDER BY started_at DESC LIMIT 1;
    IF step_id IS NULL THEN RETURN false; END IF;

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_id = p_task_id
           AND (
               (goal_type = 'GITHUB_PR_READ_ONLY_V1'
                AND current_step_key = 'github.pr.snapshot'
                AND cost_cap_microusd = 0
                AND p_evidence_class = 'GITHUB_PR_READ_ONLY_SNAPSHOT')
               OR
               (goal_type <> 'GITHUB_PR_READ_ONLY_V1'
                AND p_evidence_class <> 'GITHUB_PR_READ_ONLY_SNAPSHOT')
           )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_EVIDENCE_MISMATCH';
    END IF;

    INSERT INTO autopilot.evidence (
        task_id, step_attempt_id, evidence_class, provider,
        content_sha256, metadata_json
    ) VALUES (
        p_task_id, step_id, p_evidence_class, 'ORACLE_RESIDENT',
        p_content_sha256, COALESCE(p_summary, '{}'::jsonb)
    ) ON CONFLICT DO NOTHING;
    UPDATE autopilot.step_attempt
       SET status = 'COMPLETED', result_summary_json = COALESCE(p_summary, '{}'::jsonb),
           completed_at = now()
     WHERE step_attempt_id = step_id;
    UPDATE autopilot.task
       SET status = 'DONE', lease_owner = NULL, lease_until = NULL,
           terminal_reason_code = 'ACCEPTANCE_EVIDENCE_RETAINED',
           safe_summary_json = COALESCE(p_summary, '{}'::jsonb), completed_at = now()
     WHERE task_id = p_task_id;
    IF NOT EXISTS (SELECT 1 FROM autopilot.evidence WHERE task_id = p_task_id AND retained) THEN
        RAISE EXCEPTION 'AUTOPILOT_DONE_WITHOUT_EVIDENCE';
    END IF;
    PERFORM autopilot.record_event(
        p_task_id, 'TASK_DONE', old_state, 'DONE',
        jsonb_build_object('evidence_class', p_evidence_class, 'content_sha256', p_content_sha256),
        'ORACLE_WORKER', p_worker_id,
        'done:' || p_task_id::text || ':' || p_lease_epoch::text
    );
    RETURN true;
END;
$$;

COMMENT ON SCHEMA autopilot IS
'School Autopilot canonical orchestration state. v1.2 permits one exact, public, read-only GitHub PR snapshot and no external mutation.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0301_autopilot_github_pr_read_only')
ON CONFLICT DO NOTHING;

COMMIT;
