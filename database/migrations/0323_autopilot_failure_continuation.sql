\set ON_ERROR_STOP on
BEGIN;

-- A failed role lane must not stop the project queue.  Existing independent
-- READY work remains claimable, while a bounded technical BLOCKED result gets
-- exactly one REPAIR attempt and one read-only VERIFY attempt.  A dependent
-- pre-declared successor is released only after success or verified repair.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0322_autopilot_chatgpt_role_dispatch'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_FAILURE_CONTINUATION_REQUIRES_0322';
    END IF;
END $$;

DO $$
DECLARE
    constraint_def text;
    constraint_expression text;
BEGIN
    SELECT pg_get_constraintdef(oid)
      INTO constraint_def
      FROM pg_constraint
     WHERE conrelid = 'autopilot.task'::regclass
       AND conname = 'task_goal_type_check';
    IF constraint_def IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_GOAL_CONSTRAINT_MISSING';
    END IF;
    IF position('CHATGPT_ROLE_FOLLOWUP_V1' IN constraint_def) = 0 THEN
        constraint_expression := substring(constraint_def FROM 8 FOR length(constraint_def) - 8);
        ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
        EXECUTE format(
            'ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK ((goal_type = %L) OR (%s))',
            'CHATGPT_ROLE_FOLLOWUP_V1', constraint_expression
        );
    END IF;
END $$;

ALTER TABLE autopilot.role_dispatch_outbox
    ADD COLUMN mode text NOT NULL DEFAULT 'READ_ONLY',
    ADD COLUMN repair_attempt smallint NOT NULL DEFAULT 0,
    ADD COLUMN origin_task_id uuid REFERENCES autopilot.task(task_id),
    ADD COLUMN prior_task_id uuid REFERENCES autopilot.task(task_id),
    ADD COLUMN blocked_result_code text,
    ADD COLUMN blocked_summary text,
    ADD CONSTRAINT role_dispatch_outbox_mode_check CHECK (
        mode IN ('READ_ONLY', 'REPAIR', 'VERIFY')
    ),
    ADD CONSTRAINT role_dispatch_outbox_followup_shape CHECK (
        (
            mode = 'READ_ONLY'
            AND repair_attempt = 0
            AND origin_task_id IS NULL
            AND prior_task_id IS NULL
            AND blocked_result_code IS NULL
            AND blocked_summary IS NULL
        ) OR (
            mode IN ('REPAIR', 'VERIFY')
            AND repair_attempt = 1
            AND origin_task_id IS NOT NULL
            AND prior_task_id IS NOT NULL
            AND blocked_result_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
            AND length(blocked_summary) BETWEEN 1 AND 160
            AND blocked_summary !~ '[[:cntrl:]]'
        )
    );

CREATE TABLE autopilot.role_dispatch_followup (
    parent_task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    followup_kind text NOT NULL CHECK (
        followup_kind IN ('CONTINUATION', 'REPAIR', 'VERIFY')
    ),
    followup_task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    origin_task_id uuid NOT NULL REFERENCES autopilot.task(task_id),
    trigger_result_code text NOT NULL CHECK (
        trigger_result_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (parent_task_id, followup_kind),
    UNIQUE (followup_task_id)
);

REVOKE ALL ON TABLE autopilot.role_dispatch_followup FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.role_dispatch_followup
    FROM autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.role_blocker_requires_owner(p_result_code text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT p_result_code ~ '(^|_)OWNER(_|$)'
        OR p_result_code ~ '(^|_)(ACCOUNT|CREDENTIAL|SECRET|SPEND|PAYMENT|BILLING|CANON_DECISION)(_|$)';
$$;

REVOKE ALL ON FUNCTION autopilot.role_blocker_requires_owner(text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION autopilot.create_chatgpt_role_followup_task(
    p_task_key text,
    p_goal_json jsonb,
    p_priority integer DEFAULT 20,
    p_created_by text DEFAULT 'AUTOPILOT_FAILURE_CONTROLLER',
    p_source text DEFAULT 'AUTOPILOT_FOLLOWUP'
)
RETURNS TABLE(task_id uuid, status text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.task;
    inserted autopilot.task;
    origin_row autopilot.task;
    prior_row autopilot.task;
    goal_keys text[];
BEGIN
    IF p_task_key IS NULL OR p_task_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_INVALID';
    END IF;
    IF p_priority NOT IN (0, 10, 20, 30)
       OR length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_FOLLOWUP_ORIGIN_INVALID';
    END IF;
    IF jsonb_typeof(COALESCE(p_goal_json, 'null'::jsonb)) <> 'object'
       OR octet_length(p_goal_json::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_FOLLOWUP_GOAL_INVALID';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO goal_keys
      FROM jsonb_object_keys(p_goal_json) AS keys(key);
    IF goal_keys IS DISTINCT FROM ARRAY[
           'blocked_result_code', 'blocked_summary', 'dispatch_epoch',
           'expected_head_sha', 'mailbox_pr', 'mode', 'origin_task_id',
           'prior_task_id', 'repair_attempt', 'repository', 'role', 'target_pr'
       ]
       OR p_goal_json->'repository' IS DISTINCT FROM '"olegmed1-art/bridge-video-free"'::jsonb
       OR p_goal_json->'mailbox_pr' IS DISTINCT FROM '1150'::jsonb
       OR COALESCE(p_goal_json->>'role', '') NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
       OR jsonb_typeof(p_goal_json->'target_pr') <> 'number'
       OR COALESCE(p_goal_json->>'target_pr', '') !~ '^[1-9][0-9]{0,6}$'
       OR (p_goal_json->>'target_pr')::integer > 1000000
       OR COALESCE(p_goal_json->>'expected_head_sha', '') !~ '^[0-9a-f]{40}$'
       OR jsonb_typeof(p_goal_json->'dispatch_epoch') <> 'number'
       OR COALESCE(p_goal_json->>'dispatch_epoch', '') !~ '^[1-9][0-9]{0,9}$'
       OR (p_goal_json->>'dispatch_epoch')::bigint > 1000000
       OR COALESCE(p_goal_json->>'mode', '') NOT IN ('REPAIR', 'VERIFY')
       OR p_goal_json->'repair_attempt' IS DISTINCT FROM '1'::jsonb
       OR COALESCE(p_goal_json->>'origin_task_id', '') !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_goal_json->>'prior_task_id', '') !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_goal_json->>'blocked_result_code', '') !~ '^[A-Z][A-Z0-9_]{0,63}$'
       OR length(COALESCE(p_goal_json->>'blocked_summary', '')) NOT BETWEEN 1 AND 160
       OR p_goal_json->>'blocked_summary' ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_FOLLOWUP_GOAL_INVALID';
    END IF;

    SELECT * INTO origin_row FROM autopilot.task AS t
     WHERE t.task_id = (p_goal_json->>'origin_task_id')::uuid;
    SELECT * INTO prior_row FROM autopilot.task AS t
     WHERE t.task_id = (p_goal_json->>'prior_task_id')::uuid;
    IF origin_row.task_id IS NULL
       OR origin_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
       OR prior_row.task_id IS NULL
       OR origin_row.goal_json->>'repository' <> p_goal_json->>'repository'
       OR origin_row.goal_json->>'role' <> p_goal_json->>'role'
       OR origin_row.goal_json->>'target_pr' <> p_goal_json->>'target_pr'
       OR (
           p_goal_json->>'mode' = 'REPAIR'
           AND (
               prior_row.task_id <> origin_row.task_id
               OR origin_row.goal_json->>'expected_head_sha' <>
                  p_goal_json->>'expected_head_sha'
           )
       )
       OR (
           p_goal_json->>'mode' = 'VERIFY'
           AND (
               prior_row.goal_type <> 'CHATGPT_ROLE_FOLLOWUP_V1'
               OR prior_row.goal_json->>'mode' <> 'REPAIR'
               OR prior_row.goal_json->>'origin_task_id' <>
                  origin_row.task_id::text
           )
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_FOLLOWUP_LINEAGE_INVALID';
    END IF;

    SELECT * INTO existing FROM autopilot.task AS t WHERE t.task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> 'CHATGPT_ROLE_FOLLOWUP_V1'
           OR existing.goal_json <> p_goal_json
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> 0
           OR existing.created_by <> p_created_by
           OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    INSERT INTO autopilot.task (
        task_key, goal_type, goal_json, status, current_step_key,
        acceptance_contract_json, allowed_capabilities_json, priority,
        model_turn_cap, cost_cap_microusd, created_by, source
    ) VALUES (
        p_task_key, 'CHATGPT_ROLE_FOLLOWUP_V1', p_goal_json, 'READY',
        'github.chatgpt.role.dispatch',
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'exact_head_required', true,
            'mailbox_pr', 1150,
            'mode', p_goal_json->>'mode',
            'repair_attempt_cap', 1,
            'cost_actual_microusd', 0
        ),
        '["github.chatgpt.role.dispatch"]'::jsonb,
        p_priority::smallint, 0, 0, p_created_by, p_source
    ) ON CONFLICT (task_key) DO NOTHING
    RETURNING * INTO inserted;

    IF inserted.task_id IS NULL THEN
        SELECT * INTO existing FROM autopilot.task AS t WHERE t.task_key = p_task_key;
        IF NOT FOUND OR existing.goal_type <> 'CHATGPT_ROLE_FOLLOWUP_V1'
           OR existing.goal_json <> p_goal_json
           OR existing.priority <> p_priority::smallint
           OR existing.cost_cap_microusd <> 0
           OR existing.created_by <> p_created_by
           OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.task_id, existing.status, false;
        RETURN;
    END IF;

    PERFORM autopilot.record_event(
        inserted.task_id, 'TASK_READY', 'NEW', 'READY',
        jsonb_build_object(
            'goal_type', inserted.goal_type,
            'mode', p_goal_json->>'mode',
            'origin_task_id', p_goal_json->>'origin_task_id'
        ),
        'SYSTEM', 'autopilot-failure-controller', 'create:' || p_task_key
    );
    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.create_chatgpt_role_followup_task(
    text,jsonb,integer,text,text
) FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.on_role_outbox_prepare()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    task_row autopilot.task;
BEGIN
    SELECT * INTO task_row FROM autopilot.task AS t
     WHERE t.task_id = NEW.task_id;
    IF NOT FOUND OR task_row.goal_type NOT IN (
        'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_TASK_INVALID';
    END IF;
    IF task_row.goal_type = 'CHATGPT_ROLE_FOLLOWUP_V1' THEN
        NEW.mode := task_row.goal_json->>'mode';
        NEW.repair_attempt := (task_row.goal_json->>'repair_attempt')::smallint;
        NEW.origin_task_id := (task_row.goal_json->>'origin_task_id')::uuid;
        NEW.prior_task_id := (task_row.goal_json->>'prior_task_id')::uuid;
        NEW.blocked_result_code := task_row.goal_json->>'blocked_result_code';
        NEW.blocked_summary := task_row.goal_json->>'blocked_summary';
    ELSE
        NEW.mode := 'READ_ONLY';
        NEW.repair_attempt := 0;
        NEW.origin_task_id := NULL;
        NEW.prior_task_id := NULL;
        NEW.blocked_result_code := NULL;
        NEW.blocked_summary := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER autopilot_role_outbox_prepare
BEFORE INSERT ON autopilot.role_dispatch_outbox
FOR EACH ROW EXECUTE FUNCTION autopilot.on_role_outbox_prepare();

REVOKE ALL ON FUNCTION autopilot.on_role_outbox_prepare() FROM PUBLIC;

-- Permit the existing, already-proven dispatcher to prepare the new bounded
-- follow-up task type.  The insert trigger above supplies its mode/context.
DO $migration$
DECLARE
    function_sql text;
    old_guard text := $old$IF NOT FOUND OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1' THEN$old$;
    new_guard text := $new$IF NOT FOUND OR task_row.goal_type NOT IN (
        'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
    ) THEN$new$;
BEGIN
    SELECT pg_get_functiondef(
        'autopilot.prepare_role_dispatch(uuid,text,bigint)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL OR strpos(function_sql, old_guard) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_PREPARE_0322_DEFINITION_UNEXPECTED';
    END IF;
    EXECUTE replace(function_sql, old_guard, new_guard);
END
$migration$;

CREATE OR REPLACE FUNCTION autopilot.claim_role_dispatch_outbox_v2(
    p_publisher_id text, p_lease_seconds integer DEFAULT 60
)
RETURNS TABLE(
    dispatch_id uuid, repository text, mailbox_pr integer, role text,
    target_pr integer, expected_head_sha text, dispatch_epoch bigint,
    task_fingerprint text, prepared_at_epoch bigint,
    claim_epoch bigint, attempt_no smallint, mode text,
    repair_attempt smallint, origin_task_id uuid, prior_task_id uuid,
    blocked_result_code text, blocked_summary text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
    SELECT claimed.dispatch_id, claimed.repository, claimed.mailbox_pr,
           claimed.role, claimed.target_pr, claimed.expected_head_sha,
           claimed.dispatch_epoch, claimed.task_fingerprint,
           claimed.prepared_at_epoch, claimed.claim_epoch, claimed.attempt_no,
           outbox.mode, outbox.repair_attempt, outbox.origin_task_id,
           outbox.prior_task_id, outbox.blocked_result_code,
           outbox.blocked_summary
      FROM autopilot.claim_role_dispatch_outbox(
               p_publisher_id, p_lease_seconds
           ) AS claimed
      JOIN autopilot.role_dispatch_outbox AS outbox
        ON outbox.dispatch_id = claimed.dispatch_id;
$$;

REVOKE ALL ON FUNCTION autopilot.claim_role_dispatch_outbox_v2(text,integer)
    FROM PUBLIC, autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.claim_role_dispatch_outbox_v2(text,integer)
    TO autopilot_runtime;

-- A repair is allowed to report the new exact target head it produced.  A
-- normal audit and the post-repair VERIFY remain bound to the expected head.
DO $migration$
DECLARE
    function_sql text;
    old_goal_guard text := $old$OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'$old$;
    new_goal_guard text := $new$OR task_row.goal_type NOT IN (
           'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
       )$new$;
    old_head_guard text := $old$OR outbox_row.expected_head_sha <> p_body->>'target_head_sha'$old$;
    new_head_guard text := $new$OR (
           outbox_row.mode IN ('READ_ONLY', 'VERIFY')
           AND outbox_row.expected_head_sha <> p_body->>'target_head_sha'
       )$new$;
BEGIN
    SELECT pg_get_functiondef(
        'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL
       OR strpos(function_sql, old_goal_guard) = 0
       OR strpos(function_sql, old_head_guard) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_0322_DEFINITION_UNEXPECTED';
    END IF;
    function_sql := replace(function_sql, old_goal_guard, new_goal_guard);
    function_sql := replace(function_sql, old_head_guard, new_head_guard);
    EXECUTE function_sql;
END
$migration$;

CREATE OR REPLACE FUNCTION autopilot.materialize_role_continuation(
    p_parent_task_id uuid, p_result_code text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    parent_row autopilot.task;
    followup_id uuid;
BEGIN
    SELECT * INTO parent_row FROM autopilot.task AS t
     WHERE t.task_id = p_parent_task_id;
    IF NOT FOUND OR parent_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
       OR parent_row.goal_json->'successor_task_key' IS NULL
       OR parent_row.goal_json->'successor_task_key' = 'null'::jsonb THEN
        RETURN NULL;
    END IF;
    SELECT created.task_id INTO followup_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          parent_row.goal_json->>'successor_task_key',
          jsonb_build_object(
              'repository', parent_row.goal_json->>'repository',
              'mailbox_pr', (parent_row.goal_json->>'mailbox_pr')::integer,
              'role', parent_row.goal_json->>'successor_role',
              'target_pr', (parent_row.goal_json->>'successor_target_pr')::integer,
              'expected_head_sha', parent_row.goal_json->>'successor_expected_head_sha',
              'dispatch_epoch', (parent_row.goal_json->>'dispatch_epoch')::bigint + 1,
              'successor_task_key', NULL,
              'successor_role', NULL,
              'successor_target_pr', NULL,
              'successor_expected_head_sha', NULL
          ),
          parent_row.priority, 'CHATGPT_ROLE_DISPATCH_V1', 'AUTOPILOT_SUCCESSOR'
      ) AS created;
    INSERT INTO autopilot.role_dispatch_followup (
        parent_task_id, followup_kind, followup_task_id,
        origin_task_id, trigger_result_code
    ) VALUES (
        parent_row.task_id, 'CONTINUATION', followup_id,
        parent_row.task_id, p_result_code
    ) ON CONFLICT (parent_task_id, followup_kind) DO NOTHING;
    RETURN followup_id;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.materialize_role_repair(
    p_origin_task_id uuid, p_result_code text, p_summary text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    origin_row autopilot.task;
    followup_id uuid;
BEGIN
    SELECT * INTO origin_row FROM autopilot.task AS t
     WHERE t.task_id = p_origin_task_id;
    IF NOT FOUND OR origin_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
       OR autopilot.role_blocker_requires_owner(p_result_code) THEN
        RETURN NULL;
    END IF;
    SELECT created.task_id INTO followup_id
      FROM autopilot.create_chatgpt_role_followup_task(
          'role-repair:' || origin_row.task_id::text,
          jsonb_build_object(
              'repository', origin_row.goal_json->>'repository',
              'mailbox_pr', (origin_row.goal_json->>'mailbox_pr')::integer,
              'role', origin_row.goal_json->>'role',
              'target_pr', (origin_row.goal_json->>'target_pr')::integer,
              'expected_head_sha', origin_row.goal_json->>'expected_head_sha',
              'dispatch_epoch', (origin_row.goal_json->>'dispatch_epoch')::bigint + 1,
              'mode', 'REPAIR',
              'repair_attempt', 1,
              'origin_task_id', origin_row.task_id::text,
              'prior_task_id', origin_row.task_id::text,
              'blocked_result_code', p_result_code,
              'blocked_summary', p_summary
          ),
          origin_row.priority, 'AUTOPILOT_FAILURE_CONTROLLER', 'AUTOPILOT_REPAIR'
      ) AS created;
    INSERT INTO autopilot.role_dispatch_followup (
        parent_task_id, followup_kind, followup_task_id,
        origin_task_id, trigger_result_code
    ) VALUES (
        origin_row.task_id, 'REPAIR', followup_id,
        origin_row.task_id, p_result_code
    ) ON CONFLICT (parent_task_id, followup_kind) DO NOTHING;
    RETURN followup_id;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.materialize_role_verification(
    p_repair_task_id uuid, p_result_head_sha text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    repair_row autopilot.task;
    followup_id uuid;
BEGIN
    SELECT * INTO repair_row FROM autopilot.task AS t
     WHERE t.task_id = p_repair_task_id;
    IF NOT FOUND OR repair_row.goal_type <> 'CHATGPT_ROLE_FOLLOWUP_V1'
       OR repair_row.goal_json->>'mode' <> 'REPAIR'
       OR p_result_head_sha !~ '^[0-9a-f]{40}$' THEN
        RETURN NULL;
    END IF;
    SELECT created.task_id INTO followup_id
      FROM autopilot.create_chatgpt_role_followup_task(
          'role-verify:' || repair_row.task_id::text,
          jsonb_build_object(
              'repository', repair_row.goal_json->>'repository',
              'mailbox_pr', (repair_row.goal_json->>'mailbox_pr')::integer,
              'role', repair_row.goal_json->>'role',
              'target_pr', (repair_row.goal_json->>'target_pr')::integer,
              'expected_head_sha', p_result_head_sha,
              'dispatch_epoch', (repair_row.goal_json->>'dispatch_epoch')::bigint + 1,
              'mode', 'VERIFY',
              'repair_attempt', 1,
              'origin_task_id', repair_row.goal_json->>'origin_task_id',
              'prior_task_id', repair_row.task_id::text,
              'blocked_result_code', repair_row.goal_json->>'blocked_result_code',
              'blocked_summary', repair_row.goal_json->>'blocked_summary'
          ),
          repair_row.priority, 'AUTOPILOT_FAILURE_CONTROLLER', 'AUTOPILOT_VERIFY'
      ) AS created;
    INSERT INTO autopilot.role_dispatch_followup (
        parent_task_id, followup_kind, followup_task_id,
        origin_task_id, trigger_result_code
    ) VALUES (
        repair_row.task_id, 'VERIFY', followup_id,
        (repair_row.goal_json->>'origin_task_id')::uuid,
        repair_row.goal_json->>'blocked_result_code'
    ) ON CONFLICT (parent_task_id, followup_kind) DO NOTHING;
    RETURN followup_id;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.materialize_role_continuation(uuid,text)
    FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.materialize_role_repair(uuid,text,text)
    FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.materialize_role_verification(uuid,text)
    FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.on_role_task_terminal()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    result_code text;
    result_summary text;
BEGIN
    IF OLD.status = NEW.status
       OR NEW.status NOT IN (
           'DONE', 'FAILED_CLOSED', 'OWNER_REQUIRED', 'BUDGET_STOP', 'CANCELLED'
       )
       OR OLD.status IN (
           'DONE', 'FAILED_CLOSED', 'OWNER_REQUIRED', 'BUDGET_STOP', 'CANCELLED'
       ) THEN
        RETURN NEW;
    END IF;
    result_code := COALESCE(
        NEW.safe_summary_json->>'result_code',
        NEW.terminal_reason_code,
        'UNKNOWN_ROLE_RESULT'
    );

    IF NEW.goal_type = 'CHATGPT_ROLE_DISPATCH_V1' THEN
        IF NEW.status = 'FAILED_CLOSED'
           AND NEW.safe_summary_json->>'status' = 'BLOCKED' THEN
            result_summary := NEW.safe_summary_json->>'summary';
            IF result_summary IS NOT NULL THEN
                PERFORM autopilot.materialize_role_repair(
                    NEW.task_id, result_code, result_summary
                );
            END IF;
        ELSIF NEW.status = 'DONE' THEN
            -- The 0322 callback also creates this successor.  The task-key
            -- contract makes the call idempotent while this trigger records
            -- explicit continuation lineage.
            PERFORM autopilot.materialize_role_continuation(
                NEW.task_id, result_code
            );
        END IF;
    ELSIF NEW.goal_type = 'CHATGPT_ROLE_FOLLOWUP_V1'
          AND NEW.goal_json->>'mode' = 'REPAIR'
          AND NEW.status = 'DONE' THEN
        PERFORM autopilot.materialize_role_verification(
            NEW.task_id, NEW.safe_summary_json->>'target_head_sha'
        );
    ELSIF NEW.goal_type = 'CHATGPT_ROLE_FOLLOWUP_V1'
          AND NEW.goal_json->>'mode' = 'VERIFY'
          AND NEW.status = 'DONE' THEN
        PERFORM autopilot.materialize_role_continuation(
            (NEW.goal_json->>'origin_task_id')::uuid,
            'REPAIR_VERIFIED'
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS autopilot_role_task_terminal_followup ON autopilot.task;
CREATE TRIGGER autopilot_role_task_terminal_followup
AFTER UPDATE OF status ON autopilot.task
FOR EACH ROW EXECUTE FUNCTION autopilot.on_role_task_terminal();

REVOKE ALL ON FUNCTION autopilot.on_role_task_terminal() FROM PUBLIC;

COMMENT ON TABLE autopilot.role_dispatch_followup IS
'Idempotent lineage for independent continuation, one bounded repair, and one verification.';
COMMENT ON FUNCTION autopilot.on_role_task_terminal() IS
'Keeps independent READY work claimable and repairs a blocked lane once before releasing its dependent successor.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0323_autopilot_failure_continuation')
ON CONFLICT DO NOTHING;

COMMIT;
