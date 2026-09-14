\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    root_item uuid;
    next_item uuid;
    task_row record;
    probe record;
    materialized record;
    dispatch record;
    outbox record;
    target_chat record;
    proof jsonb;
    terminal jsonb;
    result record;
    raised boolean;
    published boolean;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0327_autopilot_delivery_proof'
    ) THEN RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_MIGRATION_MISSING'; END IF;

    SELECT work_item_id INTO root_item FROM autopilot.register_universal_work_item(
        'sql-delivery-proof-root-326','AUTOPILOT','DELIVERY_PROOF_E2E',
        'Prove UI-visible delivery, running acknowledgement and one terminal receipt.',
        1150,0,'{"media":false}'::jsonb,NULL,'database-test','SQL_TEST'
    );
    SELECT work_item_id INTO next_item FROM autopilot.register_universal_work_item(
        'sql-delivery-proof-next-326','KNOWLEDGE','DELIVERY_PROOF_NEXT',
        'Prove the next eligible task is released after the terminal receipt.',
        1150,10,'{"media":false}'::jsonb,'sql-delivery-proof-root-326','database-test','SQL_TEST'
    );
    SELECT * INTO probe FROM autopilot.claim_project_work_probe('sql-delivery-worker-326',60);
    SELECT * INTO materialized FROM autopilot.materialize_project_work_probe(
        root_item,'sql-delivery-worker-326',probe.lease_epoch,true,repeat('a',40)
    );
    SELECT * INTO task_row FROM autopilot.claim_next_task('sql-delivery-worker-326',60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        task_row.task_id,'sql-delivery-worker-326',task_row.lease_epoch
    );
    SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('sql-publisher-326',60);
    IF outbox.dispatch_id IS DISTINCT FROM dispatch.dispatch_id THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_OUTBOX_CLAIM_MISMATCH dispatch=% claimed=%',
            dispatch.dispatch_id,outbox.dispatch_id;
    END IF;
    IF (SELECT delivery_contract_version FROM autopilot.role_dispatch_outbox
         WHERE dispatch_id=dispatch.dispatch_id) IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_CONTRACT_VERSION_INVALID';
    END IF;
    published:=autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-publisher-326',outbox.claim_epoch,9900326,repeat('b',64)
    );
    IF NOT published OR (SELECT status FROM autopilot.role_dispatch_outbox
         WHERE dispatch_id=dispatch.dispatch_id)<>'PUBLISHED' THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PUBLISH_NOT_ISOLATED';
    END IF;
    SELECT chat_id, chat_name, executor_id INTO STRICT target_chat
      FROM autopilot.role_chat_registry
     WHERE role_id = dispatch.role
       AND enabled;

    raised:=false;
    BEGIN
        PERFORM autopilot.mark_role_dispatch_sent(
            dispatch.dispatch_id,'sql-publisher-326',outbox.claim_epoch,9900326,repeat('b',64)
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_GITHUB_IS_NOT_CHATGPT_DELIVERY%';
    END;
    IF NOT raised THEN RAISE EXCEPTION 'AUTOPILOT_FALSE_GITHUB_SENT_ACCEPTED'; END IF;

    terminal:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
        'status','SUCCEEDED','result_code','DELIVERY_PROOF_E2E_GREEN',
        'target_head_sha',dispatch.expected_head_sha,'summary','Shadow delivery proof completed.',
        'target_chat_id',target_chat.chat_id,
        'message_id','11111111-1111-1111-1111-111111111111',
        'run_id','22222222-2222-2222-2222-222222222222',
        'executor_id',target_chat.executor_id
    );
    raised:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_terminal_v2(
            'terminal-before-proof-326',repeat('c',64),true,'olegmed1-art/bridge-video-free',1150,
            'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,terminal
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_TERMINAL_EXECUTOR_MISMATCH%';
    END;
    IF NOT raised THEN RAISE EXCEPTION 'AUTOPILOT_TERMINAL_WITHOUT_UI_PROOF_ACCEPTED'; END IF;

    UPDATE autopilot.role_dispatch_outbox SET delivery_deadline_at=now()-interval '1 second'
     WHERE dispatch_id=dispatch.dispatch_id;
    PERFORM autopilot.reconcile_role_dispatch_callbacks();
    IF (SELECT status FROM autopilot.role_dispatch_outbox WHERE dispatch_id=dispatch.dispatch_id)<>'DELIVERY_FAILED' THEN
        RAISE EXCEPTION 'AUTOPILOT_MISSING_UI_PROOF_NOT_DELIVERY_FAILED';
    END IF;
    UPDATE autopilot.role_dispatch_outbox SET next_attempt_at=now() WHERE dispatch_id=dispatch.dispatch_id;
    PERFORM autopilot.reconcile_role_dispatch_callbacks();
    SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('sql-publisher-326',60);
    PERFORM autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-publisher-326',outbox.claim_epoch,9900326,repeat('b',64)
    );

    proof:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
        'target_chat_id',target_chat.chat_id,
        'target_chat_name',target_chat.chat_name,
        'message_id','11111111-1111-1111-1111-111111111111',
        'run_id','22222222-2222-2222-2222-222222222222',
        'executor_id',target_chat.executor_id,
        'ui_visible',true,'run_state','RUNNING'
    );
    SELECT * INTO result FROM autopilot.accept_role_dispatch_delivery_proof(
        'proof-326',repeat('d',64),true,'olegmed1-art/bridge-video-free',1150,
        'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,proof
    );
    IF NOT result.accepted OR result.duplicate OR result.resulting_state<>'SENT' THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_NOT_ACCEPTED';
    END IF;

    raised:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_terminal_v2(
            'stale-executor-326',repeat('e',64),true,'olegmed1-art/bridge-video-free',1150,
            'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,
            jsonb_set(terminal,'{executor_id}','"chat:different-executor"'::jsonb)
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_TERMINAL_EXECUTOR_MISMATCH%';
    END;
    IF NOT raised THEN RAISE EXCEPTION 'AUTOPILOT_STALE_EXECUTOR_CALLBACK_ACCEPTED'; END IF;

    raised:=false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_terminal_v2(
            'head-change-326',repeat('f',64),true,'olegmed1-art/bridge-video-free',1150,
            'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,
            jsonb_set(terminal,'{target_head_sha}',to_jsonb(repeat('9',40)))
        );
    EXCEPTION WHEN OTHERS THEN
        raised:=SQLERRM LIKE '%AUTOPILOT_CALLBACK_BINDING_INVALID%';
    END;
    IF NOT raised THEN RAISE EXCEPTION 'AUTOPILOT_HEAD_CHANGE_CALLBACK_ACCEPTED'; END IF;

    SELECT * INTO result FROM autopilot.accept_role_dispatch_terminal_v2(
        'terminal-326',repeat('1',64),true,'olegmed1-art/bridge-video-free',1150,
        'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,terminal
    );
    IF NOT result.accepted OR result.duplicate
       OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=root_item)<>'DONE'
       OR (SELECT state FROM autopilot.project_work_item WHERE work_item_id=next_item)<>'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_DID_NOT_WAKE_NEXT_TASK';
    END IF;
    SELECT * INTO result FROM autopilot.accept_role_dispatch_terminal_v2(
        'terminal-326',repeat('1',64),true,'olegmed1-art/bridge-video-free',1150,
        'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,terminal
    );
    IF result.accepted OR NOT result.duplicate
       OR (SELECT count(*) FROM autopilot.role_dispatch_callback_receipt WHERE dispatch_id=dispatch.dispatch_id)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_TERMINAL_RECEIPT_NOT_EXACTLY_ONCE';
    END IF;
END $$;

ROLLBACK;
