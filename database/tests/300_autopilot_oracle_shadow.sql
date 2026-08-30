\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    smoke_id uuid;
    wait_id uuid;
    exhausted_wait_id uuid;
    expired_wait_id uuid;
    owner_id uuid;
    budget_id uuid;
    stale_id uuid;
    claimed record;
    result record;
    wrong_fence boolean;
    append_only_blocked boolean := false;
BEGIN
    SELECT task_id INTO smoke_id
      FROM autopilot.create_shadow_task(
        'sql-smoke-1', 'AUTOPILOT_SMOKE_V1', '{}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST'
      );

    -- Exact replay returns the same task; conflicting reuse fails closed.
    IF (SELECT task_id FROM autopilot.create_shadow_task(
        'sql-smoke-1', 'AUTOPILOT_SMOKE_V1', '{}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST')) <> smoke_id THEN
        RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENT_REPLAY_FAILED';
    END IF;
    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
            'sql-smoke-1', 'OWNER_BOUNDARY_V1', '{}'::jsonb, 20, 0,
            'database-test', 'SQL_TEST');
        RAISE EXCEPTION 'AUTOPILOT_IDEMPOTENCY_CONFLICT_NOT_REJECTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_IDEMPOTENCY_CONFLICT%' THEN RAISE; END IF;
    END;

    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> smoke_id OR claimed.lease_epoch <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_SMOKE_CLAIM_INVALID';
    END IF;
    SELECT autopilot.complete_task(
        smoke_id, 'wrong-worker', claimed.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION', repeat('a', 64), '{}'::jsonb
    ) INTO wrong_fence;
    IF wrong_fence THEN RAISE EXCEPTION 'AUTOPILOT_WRONG_WORKER_ACCEPTED'; END IF;
    IF NOT autopilot.complete_task(
        smoke_id, 'sql-worker-1', claimed.lease_epoch,
        'SYNTHETIC_SHADOW_COMPLETION', repeat('a', 64),
        '{"production_mutation":false,"model_calls":0}'::jsonb
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_SMOKE_COMPLETION_REJECTED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = smoke_id) <> 'DONE'
       OR NOT EXISTS (SELECT 1 FROM autopilot.evidence WHERE task_id = smoke_id AND retained) THEN
        RAISE EXCEPTION 'AUTOPILOT_DONE_EVIDENCE_GATE_FAILED';
    END IF;
    IF autopilot.record_event(
        smoke_id, 'TASK_DONE', 'RUNNING', 'DONE',
        jsonb_build_object(
            'evidence_class', 'SYNTHETIC_SHADOW_COMPLETION',
            'content_sha256', repeat('a', 64)
        ),
        'ORACLE_WORKER', 'sql-worker-1',
        'done:' || smoke_id::text || ':' || claimed.lease_epoch::text
    ) IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_IDEMPOTENT_REPLAY_FAILED';
    END IF;

    -- Wait -> verified event -> READY -> second claim -> DONE.
    SELECT task_id INTO wait_id
      FROM autopilot.create_shadow_task(
        'sql-wait-1', 'EXTERNAL_WAIT_SHADOW_V1',
        '{"correlation_id":"sql:wait:1"}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> wait_id OR claimed.step_cursor <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_INITIAL_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.mark_waiting_external(
        wait_id, 'sql-worker-1', claimed.lease_epoch,
        'SYNTHETIC', 'sql:wait:1', 'SHADOW_RESUME', 300
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_CREATION_FAILED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = wait_id) <> 'WAITING_EXTERNAL' THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_STATE_INVALID';
    END IF;
    SELECT * INTO result FROM autopilot.ingest_external_event(
        'SYNTHETIC', 'sql-event-1', 'SHADOW_RESUME', 'sql:wait:1', repeat('b', 64), true
    );
    IF NOT result.accepted OR result.resumed_task_id <> wait_id OR result.resulting_state <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_RESUME_FAILED';
    END IF;
    SELECT * INTO result FROM autopilot.ingest_external_event(
        'SYNTHETIC', 'sql-event-1', 'SHADOW_RESUME', 'sql:wait:1', repeat('b', 64), true
    );
    IF result.accepted OR result.resulting_state <> 'DUPLICATE' THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_DEDUPE_FAILED';
    END IF;
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> wait_id OR claimed.step_cursor <> 1 OR claimed.lease_epoch <> 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_RESUME_CLAIM_INVALID';
    END IF;
    IF NOT autopilot.complete_task(
        wait_id, 'sql-worker-1', claimed.lease_epoch,
        'SYNTHETIC_SHADOW_RESUME', repeat('c', 64),
        '{"production_mutation":false,"duplicate_side_effects":0}'::jsonb
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_WAIT_COMPLETION_FAILED';
    END IF;

    -- A verified event at the retry boundary is retained and terminalized
    -- explicitly; it must never create a READY task whose next claim exceeds
    -- max_attempts.
    SELECT task_id INTO exhausted_wait_id
      FROM autopilot.create_shadow_task(
        'sql-wait-exhausted-1', 'EXTERNAL_WAIT_SHADOW_V1',
        '{"correlation_id":"sql:wait:exhausted:1"}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> exhausted_wait_id OR NOT autopilot.mark_waiting_external(
        exhausted_wait_id, 'sql-worker-1', claimed.lease_epoch,
        'SYNTHETIC', 'sql:wait:exhausted:1', 'SHADOW_RESUME', 300
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_EXHAUSTED_WAIT_SETUP_FAILED';
    END IF;
    UPDATE autopilot.task SET attempts = max_attempts
     WHERE task_id = exhausted_wait_id AND status = 'WAITING_EXTERNAL';
    SELECT * INTO result FROM autopilot.ingest_external_event(
        'SYNTHETIC', 'sql-event-exhausted-1', 'SHADOW_RESUME',
        'sql:wait:exhausted:1', repeat('e', 64), true
    );
    IF NOT result.accepted OR result.resumed_task_id <> exhausted_wait_id
       OR result.resulting_state <> 'FAILED_CLOSED'
       OR (SELECT terminal_reason_code FROM autopilot.task
            WHERE task_id = exhausted_wait_id) <> 'EXTERNAL_RESUME_BUDGET_EXHAUSTED'
       OR NOT EXISTS (
            SELECT 1 FROM autopilot.wait_condition
             WHERE task_id = exhausted_wait_id AND status = 'SATISFIED'
               AND satisfied_by_event_id IS NOT NULL
       )
       OR EXISTS (
            SELECT 1 FROM autopilot.task
             WHERE task_id = exhausted_wait_id AND status = 'READY'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_EXHAUSTED_WAIT_EVENT_DISCARDED';
    END IF;

    -- An unanswered external wait expires closed instead of hanging forever.
    SELECT task_id INTO expired_wait_id
      FROM autopilot.create_shadow_task(
        'sql-wait-expiry-1', 'EXTERNAL_WAIT_SHADOW_V1',
        '{"correlation_id":"sql:wait:expiry:1"}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> expired_wait_id OR NOT autopilot.mark_waiting_external(
        expired_wait_id, 'sql-worker-1', claimed.lease_epoch,
        'SYNTHETIC', 'sql:wait:expiry:1', 'SHADOW_RESUME', 300
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_EXPIRING_WAIT_SETUP_FAILED';
    END IF;
    UPDATE autopilot.wait_condition SET deadline_at = now() - interval '1 second'
     WHERE task_id = expired_wait_id AND status = 'ACTIVE';
    SELECT * INTO result FROM autopilot.reconcile_stale_tasks();
    IF result.failed_closed <> 1
       OR (SELECT status FROM autopilot.task WHERE task_id = expired_wait_id) <> 'FAILED_CLOSED'
       OR EXISTS (
           SELECT 1 FROM autopilot.wait_condition
            WHERE task_id = expired_wait_id AND status = 'ACTIVE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_EXPIRED_WAIT_NOT_CLOSED';
    END IF;

    -- Owner-only capability must terminate without performing an action.
    SELECT task_id INTO owner_id
      FROM autopilot.create_shadow_task(
        'sql-owner-1', 'OWNER_BOUNDARY_V1', '{}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> owner_id THEN
        RAISE EXCEPTION 'AUTOPILOT_OWNER_CLAIM_FAILED';
    END IF;
    IF autopilot.mark_owner_required(
        owner_id, 'wrong-worker', claimed.lease_epoch, 'ACCOUNT_OWNER_ACTION_REQUIRED'
    ) OR EXISTS (SELECT 1 FROM autopilot.evidence WHERE task_id = owner_id) THEN
        RAISE EXCEPTION 'AUTOPILOT_OWNER_WRONG_WORKER_MUTATED';
    END IF;
    IF NOT autopilot.mark_owner_required(
        owner_id, 'sql-worker-1', claimed.lease_epoch, 'ACCOUNT_OWNER_ACTION_REQUIRED'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_OWNER_BOUNDARY_FAILED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = owner_id) <> 'OWNER_REQUIRED' THEN
        RAISE EXCEPTION 'AUTOPILOT_OWNER_STATE_INVALID';
    END IF;

    -- A stale lease is fenced, its attempt is closed, and work is requeued.
    SELECT task_id INTO stale_id
      FROM autopilot.create_shadow_task(
        'sql-stale-1', 'AUTOPILOT_SMOKE_V1', '{}'::jsonb, 20, 0,
        'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    UPDATE autopilot.task SET lease_until = now() - interval '1 second'
     WHERE task_id = stale_id AND lease_epoch = claimed.lease_epoch;
    SELECT * INTO result FROM autopilot.reconcile_stale_tasks();
    IF result.requeued <> 1
       OR (SELECT status FROM autopilot.task WHERE task_id = stale_id) <> 'READY'
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.step_attempt
            WHERE task_id = stale_id AND lease_epoch = claimed.lease_epoch
              AND status = 'FAILED_CLOSED' AND error_code = 'STALE_LEASE_RECOVERED'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_STALE_LEASE_RECOVERY_FAILED';
    END IF;
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> stale_id OR claimed.lease_epoch <> 2
       OR NOT autopilot.complete_task(
           stale_id, 'sql-worker-1', claimed.lease_epoch,
           'SYNTHETIC_SHADOW_COMPLETION', repeat('d', 64), '{}'::jsonb
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_STALE_RETRY_COMPLETION_FAILED';
    END IF;

    -- Cost reservation is atomic and stops before a call can exceed the cap.
    SELECT task_id INTO budget_id
      FROM autopilot.create_shadow_task(
        'sql-budget-1', 'AUTOPILOT_SMOKE_V1', '{}'::jsonb, 20, 10,
        'database-test', 'SQL_TEST'
      );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-worker-1', 60);
    IF claimed.task_id <> budget_id THEN RAISE EXCEPTION 'AUTOPILOT_BUDGET_CLAIM_FAILED'; END IF;
    IF autopilot.reserve_usage(
        budget_id, 'sql-worker-1', claimed.lease_epoch, 'OPENAI', 11, 'sql-budget-reserve-1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_BUDGET_OVERAGE_ACCEPTED';
    END IF;
    IF (SELECT status FROM autopilot.task WHERE task_id = budget_id) <> 'BUDGET_STOP' THEN
        RAISE EXCEPTION 'AUTOPILOT_BUDGET_STOP_FAILED';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.step_attempt
         WHERE task_id = budget_id AND status = 'RUNNING'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_BUDGET_STEP_LEFT_RUNNING';
    END IF;

    -- Canonical journals are append-only.
    BEGIN
        UPDATE autopilot.task_event SET payload_json = '{}'::jsonb
         WHERE task_id = smoke_id;
    EXCEPTION WHEN OTHERS THEN
        append_only_blocked := SQLERRM LIKE '%AUTOPILOT_APPEND_ONLY%';
    END;
    IF NOT append_only_blocked THEN
        RAISE EXCEPTION 'AUTOPILOT_EVENT_MUTATION_NOT_BLOCKED';
    END IF;

    -- Runtime principal has RPCs and status view only, never direct table writes.
    IF has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'SELECT')
       OR has_table_privilege('autopilot_runtime_principal', 'autopilot.task', 'INSERT')
       OR NOT has_table_privilege('autopilot_runtime_principal', 'autopilot.task_status', 'SELECT')
       OR NOT has_function_privilege(
            'autopilot_runtime_principal', 'autopilot.claim_next_task(text,integer)', 'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_RUNTIME_PRIVILEGE_BOUNDARY_INVALID';
    END IF;

    BEGIN
        PERFORM * FROM autopilot.create_shadow_task(
            'sql-unknown-1', 'ARBITRARY_SHELL', '{}'::jsonb, 20, 0,
            'database-test', 'SQL_TEST');
        RAISE EXCEPTION 'AUTOPILOT_UNKNOWN_CAPABILITY_ACCEPTED';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%AUTOPILOT_CAPABILITY_UNKNOWN%' THEN RAISE; END IF;
    END;
END $$;

ROLLBACK;
