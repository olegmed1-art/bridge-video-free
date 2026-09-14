\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    origin_id uuid;
    repair_id uuid;
    verify_id uuid;
    failed_verify_id uuid;
    continuation_id uuid;
    owner_id uuid;
    dispatch record;
    claimed_task record;
    claimed_outbox record;
    callback_result record;
    callback_body jsonb;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0323_autopilot_failure_continuation'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_FAILURE_CONTINUATION_MIGRATION_MISSING';
    END IF;

    SELECT task_id INTO origin_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-failure-origin-1',
          jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free',
              'mailbox_pr', 1150,
              'role', 'RECOGNIZER',
              'target_pr', 1106,
              'expected_head_sha', repeat('a', 40),
              'dispatch_epoch', 100,
              'successor_task_key', 'sql-independent-continuation-1',
              'successor_role', 'KNOWLEDGE',
              'successor_target_pr', 1129,
              'successor_expected_head_sha', repeat('b', 40)
          ),
          0, 'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed_task
      FROM autopilot.claim_next_task('sql-failure-worker-1', 60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        origin_id, 'sql-failure-worker-1', claimed_task.lease_epoch
    );
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-failure-publisher-1', 60);
    IF claimed_outbox.mode <> 'READ_ONLY'
       OR claimed_outbox.repair_attempt <> 0
       OR claimed_outbox.origin_task_id IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_ORIGIN_DISPATCH_MODE_INVALID';
    END IF;
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-failure-publisher-1',
        claimed_outbox.claim_epoch, 8101, repeat('1', 64)
    );
    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text,
        'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role,
        'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr,
        'status', 'BLOCKED',
        'result_code', 'RECOGNIZER_READINESS_GAP',
        'target_head_sha', dispatch.expected_head_sha,
        'summary', 'Independent holdout evidence is missing.'
    );
    SELECT * INTO callback_result
      FROM autopilot.accept_role_dispatch_callback(
          'delivery-failure-origin-1', repeat('2', 64), true,
          'olegmed1-art/bridge-video-free', 1150,
          'olegmed1-art', 315099490, 'OWNER',
          'chatgpt-codex-connector', 1144995, callback_body
      );
    IF callback_result.resulting_state <> 'FAILED_CLOSED' THEN
        RAISE EXCEPTION 'AUTOPILOT_BLOCKED_ORIGIN_NOT_RETAINED';
    END IF;
    SELECT followup_task_id INTO repair_id
      FROM autopilot.role_dispatch_followup
     WHERE parent_task_id = origin_id AND followup_kind = 'REPAIR';
    SELECT followup_task_id INTO continuation_id
      FROM autopilot.role_dispatch_followup
     WHERE parent_task_id = origin_id AND followup_kind = 'CONTINUATION';
    IF repair_id IS NULL OR continuation_id IS NOT NULL
       OR (SELECT status FROM autopilot.task WHERE task_id = repair_id) <> 'READY'
       OR (SELECT count(*) FROM autopilot.role_dispatch_followup
            WHERE parent_task_id = origin_id) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_BLOCKED_FOLLOWUPS_NOT_MATERIALIZED';
    END IF;

    SELECT * INTO claimed_task
      FROM autopilot.claim_next_task('sql-repair-worker-1', 60);
    IF claimed_task.task_id <> repair_id
       OR claimed_task.goal_type <> 'CHATGPT_ROLE_FOLLOWUP_V1'
       OR claimed_task.goal_json->>'mode' <> 'REPAIR' THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_NOT_CLAIMED';
    END IF;
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        repair_id, 'sql-repair-worker-1', claimed_task.lease_epoch
    );
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-repair-publisher-1', 60);
    IF claimed_outbox.mode <> 'REPAIR'
       OR claimed_outbox.repair_attempt <> 1
       OR claimed_outbox.origin_task_id <> origin_id
       OR claimed_outbox.prior_task_id <> origin_id
       OR claimed_outbox.blocked_result_code <> 'RECOGNIZER_READINESS_GAP' THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_CONTEXT_INVALID';
    END IF;
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-repair-publisher-1',
        claimed_outbox.claim_epoch, 8102, repeat('3', 64)
    );
    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text,
        'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role,
        'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr,
        'status', 'SUCCEEDED',
        'result_code', 'MINIMAL_REPAIR_TESTED',
        'target_head_sha', repeat('c', 40),
        'summary', 'Minimal repair and targeted regression completed.'
    );
    SELECT * INTO callback_result
      FROM autopilot.accept_role_dispatch_callback(
          'delivery-repair-1', repeat('4', 64), true,
          'olegmed1-art/bridge-video-free', 1150,
          'olegmed1-art', 315099490, 'OWNER',
          'chatgpt-codex-connector', 1144995, callback_body
      );
    IF callback_result.resulting_state <> 'DONE' THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_RESULT_INVALID';
    END IF;
    SELECT followup_task_id INTO verify_id
      FROM autopilot.role_dispatch_followup
     WHERE parent_task_id = repair_id AND followup_kind = 'VERIFY';
    IF verify_id IS NULL
       OR (SELECT goal_json->>'expected_head_sha' FROM autopilot.task
            WHERE task_id = verify_id) <> repeat('c', 40)
       OR EXISTS (
           SELECT 1 FROM autopilot.role_dispatch_followup
            WHERE parent_task_id = repair_id AND followup_kind = 'REPAIR'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_VERIFY_NOT_BOUNDED';
    END IF;

    SELECT * INTO claimed_task
      FROM autopilot.claim_next_task('sql-verify-worker-1', 60);
    IF claimed_task.task_id <> verify_id
       OR claimed_task.goal_json->>'mode' <> 'VERIFY' THEN
        RAISE EXCEPTION 'AUTOPILOT_VERIFY_NOT_CLAIMED';
    END IF;
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        verify_id, 'sql-verify-worker-1', claimed_task.lease_epoch
    );
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-verify-publisher-1', 60);
    IF claimed_outbox.mode <> 'VERIFY'
       OR claimed_outbox.prior_task_id <> repair_id THEN
        RAISE EXCEPTION 'AUTOPILOT_VERIFY_CONTEXT_INVALID';
    END IF;
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-verify-publisher-1',
        claimed_outbox.claim_epoch, 8103, repeat('5', 64)
    );
    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text,
        'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role,
        'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr,
        'status', 'SUCCEEDED',
        'result_code', 'REPAIR_VERIFIED',
        'target_head_sha', repeat('c', 40),
        'summary', 'Repair verification passed on the exact changed head.'
    );
    PERFORM * FROM autopilot.accept_role_dispatch_callback(
        'delivery-verify-1', repeat('6', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body
    );
    SELECT followup_task_id INTO continuation_id
      FROM autopilot.role_dispatch_followup
     WHERE parent_task_id = origin_id AND followup_kind = 'CONTINUATION';
    IF (SELECT status FROM autopilot.task WHERE task_id = verify_id) <> 'DONE'
       OR continuation_id IS NULL
       OR (SELECT status FROM autopilot.task
            WHERE task_id = continuation_id) <> 'READY'
       OR (SELECT task_key FROM autopilot.task
            WHERE task_id = continuation_id) <> 'sql-independent-continuation-1'
       OR (SELECT count(*) FROM autopilot.task
            WHERE task_key LIKE 'role-repair:%') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_VERIFIED_REPAIR_DID_NOT_CONTINUE';
    END IF;

    -- A blocked read-only verification is terminal and cannot recursively
    -- create another repair.  This task is injected only to exercise that
    -- negative edge; the controller itself creates exactly one verification.
    UPDATE autopilot.task SET not_before = now() + interval '1 hour'
     WHERE task_id = continuation_id;
    SELECT task_id INTO failed_verify_id
      FROM autopilot.create_chatgpt_role_followup_task(
          'sql-explicit-failed-verify-1',
          jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free',
              'mailbox_pr', 1150, 'role', 'RECOGNIZER', 'target_pr', 1106,
              'expected_head_sha', repeat('c', 40), 'dispatch_epoch', 103,
              'mode', 'VERIFY', 'repair_attempt', 1,
              'origin_task_id', origin_id::text,
              'prior_task_id', repair_id::text,
              'blocked_result_code', 'RECOGNIZER_READINESS_GAP',
              'blocked_summary', 'Independent holdout evidence is missing.'
          ), 0, 'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed_task
      FROM autopilot.claim_next_task('sql-failed-verify-worker-1', 60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        failed_verify_id, 'sql-failed-verify-worker-1', claimed_task.lease_epoch
    );
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox_v2(
          'sql-failed-verify-publisher-1', 60
      );
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-failed-verify-publisher-1',
        claimed_outbox.claim_epoch, 8104, repeat('7', 64)
    );
    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text,
        'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role,
        'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr,
        'status', 'BLOCKED',
        'result_code', 'VERIFY_STILL_BLOCKED',
        'target_head_sha', repeat('c', 40),
        'summary', 'Verification retained one unresolved technical blocker.'
    );
    PERFORM * FROM autopilot.accept_role_dispatch_callback(
        'delivery-failed-verify-1', repeat('8', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body
    );
    IF (SELECT status FROM autopilot.task
         WHERE task_id = failed_verify_id) <> 'FAILED_CLOSED'
       OR EXISTS (
           SELECT 1 FROM autopilot.role_dispatch_followup
            WHERE parent_task_id = failed_verify_id
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_LOOP_NOT_BOUNDED';
    END IF;

    -- An explicit owner-only blocker does not create a fake technical repair.
    SELECT task_id INTO owner_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-owner-blocker-1',
          jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free',
              'mailbox_pr', 1150, 'role', 'KNOWLEDGE', 'target_pr', 1129,
              'expected_head_sha', repeat('d', 40), 'dispatch_epoch', 200,
              'successor_task_key', NULL, 'successor_role', NULL,
              'successor_target_pr', NULL, 'successor_expected_head_sha', NULL
          ), 0, 'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed_task
      FROM autopilot.claim_next_task('sql-owner-worker-1', 60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        owner_id, 'sql-owner-worker-1', claimed_task.lease_epoch
    );
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox_v2('sql-owner-publisher-1', 60);
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-owner-publisher-1',
        claimed_outbox.claim_epoch, 8105, repeat('9', 64)
    );
    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text,
        'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role,
        'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr,
        'status', 'BLOCKED',
        'result_code', 'OWNER_INPUT_REQUIRED',
        'target_head_sha', dispatch.expected_head_sha,
        'summary', 'An account owner decision is required.'
    );
    PERFORM * FROM autopilot.accept_role_dispatch_callback(
        'delivery-owner-1', repeat('a', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body
    );
    IF EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_followup
         WHERE parent_task_id = owner_id AND followup_kind = 'REPAIR'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_OWNER_BLOCKER_CREATED_FAKE_REPAIR';
    END IF;

    IF has_table_privilege(
           'autopilot_runtime_principal',
           'autopilot.role_dispatch_followup', 'SELECT'
       )
       OR has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.create_chatgpt_role_followup_task(text,jsonb,integer,text,text)',
           'EXECUTE'
       )
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.claim_role_dispatch_outbox_v2(text,integer)', 'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_FAILURE_CONTROLLER_PRIVILEGE_INVALID';
    END IF;
END $$;

ROLLBACK;
