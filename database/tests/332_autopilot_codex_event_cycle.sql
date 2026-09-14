\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    work_id uuid;
    task_row record;
    probe record;
    materialized record;
    dispatch record;
    outbox record;
    assignment record;
    ack jsonb;
    terminal jsonb;
    result record;
    published boolean;
    raised boolean;
    event_time text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0332_autopilot_codex_event_cycle'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_EVENT_CYCLE_MIGRATION_MISSING';
    END IF;
    IF (SELECT column_default FROM information_schema.columns
         WHERE table_schema='autopilot'
           AND table_name='role_dispatch_outbox'
           AND column_name='delivery_contract_version') <> '3' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_V3_NOT_DEFAULT';
    END IF;

    -- Contract v3 is role-bound and must not depend on a pre-created ChatGPT
    -- conversation.  The row remains disabled only inside this transaction.
    UPDATE autopilot.role_chat_registry SET enabled=false
     WHERE role_id='AUTOPILOT';
    SELECT work_item_id INTO work_id
      FROM autopilot.register_universal_work_item(
        'sql-codex-event-cycle-332','AUTOPILOT','CODEX_EVENT_CYCLE_TEST',
        'Prove GitHub event delivery to a separate Codex Cloud task.',
        1150,0,'{"media":false}'::jsonb,NULL,'database-test','SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-codex-planner-332',60);
    IF probe.work_item_id IS DISTINCT FROM work_id THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_CHATLESS_ROLE_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        work_id,'sql-codex-planner-332',probe.lease_epoch,true,repeat('a',40)
      );
    SELECT * INTO task_row
      FROM autopilot.claim_next_task('sql-codex-worker-332',60);
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        task_row.task_id,'sql-codex-worker-332',task_row.lease_epoch
      );
    IF (SELECT delivery_contract_version
          FROM autopilot.role_dispatch_outbox
         WHERE dispatch_id=dispatch.dispatch_id)<>3 THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_NEW_DISPATCH_NOT_V3';
    END IF;
    SELECT * INTO assignment
      FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    IF assignment.dispatch_id IS DISTINCT FROM dispatch.dispatch_id
       OR assignment.target_chat_id IS NOT NULL
       OR assignment.execution_scope<>'REPOSITORY' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ASSIGNMENT_REQUIRES_CHAT';
    END IF;

    SELECT * INTO outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-codex-publisher-332',60);
    published:=autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-codex-publisher-332',outbox.claim_epoch,
        9900332,repeat('b',64)
    );
    IF NOT published OR (SELECT status FROM autopilot.role_dispatch_outbox
         WHERE dispatch_id=dispatch.dispatch_id)<>'PUBLISHED' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DISPATCH_NOT_PUBLISHED';
    END IF;

    terminal:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'status','SUCCEEDED',
        'result_code','CODEX_EVENT_CYCLE_GREEN',
        'target_head_sha',dispatch.expected_head_sha,
        'summary','Codex event cycle completed with exact bound evidence.'
    );
    raised:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
            'github-codex-result:99003320',repeat('c',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_CODEX_TERMINAL_BINDING_INVALID%';
        IF NOT raised THEN
            RAISE EXCEPTION 'AUTOPILOT_CODEX_PRE_ACK_REJECTION_UNEXPECTED: %',SQLERRM;
        END IF;
    END;
    IF NOT raised THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_WITHOUT_ACK_ACCEPTED';
    END IF;

    event_time:=to_char(
        clock_timestamp() AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS"Z"'
    );
    ack:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_pr',9900332,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'expected_head_sha',dispatch.expected_head_sha,
        'mode',outbox.mode,
        'command_pr',1150,
        'command_comment_id',5669716994,
        'command_created_at',event_time,
        'ack_reaction_id',417240549,
        'ack_created_at',event_time
    );
    raised:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_codex_ack(
            'github-codex-ack:417240549',repeat('d',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'olegmed1-art',315099490,'OWNER',
            'chatgpt-codex-connector',1144995,
            'lookalike[bot]',199175422,ack
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_CODEX_ACK_IDENTITY_INVALID%';
    END;
    IF NOT raised THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_LOOKALIKE_ACK_ACCEPTED';
    END IF;

    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_ack(
        'github-codex-ack:417240549',repeat('d',64),true,
        'olegmed1-art/bridge-video-free',1150,
        'olegmed1-art',315099490,'OWNER',
        'chatgpt-codex-connector',1144995,
        'chatgpt-codex-connector[bot]',199175422,ack
      );
    IF NOT result.accepted OR result.duplicate
       OR result.resulting_state<>'SENT'
       OR (SELECT status FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id)<>'SENT'
       OR (SELECT codex_command_comment_id FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id)<>5669716994
       OR (SELECT count(*)
             FROM autopilot.role_dispatch_codex_delivery_proof
            WHERE dispatch_id=dispatch.dispatch_id)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_NOT_RETAINED';
    END IF;
    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_ack(
        'github-codex-ack:417240549',repeat('d',64),true,
        'olegmed1-art/bridge-video-free',1150,
        'olegmed1-art',315099490,'OWNER',
        'chatgpt-codex-connector',1144995,
        'chatgpt-codex-connector[bot]',199175422,ack
      );
    IF result.accepted OR NOT result.duplicate THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ACK_NOT_IDEMPOTENT';
    END IF;

    raised:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
            'github-codex-result:99003321',repeat('e',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'lookalike[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_CODEX_TERMINAL_IDENTITY_INVALID%';
    END;
    IF NOT raised THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_LOOKALIKE_RESULT_ACCEPTED';
    END IF;

    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:99003321',repeat('e',64),true,
        'olegmed1-art/bridge-video-free',1150,
        'chatgpt-codex-connector[bot]',199175422,'NONE',
        'chatgpt-codex-connector',1144995,terminal
      );
    IF NOT result.accepted OR result.duplicate
       OR result.resulting_state<>'DONE'
       OR (SELECT status FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id)<>'CALLBACK_ACCEPTED'
       OR (SELECT status FROM autopilot.task
            WHERE task_id=task_row.task_id)<>'DONE'
       OR (SELECT state FROM autopilot.project_work_item
            WHERE work_item_id=work_id)<>'DONE'
       OR (SELECT count(*)
             FROM autopilot.role_dispatch_codex_terminal_receipt
            WHERE dispatch_id=dispatch.dispatch_id)<>1
       OR (SELECT count(*) FROM autopilot.evidence
            WHERE task_id=task_row.task_id
              AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT'
              AND retained)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_NOT_RETAINED';
    END IF;
    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:99003321',repeat('e',64),true,
        'olegmed1-art/bridge-video-free',1150,
        'chatgpt-codex-connector[bot]',199175422,'NONE',
        'chatgpt-codex-connector',1144995,terminal
      );
    IF result.accepted OR NOT result.duplicate THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_TERMINAL_NOT_IDEMPOTENT';
    END IF;

    IF has_table_privilege(
           'autopilot_callback','autopilot.role_dispatch_codex_delivery_proof','SELECT'
       ) OR has_table_privilege(
           'autopilot_callback','autopilot.role_dispatch_codex_terminal_receipt','SELECT'
       ) OR NOT has_function_privilege(
           'autopilot_callback',
           'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)',
           'EXECUTE'
       ) OR NOT has_function_privilege(
           'autopilot_callback',
           'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_CALLBACK_PRIVILEGE_INVALID';
    END IF;
END $$;

ROLLBACK;
