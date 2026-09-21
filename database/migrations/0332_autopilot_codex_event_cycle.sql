\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0331_autopilot_six_worker_capacity'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_EVENT_CYCLE_REQUIRES_0331';
    END IF;
END $$;

-- Retain the exact pre-cutover definitions so the guarded rollback restores
-- the previous 0331 behavior without relying on hand-copied function text.
CREATE TABLE autopilot.migration_0332_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);
INSERT INTO autopilot.migration_0332_function_backup(
    function_key,function_definition
)
SELECT fixture.function_key,pg_get_functiondef(fixture.function_oid)
  FROM (VALUES
    ('get_dispatch_assignment',
        'autopilot.get_dispatch_assignment(uuid)'::regprocedure::oid),
    ('mark_role_dispatch_published',
        'autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text)'::regprocedure::oid),
    ('claim_project_work_probe',
        'autopilot.claim_project_work_probe(text,integer)'::regprocedure::oid),
    ('project_work_transport_retryable',
        'autopilot.project_work_transport_retryable(text)'::regprocedure::oid),
    ('reconcile_role_dispatch_callbacks',
        'autopilot.reconcile_role_dispatch_callbacks()'::regprocedure::oid)
  ) AS fixture(function_key,function_oid);
REVOKE ALL ON TABLE autopilot.migration_0332_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- Contract v3 is GitHub -> authenticated owner @codex -> Codex Cloud.  A
-- dispatch PR remains discovery only.  Delivery is acknowledged only by the
-- pinned Codex GitHub App reaction, and completion only by that App's result.
ALTER TABLE autopilot.role_dispatch_outbox
    DROP CONSTRAINT role_dispatch_delivery_contract_version_check;
ALTER TABLE autopilot.role_dispatch_outbox
    ALTER COLUMN delivery_contract_version SET DEFAULT 3,
    ADD CONSTRAINT role_dispatch_delivery_contract_version_check
        CHECK (delivery_contract_version IN (1, 2, 3)),
    ADD COLUMN codex_command_pr integer CHECK (
        codex_command_pr IS NULL OR codex_command_pr BETWEEN 1 AND 1000000
    ),
    ADD COLUMN codex_command_comment_id bigint CHECK (
        codex_command_comment_id IS NULL OR codex_command_comment_id > 0
    ),
    ADD COLUMN codex_ack_reaction_id bigint CHECK (
        codex_ack_reaction_id IS NULL OR codex_ack_reaction_id > 0
    ),
    ADD COLUMN codex_ack_at timestamptz;

-- Move only non-delivered work to the new transport.  A v2 SENT row already
-- has a legacy UI proof and must finish through its original callback.  A
-- PUBLISHED row gets a fresh bounded window so an owner @codex command can
-- safely rehabilitate an in-flight dispatch during the rolling cutover.
UPDATE autopilot.role_dispatch_outbox
   SET delivery_contract_version=3,
       status=CASE WHEN status='DELIVERY_FAILED' THEN 'RETRY' ELSE status END,
       target_chat_id=NULL,target_chat_name=NULL,executor_id=NULL,
       delivery_deadline_at=CASE WHEN status='PUBLISHED'
           THEN now()+interval '10 minutes' ELSE delivery_deadline_at END,
       next_attempt_at=CASE WHEN status='DELIVERY_FAILED' THEN now()
           ELSE next_attempt_at END,
       updated_at=now()
 WHERE delivery_contract_version=2
   AND status IN ('PENDING','CLAIMED','RETRY','PUBLISHED','DELIVERY_FAILED');

CREATE TABLE autopilot.role_dispatch_codex_delivery_proof (
    delivery_id text PRIMARY KEY
        CHECK (delivery_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    payload_fingerprint text NOT NULL
        CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    dispatch_id uuid NOT NULL UNIQUE
        REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
    command_pr integer NOT NULL CHECK (command_pr BETWEEN 1 AND 1000000),
    command_comment_id bigint NOT NULL UNIQUE CHECK (command_comment_id > 0),
    command_actor_login text NOT NULL CHECK (command_actor_login = 'olegmed1-art'),
    command_actor_id bigint NOT NULL CHECK (command_actor_id = 315099490),
    command_author_association text NOT NULL
        CHECK (command_author_association = 'OWNER'),
    command_app_slug text NOT NULL
        CHECK (command_app_slug = 'chatgpt-codex-connector'),
    command_app_id bigint NOT NULL CHECK (command_app_id = 1144995),
    ack_reaction_id bigint NOT NULL UNIQUE CHECK (ack_reaction_id > 0),
    ack_actor_login text NOT NULL
        CHECK (ack_actor_login = 'chatgpt-codex-connector[bot]'),
    ack_actor_id bigint NOT NULL CHECK (ack_actor_id = 199175422),
    proof_body jsonb NOT NULL CHECK (jsonb_typeof(proof_body) = 'object'),
    accepted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT role_dispatch_codex_delivery_proof_body_bound
        CHECK (octet_length(proof_body::text) <= 4096)
);

CREATE TABLE autopilot.role_dispatch_codex_terminal_receipt (
    delivery_id text PRIMARY KEY
        CHECK (delivery_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    payload_fingerprint text NOT NULL
        CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    dispatch_id uuid NOT NULL UNIQUE
        REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
    event_pr integer NOT NULL CHECK (event_pr BETWEEN 1 AND 1000000),
    actor_login text NOT NULL
        CHECK (actor_login = 'chatgpt-codex-connector[bot]'),
    actor_id bigint NOT NULL CHECK (actor_id = 199175422),
    author_association text NOT NULL CHECK (author_association = 'NONE'),
    app_slug text NOT NULL CHECK (app_slug = 'chatgpt-codex-connector'),
    app_id bigint NOT NULL CHECK (app_id = 1144995),
    callback_body jsonb NOT NULL CHECK (jsonb_typeof(callback_body) = 'object'),
    accepted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT role_dispatch_codex_terminal_body_bound
        CHECK (octet_length(callback_body::text) <= 2048)
);

REVOKE ALL ON TABLE autopilot.role_dispatch_codex_delivery_proof
FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;
REVOKE ALL ON TABLE autopilot.role_dispatch_codex_terminal_receipt
FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

-- Assignment is role-bound, not chat-bound.  The legacy chat columns remain
-- visible for v1/v2 compatibility and dashboards, but a missing chat mapping
-- can no longer suppress a valid v3 role assignment.
DROP FUNCTION autopilot.get_dispatch_assignment(uuid);
CREATE FUNCTION autopilot.get_dispatch_assignment(p_dispatch_id uuid)
RETURNS TABLE(
    dispatch_id uuid, task_id uuid, role text, execution_scope text,
    can_repair boolean, task_kind text, objective text, task_spec_json jsonb,
    target_chat_id text, target_chat_name text, target_chat_url text,
    executor_id text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
 SELECT outbox.dispatch_id,outbox.task_id,outbox.role,roles.execution_scope,
        roles.can_repair,COALESCE(work.task_kind,'REPOSITORY_AUDIT'),
        COALESCE(work.objective,'Audit the exact target head and report a bounded result.'),
        COALESCE(work.task_spec_json,'{}'::jsonb),chat.chat_id,chat.chat_name,
        chat.chat_url,chat.executor_id
 FROM autopilot.role_dispatch_outbox AS outbox
 JOIN autopilot.role_registry AS roles
   ON roles.role_id=outbox.role AND roles.enabled
 LEFT JOIN autopilot.role_chat_registry AS chat
   ON chat.role_id=outbox.role AND chat.enabled
 LEFT JOIN autopilot.project_work_task AS map ON map.task_id=outbox.task_id
 LEFT JOIN autopilot.project_work_item AS work
   ON work.work_item_id=map.work_item_id
 WHERE outbox.dispatch_id=p_dispatch_id
$$;
REVOKE ALL ON FUNCTION autopilot.get_dispatch_assignment(uuid)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.mark_role_dispatch_published(
    p_dispatch_id uuid, p_publisher_id text, p_claim_epoch bigint,
    p_github_pull_request bigint, p_dispatch_body_sha256 text
)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    affected integer;
    contract_version smallint;
    target autopilot.role_chat_registry;
BEGIN
    IF p_github_pull_request <= 0
       OR p_dispatch_body_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_PUBLISHED_INVALID';
    END IF;

    SELECT outbox.delivery_contract_version INTO contract_version
      FROM autopilot.role_dispatch_outbox AS outbox
     WHERE outbox.dispatch_id=p_dispatch_id;
    IF contract_version IS NULL THEN
        RETURN false;
    END IF;
    IF contract_version = 2 THEN
        SELECT chat.* INTO target
          FROM autopilot.role_dispatch_outbox AS outbox
          JOIN autopilot.role_chat_registry AS chat
            ON chat.role_id=outbox.role AND chat.enabled
         WHERE outbox.dispatch_id=p_dispatch_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_UNREGISTERED';
        END IF;
    END IF;

    UPDATE autopilot.role_dispatch_outbox
       SET status='PUBLISHED', claim_owner=NULL, claim_until=NULL,
           github_dispatch_comment_id=p_github_pull_request,
           dispatch_body_sha256=p_dispatch_body_sha256,
           target_chat_id=CASE WHEN contract_version=2 THEN target.chat_id ELSE NULL END,
           target_chat_name=CASE WHEN contract_version=2 THEN target.chat_name ELSE NULL END,
           executor_id=CASE WHEN contract_version=2 THEN target.executor_id ELSE NULL END,
           published_at=now(),
           delivery_deadline_at=now()+CASE WHEN contract_version=3
               THEN interval '10 minutes' ELSE interval '5 minutes' END,
           sent_at=NULL, callback_deadline_at=NULL, delivered_at=NULL,
           codex_command_pr=NULL, codex_command_comment_id=NULL,
           codex_ack_reaction_id=NULL, codex_ack_at=NULL,
           updated_at=now(), last_error_code=NULL
     WHERE dispatch_id=p_dispatch_id AND status='CLAIMED'
       AND claim_owner=p_publisher_id AND claim_epoch=p_claim_epoch
       AND claim_until>now() AND delivery_contract_version IN (2,3);
    GET DIAGNOSTICS affected=ROW_COUNT;
    RETURN affected=1;
END $$;

CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_codex_ack(
    p_delivery_id text,
    p_payload_fingerprint text,
    p_signature_verified boolean,
    p_repository text,
    p_command_pr integer,
    p_command_actor_login text,
    p_command_actor_id bigint,
    p_command_author_association text,
    p_command_app_slug text,
    p_command_app_id bigint,
    p_ack_actor_login text,
    p_ack_actor_id bigint,
    p_body jsonb
)
RETURNS TABLE(accepted boolean, duplicate boolean, task_id uuid, resulting_state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    existing autopilot.role_dispatch_codex_delivery_proof;
    outbox autopilot.role_dispatch_outbox;
    task_row autopilot.task;
    command_created_at timestamptz;
    ack_created_at timestamptz;
BEGIN
    IF p_signature_verified IS DISTINCT FROM true
       OR p_repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
       OR p_command_actor_login IS DISTINCT FROM 'olegmed1-art'
       OR p_command_actor_id IS DISTINCT FROM 315099490::bigint
       OR p_command_author_association IS DISTINCT FROM 'OWNER'
       OR p_command_app_slug IS DISTINCT FROM 'chatgpt-codex-connector'
       OR p_command_app_id IS DISTINCT FROM 1144995::bigint
       OR p_ack_actor_login IS DISTINCT FROM 'chatgpt-codex-connector[bot]'
       OR p_ack_actor_id IS DISTINCT FROM 199175422::bigint THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_IDENTITY_INVALID';
    END IF;
    IF p_delivery_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_body,'null'::jsonb)) <> 'object'
       OR (SELECT array_agg(key ORDER BY key)
             FROM jsonb_object_keys(p_body) AS keys(key)) IS DISTINCT FROM ARRAY[
          'ack_created_at','ack_reaction_id','command_comment_id',
          'command_created_at','command_pr','dispatch_epoch','dispatch_id',
          'dispatch_pr','expected_head_sha','mode','role','target_pr',
          'task_fingerprint']
       OR COALESCE(p_body->>'dispatch_id','') !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_body->>'task_fingerprint','') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_body->>'expected_head_sha','') !~ '^[0-9a-f]{40}$'
       OR COALESCE(p_body->>'role','') !~ '^[A-Z][A-Z0-9_]{0,63}$'
       OR COALESCE(p_body->>'mode','') NOT IN ('READ_ONLY','REPAIR','VERIFY')
       OR COALESCE(p_body->>'dispatch_epoch','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_body->>'target_pr','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_body->>'dispatch_pr','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_body->>'command_pr','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_body->>'command_comment_id','') !~ '^[1-9][0-9]{0,18}$'
       OR COALESCE(p_body->>'ack_reaction_id','') !~ '^[1-9][0-9]{0,18}$'
       OR COALESCE(p_body->>'command_created_at','') !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
       OR COALESCE(p_body->>'ack_created_at','') !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_BODY_INVALID';
    END IF;

    command_created_at := (p_body->>'command_created_at')::timestamptz;
    ack_created_at := (p_body->>'ack_created_at')::timestamptz;
    IF command_created_at > ack_created_at
       OR ack_created_at > now()+interval '5 minutes' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_TIME_INVALID';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_delivery_id,0));
    SELECT * INTO existing
      FROM autopilot.role_dispatch_codex_delivery_proof
     WHERE delivery_id=p_delivery_id;
    IF FOUND THEN
        IF existing.payload_fingerprint<>p_payload_fingerprint
           OR existing.proof_body<>p_body
           OR existing.command_pr<>p_command_pr
           OR existing.command_actor_login<>p_command_actor_login
           OR existing.command_actor_id<>p_command_actor_id
           OR existing.command_author_association<>p_command_author_association
           OR existing.command_app_slug<>p_command_app_slug
           OR existing.command_app_id<>p_command_app_id
           OR existing.ack_actor_login<>p_ack_actor_login
           OR existing.ack_actor_id<>p_ack_actor_id THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_CONFLICT';
        END IF;
        RETURN QUERY SELECT false,true,
            (SELECT item.task_id FROM autopilot.role_dispatch_outbox AS item
              WHERE item.dispatch_id=existing.dispatch_id),'SENT'::text;
        RETURN;
    END IF;

    SELECT * INTO outbox FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id=(p_body->>'dispatch_id')::uuid FOR UPDATE;
    SELECT * INTO task_row FROM autopilot.task AS task
     WHERE task.task_id=outbox.task_id FOR UPDATE;
    IF outbox.dispatch_id IS NULL OR task_row.task_id IS NULL
       OR outbox.status<>'PUBLISHED'
       OR outbox.delivery_contract_version NOT IN (2,3)
       OR task_row.status<>'WAITING_EXTERNAL'
       OR outbox.repository<>p_repository
       OR outbox.dispatch_epoch<>(p_body->>'dispatch_epoch')::bigint
       OR outbox.role<>p_body->>'role'
       OR outbox.target_pr<>(p_body->>'target_pr')::integer
       OR outbox.expected_head_sha<>p_body->>'expected_head_sha'
       OR outbox.task_fingerprint<>p_body->>'task_fingerprint'
       OR outbox.mode<>p_body->>'mode'
       OR outbox.github_dispatch_comment_id<>(p_body->>'dispatch_pr')::bigint
       OR p_command_pr<>(p_body->>'command_pr')::integer
       OR p_command_pr NOT IN (
           outbox.target_pr,outbox.github_dispatch_comment_id::integer
       )
       OR (p_body->>'command_comment_id')::bigint<=0
       OR (p_body->>'ack_reaction_id')::bigint<=0
       OR command_created_at < outbox.published_at-interval '5 minutes'
       OR command_created_at > outbox.delivery_deadline_at
       OR ack_created_at > outbox.delivery_deadline_at+interval '2 minutes' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_BINDING_INVALID';
    END IF;

    INSERT INTO autopilot.role_dispatch_codex_delivery_proof(
        delivery_id,payload_fingerprint,dispatch_id,command_pr,
        command_comment_id,command_actor_login,command_actor_id,
        command_author_association,command_app_slug,command_app_id,
        ack_reaction_id,ack_actor_login,ack_actor_id,proof_body
    ) VALUES (
        p_delivery_id,p_payload_fingerprint,outbox.dispatch_id,p_command_pr,
        (p_body->>'command_comment_id')::bigint,p_command_actor_login,
        p_command_actor_id,p_command_author_association,p_command_app_slug,
        p_command_app_id,(p_body->>'ack_reaction_id')::bigint,
        p_ack_actor_login,p_ack_actor_id,p_body
    );
    UPDATE autopilot.role_dispatch_outbox
       SET delivery_contract_version=3,status='SENT',sent_at=ack_created_at,
           delivered_at=ack_created_at,callback_deadline_at=ack_created_at+interval '2 hours',
           codex_command_pr=p_command_pr,
           codex_command_comment_id=(p_body->>'command_comment_id')::bigint,
           codex_ack_reaction_id=(p_body->>'ack_reaction_id')::bigint,
           codex_ack_at=ack_created_at,updated_at=now(),last_error_code=NULL
     WHERE dispatch_id=outbox.dispatch_id;
    RETURN QUERY SELECT true,false,outbox.task_id,'SENT'::text;
END $$;

CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    p_delivery_id text,
    p_payload_fingerprint text,
    p_signature_verified boolean,
    p_repository text,
    p_event_pr integer,
    p_actor_login text,
    p_actor_id bigint,
    p_author_association text,
    p_app_slug text,
    p_app_id bigint,
    p_body jsonb
)
RETURNS TABLE(
    accepted boolean, duplicate boolean, task_id uuid,
    successor_task_id uuid, resulting_state text
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    existing autopilot.role_dispatch_codex_terminal_receipt;
    outbox autopilot.role_dispatch_outbox;
    proof autopilot.role_dispatch_codex_delivery_proof;
    task_row autopilot.task;
    body_sha256 text;
    created_successor_id uuid;
    new_state text;
BEGIN
    IF p_signature_verified IS DISTINCT FROM true
       OR p_repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
       OR p_actor_login IS DISTINCT FROM 'chatgpt-codex-connector[bot]'
       OR p_actor_id IS DISTINCT FROM 199175422::bigint
       OR p_author_association IS DISTINCT FROM 'NONE'
       OR p_app_slug IS DISTINCT FROM 'chatgpt-codex-connector'
       OR p_app_id IS DISTINCT FROM 1144995::bigint THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IDENTITY_INVALID';
    END IF;
    IF p_delivery_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(COALESCE(p_body,'null'::jsonb))<>'object'
       OR octet_length(p_body::text)>2048
       OR (SELECT array_agg(key ORDER BY key)
             FROM jsonb_object_keys(p_body) AS keys(key)) IS DISTINCT FROM ARRAY[
          'dispatch_epoch','dispatch_id','result_code','role','status','summary',
          'target_head_sha','target_pr','task_fingerprint']
       OR COALESCE(p_body->>'dispatch_id','') !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_body->>'dispatch_epoch','') !~ '^[1-9][0-9]{0,6}$'
       OR NOT autopilot.role_is_enabled(COALESCE(p_body->>'role',''))
       OR COALESCE(p_body->>'task_fingerprint','') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_body->>'target_pr','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_body->>'status','') NOT IN ('SUCCEEDED','BLOCKED')
       OR COALESCE(p_body->>'result_code','') !~ '^[A-Z][A-Z0-9_]{0,63}$'
       OR COALESCE(p_body->>'target_head_sha','') !~ '^[0-9a-f]{40}$'
       OR jsonb_typeof(p_body->'summary')<>'string'
       OR length(p_body->>'summary') NOT BETWEEN 1 AND 160
       OR p_body->>'summary' ~ '[[:cntrl:]]'
       OR p_body->>'summary' ~* '(https?://|www\.|[[:alnum:]_.+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,})'
       OR p_body->>'summary' ~* '(password|secret|token|api[_ -]?key|credential|private[_ -]?key)'
       OR p_body->>'summary' ~* '([0-9a-f]{32,}|[a-z0-9+/]{40,}={0,2})' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_BODY_INVALID';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_delivery_id,0));
    SELECT * INTO existing
      FROM autopilot.role_dispatch_codex_terminal_receipt
     WHERE delivery_id=p_delivery_id;
    IF FOUND THEN
        IF existing.payload_fingerprint<>p_payload_fingerprint
           OR existing.callback_body<>p_body
           OR existing.event_pr<>p_event_pr
           OR existing.actor_login<>p_actor_login
           OR existing.actor_id<>p_actor_id
           OR existing.author_association<>p_author_association
           OR existing.app_slug<>p_app_slug OR existing.app_id<>p_app_id THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_CONFLICT';
        END IF;
        RETURN QUERY SELECT false,true,
            (SELECT item.task_id FROM autopilot.role_dispatch_outbox AS item
              WHERE item.dispatch_id=existing.dispatch_id),NULL::uuid,
            (SELECT task.status FROM autopilot.role_dispatch_outbox AS item
              JOIN autopilot.task AS task ON task.task_id=item.task_id
             WHERE item.dispatch_id=existing.dispatch_id);
        RETURN;
    END IF;

    SELECT * INTO outbox FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id=(p_body->>'dispatch_id')::uuid FOR UPDATE;
    IF FOUND AND outbox.status='CALLBACK_ACCEPTED' THEN
        SELECT * INTO existing
          FROM autopilot.role_dispatch_codex_terminal_receipt
         WHERE dispatch_id=outbox.dispatch_id;
        IF NOT FOUND OR existing.callback_body<>p_body
           OR existing.payload_fingerprint<>p_payload_fingerprint THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_LOGICAL_CONFLICT';
        END IF;
        RETURN QUERY SELECT false,true,outbox.task_id,NULL::uuid,
            (SELECT task.status FROM autopilot.task AS task
              WHERE task.task_id=outbox.task_id);
        RETURN;
    END IF;

    SELECT * INTO proof
      FROM autopilot.role_dispatch_codex_delivery_proof
     WHERE dispatch_id=outbox.dispatch_id;
    SELECT * INTO task_row FROM autopilot.task AS task
     WHERE task.task_id=outbox.task_id FOR UPDATE;
    IF outbox.dispatch_id IS NULL OR proof.dispatch_id IS NULL
       OR task_row.task_id IS NULL OR outbox.status<>'SENT'
       OR outbox.delivery_contract_version<>3
       OR task_row.status<>'WAITING_EXTERNAL'
       OR task_row.goal_type NOT IN (
           'CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1'
       )
       OR proof.command_pr<>p_event_pr
       OR outbox.repository<>p_repository
       OR outbox.dispatch_epoch<>(p_body->>'dispatch_epoch')::bigint
       OR outbox.role<>p_body->>'role'
       OR outbox.target_pr<>(p_body->>'target_pr')::integer
       OR (outbox.mode IN ('READ_ONLY','VERIFY')
           AND outbox.expected_head_sha<>p_body->>'target_head_sha')
       OR outbox.task_fingerprint<>p_body->>'task_fingerprint' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_BINDING_INVALID';
    END IF;

    body_sha256 := encode(
        public.digest(convert_to(p_body::text,'UTF8'),'sha256'),'hex'
    );
    INSERT INTO autopilot.role_dispatch_codex_terminal_receipt(
        delivery_id,payload_fingerprint,dispatch_id,event_pr,actor_login,
        actor_id,author_association,app_slug,app_id,callback_body
    ) VALUES (
        p_delivery_id,p_payload_fingerprint,outbox.dispatch_id,p_event_pr,
        p_actor_login,p_actor_id,p_author_association,p_app_slug,p_app_id,p_body
    );
    INSERT INTO autopilot.evidence(
        task_id,step_attempt_id,evidence_class,provider,external_ref,
        content_sha256,metadata_json
    ) VALUES (
        task_row.task_id,outbox.step_attempt_id,'CHATGPT_ROLE_DISPATCH_RESULT',
        'GITHUB_WEBHOOK',p_delivery_id,body_sha256,p_body
    );

    IF p_body->>'status'='SUCCEEDED' THEN
        UPDATE autopilot.step_attempt
           SET status='COMPLETED',result_summary_json=p_body,completed_at=now()
         WHERE step_attempt_id=outbox.step_attempt_id
           AND status='WAITING_EXTERNAL';
        UPDATE autopilot.task AS target_task
           SET status='DONE',terminal_reason_code='CODEX_CLOUD_RESULT_RETAINED',
               safe_summary_json=p_body,completed_at=now()
         WHERE target_task.task_id=task_row.task_id
           AND target_task.status='WAITING_EXTERNAL';
        new_state := 'DONE';

        IF task_row.goal_json->'successor_task_key'<>'null'::jsonb THEN
            SELECT created.task_id INTO created_successor_id
              FROM autopilot.create_chatgpt_role_dispatch_task(
                  task_row.goal_json->>'successor_task_key',
                  jsonb_build_object(
                      'repository',task_row.goal_json->>'repository',
                      'mailbox_pr',(task_row.goal_json->>'mailbox_pr')::integer,
                      'role',task_row.goal_json->>'successor_role',
                      'target_pr',(task_row.goal_json->>'successor_target_pr')::integer,
                      'expected_head_sha',task_row.goal_json->>'successor_expected_head_sha',
                      'dispatch_epoch',(task_row.goal_json->>'dispatch_epoch')::bigint+1,
                      'successor_task_key',NULL,'successor_role',NULL,
                      'successor_target_pr',NULL,'successor_expected_head_sha',NULL
                  ),
                  task_row.priority,'CODEX_CLOUD_DISPATCH_V1','AUTOPILOT_SUCCESSOR'
              ) AS created;
        END IF;
    ELSE
        UPDATE autopilot.step_attempt
           SET status='FAILED_CLOSED',result_summary_json=p_body,
               error_code=left(p_body->>'result_code',64),completed_at=now()
         WHERE step_attempt_id=outbox.step_attempt_id
           AND status='WAITING_EXTERNAL';
        UPDATE autopilot.task AS target_task
           SET status='FAILED_CLOSED',
               terminal_reason_code=left(p_body->>'result_code',64),
               safe_summary_json=p_body,completed_at=now()
         WHERE target_task.task_id=task_row.task_id
           AND target_task.status='WAITING_EXTERNAL';
        new_state := 'FAILED_CLOSED';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM autopilot.evidence AS retained
         WHERE retained.task_id=task_row.task_id AND retained.retained
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DONE_WITHOUT_EVIDENCE';
    END IF;
    UPDATE autopilot.role_dispatch_outbox
       SET status='CALLBACK_ACCEPTED',completed_at=now(),updated_at=now()
     WHERE dispatch_id=outbox.dispatch_id;
    PERFORM autopilot.record_event(
        task_row.task_id,
        CASE WHEN new_state='DONE' THEN 'TASK_DONE' ELSE 'TASK_FAILED_CLOSED' END,
        'WAITING_EXTERNAL',new_state,
        jsonb_build_object(
            'dispatch_id',outbox.dispatch_id,'delivery_id',p_delivery_id,
            'result_code',p_body->>'result_code','content_sha256',body_sha256
        ),
        'EXTERNAL_EVENT',p_actor_login,
        'codex-role-callback:'||outbox.dispatch_id::text
    );
    PERFORM pg_notify('autopilot_ready','codex-terminal-next-task');
    RETURN QUERY SELECT true,false,task_row.task_id,created_successor_id,new_state;
END $$;

-- Missing ChatGPT registry rows are no longer transport failures.  Keep the
-- serializable six-slot admission fence from 0331 while selecting every
-- enabled role directly from role_registry.
CREATE OR REPLACE FUNCTION autopilot.claim_project_work_probe(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 60
)
RETURNS TABLE(
    work_item_id uuid, work_key text, repository text, role text,
    target_pr integer, prior_state text, prior_head_sha text,
    lease_epoch bigint
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    selected autopilot.project_work_item;
    capacity record;
BEGIN
    IF length(COALESCE(p_worker_id,'')) NOT BETWEEN 1 AND 256
       OR p_lease_seconds NOT BETWEEN 30 AND 300 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PROBE_LEASE_INVALID';
    END IF;
    IF NOT (SELECT enabled FROM autopilot.project_planner_state WHERE singleton) THEN
        RETURN;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_WORKER_ISOLATION_UNSUPPORTED';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('autopilot.role-worker-capacity-v1',0)
    );
    SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();
    IF capacity.active_workers+capacity.probe_reservations
           >=capacity.max_active_workers THEN
        UPDATE autopilot.project_planner_state
           SET decision_count=decision_count+1,
               last_decision_code='WAITING_FOR_WORKER_CAPACITY',
               last_work_item_id=NULL,last_decision_at=now()
         WHERE singleton;
        RETURN;
    END IF;

    SELECT item.* INTO selected
      FROM autopilot.project_work_item AS item
      JOIN autopilot.role_registry AS role
        ON role.role_id=item.role AND role.enabled
     WHERE item.state IN ('READY','BLOCKED')
       AND item.not_before<=now()
       AND (item.probe_lease_until IS NULL OR item.probe_lease_until<now())
       AND (item.priority=0 OR
            capacity.active_normal_workers+capacity.normal_probe_reservations
                <capacity.max_normal_workers)
     ORDER BY CASE item.state WHEN 'READY' THEN 0 ELSE 1 END,
              item.priority,item.not_before,item.created_at
     FOR UPDATE OF item SKIP LOCKED
     LIMIT 1;

    IF NOT FOUND THEN
        UPDATE autopilot.project_planner_state
           SET decision_count=decision_count+1,
               last_decision_code=CASE
                   WHEN capacity.active_normal_workers
                          +capacity.normal_probe_reservations
                        >=capacity.max_normal_workers
                    AND EXISTS (
                        SELECT 1 FROM autopilot.project_work_item AS item
                        JOIN autopilot.role_registry AS role
                          ON role.role_id=item.role AND role.enabled
                        WHERE item.state IN ('READY','BLOCKED')
                          AND item.priority<>0 AND item.not_before<=now()
                          AND (item.probe_lease_until IS NULL
                               OR item.probe_lease_until<now())
                    ) THEN 'WAITING_FOR_P0_RESERVED_WORKER'
                   WHEN NOT EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                   ) THEN 'IDLE_NO_REGISTERED_WORK'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state='ACTIVE'
                   ) THEN 'WAITING_FOR_ACTIVE_WORK_ITEM'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state='WAITING_DEPENDENCY'
                   ) THEN 'WAITING_FOR_DEPENDENCY'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state IN ('READY','BLOCKED') AND not_before>now()
                   ) THEN 'WAITING_FOR_RETRY_WINDOW'
                   WHEN EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state IN ('READY','BLOCKED')
                          AND probe_lease_until>=now()
                   ) THEN 'WAITING_FOR_PROBE_LEASE'
                   WHEN NOT EXISTS (
                       SELECT 1 FROM autopilot.project_work_item
                        WHERE state NOT IN ('DONE','PAUSED')
                   ) THEN 'PROJECT_DONE'
                   ELSE 'IDLE_NO_ELIGIBLE_TASK'
               END,
               last_work_item_id=NULL,last_decision_at=now()
         WHERE singleton;
        RETURN;
    END IF;

    UPDATE autopilot.project_work_item AS item
       SET probe_lease_owner=p_worker_id,
           probe_lease_epoch=item.probe_lease_epoch+1,
           probe_lease_until=now()+make_interval(secs=>p_lease_seconds),
           updated_at=now()
     WHERE item.work_item_id=selected.work_item_id
     RETURNING * INTO selected;
    UPDATE autopilot.project_planner_state
       SET decision_count=decision_count+1,last_decision_code='PROBE_CLAIMED',
           last_work_item_id=selected.work_item_id,last_decision_at=now()
     WHERE singleton;
    RETURN QUERY SELECT selected.work_item_id,selected.work_key,
        selected.repository,selected.role,selected.target_pr,selected.state,
        selected.last_observed_head_sha,selected.probe_lease_epoch;
END $$;

CREATE OR REPLACE FUNCTION autopilot.project_work_transport_retryable(
    p_result_code text
)
RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT COALESCE(p_result_code IN (
        'STALE_RETRY_BUDGET_EXHAUSTED','ROLE_DISPATCH_CLAIM_EXPIRED',
        'ROLE_DISPATCH_DELIVERY_EXHAUSTED','ROLE_CALLBACK_DEADLINE_EXCEEDED',
        'AUTOPILOT_TRANSIENT_DATABASE_ERROR','CODEX_ACK_DEADLINE_EXCEEDED',
        'CODEX_RESULT_DEADLINE_EXCEEDED'
    ),false)
$$;

CREATE OR REPLACE FUNCTION autopilot.reconcile_role_dispatch_callbacks()
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    row autopilot.role_dispatch_outbox;
    reconciled integer:=0;
    terminal boolean;
    terminal_code text;
BEGIN
    UPDATE autopilot.role_dispatch_outbox
       SET status='RETRY',updated_at=now()
     WHERE delivery_contract_version IN (1,2)
       AND status='DELIVERY_FAILED' AND next_attempt_at<=now();

    FOR row IN
        SELECT * FROM autopilot.role_dispatch_outbox
         WHERE (status='PUBLISHED' AND delivery_deadline_at<=now())
            OR (status='SENT' AND callback_deadline_at<=now())
         ORDER BY COALESCE(delivery_deadline_at,callback_deadline_at)
         FOR UPDATE SKIP LOCKED
    LOOP
        IF row.delivery_contract_version=3 THEN
            terminal_code:=CASE WHEN row.status='PUBLISHED'
                THEN 'CODEX_ACK_DEADLINE_EXCEEDED'
                ELSE 'CODEX_RESULT_DEADLINE_EXCEEDED' END;
            UPDATE autopilot.role_dispatch_outbox
               SET status='FAILED_CLOSED',last_error_code=terminal_code,
                   completed_at=now(),updated_at=now()
             WHERE dispatch_id=row.dispatch_id;
            UPDATE autopilot.step_attempt
               SET status='FAILED_CLOSED',error_code=terminal_code,
                   completed_at=now()
             WHERE step_attempt_id=row.step_attempt_id
               AND status='WAITING_EXTERNAL';
            UPDATE autopilot.task
               SET status='FAILED_CLOSED',terminal_reason_code=terminal_code,
                   safe_summary_json=jsonb_build_object(
                       'dispatch_id',row.dispatch_id,'result_code',terminal_code,
                       'summary','Codex event delivery exceeded its bounded deadline.'
                   ),completed_at=now()
             WHERE task_id=row.task_id AND status='WAITING_EXTERNAL';
            PERFORM pg_notify('autopilot_ready','codex-delivery-retry');
        ELSE
            terminal:=row.attempts>=row.max_attempts OR row.status='SENT';
            UPDATE autopilot.role_dispatch_outbox SET
                status=CASE WHEN terminal THEN 'FAILED_CLOSED'
                            ELSE 'DELIVERY_FAILED' END,
                next_attempt_at=now()+interval '60 seconds',
                last_error_code=CASE WHEN row.status='PUBLISHED'
                    THEN 'CHATGPT_DELIVERY_PROOF_MISSING'
                    ELSE 'ROLE_CALLBACK_DEADLINE_EXCEEDED' END,
                completed_at=CASE WHEN terminal THEN now() ELSE NULL END,
                updated_at=now()
             WHERE dispatch_id=row.dispatch_id;
            IF terminal THEN
                terminal_code:=CASE WHEN row.status='SENT'
                    THEN 'ROLE_CALLBACK_DEADLINE_EXCEEDED'
                    ELSE 'ROLE_DISPATCH_DELIVERY_EXHAUSTED' END;
                UPDATE autopilot.step_attempt
                   SET status='FAILED_CLOSED',error_code=terminal_code,
                       completed_at=now()
                 WHERE step_attempt_id=row.step_attempt_id
                   AND status='WAITING_EXTERNAL';
                UPDATE autopilot.task
                   SET status='FAILED_CLOSED',terminal_reason_code=terminal_code,
                       safe_summary_json=jsonb_build_object(
                           'dispatch_id',row.dispatch_id,
                           'result_code',terminal_code
                       ),completed_at=now()
                 WHERE task_id=row.task_id AND status='WAITING_EXTERNAL';
            ELSE
                PERFORM pg_notify('autopilot_ready','chatgpt-delivery-retry');
            END IF;
        END IF;
        reconciled:=reconciled+1;
    END LOOP;
    RETURN reconciled;
END $$;

COMMENT ON FUNCTION autopilot.claim_project_work_probe(text,integer) IS
'Atomically admits every enabled role into six Codex Cloud slots: five normal plus one P0 reserve; ChatGPT chat registration is not a delivery dependency.';
COMMENT ON FUNCTION autopilot.accept_role_dispatch_codex_ack(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb
) IS
'Accepts only an authenticated owner @codex command with a pinned Codex App eyes reaction; a dispatch PR alone is never delivery.';

REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_codex_ack(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb
) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_codex_ack(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb
) TO autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) TO autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) TO autopilot_runtime;
REVOKE ALL ON FUNCTION autopilot.claim_project_work_probe(text,integer)
FROM PUBLIC,autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.claim_project_work_probe(text,integer)
TO autopilot_runtime,autopilot_runtime_principal;
REVOKE ALL ON FUNCTION autopilot.project_work_transport_retryable(text)
FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.reconcile_role_dispatch_callbacks()
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_role_dispatch_callbacks()
TO autopilot_runtime;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0332_autopilot_codex_event_cycle')
ON CONFLICT DO NOTHING;

COMMIT;
