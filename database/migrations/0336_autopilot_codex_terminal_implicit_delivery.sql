\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0335_autopilot_codex_delivery_window'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_IMPLICIT_DELIVERY_REQUIRES_0335';
    END IF;
END $$;

-- The ChatGPT Codex GitHub App does not reliably emit an eyes reaction for
-- every accepted task.  A terminal comment from the same immutable bot/app,
-- bound to the exact dispatch epoch, role, target PR, head and fingerprint,
-- is stronger evidence that delivery actually occurred.  Preserve the old
-- function verbatim for rollback and allow that terminal receipt to subsume
-- the optional reaction acknowledgement without fabricating reaction data.
CREATE TABLE autopilot.migration_0336_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);
INSERT INTO autopilot.migration_0336_function_backup(
    function_key,function_definition
)
SELECT 'accept_role_dispatch_codex_terminal',pg_get_functiondef(
    'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
);
REVOKE ALL ON TABLE autopilot.migration_0336_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

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
    implicit_delivery boolean := false;
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

    -- Normal v3 path keeps the immutable eyes proof.  If that optional UI
    -- reaction never appeared, a terminal callback from the pinned Codex App
    -- may close a still-PUBLISHED dispatch directly.  No synthetic reaction
    -- id is stored; codex_ack_* stays NULL so provenance remains truthful.
    implicit_delivery := outbox.status='PUBLISHED' AND proof.dispatch_id IS NULL;
    IF outbox.dispatch_id IS NULL
       OR task_row.task_id IS NULL
       OR outbox.delivery_contract_version<>3
       OR task_row.status<>'WAITING_EXTERNAL'
       OR task_row.goal_type NOT IN (
           'CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1'
       )
       OR outbox.repository<>p_repository
       OR outbox.dispatch_epoch<>(p_body->>'dispatch_epoch')::bigint
       OR outbox.role<>p_body->>'role'
       OR outbox.target_pr<>(p_body->>'target_pr')::integer
       OR p_event_pr<>outbox.target_pr
       OR (outbox.mode IN ('READ_ONLY','VERIFY')
           AND outbox.expected_head_sha<>p_body->>'target_head_sha')
       OR outbox.task_fingerprint<>p_body->>'task_fingerprint'
       OR NOT (
           (outbox.status='SENT'
            AND proof.dispatch_id IS NOT NULL
            AND proof.command_pr=p_event_pr)
           OR implicit_delivery
       ) THEN
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
        'GITHUB_WEBHOOK',p_delivery_id,body_sha256,
        p_body || jsonb_build_object(
            'delivery_proof',CASE WHEN implicit_delivery
                THEN 'PINNED_CODEX_TERMINAL' ELSE 'CODEX_EYES_ACK' END
        )
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
       SET status='CALLBACK_ACCEPTED',completed_at=now(),updated_at=now(),
           sent_at=CASE WHEN implicit_delivery THEN COALESCE(sent_at,now()) ELSE sent_at END,
           callback_deadline_at=CASE WHEN implicit_delivery
               THEN COALESCE(callback_deadline_at,now()+interval '30 minutes')
               ELSE callback_deadline_at END,
           delivered_at=CASE WHEN implicit_delivery THEN COALESCE(delivered_at,now()) ELSE delivered_at END
     WHERE dispatch_id=outbox.dispatch_id;
    PERFORM autopilot.record_event(
        task_row.task_id,
        CASE WHEN new_state='DONE' THEN 'TASK_DONE' ELSE 'TASK_FAILED_CLOSED' END,
        'WAITING_EXTERNAL',new_state,
        jsonb_build_object(
            'dispatch_id',outbox.dispatch_id,'delivery_id',p_delivery_id,
            'result_code',p_body->>'result_code','content_sha256',body_sha256,
            'delivery_proof',CASE WHEN implicit_delivery
                THEN 'PINNED_CODEX_TERMINAL' ELSE 'CODEX_EYES_ACK' END
        ),
        'EXTERNAL_EVENT',p_actor_login,
        'codex-role-callback:'||outbox.dispatch_id::text
    );
    PERFORM pg_notify('autopilot_ready','codex-terminal-next-task');
    RETURN QUERY SELECT true,false,task_row.task_id,created_successor_id,new_state;
END $$;

COMMENT ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) IS
'Accepts an exact-bound terminal result from the pinned Codex GitHub App; a valid terminal may subsume a missing optional eyes acknowledgement without inventing reaction provenance.';

REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) TO autopilot_callback;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0336_autopilot_codex_terminal_implicit_delivery');
COMMIT;
