\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    role_task_id uuid;
    blocked_task_id uuid;
    exhausted_task_id uuid;
    timeout_task_id uuid;
    dispatch record;
    claimed_task record;
    claimed_outbox record;
    callback_body jsonb;
    callback_result record;
    successor_id uuid;
    rejection_seen boolean;
    sent_marked boolean;
    failure_status text;
    reconciled_count integer;
BEGIN
    IF strpos(
        pg_get_functiondef(
            'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)'::regprocedure
        ),
        '''physical-no-merge-v1'', ''physical-no-merge-v2'''
    ) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_BROKER_ROLLING_POLICY_COMPATIBILITY_MISSING';
    END IF;

    SELECT task_id INTO role_task_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-role-dispatch-1',
          jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free',
              'mailbox_pr', 1150,
              'role', 'RECOGNIZER',
              'target_pr', 1106,
              'expected_head_sha', repeat('a', 40),
              'dispatch_epoch', 1,
              'successor_task_key', 'sql-role-dispatch-successor-1',
              'successor_role', 'VIDEO',
              'successor_target_pr', 1125,
              'successor_expected_head_sha', repeat('b', 40)
          ),
          0, 'database-test', 'SQL_TEST'
      );

    IF (SELECT task_id FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-role-dispatch-1',
          jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free',
              'mailbox_pr', 1150, 'role', 'RECOGNIZER', 'target_pr', 1106,
              'expected_head_sha', repeat('a', 40), 'dispatch_epoch', 1,
              'successor_task_key', 'sql-role-dispatch-successor-1',
              'successor_role', 'VIDEO', 'successor_target_pr', 1125,
              'successor_expected_head_sha', repeat('b', 40)
          ), 0, 'database-test', 'SQL_TEST')) <> role_task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CREATE_REPLAY_FAILED';
    END IF;

    rejection_seen := false;
    BEGIN
        PERFORM * FROM autopilot.create_chatgpt_role_dispatch_task(
            'sql-role-invalid-1',
            jsonb_build_object(
                'repository', 'other/repository', 'mailbox_pr', 1150,
                'role', 'RECOGNIZER', 'target_pr', 1106,
                'expected_head_sha', repeat('a', 40), 'dispatch_epoch', 1,
                'successor_task_key', NULL, 'successor_role', NULL,
                'successor_target_pr', NULL, 'successor_expected_head_sha', NULL
            ), 0, 'database-test', 'SQL_TEST');
    EXCEPTION WHEN OTHERS THEN
        rejection_seen := SQLERRM LIKE '%AUTOPILOT_ROLE_GOAL_INVALID%';
    END;
    IF NOT rejection_seen THEN RAISE EXCEPTION 'AUTOPILOT_ROLE_REPOSITORY_NOT_PINNED'; END IF;

    SELECT * INTO claimed_task FROM autopilot.claim_next_task('sql-role-worker-1', 60);
    IF claimed_task.task_id <> role_task_id
       OR claimed_task.current_step_key <> 'github.chatgpt.role.dispatch'
       OR claimed_task.cost_cap_microusd <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_TASK_CLAIM_INVALID';
    END IF;

    IF EXISTS (SELECT 1 FROM autopilot.prepare_role_dispatch(
        role_task_id, 'wrong-worker', claimed_task.lease_epoch)) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_WRONG_WORKER_PREPARED';
    END IF;
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        role_task_id, 'sql-role-worker-1', claimed_task.lease_epoch);
    IF dispatch.dispatch_id IS NULL OR dispatch.repository <> 'olegmed1-art/bridge-video-free'
       OR dispatch.mailbox_pr <> 1150 OR dispatch.role <> 'RECOGNIZER'
       OR dispatch.target_pr <> 1106 OR dispatch.expected_head_sha <> repeat('a', 40)
       OR dispatch.dispatch_epoch <> 1 OR dispatch.task_fingerprint !~ '^[0-9a-f]{64}$'
       OR (SELECT status FROM autopilot.task WHERE task_id = role_task_id) <> 'WAITING_EXTERNAL'
       OR EXISTS (SELECT 1 FROM autopilot.task WHERE task_id = role_task_id AND lease_owner IS NOT NULL)
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.step_attempt
            WHERE task_id = role_task_id AND status = 'WAITING_EXTERNAL'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_PREPARE_NOT_ATOMIC';
    END IF;
    IF (SELECT dispatch_id FROM autopilot.prepare_role_dispatch(
        role_task_id, 'sql-role-worker-1', claimed_task.lease_epoch)) <> dispatch.dispatch_id
       OR (SELECT count(*) FROM autopilot.role_dispatch_outbox WHERE task_id = role_task_id) <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_PREPARE_REPLAY_FAILED';
    END IF;

    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox('sql-publisher-1', 60);
    IF claimed_outbox.dispatch_id <> dispatch.dispatch_id
       OR claimed_outbox.claim_epoch <> 1 OR claimed_outbox.attempt_no <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_CLAIM_INVALID';
    END IF;
    IF autopilot.fail_role_dispatch_outbox(
        dispatch.dispatch_id, 'wrong-publisher', claimed_outbox.claim_epoch,
        'GITHUB_TEMPORARY_FAILURE', 5
    ) IS NOT NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_WRONG_FENCE_ACCEPTED';
    END IF;
    IF autopilot.fail_role_dispatch_outbox(
        dispatch.dispatch_id, 'sql-publisher-1', claimed_outbox.claim_epoch,
        'GITHUB_TEMPORARY_FAILURE', 5
    ) <> 'RETRY' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_RETRY_FAILED';
    END IF;
    UPDATE autopilot.role_dispatch_outbox SET next_attempt_at = now()
     WHERE dispatch_id = dispatch.dispatch_id;
    SELECT * INTO claimed_outbox
      FROM autopilot.claim_role_dispatch_outbox('sql-publisher-1', 60);
    IF claimed_outbox.claim_epoch <> 2 OR claimed_outbox.attempt_no <> 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_RECLAIM_INVALID';
    END IF;
    IF autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'wrong-publisher', claimed_outbox.claim_epoch,
        7001, repeat('c', 64)
    ) THEN RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_WRONG_SENT_FENCE_ACCEPTED'; END IF;
    sent_marked := autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-publisher-1', claimed_outbox.claim_epoch,
        7001, repeat('c', 64)
    );
    IF NOT sent_marked OR (SELECT status FROM autopilot.role_dispatch_outbox
           WHERE dispatch_id = dispatch.dispatch_id) <> 'SENT' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_OUTBOX_SENT_FAILED';
    END IF;

    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text,
        'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role,
        'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr,
        'status', 'SUCCEEDED',
        'result_code', 'ROLE_TASK_COMPLETED',
        'target_head_sha', dispatch.expected_head_sha,
        'summary', 'Bounded public-safe completion evidence.'
    );
    rejection_seen := false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_callback(
            'delivery-wrong-identity', repeat('d', 64), true,
            'olegmed1-art/bridge-video-free', 1150,
            'olegmed1-art', 315099490, 'OWNER',
            'wrong-app', 1144995, callback_body
        );
    EXCEPTION WHEN OTHERS THEN
        rejection_seen := SQLERRM LIKE '%AUTOPILOT_CALLBACK_IDENTITY_INVALID%';
    END;
    IF NOT rejection_seen THEN RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_IDENTITY_NOT_PINNED'; END IF;

    rejection_seen := false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_callback(
            'delivery-forged-binding', repeat('d', 64), true,
            'olegmed1-art/bridge-video-free', 1150,
            'olegmed1-art', 315099490, 'OWNER',
            'chatgpt-codex-connector', 1144995,
            jsonb_set(callback_body, '{target_head_sha}', to_jsonb(repeat('9', 40)))
        );
    EXCEPTION WHEN OTHERS THEN
        rejection_seen := SQLERRM LIKE '%AUTOPILOT_CALLBACK_BINDING_INVALID%';
    END;
    IF NOT rejection_seen
       OR EXISTS (SELECT 1 FROM autopilot.role_dispatch_callback_receipt
                   WHERE delivery_id = 'delivery-forged-binding') THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_BINDING_NOT_ENFORCED';
    END IF;

    SELECT * INTO callback_result FROM autopilot.accept_role_dispatch_callback(
        'delivery-role-1', repeat('d', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body
    );
    successor_id := callback_result.successor_task_id;
    IF NOT callback_result.accepted OR callback_result.duplicate
       OR callback_result.task_id <> role_task_id
       OR callback_result.resulting_state <> 'DONE'
       OR successor_id IS NULL
       OR (SELECT status FROM autopilot.task WHERE task_id = role_task_id) <> 'DONE'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.evidence
            WHERE task_id = role_task_id
              AND evidence_class = 'CHATGPT_ROLE_DISPATCH_RESULT'
              AND provider = 'GITHUB_WEBHOOK' AND retained
       )
       OR (SELECT count(*) FROM autopilot.task
            WHERE task_key = 'sql-role-dispatch-successor-1') <> 1
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.task
            WHERE task_id = successor_id AND status = 'READY'
              AND goal_type = 'CHATGPT_ROLE_DISPATCH_V1'
              AND goal_json->>'role' = 'VIDEO'
              AND goal_json->>'dispatch_epoch' = '2'
              AND goal_json->'successor_task_key' = 'null'::jsonb
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_COMPLETION_INVALID';
    END IF;
    UPDATE autopilot.task SET not_before = now() + interval '1 day'
     WHERE task_id = successor_id;

    SELECT * INTO callback_result FROM autopilot.accept_role_dispatch_callback(
        'delivery-role-1', repeat('d', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body
    );
    IF callback_result.accepted OR NOT callback_result.duplicate
       OR callback_result.task_id <> role_task_id
       OR (SELECT count(*) FROM autopilot.task
            WHERE task_key = 'sql-role-dispatch-successor-1') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_REPLAY_INVALID';
    END IF;
    SELECT * INTO callback_result FROM autopilot.accept_role_dispatch_callback(
        'delivery-role-1-duplicate-comment', repeat('d', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body
    );
    IF callback_result.accepted OR NOT callback_result.duplicate
       OR callback_result.task_id <> role_task_id
       OR (SELECT count(*) FROM autopilot.task
            WHERE task_key = 'sql-role-dispatch-successor-1') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_LOGICAL_DUPLICATE_INVALID';
    END IF;
    rejection_seen := false;
    BEGIN
        PERFORM * FROM autopilot.accept_role_dispatch_callback(
            'delivery-role-1', repeat('e', 64), true,
            'olegmed1-art/bridge-video-free', 1150,
            'olegmed1-art', 315099490, 'OWNER',
            'chatgpt-codex-connector', 1144995, callback_body
        );
    EXCEPTION WHEN OTHERS THEN
        rejection_seen := SQLERRM LIKE '%AUTOPILOT_CALLBACK_DELIVERY_CONFLICT%';
    END;
    IF NOT rejection_seen THEN RAISE EXCEPTION 'AUTOPILOT_ROLE_DELIVERY_CONFLICT_NOT_REJECTED'; END IF;

    -- BLOCKED retains callback evidence, fails closed, and never materializes a successor.
    SELECT task_id INTO blocked_task_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-role-blocked-1', jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free', 'mailbox_pr', 1150,
              'role', 'BOOKS', 'target_pr', 1131,
              'expected_head_sha', repeat('f', 40), 'dispatch_epoch', 3,
              'successor_task_key', 'sql-role-blocked-successor-1',
              'successor_role', 'KNOWLEDGE', 'successor_target_pr', 1131,
              'successor_expected_head_sha', repeat('f', 40)
          ), 0, 'database-test', 'SQL_TEST');
    SELECT * INTO claimed_task FROM autopilot.claim_next_task('sql-role-worker-2', 60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        blocked_task_id, 'sql-role-worker-2', claimed_task.lease_epoch);
    SELECT * INTO claimed_outbox FROM autopilot.claim_role_dispatch_outbox('sql-publisher-2', 60);
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-publisher-2', claimed_outbox.claim_epoch,
        7002, repeat('1', 64));
    callback_body := jsonb_build_object(
        'dispatch_id', dispatch.dispatch_id::text, 'dispatch_epoch', dispatch.dispatch_epoch,
        'role', dispatch.role, 'task_fingerprint', dispatch.task_fingerprint,
        'target_pr', dispatch.target_pr, 'status', 'BLOCKED',
        'result_code', 'OWNER_INPUT_REQUIRED',
        'target_head_sha', dispatch.expected_head_sha,
        'summary', 'Owner input is required before bounded work can continue.'
    );
    SELECT * INTO callback_result FROM autopilot.accept_role_dispatch_callback(
        'delivery-role-blocked-1', repeat('2', 64), true,
        'olegmed1-art/bridge-video-free', 1150,
        'olegmed1-art', 315099490, 'OWNER',
        'chatgpt-codex-connector', 1144995, callback_body);
    IF callback_result.resulting_state <> 'FAILED_CLOSED'
       OR (SELECT status FROM autopilot.task WHERE task_id = blocked_task_id) <> 'FAILED_CLOSED'
       OR NOT EXISTS (SELECT 1 FROM autopilot.evidence WHERE task_id = blocked_task_id AND retained) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_BLOCKED_TERMINAL_INVALID';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE task_key = 'sql-role-blocked-successor-1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_BLOCKED_SUCCESSOR_INVALID';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0323_autopilot_failure_continuation'
    ) AND EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_followup
         WHERE parent_task_id = blocked_task_id AND followup_kind = 'REPAIR'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_OWNER_BLOCKER_REPAIR_INVALID';
    END IF;

    -- Final publisher failure closes the wait and its step rather than orphaning it.
    SELECT task_id INTO exhausted_task_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-role-exhausted-1', jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free', 'mailbox_pr', 1150,
              'role', 'KNOWLEDGE', 'target_pr', 1131,
              'expected_head_sha', repeat('3', 40), 'dispatch_epoch', 4,
              'successor_task_key', NULL, 'successor_role', NULL,
              'successor_target_pr', NULL, 'successor_expected_head_sha', NULL
          ), 0, 'database-test', 'SQL_TEST');
    SELECT * INTO claimed_task FROM autopilot.claim_next_task('sql-role-worker-3', 60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        exhausted_task_id, 'sql-role-worker-3', claimed_task.lease_epoch);
    UPDATE autopilot.role_dispatch_outbox SET attempts = max_attempts - 1
     WHERE dispatch_id = dispatch.dispatch_id;
    SELECT * INTO claimed_outbox FROM autopilot.claim_role_dispatch_outbox('sql-publisher-3', 60);
    failure_status := autopilot.fail_role_dispatch_outbox(
        dispatch.dispatch_id, 'sql-publisher-3', claimed_outbox.claim_epoch,
        'GITHUB_DELIVERY_EXHAUSTED', 5);
    IF failure_status <> 'FAILED_CLOSED'
       OR (SELECT status FROM autopilot.task WHERE task_id = exhausted_task_id) <> 'FAILED_CLOSED'
       OR EXISTS (SELECT 1 FROM autopilot.step_attempt
                   WHERE task_id = exhausted_task_id AND status = 'WAITING_EXTERNAL') THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_DELIVERY_EXHAUSTION_INVALID';
    END IF;

    -- A sent wake without a callback cannot remain waiting forever.
    SELECT task_id INTO timeout_task_id
      FROM autopilot.create_chatgpt_role_dispatch_task(
          'sql-role-timeout-1', jsonb_build_object(
              'repository', 'olegmed1-art/bridge-video-free', 'mailbox_pr', 1150,
              'role', 'VIDEO', 'target_pr', 1125,
              'expected_head_sha', repeat('4', 40), 'dispatch_epoch', 5,
              'successor_task_key', NULL, 'successor_role', NULL,
              'successor_target_pr', NULL, 'successor_expected_head_sha', NULL
          ), 0, 'database-test', 'SQL_TEST');
    SELECT * INTO claimed_task FROM autopilot.claim_next_task('sql-role-worker-4', 60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        timeout_task_id, 'sql-role-worker-4', claimed_task.lease_epoch);
    SELECT * INTO claimed_outbox FROM autopilot.claim_role_dispatch_outbox('sql-publisher-4', 60);
    PERFORM autopilot.mark_role_dispatch_sent(
        dispatch.dispatch_id, 'sql-publisher-4', claimed_outbox.claim_epoch,
        7004, repeat('4', 64));
    UPDATE autopilot.role_dispatch_outbox SET callback_deadline_at = now() - interval '1 second'
     WHERE dispatch_id = dispatch.dispatch_id;
    reconciled_count := autopilot.reconcile_role_dispatch_callbacks();
    IF reconciled_count <> 1
       OR (SELECT status FROM autopilot.task WHERE task_id = timeout_task_id) <> 'FAILED_CLOSED'
       OR (SELECT terminal_reason_code FROM autopilot.task WHERE task_id = timeout_task_id)
          <> 'ROLE_CALLBACK_DEADLINE_EXCEEDED' THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_TIMEOUT_INVALID';
    END IF;

    IF has_table_privilege('autopilot_runtime_principal', 'autopilot.role_dispatch_outbox', 'SELECT')
       OR has_table_privilege('autopilot_runtime_principal', 'autopilot.role_dispatch_callback_receipt', 'SELECT')
       OR has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)', 'EXECUTE')
       OR has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)',
           'EXECUTE')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.prepare_role_dispatch(uuid,text,bigint)', 'EXECUTE')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.claim_role_dispatch_outbox(text,integer)', 'EXECUTE')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text)', 'EXECUTE')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.fail_role_dispatch_outbox(uuid,text,bigint,text,integer)', 'EXECUTE')
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.reconcile_role_dispatch_callbacks()', 'EXECUTE') THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_RUNTIME_BOUNDARY_INVALID';
    END IF;
    IF has_table_privilege('autopilot_callback', 'autopilot.role_dispatch_outbox', 'SELECT')
       OR has_table_privilege('autopilot_callback', 'autopilot.role_dispatch_callback_receipt', 'SELECT')
       OR NOT has_function_privilege(
           'autopilot_callback',
           'autopilot.accept_role_dispatch_callback(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)',
           'EXECUTE')
       OR has_function_privilege(
           'autopilot_callback', 'autopilot.claim_role_dispatch_outbox(text,integer)', 'EXECUTE') THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_CALLBACK_BOUNDARY_INVALID';
    END IF;
END $$;

ROLLBACK;
