\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0325_autopilot_durable_dependency_wakeup'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_REQUIRES_0325';
    END IF;
END $$;

CREATE TABLE autopilot.role_chat_registry (
    role_id text PRIMARY KEY REFERENCES autopilot.role_registry(role_id),
    chat_id text NOT NULL UNIQUE
        CHECK (chat_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    chat_name text NOT NULL CHECK (length(chat_name) BETWEEN 1 AND 80 AND chat_name !~ '[[:cntrl:]]'),
    chat_url text NOT NULL UNIQUE CHECK (chat_url ~ '^https://chatgpt\.com/.+/c/[0-9a-f-]{36}$'),
    executor_id text NOT NULL UNIQUE
        CHECK (executor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    enabled boolean NOT NULL DEFAULT true,
    is_dispatcher boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Existing School chats only. This migration never creates a ChatGPT chat.
INSERT INTO autopilot.role_chat_registry(
    role_id, chat_id, chat_name, chat_url, executor_id, is_dispatcher
) VALUES
 ('AUTOPILOT','6aa37eec-3910-83eb-829e-72914fdbed07','СЛАВИК / AUTOPILOT','https://chatgpt.com/g/g-p-6a75852209c48191809bf433b9e854f6-shkola-sportivnogo-bridzha/c/6aa37eec-3910-83eb-829e-72914fdbed07','chat:6aa37eec-3910-83eb-829e-72914fdbed07',false),
 ('PLANNING','6aa558b2-f470-83eb-805a-95808a18c562','ДИСПЕТЧЕР','https://chatgpt.com/g/g-p-6a75852209c48191809bf433b9e854f6-shkola-sportivnogo-bridzha/c/6aa558b2-f470-83eb-805a-95808a18c562','chat:6aa558b2-f470-83eb-805a-95808a18c562',true),
 ('KNOWLEDGE','6aa023a6-e2e8-83ed-83d1-8179f9161332','KNOWLEDGE / CANON','https://chatgpt.com/g/g-p-6a75852209c48191809bf433b9e854f6-shkola-sportivnogo-bridzha/c/6aa023a6-e2e8-83ed-83d1-8179f9161332','chat:6aa023a6-e2e8-83ed-83d1-8179f9161332',false)
ON CONFLICT (role_id) DO NOTHING;

REVOKE ALL ON TABLE autopilot.role_chat_registry
FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

ALTER TABLE autopilot.role_dispatch_outbox
    ADD COLUMN delivery_contract_version smallint,
    ADD COLUMN target_chat_id text,
    ADD COLUMN target_chat_name text,
    ADD COLUMN executor_id text,
    ADD COLUMN published_at timestamptz,
    ADD COLUMN delivery_deadline_at timestamptz,
    ADD COLUMN delivered_at timestamptz;
UPDATE autopilot.role_dispatch_outbox SET delivery_contract_version = 1;
ALTER TABLE autopilot.role_dispatch_outbox
    ALTER COLUMN delivery_contract_version SET DEFAULT 2,
    ALTER COLUMN delivery_contract_version SET NOT NULL,
    ADD CONSTRAINT role_dispatch_delivery_contract_version_check
        CHECK (delivery_contract_version IN (1,2));

ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_outbox_status_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_outbox_status_check
    CHECK (status IN (
        'PENDING','CLAIMED','RETRY','PUBLISHED','DELIVERY_FAILED',
        'SENT','CALLBACK_ACCEPTED','FAILED_CLOSED'
    ));

CREATE TABLE autopilot.role_dispatch_delivery_proof (
    delivery_id text PRIMARY KEY CHECK (delivery_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    payload_fingerprint text NOT NULL CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    dispatch_id uuid NOT NULL UNIQUE REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
    target_chat_id text NOT NULL,
    target_chat_name text NOT NULL,
    message_id text NOT NULL,
    run_id text NOT NULL,
    executor_id text NOT NULL,
    proof_body jsonb NOT NULL CHECK (jsonb_typeof(proof_body) = 'object'),
    accepted_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON TABLE autopilot.role_dispatch_delivery_proof
FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

CREATE TABLE autopilot.dispatcher_wake_outbox (
    wake_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cause_dispatch_id uuid NOT NULL UNIQUE REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
    target_chat_id text NOT NULL,
    target_chat_name text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','ACKNOWLEDGED')),
    reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    requested_at timestamptz NOT NULL DEFAULT now(),
    acknowledged_at timestamptz
);
REVOKE ALL ON TABLE autopilot.dispatcher_wake_outbox
FROM PUBLIC, autopilot_runtime, autopilot_runtime_principal, autopilot_callback;

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
 FROM autopilot.role_dispatch_outbox outbox
 JOIN autopilot.role_registry roles ON roles.role_id=outbox.role AND roles.enabled
 JOIN autopilot.role_chat_registry chat ON chat.role_id=outbox.role AND chat.enabled
 LEFT JOIN autopilot.project_work_task map ON map.task_id=outbox.task_id
 LEFT JOIN autopilot.project_work_item work ON work.work_item_id=map.work_item_id
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
DECLARE affected integer; target autopilot.role_chat_registry;
BEGIN
    IF p_github_pull_request <= 0 OR p_dispatch_body_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_PUBLISHED_INVALID';
    END IF;
    SELECT chat.* INTO target FROM autopilot.role_dispatch_outbox outbox
      JOIN autopilot.role_chat_registry chat ON chat.role_id=outbox.role AND chat.enabled
     WHERE outbox.dispatch_id=p_dispatch_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_UNREGISTERED'; END IF;
    UPDATE autopilot.role_dispatch_outbox
       SET status='PUBLISHED', claim_owner=NULL, claim_until=NULL,
           github_dispatch_comment_id=p_github_pull_request,
           dispatch_body_sha256=p_dispatch_body_sha256,
           target_chat_id=target.chat_id, target_chat_name=target.chat_name,
           executor_id=target.executor_id, published_at=now(),
           delivery_deadline_at=now()+interval '5 minutes',
           sent_at=NULL, callback_deadline_at=NULL, delivered_at=NULL,
           updated_at=now(), last_error_code=NULL
     WHERE dispatch_id=p_dispatch_id AND status='CLAIMED'
       AND claim_owner=p_publisher_id AND claim_epoch=p_claim_epoch
       AND claim_until>now() AND delivery_contract_version=2;
    GET DIAGNOSTICS affected=ROW_COUNT;
    RETURN affected=1;
END $$;

CREATE OR REPLACE FUNCTION autopilot.mark_role_dispatch_sent(
    p_dispatch_id uuid, p_publisher_id text, p_claim_epoch bigint,
    p_github_comment_id bigint, p_dispatch_body_sha256 text
)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE affected integer;
BEGIN
    IF p_github_comment_id<=0 OR p_dispatch_body_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_SENT_INVALID';
    END IF;
    -- Contract-v1 rows may already be in flight when this migration lands.
    -- Keep their fenced completion path available, but never let a GitHub
    -- resource mark a contract-v2 dispatch as delivered to ChatGPT.
    UPDATE autopilot.role_dispatch_outbox
       SET status='SENT',claim_owner=NULL,claim_until=NULL,
           github_dispatch_comment_id=p_github_comment_id,
           dispatch_body_sha256=p_dispatch_body_sha256,
           sent_at=now(),callback_deadline_at=now()+interval '15 minutes',
           updated_at=now(),last_error_code=NULL
     WHERE dispatch_id=p_dispatch_id AND status='CLAIMED'
       AND claim_owner=p_publisher_id AND claim_epoch=p_claim_epoch
       AND claim_until>now() AND delivery_contract_version=1;
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected=0 AND EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_outbox
         WHERE dispatch_id=p_dispatch_id AND delivery_contract_version=2
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_IS_NOT_CHATGPT_DELIVERY';
    END IF;
    RETURN affected=1;
END $$;

CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_delivery_proof(
    p_delivery_id text, p_payload_fingerprint text, p_signature_verified boolean,
    p_repository text, p_mailbox_pr integer, p_actor_login text, p_actor_id bigint,
    p_author_association text, p_app_slug text, p_app_id bigint, p_body jsonb
)
RETURNS TABLE(accepted boolean, duplicate boolean, task_id uuid, resulting_state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE existing autopilot.role_dispatch_delivery_proof; outbox autopilot.role_dispatch_outbox; target autopilot.role_chat_registry;
BEGIN
    IF p_signature_verified IS DISTINCT FROM true
       OR p_repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
       OR p_mailbox_pr IS DISTINCT FROM 1150 OR p_actor_login IS DISTINCT FROM 'olegmed1-art'
       OR p_actor_id IS DISTINCT FROM 315099490::bigint OR p_author_association IS DISTINCT FROM 'OWNER'
       OR p_app_slug IS DISTINCT FROM 'chatgpt-codex-connector' OR p_app_id IS DISTINCT FROM 1144995::bigint THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_IDENTITY_INVALID';
    END IF;
    IF p_delivery_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
       OR p_payload_fingerprint !~ '^[0-9a-f]{64}$'
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_body) keys(key)) IS DISTINCT FROM ARRAY[
          'dispatch_epoch','dispatch_id','executor_id','message_id','role','run_id','run_state',
          'target_chat_id','target_chat_name','target_pr','task_fingerprint','ui_visible']
       OR p_body->'ui_visible' IS DISTINCT FROM 'true'::jsonb
       OR p_body->>'run_state' <> 'RUNNING' THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_BODY_INVALID';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_delivery_id,0));
    SELECT * INTO existing FROM autopilot.role_dispatch_delivery_proof WHERE delivery_id=p_delivery_id;
    IF FOUND THEN
        IF existing.payload_fingerprint<>p_payload_fingerprint OR existing.proof_body<>p_body THEN
            RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_CONFLICT';
        END IF;
        RETURN QUERY SELECT false,true,
            (SELECT task_id FROM autopilot.role_dispatch_outbox WHERE dispatch_id=existing.dispatch_id),'SENT'::text;
        RETURN;
    END IF;
    SELECT * INTO outbox FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id=(p_body->>'dispatch_id')::uuid FOR UPDATE;
    SELECT * INTO target FROM autopilot.role_chat_registry WHERE role_id=outbox.role AND enabled;
    IF outbox.dispatch_id IS NULL OR target.role_id IS NULL
       OR outbox.status<>'PUBLISHED' OR outbox.delivery_deadline_at<=now()
       OR outbox.repository<>p_repository OR outbox.mailbox_pr<>p_mailbox_pr
       OR outbox.dispatch_epoch<>(p_body->>'dispatch_epoch')::bigint
       OR outbox.role<>p_body->>'role' OR outbox.target_pr<>(p_body->>'target_pr')::integer
       OR outbox.task_fingerprint<>p_body->>'task_fingerprint'
       OR target.chat_id<>p_body->>'target_chat_id' OR target.chat_name<>p_body->>'target_chat_name'
       OR target.executor_id<>p_body->>'executor_id'
       OR COALESCE(p_body->>'message_id','') !~ '^[0-9a-f-]{36}$'
       OR COALESCE(p_body->>'run_id','') !~ '^[0-9a-f-]{36}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_BINDING_INVALID';
    END IF;
    INSERT INTO autopilot.role_dispatch_delivery_proof(
        delivery_id,payload_fingerprint,dispatch_id,target_chat_id,target_chat_name,
        message_id,run_id,executor_id,proof_body
    ) VALUES (
        p_delivery_id,p_payload_fingerprint,outbox.dispatch_id,target.chat_id,target.chat_name,
        p_body->>'message_id',p_body->>'run_id',target.executor_id,p_body
    );
    UPDATE autopilot.role_dispatch_outbox SET status='SENT',sent_at=now(),delivered_at=now(),
        callback_deadline_at=now()+interval '30 minutes',updated_at=now()
     WHERE dispatch_id=outbox.dispatch_id;
    RETURN QUERY SELECT true,false,outbox.task_id,'SENT'::text;
END $$;

CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_terminal_v2(
    p_delivery_id text, p_payload_fingerprint text, p_signature_verified boolean,
    p_repository text, p_mailbox_pr integer, p_actor_login text, p_actor_id bigint,
    p_author_association text, p_app_slug text, p_app_id bigint, p_body jsonb
)
RETURNS TABLE(accepted boolean, duplicate boolean, task_id uuid, successor_task_id uuid, resulting_state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE proof autopilot.role_dispatch_delivery_proof; callback_result record; stripped jsonb; dispatcher autopilot.role_chat_registry;
BEGIN
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_body) keys(key)) IS DISTINCT FROM ARRAY[
       'dispatch_epoch','dispatch_id','executor_id','message_id','result_code','role','run_id','status',
       'summary','target_chat_id','target_head_sha','target_pr','task_fingerprint'] THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_PROOF_BODY_INVALID';
    END IF;
    SELECT * INTO proof FROM autopilot.role_dispatch_delivery_proof
     WHERE dispatch_id=(p_body->>'dispatch_id')::uuid;
    IF NOT FOUND OR proof.target_chat_id<>p_body->>'target_chat_id'
       OR proof.message_id<>p_body->>'message_id' OR proof.run_id<>p_body->>'run_id'
       OR proof.executor_id<>p_body->>'executor_id' THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_EXECUTOR_MISMATCH';
    END IF;
    stripped := p_body - ARRAY['target_chat_id','message_id','run_id','executor_id'];
    SELECT * INTO callback_result FROM autopilot.accept_role_dispatch_callback(
        p_delivery_id,p_payload_fingerprint,p_signature_verified,p_repository,p_mailbox_pr,
        p_actor_login,p_actor_id,p_author_association,p_app_slug,p_app_id,stripped
    );
    PERFORM pg_notify('autopilot_ready','terminal-receipt-next-task');
    IF callback_result.accepted AND NOT EXISTS (
        SELECT 1 FROM autopilot.task WHERE status IN ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')
    ) AND NOT EXISTS (
        SELECT 1 FROM autopilot.project_work_item WHERE state IN ('READY','BLOCKED','ACTIVE','WAITING_DEPENDENCY')
    ) THEN
        SELECT * INTO dispatcher FROM autopilot.role_chat_registry WHERE is_dispatcher AND enabled;
        IF FOUND THEN
            INSERT INTO autopilot.dispatcher_wake_outbox(
                cause_dispatch_id,target_chat_id,target_chat_name,reason_code
            ) VALUES (
                (p_body->>'dispatch_id')::uuid,dispatcher.chat_id,dispatcher.chat_name,'QUEUE_EMPTY_DECOMPOSITION_REQUIRED'
            ) ON CONFLICT (cause_dispatch_id) DO NOTHING;
            PERFORM pg_notify('autopilot_dispatcher_ready',(p_body->>'dispatch_id'));
        END IF;
    END IF;
    RETURN QUERY SELECT callback_result.accepted,callback_result.duplicate,callback_result.task_id,
        callback_result.successor_task_id,callback_result.resulting_state;
END $$;

CREATE OR REPLACE FUNCTION autopilot.reconcile_role_dispatch_callbacks()
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE row autopilot.role_dispatch_outbox; reconciled integer:=0; terminal boolean;
BEGIN
    -- Keep DELIVERY_FAILED observable and durable for at least one retry
    -- interval, then return it to the pre-existing claimable RETRY state.
    UPDATE autopilot.role_dispatch_outbox
       SET status='RETRY',updated_at=now()
     WHERE status='DELIVERY_FAILED' AND next_attempt_at<=now();
    FOR row IN SELECT * FROM autopilot.role_dispatch_outbox
        WHERE (status='PUBLISHED' AND delivery_deadline_at<=now())
           OR (status='SENT' AND callback_deadline_at<=now())
        ORDER BY COALESCE(delivery_deadline_at,callback_deadline_at) FOR UPDATE SKIP LOCKED
    LOOP
        terminal := row.attempts>=row.max_attempts OR row.status='SENT';
        UPDATE autopilot.role_dispatch_outbox SET
            status=CASE WHEN terminal THEN 'FAILED_CLOSED' ELSE 'DELIVERY_FAILED' END,
            next_attempt_at=now()+interval '60 seconds',
            last_error_code=CASE WHEN row.status='PUBLISHED' THEN 'CHATGPT_DELIVERY_PROOF_MISSING' ELSE 'ROLE_CALLBACK_DEADLINE_EXCEEDED' END,
            completed_at=CASE WHEN terminal THEN now() ELSE NULL END, updated_at=now()
         WHERE dispatch_id=row.dispatch_id;
        IF terminal THEN
            UPDATE autopilot.step_attempt SET status='FAILED_CLOSED',
                error_code=CASE WHEN row.status='SENT'
                    THEN 'ROLE_CALLBACK_DEADLINE_EXCEEDED'
                    ELSE 'ROLE_DISPATCH_DELIVERY_EXHAUSTED' END,
                completed_at=now()
             WHERE step_attempt_id=row.step_attempt_id AND status='WAITING_EXTERNAL';
            UPDATE autopilot.task SET status='FAILED_CLOSED',
                terminal_reason_code=CASE WHEN row.status='SENT'
                    THEN 'ROLE_CALLBACK_DEADLINE_EXCEEDED'
                    ELSE 'ROLE_DISPATCH_DELIVERY_EXHAUSTED' END,
                safe_summary_json=jsonb_build_object(
                    'dispatch_id',row.dispatch_id,
                    'result_code',CASE WHEN row.status='SENT'
                        THEN 'ROLE_CALLBACK_DEADLINE_EXCEEDED'
                        ELSE 'ROLE_DISPATCH_DELIVERY_EXHAUSTED' END
                ),completed_at=now()
             WHERE task_id=row.task_id AND status='WAITING_EXTERNAL';
        ELSE
            PERFORM pg_notify('autopilot_ready','chatgpt-delivery-retry');
        END IF;
        reconciled:=reconciled+1;
    END LOOP;
    RETURN reconciled;
END $$;

REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text) TO autopilot_runtime;
REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text) TO autopilot_runtime;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) FROM autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) TO autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_terminal_v2(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_terminal_v2(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb) TO autopilot_callback;

INSERT INTO public.schema_migration(migration_key) VALUES ('0327_autopilot_delivery_proof');
COMMIT;
