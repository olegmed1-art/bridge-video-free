\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    i integer;
    probe record;
    materialized record;
    capacity record;
    p0_id uuid;
    p0_task_id uuid;
    unleased_normal_id uuid;
    null_lease_rejected boolean := false;
    priority_mutation_rejected boolean := false;
    overflow_rejected boolean := false;
    mailbox_pr integer := 1150;
BEGIN
    IF to_regclass('autopilot.role_dispatch_mailbox_registry') IS NOT NULL THEN
        SELECT registry.mailbox_pr INTO STRICT mailbox_pr
          FROM autopilot.role_dispatch_mailbox_registry AS registry
         WHERE registry.lifecycle='ACTIVE';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0331_autopilot_six_worker_capacity'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_SIX_WORKER_MIGRATION_MISSING';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM autopilot.role_registry AS role
          LEFT JOIN autopilot.role_chat_registry AS chat
            ON chat.role_id = role.role_id AND chat.enabled
         WHERE role.enabled
           AND role.role_id <> 'PLANNING'
           AND (
               chat.role_id IS NULL
               OR chat.chat_id <> '6aa6a4c0-4858-83eb-872c-4bc3451edc83'
               OR chat.chat_url <> 'https://chatgpt.com/c/6aa6a4c0-4858-83eb-872c-4bc3451edc83'
               OR chat.executor_id <> 'chat:6aa6a4c0-4858-83eb-872c-4bc3451edc83'
           )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ENABLED_ROLE_NOT_ROUTED_TO_SLAVIK';
    END IF;

    IF (SELECT max_active_workers FROM autopilot.project_planner_state WHERE singleton) <> 6
       OR (SELECT max_normal_workers FROM autopilot.project_planner_state WHERE singleton) <> 5 THEN
        RAISE EXCEPTION 'AUTOPILOT_WORKER_POLICY_NOT_SIX_PLUS_P0_RESERVE';
    END IF;

    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE goal_type IN (
             'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
         )
           AND status IN (
             'NEW', 'VALIDATING', 'READY', 'RUNNING',
             'WAITING_EXTERNAL', 'EVALUATING'
           )
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_SIX_WORKER_TEST_REQUIRES_IDLE_BASELINE';
    END IF;

    -- Fill the five normal slots.
    FOR i IN 1..6 LOOP
        PERFORM * FROM autopilot.register_universal_work_item(
            'sql-six-worker-normal-' || i,
            'AUTOPILOT',
            'SIX_WORKER_CAPACITY_TEST',
            'Prove bounded normal worker admission.',
            1600 + i,
            10,
            jsonb_build_object('fixture', i),
            NULL,
            'database-test',
            'SQL_TEST'
        );
    END LOOP;

    FOR i IN 1..5 LOOP
        SELECT * INTO probe
          FROM autopilot.claim_project_work_probe('sql-six-worker-' || i, 60);
        IF NOT FOUND OR probe.work_key NOT LIKE 'sql-six-worker-normal-%' THEN
            RAISE EXCEPTION 'AUTOPILOT_NORMAL_SLOT_NOT_CLAIMED_%', i;
        END IF;
        SELECT * INTO materialized
          FROM autopilot.materialize_project_work_probe(
              probe.work_item_id,
              'sql-six-worker-' || i,
              probe.lease_epoch,
              true,
              repeat(to_hex(i), 40)
          );
        IF materialized.resulting_state <> 'ACTIVE' OR NOT materialized.created THEN
            RAISE EXCEPTION 'AUTOPILOT_NORMAL_SLOT_NOT_MATERIALIZED_%', i;
        END IF;
    END LOOP;

    SELECT work_item_id INTO unleased_normal_id
      FROM autopilot.project_work_item
     WHERE work_key LIKE 'sql-six-worker-normal-%'
       AND state = 'READY';
    IF unleased_normal_id IS NULL THEN
        RAISE EXCEPTION 'AUTOPILOT_UNLEASED_NORMAL_FIXTURE_MISSING';
    END IF;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-six-worker-normal-blocked', 60);
    IF FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_SIXTH_NORMAL_CONSUMED_P0_RESERVE';
    END IF;
    IF (SELECT last_decision_code FROM autopilot.project_planner_state WHERE singleton)
       <> 'WAITING_FOR_P0_RESERVED_WORKER' THEN
        RAISE EXCEPTION 'AUTOPILOT_P0_RESERVE_WAIT_REASON_MISSING';
    END IF;

    -- A P0 item may consume the sixth slot even while normal work is waiting.
    SELECT work_item_id INTO p0_id
      FROM autopilot.register_universal_work_item(
          'sql-six-worker-p0',
          'AUTOPILOT',
          'SIX_WORKER_CAPACITY_TEST',
          'Prove admission to the reserved P0 worker slot.',
          1690,
          0,
          '{"fixture":"p0"}'::jsonb,
          NULL,
          'database-test',
          'SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-six-worker-p0', 60);
    IF NOT FOUND OR probe.work_item_id <> p0_id THEN
        RAISE EXCEPTION 'AUTOPILOT_P0_RESERVED_SLOT_NOT_CLAIMED';
    END IF;

    -- A held P0 reservation must not authorize a different unleased normal
    -- item through NULL worker/epoch arguments.
    BEGIN
        PERFORM * FROM autopilot.materialize_project_work_probe(
            unleased_normal_id,
            NULL,
            0,
            true,
            repeat('c', 40)
        );
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'AUTOPILOT_PROJECT_PROBE_FENCED' THEN
            null_lease_rejected := true;
        ELSE
            RAISE;
        END IF;
    END;
    IF NOT null_lease_rejected THEN
        RAISE EXCEPTION 'AUTOPILOT_NULL_LEASE_RESERVATION_BYPASS_NOT_REJECTED';
    END IF;

    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          probe.work_item_id,
          'sql-six-worker-p0',
          probe.lease_epoch,
          true,
          repeat('a', 40)
      );
    IF materialized.resulting_state <> 'ACTIVE' OR NOT materialized.created THEN
        RAISE EXCEPTION 'AUTOPILOT_P0_RESERVED_SLOT_NOT_MATERIALIZED';
    END IF;
    p0_task_id := materialized.task_id;

    SELECT * INTO capacity FROM autopilot.role_worker_capacity_snapshot();
    IF capacity.max_active_workers <> 6
       OR capacity.max_normal_workers <> 5
       OR capacity.active_workers <> 6
       OR capacity.active_normal_workers <> 5
       OR capacity.probe_reservations <> 0
       OR capacity.available_workers <> 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_SIX_WORKER_CAPACITY_SNAPSHOT_INVALID';
    END IF;

    -- Active identity changes cannot silently turn the P0 worker into a sixth
    -- normal worker.
    BEGIN
        UPDATE autopilot.task SET priority = 10 WHERE task_id = p0_task_id;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'AUTOPILOT_ACTIVE_ROLE_IDENTITY_IMMUTABLE' THEN
            priority_mutation_rejected := true;
        ELSE
            RAISE;
        END IF;
    END;
    IF NOT priority_mutation_rejected THEN
        RAISE EXCEPTION 'AUTOPILOT_ACTIVE_PRIORITY_MUTATION_NOT_REJECTED';
    END IF;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-six-worker-overflow', 60);
    IF FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_SEVENTH_WORKER_WAS_CLAIMED';
    END IF;
    IF (SELECT last_decision_code FROM autopilot.project_planner_state WHERE singleton)
       <> 'WAITING_FOR_WORKER_CAPACITY' THEN
        RAISE EXCEPTION 'AUTOPILOT_GLOBAL_CAPACITY_WAIT_REASON_MISSING';
    END IF;

    -- The task-level guard closes the internal bypass path as well.
    BEGIN
        PERFORM * FROM autopilot.create_chatgpt_role_dispatch_task(
            'sql-six-worker-direct-overflow',
            jsonb_build_object(
                'repository', 'olegmed1-art/bridge-video-free',
                'mailbox_pr', mailbox_pr,
                'role', 'AUTOPILOT',
                'target_pr', 1699,
                'expected_head_sha', repeat('b', 40),
                'dispatch_epoch', 1,
                'successor_task_key', NULL,
                'successor_role', NULL,
                'successor_target_pr', NULL,
                'successor_expected_head_sha', NULL
            ),
            0,
            'database-test',
            'SQL_TEST'
        );
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM = 'AUTOPILOT_ROLE_WORKER_CAPACITY_EXHAUSTED' THEN
            overflow_rejected := true;
        ELSE
            RAISE;
        END IF;
    END;
    IF NOT overflow_rejected THEN
        RAISE EXCEPTION 'AUTOPILOT_DIRECT_CAPACITY_BYPASS_NOT_REJECTED';
    END IF;

    IF (SELECT count(*) FROM autopilot.task
         WHERE goal_type IN (
             'CHATGPT_ROLE_DISPATCH_V1', 'CHATGPT_ROLE_FOLLOWUP_V1'
         )
           AND status IN (
             'NEW', 'VALIDATING', 'READY', 'RUNNING',
             'WAITING_EXTERNAL', 'EVALUATING'
           )) <> 6 THEN
        RAISE EXCEPTION 'AUTOPILOT_ACTIVE_ROLE_TASK_COUNT_NOT_SIX';
    END IF;
END $$;

ROLLBACK;
