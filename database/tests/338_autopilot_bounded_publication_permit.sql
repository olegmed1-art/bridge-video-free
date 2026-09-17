\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.expect_publication_rejection(command jsonb, comment_id bigint, digest text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE denied boolean:=false;
BEGIN
    BEGIN
        PERFORM autopilot.authorize_codex_publication(command,comment_id,digest);
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE 'PUBLICATION_%' THEN RAISE; END IF;
        denied:=true;
    END;
    IF NOT denied THEN RAISE EXCEPTION 'TEST_UNSAFE_PUBLICATION_AUTHORIZED'; END IF;
END $$;

DO $$
DECLARE
    work_id uuid;
    probe record;
    materialized record;
    claimed record;
    dispatch record;
    outbox record;
    assignment record;
    repair_id uuid;
    command jsonb;
    legacy_command jsonb;
    ack jsonb;
    terminal jsonb;
    terminal_result record;
    event_time text;
    key text;
    result jsonb;
    raised boolean:=false;
BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.codex_publication_permit) THEN
        RAISE EXCEPTION 'TEST_PUBLICATION_LEDGER_NOT_EMPTY';
    END IF;
    SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
        'sql-publication-338','AUTOPILOT','REPOSITORY_REPAIR','Bounded synthetic publication test.',
        1150,0,'{"expected_changed_files":["tests/test_example.py"]}'::jsonb,
        NULL,'database-test','SQL_TEST');
    SELECT * INTO probe FROM autopilot.claim_project_work_probe('sql-pub-planner-338',60);
    SELECT * INTO materialized FROM autopilot.materialize_project_work_probe(
        work_id,'sql-pub-planner-338',probe.lease_epoch,true,repeat('a',40));
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-pub-worker-338',60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-pub-worker-338',claimed.lease_epoch);
    SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('sql-pub-publisher-338',60);
    PERFORM autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-pub-publisher-338',outbox.claim_epoch,9900337,repeat('b',64));
    repair_id:=autopilot.materialize_role_repair(
        materialized.task_id,'BOUNDED_DEFECT','One synthetic bounded defect.');
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-pub-repair-338',60);
    IF claimed.task_id IS DISTINCT FROM repair_id THEN RAISE EXCEPTION 'TEST_REPAIR_NOT_CLAIMED'; END IF;
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-pub-repair-338',claimed.lease_epoch);
    SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('sql-pub-publisher-338',60);
    PERFORM autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-pub-publisher-338',outbox.claim_epoch,9900338,repeat('b',64));
    SELECT * INTO assignment FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    event_time:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"');
    ack:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,'dispatch_pr',9900338,
        'dispatch_epoch',dispatch.dispatch_epoch,'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
        'expected_head_sha',dispatch.expected_head_sha,'mode','REPAIR',
        'command_pr',dispatch.mailbox_pr,'command_comment_id',99003380,
        'command_created_at',event_time,
        'ack_reaction_id',99003382,'ack_created_at',event_time);
    PERFORM * FROM autopilot.accept_role_dispatch_codex_ack(
        'github-codex-ack:99003382',repeat('d',64),true,
        'olegmed1-art/bridge-video-free',dispatch.mailbox_pr,
        'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,
        'chatgpt-codex-connector[bot]',199175422,ack);
    command:=jsonb_build_object(
        'comment_id',99003380,'command_pr',dispatch.mailbox_pr,'created_at',event_time,
        'dispatch_id',dispatch.dispatch_id::text,'dispatch_pr',9900338,
        'dispatch_epoch',dispatch.dispatch_epoch,'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
        'expected_head_sha',dispatch.expected_head_sha,'mode','REPAIR',
        'execution_scope',assignment.execution_scope,'can_repair',assignment.can_repair,
        'task_kind',assignment.task_kind,'objective',assignment.objective,'task_spec',assignment.task_spec_json);

    -- Public IDs plus authentic ACK do not constitute provenance authority.
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('e',64));
    INSERT INTO autopilot.codex_publication_permit(
        dispatch_id,command_comment_id,publication_comment_id,approval_comment_id,payload_sha256,
        provenance_evidence_sha256,expires_at)
    VALUES (dispatch.dispatch_id,99003380,99003381,99003383,repeat('e',64),repeat('f',64),clock_timestamp()+interval '10 minutes');
    result:=autopilot.authorize_codex_publication(command,99003381,repeat('e',64));
    IF result IS DISTINCT FROM '{"state":"SENT"}'::jsonb THEN RAISE EXCEPTION 'TEST_VALID_PUBLICATION_DENIED'; END IF;

    -- All 16 required command fields must reject both absence and explicit null.
    FOR key IN SELECT jsonb_object_keys(command) LOOP
        PERFORM pg_temp.expect_publication_rejection(command-key,99003381,repeat('e',64));
        PERFORM pg_temp.expect_publication_rejection(jsonb_set(command,ARRAY[key],'null'::jsonb),99003381,repeat('e',64));
    END LOOP;
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('0',64));
    PERFORM pg_temp.expect_publication_rejection(command,99003383,repeat('e',64));
    PERFORM pg_temp.expect_publication_rejection(command,NULL,repeat('e',64));
    PERFORM pg_temp.expect_publication_rejection(command,99003381,NULL);
    UPDATE autopilot.codex_publication_permit SET revoked=true WHERE dispatch_id=dispatch.dispatch_id;
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('e',64));
    UPDATE autopilot.codex_publication_permit SET revoked=false,
        expires_at=clock_timestamp()+interval '10 seconds' WHERE dispatch_id=dispatch.dispatch_id;
    result:=autopilot.authorize_codex_publication(command,99003381,repeat('e',64));
    IF result IS DISTINCT FROM '{"state":"RECOVERY_ONLY"}'::jsonb THEN
        RAISE EXCEPTION 'TEST_EXPIRING_PERMIT_NOT_RECOVERY_ONLY';
    END IF;
    UPDATE autopilot.codex_publication_permit
       SET created_at=clock_timestamp()-interval '10 minutes',
           expires_at=clock_timestamp()-interval '1 second'
     WHERE dispatch_id=dispatch.dispatch_id;
    result:=autopilot.authorize_codex_publication(command,99003381,repeat('e',64));
    IF result IS DISTINCT FROM '{"state":"RECOVERY_ONLY"}'::jsonb THEN
        RAISE EXCEPTION 'TEST_EXPIRED_PERMIT_NOT_RECOVERY_ONLY';
    END IF;
    UPDATE autopilot.codex_publication_permit SET expires_at=clock_timestamp()+interval '10 minutes'
        WHERE dispatch_id=dispatch.dispatch_id;
    UPDATE autopilot.role_registry SET enabled=false WHERE role_id='AUTOPILOT';
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('e',64));
    UPDATE autopilot.role_registry SET enabled=true WHERE role_id='AUTOPILOT';
    UPDATE autopilot.project_work_item SET state='PAUSED' WHERE work_item_id=work_id;
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('e',64));
    UPDATE autopilot.project_work_item SET state='ACTIVE',last_task_id=NULL WHERE work_item_id=work_id;
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('e',64));
    UPDATE autopilot.project_work_item SET last_task_id=repair_id WHERE work_item_id=work_id;
    UPDATE autopilot.role_dispatch_outbox SET callback_deadline_at=clock_timestamp()+interval '10 seconds'
        WHERE dispatch_id=dispatch.dispatch_id;
    PERFORM pg_temp.expect_publication_rejection(command,99003381,repeat('e',64));

    -- No broad table read/write, no permit self-issue, no assignment-reader grant.
    IF has_table_privilege('autopilot_callback','autopilot.codex_publication_permit','SELECT')
       OR has_table_privilege('autopilot_callback','autopilot.codex_publication_permit','INSERT')
       OR has_table_privilege('autopilot_runtime','autopilot.codex_publication_permit','INSERT')
       OR has_function_privilege('autopilot_callback','autopilot.get_dispatch_assignment(uuid)','EXECUTE')
       OR NOT has_function_privilege('autopilot_callback',
           'autopilot.authorize_codex_publication(jsonb,bigint,text)','EXECUTE') THEN
        RAISE EXCEPTION 'TEST_PUBLICATION_PRIVILEGE_INVALID';
    END IF;

    -- A bounded publication result is the sole terminal allowed on the
    -- audited target PR after mailbox rotation. It must be tied to the exact
    -- retained permit while its owner command/proof remain mailbox-bound.
    UPDATE autopilot.codex_publication_permit
       SET revoked=false,expires_at=clock_timestamp()+interval '10 minutes'
     WHERE dispatch_id=dispatch.dispatch_id;
    UPDATE autopilot.role_dispatch_outbox
       SET callback_deadline_at=clock_timestamp()+interval '10 minutes'
     WHERE dispatch_id=dispatch.dispatch_id;
    terminal:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'status','SUCCEEDED',
        'result_code','BOUNDED_REPAIR_PUBLISHED',
        'target_head_sha',repeat('c',40),
        'summary','Bounded repair published and verified at the exact head.');
    SELECT * INTO terminal_result
      FROM autopilot.accept_role_dispatch_codex_terminal(
        'github-codex-result:99003381',repeat('a',64),true,
        'olegmed1-art/bridge-video-free',dispatch.target_pr,
        'chatgpt-codex-connector[bot]',199175422,'NONE',
        'chatgpt-codex-connector',1144995,terminal);
    IF NOT terminal_result.accepted OR terminal_result.duplicate
       OR terminal_result.resulting_state<>'DONE' THEN
        RAISE EXCEPTION 'TEST_TARGET_PUBLICATION_TERMINAL_DENIED';
    END IF;
    result:=autopilot.authorize_codex_publication(command,99003381,repeat('e',64));
    IF result->>'state'<>'CALLBACK_ACCEPTED'
       OR result->'terminal_body' IS DISTINCT FROM terminal THEN
        RAISE EXCEPTION 'TEST_MAILBOX_PUBLICATION_REPLAY_DENIED';
    END IF;

    -- Historical publications completed before 0345 have target-bound proof.
    -- Their exact receipt replay must remain readable after forward migration.
    UPDATE autopilot.role_dispatch_codex_delivery_proof
       SET command_pr=dispatch.target_pr,
           proof_body=jsonb_set(proof_body,'{command_pr}',to_jsonb(dispatch.target_pr))
     WHERE dispatch_id=dispatch.dispatch_id;
    legacy_command:=jsonb_set(command,'{command_pr}',to_jsonb(dispatch.target_pr));
    result:=autopilot.authorize_codex_publication(
        legacy_command,99003381,repeat('e',64));
    IF result->>'state'<>'CALLBACK_ACCEPTED'
       OR result->'terminal_body' IS DISTINCT FROM terminal THEN
        RAISE EXCEPTION 'TEST_LEGACY_PUBLICATION_REPLAY_DENIED';
    END IF;
END $$;
ROLLBACK;
