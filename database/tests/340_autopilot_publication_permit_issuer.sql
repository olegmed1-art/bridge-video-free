\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.expect_issuer_rejection(evidence jsonb)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE denied boolean:=false;
BEGIN
    BEGIN
        PERFORM autopilot.issue_codex_publication_permit(evidence,600);
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE 'PUBLICATION_%' THEN RAISE; END IF;
        denied:=true;
    END;
    IF NOT denied THEN RAISE EXCEPTION 'TEST_UNSAFE_PERMIT_ISSUED'; END IF;
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
    ack jsonb;
    evidence jsonb;
    issued jsonb;
    replayed jsonb;
    event_time text;
BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.codex_publication_permit) THEN
        RAISE EXCEPTION 'TEST_ISSUER_LEDGER_NOT_EMPTY';
    END IF;
    SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
        'sql-publication-issuer-340','AUTOPILOT','REPOSITORY_REPAIR','Owner-bound permit issuer test.',
        1150,0,'{"expected_changed_files":["tests/test_example.py"]}'::jsonb,
        NULL,'database-test','SQL_TEST');
    SELECT * INTO probe FROM autopilot.claim_project_work_probe('sql-pub-issuer-planner-340',60);
    SELECT * INTO materialized FROM autopilot.materialize_project_work_probe(
        work_id,'sql-pub-issuer-planner-340',probe.lease_epoch,true,repeat('a',40));
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-pub-issuer-worker-340',60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-pub-issuer-worker-340',claimed.lease_epoch);
    SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('sql-pub-issuer-publisher-340',60);
    PERFORM autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-pub-issuer-publisher-340',outbox.claim_epoch,9903400,repeat('b',64));
    repair_id:=autopilot.materialize_role_repair(
        materialized.task_id,'BOUNDED_DEFECT','Synthetic publication defect.');
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-pub-issuer-repair-340',60);
    IF claimed.task_id IS DISTINCT FROM repair_id THEN RAISE EXCEPTION 'TEST_ISSUER_REPAIR_NOT_CLAIMED'; END IF;
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-pub-issuer-repair-340',claimed.lease_epoch);
    SELECT * INTO outbox FROM autopilot.claim_role_dispatch_outbox_v2('sql-pub-issuer-publisher-340',60);
    PERFORM autopilot.mark_role_dispatch_published(
        dispatch.dispatch_id,'sql-pub-issuer-publisher-340',outbox.claim_epoch,9903401,repeat('b',64));
    SELECT * INTO assignment FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    event_time:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"');
    ack:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,'dispatch_pr',9903401,
        'dispatch_epoch',dispatch.dispatch_epoch,'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,'target_pr',dispatch.target_pr,
        'expected_head_sha',dispatch.expected_head_sha,'mode','REPAIR',
        'command_pr',1150,'command_comment_id',99034010,'command_created_at',event_time,
        'ack_reaction_id',99034011,'ack_created_at',event_time);
    PERFORM * FROM autopilot.accept_role_dispatch_codex_ack(
        'github-codex-ack:99034011',repeat('d',64),true,'olegmed1-art/bridge-video-free',1150,
        'olegmed1-art',315099490,'OWNER','chatgpt-codex-connector',1144995,
        'chatgpt-codex-connector[bot]',199175422,ack);

    evidence:=jsonb_build_object(
        'dispatch_id',dispatch.dispatch_id::text,
        'dispatch_epoch',dispatch.dispatch_epoch,
        'role',dispatch.role,
        'task_fingerprint',dispatch.task_fingerprint,
        'target_pr',dispatch.target_pr,
        'expected_head_sha',dispatch.expected_head_sha,
        'command_comment_id',99034010,
        'publication_comment_id',99034012,
        'approval_comment_id',99034013,
        'payload_sha256',repeat('e',64),
        'provenance_evidence_sha256',repeat('f',64));
    issued:=autopilot.issue_codex_publication_permit(evidence,600);
    replayed:=autopilot.issue_codex_publication_permit(evidence,600);
    IF issued IS DISTINCT FROM replayed OR issued->>'state'<>'ISSUED' THEN
        RAISE EXCEPTION 'TEST_ISSUER_IDEMPOTENT_REPLAY_FAILED';
    END IF;
    IF NOT EXISTS(
        SELECT 1 FROM autopilot.codex_publication_permit p
        JOIN autopilot.role_dispatch_outbox o USING(dispatch_id)
        WHERE p.dispatch_id=dispatch.dispatch_id
          AND p.command_comment_id=99034010
          AND p.publication_comment_id=99034012
          AND p.approval_comment_id=99034013
          AND p.payload_sha256=repeat('e',64)
          AND p.provenance_evidence_sha256=repeat('f',64)
          AND p.expires_at<=o.callback_deadline_at
          AND p.expires_at>clock_timestamp()+interval '45 seconds') THEN
        RAISE EXCEPTION 'TEST_ISSUER_LEDGER_BINDING_INVALID';
    END IF;

    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{dispatch_epoch}',to_jsonb(dispatch.dispatch_epoch+1)));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{role}',to_jsonb('OTHER'::text)));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{task_fingerprint}',to_jsonb(repeat('0',64))));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{target_pr}',to_jsonb(dispatch.target_pr+1)));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{expected_head_sha}',to_jsonb(repeat('0',40))));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{command_comment_id}',to_jsonb(990034099::bigint)));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{publication_comment_id}',to_jsonb(990034098::bigint)));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{approval_comment_id}',to_jsonb(990034097::bigint)));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{payload_sha256}',to_jsonb(repeat('1',64))));
    PERFORM pg_temp.expect_issuer_rejection(jsonb_set(evidence,'{provenance_evidence_sha256}',to_jsonb(repeat('2',64))));

    UPDATE autopilot.codex_publication_permit SET expires_at=clock_timestamp()+interval '10 seconds'
      WHERE dispatch_id=dispatch.dispatch_id;
    PERFORM pg_temp.expect_issuer_rejection(evidence);
    UPDATE autopilot.codex_publication_permit SET expires_at=clock_timestamp()+interval '5 minutes',revoked=true
      WHERE dispatch_id=dispatch.dispatch_id;
    PERFORM pg_temp.expect_issuer_rejection(evidence);

    IF has_function_privilege('autopilot_callback',
           'autopilot.issue_codex_publication_permit(jsonb,integer)','EXECUTE')
       OR has_function_privilege('autopilot_runtime',
           'autopilot.issue_codex_publication_permit(jsonb,integer)','EXECUTE')
       OR has_function_privilege('autopilot_runtime_principal',
           'autopilot.issue_codex_publication_permit(jsonb,integer)','EXECUTE')
       OR has_table_privilege('autopilot_callback','autopilot.codex_publication_permit','INSERT')
       OR has_table_privilege('autopilot_runtime','autopilot.codex_publication_permit','INSERT') THEN
        RAISE EXCEPTION 'TEST_ISSUER_PRIVILEGE_WIDENED';
    END IF;
END $$;
ROLLBACK;
