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
    terminal jsonb;
    result record;
    published boolean;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0336_autopilot_codex_terminal_implicit_delivery'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_DELIVERY_MIGRATION_MISSING';
    END IF;

    SELECT work_item_id INTO work_id
      FROM autopilot.register_universal_work_item(
        'sql-codex-terminal-implicit-336','AUTOPILOT',
        'CODEX_TERMINAL_IMPLICIT_DELIVERY_TEST',
        'Prove an exact-bound pinned Codex terminal result can prove delivery without an optional eyes reaction.',
        1150,0,'{"media":false}'::jsonb,NULL,'database-test','SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-codex-planner-336',60);
    IF probe.work_item_id IS DISTINCT FROM work_id THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_WORK_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        work_id,'sql-codex-planner-336',probe.lease_epoch,true,repeat('6',40)
      );
    SELECT * INTO task_row
      FROM autopilot.claim_next_task('sql-codex-worker-336',60);
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        task_row.task_id,'sql-codex-worker-336',task_row.lease_epoch
      );
    SELECT * INTO outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-codex-publisher-336',60);
    published:=autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-codex-publisher-336',outbox.claim_epoch,
        9900336,repeat('7',64)
    );
    IF NOT published OR (SELECT status FROM autopilot.role_dispatch_outbox
         WHERE dispatch_id=dispatch.dispatch_id)<>'PUBLISHED' THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_DISPATCH_NOT_PUBLISHED';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_codex_delivery_proof
         WHERE dispatch_id=dispatch.dispatch_id
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_UNEXPECTED_ACK_PROOF';
    END IF;

    terminal:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'status','SUCCEEDED',
        'result_code','CODEX_PINNED_TERMINAL_GREEN',
        'target_head_sha',dispatch.expected_head_sha,
        'summary','Pinned Codex terminal proved delivery without an eyes reaction.'
    );
    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:9900336',repeat('8',64),true,
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
       OR (SELECT codex_ack_reaction_id FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id) IS NOT NULL
       OR (SELECT delivered_at FROM autopilot.role_dispatch_outbox
            WHERE dispatch_id=dispatch.dispatch_id) IS NULL
       OR EXISTS (
            SELECT 1 FROM autopilot.role_dispatch_codex_delivery_proof
             WHERE dispatch_id=dispatch.dispatch_id
       )
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.evidence
             WHERE task_id=task_row.task_id
               AND evidence_class='CHATGPT_ROLE_DISPATCH_RESULT'
               AND metadata_json->>'delivery_proof'='PINNED_CODEX_TERMINAL'
               AND retained
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_TERMINAL_NOT_RETAINED';
    END IF;

    SELECT * INTO result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:9900336',repeat('8',64),true,
        'olegmed1-art/bridge-video-free',1150,
        'chatgpt-codex-connector[bot]',199175422,'NONE',
        'chatgpt-codex-connector',1144995,terminal
      );
    IF result.accepted OR NOT result.duplicate THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_IMPLICIT_TERMINAL_NOT_IDEMPOTENT';
    END IF;
END $$;

ROLLBACK;
