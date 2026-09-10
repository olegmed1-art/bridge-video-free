\set ON_ERROR_STOP on
BEGIN;

-- Event-driven GitHub mailbox bridge for bounded ChatGPT role work.
--
-- Production intentionally remained at 0302 while later artifacts were being
-- proven.  This bridge must be applied only after the complete 0321 baseline;
-- otherwise 0303/0306 would later replace its constraints.  Fail before any
-- schema change instead of permitting that unsafe ordering.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0321_autopilot_broker_schema_versioned_gate'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_DISPATCH_REQUIRES_0321';
    END IF;
END $$;

DO $$
DECLARE
    attrs record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autopilot_callback') THEN
        CREATE ROLE autopilot_callback NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
      INTO attrs FROM pg_roles WHERE rolname = 'autopilot_callback';
    IF attrs.rolcanlogin OR attrs.rolsuper OR attrs.rolcreatedb OR attrs.rolcreaterole
       OR attrs.rolreplication OR attrs.rolbypassrls THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_ROLE_UNSAFE';
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
    IF position('CHATGPT_ROLE_DISPATCH_V1' IN constraint_def) = 0 THEN
        constraint_expression := substring(constraint_def FROM 8 FOR length(constraint_def) - 8);
        ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
        EXECUTE format(
            'ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK ((goal_type = %L) OR (%s))',
            'CHATGPT_ROLE_DISPATCH_V1', constraint_expression
        );
    END IF;

    SELECT pg_get_constraintdef(oid)
      INTO constraint_def
      FROM pg_constraint
     WHERE conrelid = 'autopilot.step_attempt'::regclass
       AND conname = 'step_attempt_capability_name_check';
    IF constraint_def IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_STEP_CAPABILITY_CONSTRAINT_MISSING';
    END IF;
    IF position('github.chatgpt.role.dispatch' IN constraint_def) = 0 THEN
        constraint_expression := substring(constraint_def FROM 8 FOR length(constraint_def) - 8);
        ALTER TABLE autopilot.step_attempt DROP CONSTRAINT step_attempt_capability_name_check;
        EXECUTE format(
            'ALTER TABLE autopilot.step_attempt ADD CONSTRAINT step_attempt_capability_name_check CHECK ((capability_name = %L) OR (%s))',
            'github.chatgpt.role.dispatch', constraint_expression
        );
    END IF;

    SELECT pg_get_constraintdef(oid)
      INTO constraint_def
      FROM pg_constraint
     WHERE conrelid = 'autopilot.evidence'::regclass
       AND conname = 'evidence_evidence_class_check';
    IF constraint_def IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_CLASS_CONSTRAINT_MISSING';
    END IF;
    IF position('CHATGPT_ROLE_DISPATCH_RESULT' IN constraint_def) = 0 THEN
        constraint_expression := substring(constraint_def FROM 8 FOR length(constraint_def) - 8);
        ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_evidence_class_check;
        EXECUTE format(
            'ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_evidence_class_check CHECK ((evidence_class = %L) OR (%s))',
            'CHATGPT_ROLE_DISPATCH_RESULT', constraint_expression
        );
    END IF;

    SELECT pg_get_constraintdef(oid)
      INTO constraint_def
      FROM pg_constraint
     WHERE conrelid = 'autopilot.evidence'::regclass
       AND conname = 'evidence_provider_check';
    IF constraint_def IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_EVIDENCE_PROVIDER_CONSTRAINT_MISSING';
    END IF;
    IF position('GITHUB_WEBHOOK' IN constraint_def) = 0 THEN
        constraint_expression := substring(constraint_def FROM 8 FOR length(constraint_def) - 8);
        ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_provider_check;
        EXECUTE format(
            'ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_provider_check CHECK ((provider = %L) OR (%s))',
            'GITHUB_WEBHOOK', constraint_expression
        );
    END IF;
END $$;

-- Keep draft-repair available during a rolling broker deployment.  The v1
-- policy remains accepted for the currently pinned release; v2 adds only the
-- separately bounded role-comment route.  Artifact/policy/provenance digests
-- remain independently enforced by the worker and the 0319-0321 gates.
DO $migration$
DECLARE
    function_sql text;
    old_validation text := $old$
           OR p_summary->>'broker_policy_version' IS DISTINCT FROM 'physical-no-merge-v1'
           OR COALESCE(p_summary->>'broker_source_sha', '') !~$old$;
    new_validation text := $new$
           OR COALESCE(p_summary->>'broker_policy_version', '') NOT IN (
              'physical-no-merge-v1', 'physical-no-merge-v2'
           )
           OR COALESCE(p_summary->>'broker_source_sha', '') !~$new$;
BEGIN
    SELECT pg_get_functiondef(
        'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL
       OR (strpos(function_sql, old_validation) = 0
           AND strpos(function_sql, new_validation) = 0) THEN
        RAISE EXCEPTION 'AUTOPILOT_COMPLETE_TASK_0321_DEFINITION_UNEXPECTED';
    END IF;
    IF strpos(function_sql, new_validation) = 0 THEN
        EXECUTE replace(function_sql, old_validation, new_validation);
    END IF;
END
$migration$;

CREATE TABLE autopilot.role_dispatch_outbox (
    dispatch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL UNIQUE REFERENCES autopilot.task(task_id),
    step_attempt_id uuid NOT NULL UNIQUE REFERENCES autopilot.step_attempt(step_attempt_id),
    repository text NOT NULL CHECK (repository = 'olegmed1-art/bridge-video-free'),
    mailbox_pr integer NOT NULL CHECK (mailbox_pr = 1150),
    role text NOT NULL CHECK (role IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')),
    target_pr integer NOT NULL CHECK (target_pr BETWEEN 1 AND 1000000),
    expected_head_sha text NOT NULL CHECK (expected_head_sha ~ '^[0-9a-f]{40}$'),
    dispatch_epoch bigint NOT NULL CHECK (dispatch_epoch BETWEEN 1 AND 1000000),
    task_fingerprint text NOT NULL CHECK (task_fingerprint ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN (
        'PENDING', 'CLAIMED', 'RETRY', 'SENT', 'CALLBACK_ACCEPTED', 'FAILED_CLOSED'
    )),
    claim_owner text,
    claim_epoch bigint NOT NULL DEFAULT 0 CHECK (claim_epoch >= 0),
    claim_until timestamptz,
    attempts smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    max_attempts smallint NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 5),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    github_dispatch_comment_id bigint CHECK (github_dispatch_comment_id > 0),
    dispatch_body_sha256 text CHECK (dispatch_body_sha256 ~ '^[0-9a-f]{64}$'),
    last_error_code text CHECK (last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    prepared_by text NOT NULL CHECK (length(prepared_by) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    callback_deadline_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT role_dispatch_outbox_claim_shape CHECK (
        (status = 'CLAIMED' AND claim_owner IS NOT NULL AND claim_until IS NOT NULL)
        OR (status <> 'CLAIMED' AND claim_owner IS NULL AND claim_until IS NULL)
    ),
    CONSTRAINT role_dispatch_outbox_sent_shape CHECK (
        (status IN ('SENT', 'CALLBACK_ACCEPTED')
         AND github_dispatch_comment_id IS NOT NULL
         AND dispatch_body_sha256 IS NOT NULL
         AND sent_at IS NOT NULL
         AND callback_deadline_at IS NOT NULL)
        OR (status NOT IN ('SENT', 'CALLBACK_ACCEPTED'))
    ),
    CONSTRAINT role_dispatch_outbox_terminal_shape CHECK (
        (status IN ('CALLBACK_ACCEPTED', 'FAILED_CLOSED') AND completed_at IS NOT NULL)
        OR (status NOT IN ('CALLBACK_ACCEPTED', 'FAILED_CLOSED') AND completed_at IS NULL)
    )
);

CREATE INDEX role_dispatch_outbox_ready_idx
    ON autopilot.role_dispatch_outbox (next_attempt_at, created_at)
    WHERE status IN ('PENDING', 'RETRY');
CREATE INDEX role_dispatch_outbox_claim_idx
    ON autopilot.role_dispatch_outbox (claim_until)
    WHERE status = 'CLAIMED';

CREATE TABLE autopilot.role_dispatch_callback_receipt (
    delivery_id text PRIMARY KEY
        CHECK (delivery_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    payload_fingerprint text NOT NULL CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    dispatch_id uuid NOT NULL UNIQUE REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
    actor_login text NOT NULL CHECK (actor_login = 'olegmed1-art'),
    actor_id bigint NOT NULL CHECK (actor_id = 315099490),
    author_association text NOT NULL CHECK (author_association = 'OWNER'),
    app_slug text NOT NULL CHECK (app_slug = 'chatgpt-codex-connector'),
    app_id bigint NOT NULL CHECK (app_id = 1144995),
    callback_body jsonb NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT role_dispatch_callback_body_object CHECK (jsonb_typeof(callback_body) = 'object'),
    CONSTRAINT role_dispatch_callback_body_bound CHECK (octet_length(callback_body::text) <= 2048)
);

REVOKE ALL ON TABLE autopilot.role_dispatch_outbox FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.role_dispatch_callback_receipt FROM PUBLIC;
REVOKE ALL ON TABLE autopilot.role_dispatch_outbox FROM autopilot_runtime, autopilot_runtime_principal;
REVOKE ALL ON TABLE autopilot.role_dispatch_callback_receipt FROM autopilot_runtime, autopilot_runtime_principal;
REVOKE ALL ON TABLE autopilot.role_dispatch_outbox FROM autopilot_callback;
REVOKE ALL ON TABLE autopilot.role_dispatch_callback_receipt FROM autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.create_chatgpt_role_dispatch_task(
    p_task_key text,
    p_goal_json jsonb,
    p_priority integer DEFAULT 20,
    p_created_by text DEFAULT 'DIRECTOR',
    p_source text DEFAULT 'CHATGPT'
)
RETURNS TABLE(task_id uuid, status text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    successor_present_count integer;
    existing autopilot.task;
    inserted autopilot.task;
BEGIN
    IF p_task_key IS NULL OR p_task_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_TASK_KEY_INVALID';
    END IF;
    IF p_priority NOT IN (0, 10, 20, 30) THEN
        RAISE EXCEPTION 'AUTOPILOT_PRIORITY_INVALID';
    END IF;
    IF length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_ORIGIN_INVALID';
    END IF;
    IF jsonb_typeof(COALESCE(p_goal_json, 'null'::jsonb)) <> 'object'
       OR octet_length(p_goal_json::text) > 8192 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_GOAL_INVALID';
    END IF;

    SELECT count(*) FILTER (WHERE value <> 'null'::jsonb)
      INTO successor_present_count
      FROM jsonb_each(p_goal_json) AS fields(key, value)
     WHERE key IN (
         'successor_task_key', 'successor_role', 'successor_target_pr',
         'successor_expected_head_sha'
     );

    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_goal_json) AS keys(key))
       IS DISTINCT FROM ARRAY[
           'dispatch_epoch', 'expected_head_sha', 'mailbox_pr', 'repository', 'role',
           'successor_expected_head_sha', 'successor_role', 'successor_target_pr',
           'successor_task_key', 'target_pr'
       ]
       OR p_goal_json->'repository' IS DISTINCT FROM '"olegmed1-art/bridge-video-free"'::jsonb
       OR p_goal_json->'mailbox_pr' IS DISTINCT FROM '1150'::jsonb
       OR COALESCE(p_goal_json->>'role', '') NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
       OR jsonb_typeof(p_goal_json->'target_pr') <> 'number'
       OR (p_goal_json->>'target_pr') !~ '^[1-9][0-9]{0,6}$'
       OR (p_goal_json->>'target_pr')::integer > 1000000
       OR COALESCE(p_goal_json->>'expected_head_sha', '') !~ '^[0-9a-f]{40}$'
       OR jsonb_typeof(p_goal_json->'dispatch_epoch') <> 'number'
       OR (p_goal_json->>'dispatch_epoch') !~ '^[1-9][0-9]{0,9}$'
       OR (p_goal_json->>'dispatch_epoch')::bigint > 1000000
       OR successor_present_count NOT IN (0, 4)
       OR (successor_present_count = 0 AND EXISTS (
           SELECT 1 FROM jsonb_each(p_goal_json) AS fields(key, value)
            WHERE key IN (
                'successor_task_key', 'successor_role', 'successor_target_pr',
                'successor_expected_head_sha'
            ) AND value <> 'null'::jsonb
       ))
       OR (successor_present_count = 4 AND (
           (p_goal_json->>'dispatch_epoch')::bigint >= 1000000
           OR
           COALESCE(p_goal_json->>'successor_task_key', '') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
           OR COALESCE(p_goal_json->>'successor_role', '') NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
           OR jsonb_typeof(p_goal_json->'successor_target_pr') <> 'number'
           OR (p_goal_json->>'successor_target_pr') !~ '^[1-9][0-9]{0,6}$'
           OR (p_goal_json->>'successor_target_pr')::integer > 1000000
           OR COALESCE(p_goal_json->>'successor_expected_head_sha', '') !~ '^[0-9a-f]{40}$'
       )) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_GOAL_INVALID';
    END IF;

    SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
    IF FOUND THEN
        IF existing.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
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
        p_task_key, 'CHATGPT_ROLE_DISPATCH_V1', p_goal_json, 'READY',
        'github.chatgpt.role.dispatch',
        jsonb_build_object(
            'retained_evidence_required', true,
            'production_mutation', false,
            'exact_head_required', true,
            'mailbox_pr', 1150,
            'cost_actual_microusd', 0
        ),
        '["github.chatgpt.role.dispatch"]'::jsonb,
        p_priority::smallint, 0, 0, p_created_by, p_source
    ) ON CONFLICT (task_key) DO NOTHING
    RETURNING * INTO inserted;

    IF inserted.task_id IS NULL THEN
        SELECT * INTO existing FROM autopilot.task WHERE task_key = p_task_key;
        IF NOT FOUND OR existing.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
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
        jsonb_build_object('goal_type', inserted.goal_type, 'role', p_goal_json->>'role'),
        'DIRECTOR', left(p_created_by, 256), 'create:' || p_task_key
    );
    RETURN QUERY SELECT inserted.task_id, inserted.status, true;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.prepare_role_dispatch(
    p_task_id uuid, p_worker_id text, p_lease_epoch bigint
)
RETURNS TABLE(
    dispatch_id uuid, repository text, mailbox_pr integer, role text,
    target_pr integer, expected_head_sha text, dispatch_epoch bigint,
    task_fingerprint text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    task_row autopilot.task;
    step_id uuid;
    outbox_row autopilot.role_dispatch_outbox;
    computed_fingerprint text;
BEGIN
    IF length(COALESCE(p_worker_id, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_WORKER_ID_INVALID';
    END IF;

    SELECT * INTO task_row FROM autopilot.task
     WHERE task_id = p_task_id FOR UPDATE;
    IF NOT FOUND OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1' THEN
        RETURN;
    END IF;
    computed_fingerprint := encode(public.digest(
        convert_to(task_row.task_id::text || ':' || task_row.goal_json::text, 'UTF8'), 'sha256'
    ), 'hex');

    IF task_row.status = 'WAITING_EXTERNAL' AND task_row.lease_epoch = p_lease_epoch THEN
        SELECT * INTO outbox_row FROM autopilot.role_dispatch_outbox
         WHERE task_id = p_task_id;
        IF FOUND AND outbox_row.task_fingerprint = computed_fingerprint
           AND outbox_row.prepared_by = p_worker_id THEN
            RETURN QUERY SELECT outbox_row.dispatch_id, outbox_row.repository,
                outbox_row.mailbox_pr, outbox_row.role, outbox_row.target_pr,
                outbox_row.expected_head_sha, outbox_row.dispatch_epoch,
                outbox_row.task_fingerprint;
        END IF;
        RETURN;
    END IF;

    IF task_row.status <> 'RUNNING'
       OR task_row.current_step_key <> 'github.chatgpt.role.dispatch'
       OR task_row.lease_owner <> p_worker_id
       OR task_row.lease_epoch <> p_lease_epoch
       OR task_row.cost_cap_microusd <> 0
       OR task_row.cost_reserved_microusd <> 0
       OR task_row.cost_actual_microusd <> 0 THEN
        RETURN;
    END IF;
    SELECT step_attempt_id INTO step_id FROM autopilot.step_attempt
     WHERE task_id = p_task_id AND lease_epoch = p_lease_epoch
       AND status = 'RUNNING' AND capability_name = 'github.chatgpt.role.dispatch'
     ORDER BY started_at DESC LIMIT 1;
    IF step_id IS NULL THEN RETURN; END IF;

    INSERT INTO autopilot.role_dispatch_outbox (
        task_id, step_attempt_id, repository, mailbox_pr, role, target_pr,
        expected_head_sha, dispatch_epoch, task_fingerprint, prepared_by
    ) VALUES (
        p_task_id, step_id, task_row.goal_json->>'repository',
        (task_row.goal_json->>'mailbox_pr')::integer, task_row.goal_json->>'role',
        (task_row.goal_json->>'target_pr')::integer,
        task_row.goal_json->>'expected_head_sha',
        (task_row.goal_json->>'dispatch_epoch')::bigint,
        computed_fingerprint, p_worker_id
    ) RETURNING * INTO outbox_row;

    UPDATE autopilot.step_attempt SET status = 'WAITING_EXTERNAL'
     WHERE step_attempt_id = step_id AND status = 'RUNNING';
    UPDATE autopilot.task
       SET status = 'WAITING_EXTERNAL', lease_owner = NULL, lease_until = NULL
     WHERE task_id = p_task_id;
    PERFORM autopilot.record_event(
        p_task_id, 'ROLE_DISPATCH_QUEUED', 'RUNNING', 'WAITING_EXTERNAL',
        jsonb_build_object(
            'dispatch_id', outbox_row.dispatch_id,
            'dispatch_epoch', outbox_row.dispatch_epoch,
            'role', outbox_row.role,
            'task_fingerprint', outbox_row.task_fingerprint
        ),
        'ORACLE_WORKER', p_worker_id,
        'role-dispatch:' || outbox_row.dispatch_id::text
    );

    RETURN QUERY SELECT outbox_row.dispatch_id, outbox_row.repository,
        outbox_row.mailbox_pr, outbox_row.role, outbox_row.target_pr,
        outbox_row.expected_head_sha, outbox_row.dispatch_epoch,
        outbox_row.task_fingerprint;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.claim_role_dispatch_outbox(
    p_publisher_id text, p_lease_seconds integer DEFAULT 60
)
RETURNS TABLE(
    dispatch_id uuid, repository text, mailbox_pr integer, role text,
    target_pr integer, expected_head_sha text, dispatch_epoch bigint,
    task_fingerprint text, prepared_at_epoch bigint,
    claim_epoch bigint, attempt_no smallint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    claimed autopilot.role_dispatch_outbox;
    expired autopilot.role_dispatch_outbox;
BEGIN
    IF length(COALESCE(p_publisher_id, '')) NOT BETWEEN 1 AND 256
       OR p_lease_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_CLAIM_INVALID';
    END IF;
    FOR expired IN
        SELECT * FROM autopilot.role_dispatch_outbox
         WHERE status = 'CLAIMED' AND claim_until <= now()
           AND attempts >= max_attempts
         FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE autopilot.role_dispatch_outbox
           SET status = 'FAILED_CLOSED', claim_owner = NULL, claim_until = NULL,
               last_error_code = 'ROLE_DISPATCH_CLAIM_EXPIRED',
               updated_at = now(), completed_at = now()
         WHERE dispatch_id = expired.dispatch_id;
        UPDATE autopilot.step_attempt
           SET status = 'FAILED_CLOSED', error_code = 'ROLE_DISPATCH_DELIVERY_EXHAUSTED',
               completed_at = now()
         WHERE step_attempt_id = expired.step_attempt_id AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.task
           SET status = 'FAILED_CLOSED', terminal_reason_code = 'ROLE_DISPATCH_DELIVERY_EXHAUSTED',
               completed_at = now(),
               safe_summary_json = '{"result_code":"ROLE_DISPATCH_CLAIM_EXPIRED"}'::jsonb
         WHERE task_id = expired.task_id AND status = 'WAITING_EXTERNAL';
        PERFORM autopilot.record_event(
            expired.task_id, 'TASK_FAILED_CLOSED', 'WAITING_EXTERNAL', 'FAILED_CLOSED',
            jsonb_build_object('reason_code', 'ROLE_DISPATCH_DELIVERY_EXHAUSTED'),
            'SYSTEM', 'role-dispatch-outbox',
            'role-dispatch-expired:' || expired.dispatch_id::text
        );
    END LOOP;
    WITH candidate AS (
        SELECT outbox.dispatch_id
          FROM autopilot.role_dispatch_outbox outbox
         WHERE (
             (outbox.status IN ('PENDING', 'RETRY') AND outbox.next_attempt_at <= now())
             OR (outbox.status = 'CLAIMED' AND outbox.claim_until <= now())
         ) AND outbox.attempts < outbox.max_attempts
         ORDER BY outbox.next_attempt_at, outbox.created_at
         FOR UPDATE SKIP LOCKED LIMIT 1
    )
    UPDATE autopilot.role_dispatch_outbox outbox
       SET status = 'CLAIMED', claim_owner = p_publisher_id,
           claim_epoch = outbox.claim_epoch + 1,
           claim_until = now() + make_interval(secs => p_lease_seconds),
           attempts = outbox.attempts + 1, updated_at = now()
      FROM candidate WHERE outbox.dispatch_id = candidate.dispatch_id
    RETURNING outbox.* INTO claimed;
    IF NOT FOUND THEN RETURN; END IF;
    RETURN QUERY SELECT claimed.dispatch_id, claimed.repository, claimed.mailbox_pr,
        claimed.role, claimed.target_pr, claimed.expected_head_sha,
        claimed.dispatch_epoch, claimed.task_fingerprint,
        floor(extract(epoch FROM claimed.created_at))::bigint,
        claimed.claim_epoch, claimed.attempts;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.mark_role_dispatch_sent(
    p_dispatch_id uuid, p_publisher_id text, p_claim_epoch bigint,
    p_github_comment_id bigint, p_dispatch_body_sha256 text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    affected integer;
BEGIN
    IF p_github_comment_id <= 0 OR p_dispatch_body_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_SENT_INVALID';
    END IF;
    UPDATE autopilot.role_dispatch_outbox
       SET status = 'SENT', claim_owner = NULL, claim_until = NULL,
           github_dispatch_comment_id = p_github_comment_id,
           dispatch_body_sha256 = p_dispatch_body_sha256,
           sent_at = now(), callback_deadline_at = now() + interval '15 minutes',
           updated_at = now(), last_error_code = NULL
     WHERE dispatch_id = p_dispatch_id AND status = 'CLAIMED'
       AND claim_owner = p_publisher_id AND claim_epoch = p_claim_epoch
       AND claim_until > now();
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN affected = 1;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.reconcile_role_dispatch_callbacks()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    expired autopilot.role_dispatch_outbox;
    reconciled integer := 0;
BEGIN
    FOR expired IN
        SELECT outbox.* FROM autopilot.role_dispatch_outbox outbox
         WHERE outbox.status = 'SENT'
           AND outbox.callback_deadline_at IS NOT NULL
           AND outbox.callback_deadline_at <= now()
         ORDER BY outbox.callback_deadline_at
         FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE autopilot.step_attempt
           SET status = 'FAILED_CLOSED', error_code = 'ROLE_CALLBACK_DEADLINE_EXCEEDED',
               completed_at = now()
         WHERE step_attempt_id = expired.step_attempt_id AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.task
           SET status = 'FAILED_CLOSED', terminal_reason_code = 'ROLE_CALLBACK_DEADLINE_EXCEEDED',
               safe_summary_json = jsonb_build_object(
                   'dispatch_id', expired.dispatch_id,
                   'result_code', 'ROLE_CALLBACK_DEADLINE_EXCEEDED'
               ), completed_at = now()
         WHERE task_id = expired.task_id AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.role_dispatch_outbox
           SET status = 'FAILED_CLOSED', completed_at = now(), updated_at = now(),
               last_error_code = 'ROLE_CALLBACK_DEADLINE_EXCEEDED'
         WHERE dispatch_id = expired.dispatch_id AND status = 'SENT';
        IF FOUND THEN
            PERFORM autopilot.record_event(
                expired.task_id, 'TASK_FAILED_CLOSED', 'WAITING_EXTERNAL', 'FAILED_CLOSED',
                jsonb_build_object('reason_code', 'ROLE_CALLBACK_DEADLINE_EXCEEDED'),
                'SYSTEM', 'role-callback-reconciler',
                'role-callback-timeout:' || expired.dispatch_id::text
            );
            reconciled := reconciled + 1;
        END IF;
    END LOOP;
    RETURN reconciled;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.fail_role_dispatch_outbox(
    p_dispatch_id uuid, p_publisher_id text, p_claim_epoch bigint,
    p_error_code text, p_retry_seconds integer DEFAULT 60
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    outbox_row autopilot.role_dispatch_outbox;
    resulting_status text;
BEGIN
    IF p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
       OR p_retry_seconds NOT BETWEEN 5 AND 3600 THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_FAILURE_INVALID';
    END IF;
    SELECT * INTO outbox_row FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id = p_dispatch_id AND status = 'CLAIMED'
       AND claim_owner = p_publisher_id AND claim_epoch = p_claim_epoch
     FOR UPDATE;
    IF NOT FOUND THEN RETURN NULL; END IF;
    resulting_status := CASE WHEN outbox_row.attempts >= outbox_row.max_attempts
                             THEN 'FAILED_CLOSED' ELSE 'RETRY' END;
    UPDATE autopilot.role_dispatch_outbox
       SET status = resulting_status, claim_owner = NULL, claim_until = NULL,
           next_attempt_at = now() + make_interval(secs => p_retry_seconds),
           last_error_code = p_error_code, updated_at = now(),
           completed_at = CASE WHEN resulting_status = 'FAILED_CLOSED' THEN now() ELSE NULL END
     WHERE dispatch_id = p_dispatch_id;
    IF resulting_status = 'FAILED_CLOSED' THEN
        UPDATE autopilot.step_attempt
           SET status = 'FAILED_CLOSED', error_code = 'ROLE_DISPATCH_DELIVERY_EXHAUSTED',
               completed_at = now()
         WHERE step_attempt_id = outbox_row.step_attempt_id AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.task
           SET status = 'FAILED_CLOSED', terminal_reason_code = 'ROLE_DISPATCH_DELIVERY_EXHAUSTED',
               completed_at = now(), safe_summary_json = jsonb_build_object('result_code', p_error_code)
         WHERE task_id = outbox_row.task_id AND status = 'WAITING_EXTERNAL';
        PERFORM autopilot.record_event(
            outbox_row.task_id, 'TASK_FAILED_CLOSED', 'WAITING_EXTERNAL', 'FAILED_CLOSED',
            jsonb_build_object('reason_code', 'ROLE_DISPATCH_DELIVERY_EXHAUSTED'),
            'ORACLE_WORKER', p_publisher_id,
            'role-dispatch-failed:' || p_dispatch_id::text
        );
    END IF;
    RETURN resulting_status;
END;
$$;

CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_callback(
    p_delivery_id text, p_payload_fingerprint text, p_signature_verified boolean,
    p_repository text, p_mailbox_pr integer,
    p_actor_login text, p_actor_id bigint, p_author_association text,
    p_app_slug text, p_app_id bigint,
    p_body jsonb
)
RETURNS TABLE(accepted boolean, duplicate boolean, task_id uuid, successor_task_id uuid, resulting_state text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    callback_keys text[];
    existing_receipt autopilot.role_dispatch_callback_receipt;
    outbox_row autopilot.role_dispatch_outbox;
    task_row autopilot.task;
    body_sha256 text;
    created_successor_id uuid;
    new_state text;
BEGIN
    IF p_signature_verified IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_SIGNATURE_INVALID';
    END IF;
    IF p_repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
       OR p_mailbox_pr IS DISTINCT FROM 1150
       OR p_actor_login IS DISTINCT FROM 'olegmed1-art'
       OR p_actor_id IS DISTINCT FROM 315099490::bigint
       OR p_author_association IS DISTINCT FROM 'OWNER'
       OR p_app_slug IS DISTINCT FROM 'chatgpt-codex-connector'
       OR p_app_id IS DISTINCT FROM 1144995::bigint THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_IDENTITY_INVALID';
    END IF;
    IF p_delivery_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_body, 'null'::jsonb)) <> 'object'
       OR octet_length(p_body::text) > 2048 THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_INVALID';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO callback_keys
      FROM jsonb_object_keys(p_body) AS keys(key);
    IF callback_keys IS DISTINCT FROM ARRAY[
           'dispatch_epoch', 'dispatch_id', 'result_code', 'role', 'status',
           'summary', 'target_head_sha', 'target_pr', 'task_fingerprint'
       ]
       OR COALESCE(p_body->>'dispatch_id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR jsonb_typeof(p_body->'dispatch_epoch') <> 'number'
       OR COALESCE(p_body->>'dispatch_epoch', '') !~ '^[1-9][0-9]{0,9}$'
       OR (p_body->>'dispatch_epoch')::bigint > 1000000
       OR COALESCE(p_body->>'role', '') NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
       OR COALESCE(p_body->>'task_fingerprint', '') !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(p_body->'target_pr') <> 'number'
       OR COALESCE(p_body->>'target_pr', '') !~ '^[1-9][0-9]{0,6}$'
       OR (p_body->>'target_pr')::integer > 1000000
       OR COALESCE(p_body->>'status', '') NOT IN ('SUCCEEDED', 'BLOCKED')
       OR COALESCE(p_body->>'result_code', '') !~ '^[A-Z][A-Z0-9_]{0,63}$'
       OR COALESCE(p_body->>'target_head_sha', '') !~ '^[0-9a-f]{40}$'
       OR jsonb_typeof(p_body->'summary') <> 'string'
       OR length(p_body->>'summary') NOT BETWEEN 1 AND 160
       OR p_body->>'summary' ~ '[[:cntrl:]]'
       OR p_body->>'summary' ~* '(https?://|www\.|[[:alnum:]_.+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,})'
       OR p_body->>'summary' ~* '(password|secret|token|api[_ -]?key|credential|private[_ -]?key)'
       OR p_body->>'summary' ~* '([0-9a-f]{32,}|[a-z0-9+/]{40,}={0,2})' THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_BODY_INVALID';
    END IF;

    -- Serialize concurrent redeliveries before the receipt lookup.  Without
    -- this lock, two transactions could both miss the receipt and the loser
    -- would observe CALLBACK_ACCEPTED instead of the required duplicate result.
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_delivery_id, 0));
    SELECT * INTO existing_receipt
      FROM autopilot.role_dispatch_callback_receipt
     WHERE delivery_id = p_delivery_id;
    IF FOUND THEN
        IF existing_receipt.payload_fingerprint <> p_payload_fingerprint
           OR existing_receipt.callback_body <> p_body
           OR existing_receipt.actor_login <> p_actor_login
           OR existing_receipt.actor_id <> p_actor_id
           OR existing_receipt.author_association <> p_author_association
           OR existing_receipt.app_slug <> p_app_slug
           OR existing_receipt.app_id <> p_app_id THEN
            RAISE EXCEPTION 'AUTOPILOT_CALLBACK_DELIVERY_CONFLICT';
        END IF;
        RETURN QUERY SELECT false, true,
            (SELECT o.task_id FROM autopilot.role_dispatch_outbox o
              WHERE o.dispatch_id = existing_receipt.dispatch_id),
            NULL::uuid,
            (SELECT t.status FROM autopilot.role_dispatch_outbox o
              JOIN autopilot.task t ON t.task_id = o.task_id
             WHERE o.dispatch_id = existing_receipt.dispatch_id);
        RETURN;
    END IF;

    SELECT * INTO outbox_row FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id = (p_body->>'dispatch_id')::uuid FOR UPDATE;
    IF FOUND AND outbox_row.status = 'CALLBACK_ACCEPTED' THEN
        SELECT * INTO existing_receipt
          FROM autopilot.role_dispatch_callback_receipt
         WHERE dispatch_id = outbox_row.dispatch_id;
        IF NOT FOUND OR existing_receipt.callback_body <> p_body
           OR existing_receipt.payload_fingerprint <> p_payload_fingerprint THEN
            RAISE EXCEPTION 'AUTOPILOT_CALLBACK_LOGICAL_CONFLICT';
        END IF;
        RETURN QUERY SELECT false, true, outbox_row.task_id, NULL::uuid,
            (SELECT t.status FROM autopilot.task t WHERE t.task_id = outbox_row.task_id);
        RETURN;
    END IF;
    IF NOT FOUND OR outbox_row.status <> 'SENT' THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_DISPATCH_NOT_SENT';
    END IF;
    SELECT * INTO task_row FROM autopilot.task AS t
     WHERE t.task_id = outbox_row.task_id FOR UPDATE;
    IF NOT FOUND OR task_row.status <> 'WAITING_EXTERNAL'
       OR task_row.goal_type <> 'CHATGPT_ROLE_DISPATCH_V1'
       OR outbox_row.repository <> p_repository
       OR outbox_row.mailbox_pr <> p_mailbox_pr
       OR outbox_row.dispatch_epoch <> (p_body->>'dispatch_epoch')::bigint
       OR outbox_row.role <> p_body->>'role'
       OR outbox_row.target_pr <> (p_body->>'target_pr')::integer
       OR outbox_row.expected_head_sha <> p_body->>'target_head_sha'
       OR outbox_row.task_fingerprint <> p_body->>'task_fingerprint' THEN
        RAISE EXCEPTION 'AUTOPILOT_CALLBACK_BINDING_INVALID';
    END IF;

    body_sha256 := encode(public.digest(convert_to(p_body::text, 'UTF8'), 'sha256'), 'hex');
    INSERT INTO autopilot.role_dispatch_callback_receipt (
        delivery_id, payload_fingerprint, dispatch_id, actor_login, actor_id,
        author_association,
        app_slug, app_id, callback_body
    ) VALUES (
        p_delivery_id, p_payload_fingerprint, outbox_row.dispatch_id,
        p_actor_login, p_actor_id, p_author_association, p_app_slug, p_app_id, p_body
    );
    INSERT INTO autopilot.evidence (
        task_id, step_attempt_id, evidence_class, provider, external_ref,
        content_sha256, metadata_json
    ) VALUES (
        task_row.task_id, outbox_row.step_attempt_id, 'CHATGPT_ROLE_DISPATCH_RESULT',
        'GITHUB_WEBHOOK', p_delivery_id, body_sha256, p_body
    );

    IF p_body->>'status' = 'SUCCEEDED' THEN
        UPDATE autopilot.step_attempt SET status = 'COMPLETED', result_summary_json = p_body,
               completed_at = now()
         WHERE step_attempt_id = outbox_row.step_attempt_id AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.task AS t
           SET status = 'DONE', terminal_reason_code = 'CHATGPT_ROLE_RESULT_RETAINED',
               safe_summary_json = p_body, completed_at = now()
         WHERE t.task_id = task_row.task_id AND t.status = 'WAITING_EXTERNAL';
        new_state := 'DONE';

        IF task_row.goal_json->'successor_task_key' <> 'null'::jsonb THEN
            SELECT created.task_id INTO created_successor_id
              FROM autopilot.create_chatgpt_role_dispatch_task(
                  task_row.goal_json->>'successor_task_key',
                  jsonb_build_object(
                      'repository', task_row.goal_json->>'repository',
                      'mailbox_pr', (task_row.goal_json->>'mailbox_pr')::integer,
                      'role', task_row.goal_json->>'successor_role',
                      'target_pr', (task_row.goal_json->>'successor_target_pr')::integer,
                      'expected_head_sha', task_row.goal_json->>'successor_expected_head_sha',
                      'dispatch_epoch', (task_row.goal_json->>'dispatch_epoch')::bigint + 1,
                      'successor_task_key', NULL,
                      'successor_role', NULL,
                      'successor_target_pr', NULL,
                      'successor_expected_head_sha', NULL
                  ),
                  task_row.priority, 'CHATGPT_ROLE_DISPATCH_V1', 'AUTOPILOT_SUCCESSOR'
              ) AS created;
        END IF;
    ELSE
        UPDATE autopilot.step_attempt SET status = 'FAILED_CLOSED', result_summary_json = p_body,
               error_code = left(p_body->>'result_code', 64), completed_at = now()
         WHERE step_attempt_id = outbox_row.step_attempt_id AND status = 'WAITING_EXTERNAL';
        UPDATE autopilot.task AS t
           SET status = 'FAILED_CLOSED', terminal_reason_code = left(p_body->>'result_code', 64),
               safe_summary_json = p_body, completed_at = now()
         WHERE t.task_id = task_row.task_id AND t.status = 'WAITING_EXTERNAL';
        new_state := 'FAILED_CLOSED';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.evidence AS e
         WHERE e.task_id = task_row.task_id AND e.retained
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DONE_WITHOUT_EVIDENCE';
    END IF;
    UPDATE autopilot.role_dispatch_outbox
       SET status = 'CALLBACK_ACCEPTED', completed_at = now(), updated_at = now()
     WHERE dispatch_id = outbox_row.dispatch_id;
    PERFORM autopilot.record_event(
        task_row.task_id,
        CASE WHEN new_state = 'DONE' THEN 'TASK_DONE' ELSE 'TASK_FAILED_CLOSED' END,
        'WAITING_EXTERNAL', new_state,
        jsonb_build_object(
            'dispatch_id', outbox_row.dispatch_id,
            'delivery_id', p_delivery_id,
            'result_code', p_body->>'result_code',
            'content_sha256', body_sha256
        ),
        'EXTERNAL_EVENT', p_actor_login,
        'role-callback:' || outbox_row.dispatch_id::text
    );
    RETURN QUERY SELECT true, false, task_row.task_id,
        created_successor_id, new_state;
END;
$$;

REVOKE ALL ON FUNCTION autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.prepare_role_dispatch(uuid,text,bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.claim_role_dispatch_outbox(text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.fail_role_dispatch_outbox(uuid,text,bigint,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.reconcile_role_dispatch_callbacks() FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) FROM PUBLIC;

REVOKE ALL ON FUNCTION autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)
    FROM autopilot_runtime, autopilot_runtime_principal;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)
    FROM autopilot_runtime, autopilot_runtime_principal;
GRANT USAGE ON SCHEMA autopilot TO autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)
    TO autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.prepare_role_dispatch(uuid,text,bigint) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.claim_role_dispatch_outbox(text,integer) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.fail_role_dispatch_outbox(uuid,text,bigint,text,integer) TO autopilot_runtime;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_role_dispatch_callbacks() TO autopilot_runtime;

COMMENT ON TABLE autopilot.role_dispatch_outbox IS
'Durable zero-cost GitHub mailbox dispatch queue; callback completion remains a separate ungranted boundary.';
COMMENT ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) IS
'Dedicated-role callback RPC. Caller must verify the GitHub webhook signature and provide the exact canonical callback body.';

INSERT INTO public.schema_migration(migration_key)
VALUES ('0322_autopilot_chatgpt_role_dispatch')
ON CONFLICT DO NOTHING;

COMMIT;
