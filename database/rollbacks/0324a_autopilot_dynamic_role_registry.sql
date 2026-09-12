\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM autopilot.project_work_item WHERE role NOT IN ('RECOGNIZER','VIDEO','BOOKS','KNOWLEDGE'))
    OR EXISTS (SELECT 1 FROM autopilot.role_dispatch_outbox WHERE role NOT IN ('RECOGNIZER','VIDEO','BOOKS','KNOWLEDGE')) THEN
  RAISE EXCEPTION 'AUTOPILOT_DYNAMIC_ROLE_ROLLBACK_HAS_NONLEGACY_DATA';
 END IF;
END $$;

DROP FUNCTION IF EXISTS autopilot.get_dispatch_assignment(uuid);
DROP FUNCTION IF EXISTS autopilot.register_universal_work_item(text,text,text,text,integer,integer,jsonb,text,text,text);
DROP TRIGGER IF EXISTS project_work_item_enabled_role ON autopilot.project_work_item;
DROP FUNCTION IF EXISTS autopilot.enforce_enabled_role();
ALTER TABLE autopilot.project_work_item DROP CONSTRAINT IF EXISTS project_work_item_role_registry_fk;
ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT IF EXISTS role_dispatch_outbox_role_registry_fk;
ALTER TABLE autopilot.project_work_item DROP CONSTRAINT IF EXISTS project_work_item_task_kind_check;
ALTER TABLE autopilot.project_work_item DROP CONSTRAINT IF EXISTS project_work_item_objective_check;
ALTER TABLE autopilot.project_work_item DROP CONSTRAINT IF EXISTS project_work_item_task_spec_check;
ALTER TABLE autopilot.project_work_item DROP COLUMN IF EXISTS task_kind;
ALTER TABLE autopilot.project_work_item DROP COLUMN IF EXISTS objective;
ALTER TABLE autopilot.project_work_item DROP COLUMN IF EXISTS task_spec_json;
ALTER TABLE autopilot.project_work_item ADD CONSTRAINT project_work_item_role_check CHECK (role IN ('RECOGNIZER','VIDEO','BOOKS','KNOWLEDGE'));
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_outbox_role_check CHECK (role IN ('RECOGNIZER','VIDEO','BOOKS','KNOWLEDGE'));

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

CREATE OR REPLACE FUNCTION autopilot.register_project_work_item(
    p_work_key text,
    p_role text,
    p_target_pr integer,
    p_priority integer DEFAULT 20,
    p_depends_on_work_key text DEFAULT NULL,
    p_created_by text DEFAULT 'CHATGPT_DIRECTOR',
    p_source text DEFAULT 'SCHOOL_WORKING_BOARD'
)
RETURNS TABLE(work_item_id uuid, state text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $$
DECLARE
    existing autopilot.project_work_item;
    inserted autopilot.project_work_item;
    dependency_id uuid;
BEGIN
    IF p_work_key IS NULL
       OR p_work_key !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$'
       OR p_role IS NULL
       OR p_role NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')
       OR p_target_pr IS NULL
       OR p_target_pr NOT BETWEEN 1 AND 1000000
       OR p_priority IS NULL
       OR p_priority NOT IN (0, 10, 20, 30)
       OR length(COALESCE(p_created_by, '')) NOT BETWEEN 1 AND 256
       OR length(COALESCE(p_source, '')) NOT BETWEEN 1 AND 256 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_ITEM_INVALID';
    END IF;
    IF p_depends_on_work_key IS NOT NULL THEN
        IF p_depends_on_work_key = p_work_key THEN
            RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_SELF_DEPENDENCY';
        END IF;
        SELECT item.work_item_id INTO dependency_id
          FROM autopilot.project_work_item AS item
         WHERE item.work_key = p_depends_on_work_key;
        IF dependency_id IS NULL THEN
            RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_DEPENDENCY_UNKNOWN';
        END IF;
    END IF;

    SELECT * INTO existing
      FROM autopilot.project_work_item AS item
     WHERE item.work_key = p_work_key;
    IF FOUND THEN
        IF existing.role <> p_role
           OR existing.target_pr <> p_target_pr
           OR existing.priority <> p_priority::smallint
           OR existing.depends_on_work_item_id IS DISTINCT FROM dependency_id
           OR existing.created_by <> p_created_by
           OR existing.source <> p_source THEN
            RAISE EXCEPTION 'AUTOPILOT_PROJECT_WORK_IDEMPOTENCY_CONFLICT';
        END IF;
        RETURN QUERY SELECT existing.work_item_id, existing.state, false;
        RETURN;
    END IF;

    INSERT INTO autopilot.project_work_item (
        work_key, role, target_pr, priority, depends_on_work_item_id,
        created_by, source
    ) VALUES (
        p_work_key, p_role, p_target_pr, p_priority::smallint, dependency_id,
        p_created_by, p_source
    )
    RETURNING * INTO inserted;

    UPDATE autopilot.project_planner_state
       SET decision_count = decision_count + 1,
           last_decision_code = 'WORK_REGISTERED',
           last_work_item_id = inserted.work_item_id,
           last_decision_at = now()
     WHERE singleton;
    PERFORM pg_notify('autopilot_ready', 'project-work-registered');
    RETURN QUERY SELECT inserted.work_item_id, inserted.state, true;
END;
$$;
REVOKE ALL ON FUNCTION autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text) FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text) FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.register_project_work_item(text,text,integer,integer,text,text,text) FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) TO autopilot_callback;
DROP FUNCTION IF EXISTS autopilot.role_is_enabled(text);
DROP TABLE IF EXISTS autopilot.role_registry;
DELETE FROM public.schema_migration WHERE migration_key='0324a_autopilot_dynamic_role_registry';
COMMIT;


