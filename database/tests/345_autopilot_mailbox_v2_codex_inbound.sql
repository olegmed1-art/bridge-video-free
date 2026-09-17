\set ON_ERROR_STOP on
BEGIN;

-- Synthetic fixtures are for isolated SQL CI only, never a production probe.
DO $test$
DECLARE
    work_id uuid;
    task_row record;
    probe record;
    materialized record;
    dispatch record;
    outbox record;
    result record;
    ack jsonb;
    terminal jsonb;
    event_time text;
    run_suffix text := txid_current()::text;
    command_comment_id bigint := 99003450;
    reaction_id bigint := 99003451;
    rejected boolean;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0345_autopilot_mailbox_v2_codex_inbound'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_INBOUND_MIGRATION_MISSING';
    END IF;
    IF pg_get_functiondef(
        'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure
       ) NOT LIKE '%MAILBOX_V2_CODEX_INBOUND_V1%outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer%'
       OR pg_get_functiondef(
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
       ) NOT LIKE '%MAILBOX_V2_CODEX_INBOUND_V1%p_event_pr NOT IN (outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)%'
       OR pg_get_functiondef(
        'autopilot.authorize_codex_publication(jsonb,bigint,text)'::regprocedure
       ) NOT LIKE '%MAILBOX_V2_CODEX_PUBLICATION_V1%''command_pr'',outbox.mailbox_pr%proof.command_pr IS DISTINCT FROM outbox.mailbox_pr%'
       OR pg_get_functiondef(
        'autopilot.issue_codex_publication_permit(jsonb,integer)'::regprocedure
       ) NOT LIKE '%MAILBOX_V2_CODEX_ISSUER_V1%proof.command_pr IS DISTINCT FROM outbox.mailbox_pr%' THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_INBOUND_DEFINITION_INVALID';
    END IF;

    SELECT work_item_id INTO work_id
      FROM autopilot.register_universal_work_item(
        'sql-mailbox-v2-codex-inbound-345-'||run_suffix,
        'AUTOPILOT','MAILBOX_V2_CODEX_INBOUND_TEST',
        'Prove that a clean mailbox can carry a Codex ACK and terminal result for a distinct retained target PR.',
        1150,0,'{"media":false}'::jsonb,NULL,'database-test','SQL_TEST'
      );
    IF (SELECT mailbox_pr FROM autopilot.project_work_item WHERE work_item_id=work_id)<>1637
       OR (SELECT target_pr FROM autopilot.project_work_item WHERE work_item_id=work_id)<>1150 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TEST_BINDING_INVALID';
    END IF;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-mailbox-v2-planner-345',60);
    IF probe.work_item_id IS DISTINCT FROM work_id THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_WORK_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        work_id,'sql-mailbox-v2-planner-345',probe.lease_epoch,true,repeat('2',40)
      );
    SELECT * INTO task_row
      FROM autopilot.claim_next_task('sql-mailbox-v2-worker-345',60);
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        task_row.task_id,'sql-mailbox-v2-worker-345',task_row.lease_epoch
      );
    SELECT * INTO outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-mailbox-v2-publisher-345',60);
    IF NOT autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-mailbox-v2-publisher-345',outbox.claim_epoch,
        9900345,repeat('7',64)
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_DISPATCH_NOT_PUBLISHED';
    END IF;

    event_time:=to_char(clock_timestamp() AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS"Z"');
    ack:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_pr',9900345,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'expected_head_sha',dispatch.expected_head_sha,
        'mode',outbox.mode,
        'command_pr',1637,
        'command_comment_id',command_comment_id,
        'command_created_at',event_time,
        'ack_reaction_id',reaction_id,
        'ack_created_at',event_time
    );

    rejected:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_codex_ack(
            'github-codex-ack:345-wrong-target-'||run_suffix,repeat('d',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'olegmed1-art',315099490,'OWNER',
            'chatgpt-codex-connector',1144995,
            'chatgpt-codex-connector[bot]',199175422,
            ack||jsonb_build_object('command_pr',1150)
        );
    EXCEPTION WHEN OTHERS THEN
        rejected:=SQLERRM='AUTOPILOT_CODEX_ACK_BINDING_INVALID';
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TARGET_ACCEPTED_AS_MAILBOX';
    END IF;

    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_ack(
        'github-codex-ack:345-'||run_suffix,repeat('d',64),true,
        'olegmed1-art/bridge-video-free',1637,
        'olegmed1-art',315099490,'OWNER',
        'chatgpt-codex-connector',1144995,
        'chatgpt-codex-connector[bot]',199175422,ack
      );
    IF NOT result.accepted OR result.duplicate OR result.resulting_state<>'SENT'
       OR (SELECT status FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id)<>'SENT'
       OR (SELECT count(*) FROM autopilot.role_dispatch_codex_delivery_proof
            WHERE dispatch_id=dispatch.dispatch_id)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ACK_NOT_RETAINED';
    END IF;

    terminal:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'status','BLOCKED',
        'result_code','RUNTIME_EVIDENCE_INCOMPLETE',
        'target_head_sha',dispatch.expected_head_sha,
        'summary','Task blocked. See execution details above.'
    );

    rejected:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_codex_terminal(
            'github-codex-result:345-wrong-target-'||run_suffix,repeat('e',64),true,
            'olegmed1-art/bridge-video-free',1150,
            'chatgpt-codex-connector[bot]',199175422,'NONE',
            'chatgpt-codex-connector',1144995,terminal
        );
    EXCEPTION WHEN OTHERS THEN
        rejected:=SQLERRM='AUTOPILOT_CODEX_TERMINAL_BINDING_INVALID';
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TARGET_ACCEPTED_AS_EVENT_PR';
    END IF;

    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:345-'||run_suffix,repeat('e',64),true,
        'olegmed1-art/bridge-video-free',1637,
        'chatgpt-codex-connector[bot]',199175422,'NONE',
        'chatgpt-codex-connector',1144995,terminal
      );
    IF NOT result.accepted OR result.duplicate
       OR result.resulting_state<>'FAILED_CLOSED'
       OR (SELECT status FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id)<>'CALLBACK_ACCEPTED'
       OR (SELECT status FROM autopilot.task
            WHERE task_id=task_row.task_id)<>'FAILED_CLOSED'
       OR (SELECT count(*) FROM autopilot.role_dispatch_codex_terminal_receipt
            WHERE dispatch_id=dispatch.dispatch_id)<>1
       OR (SELECT count(*) FROM autopilot.evidence
            WHERE task_id=task_row.task_id
              AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT'
              AND retained)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TERMINAL_NOT_RETAINED';
    END IF;

    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:345-'||run_suffix,repeat('e',64),true,
        'olegmed1-art/bridge-video-free',1637,
        'chatgpt-codex-connector[bot]',199175422,'NONE',
        'chatgpt-codex-connector',1144995,terminal
      );
    IF result.accepted OR NOT result.duplicate
       OR (SELECT count(*) FROM autopilot.role_dispatch_codex_terminal_receipt
            WHERE dispatch_id=dispatch.dispatch_id)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TERMINAL_NOT_IDEMPOTENT';
    END IF;
END $test$;

ROLLBACK;
