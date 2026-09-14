\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    blocker_item uuid;
    independent_item uuid;
    dependent_item uuid;
    blocker_task uuid;
    repair_task uuid;
    independent_task uuid;
    dependent_task uuid;
    changed_head_task uuid;
    probe record;
    materialized record;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key = '0324_autopilot_project_planner'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PLANNER_MIGRATION_MISSING';
    END IF;

    SELECT work_item_id INTO blocker_item
      FROM autopilot.register_project_work_item(
          'sql-project-blocker-1', 'RECOGNIZER', 1106, 0, NULL,
          'database-test', 'SQL_TEST'
      );
    SELECT work_item_id INTO independent_item
      FROM autopilot.register_project_work_item(
          'sql-project-independent-1', 'KNOWLEDGE', 1129, 10, NULL,
          'database-test', 'SQL_TEST'
      );
    SELECT work_item_id INTO dependent_item
      FROM autopilot.register_project_work_item(
          'sql-project-dependent-1', 'VIDEO', 1125, 20,
          'sql-project-independent-1', 'database-test', 'SQL_TEST'
      );

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-project-worker-1', 60);
    IF probe.work_item_id <> blocker_item
       OR probe.prior_state <> 'READY' THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PRIORITY_SELECTION_INVALID';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          probe.work_item_id, 'sql-project-worker-1', probe.lease_epoch,
          true, repeat('a', 40)
      );
    blocker_task := materialized.task_id;
    IF blocker_task IS NULL
       OR materialized.resulting_state <> 'ACTIVE'
       OR materialized.created IS NOT true THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_TASK_NOT_MATERIALIZED';
    END IF;

    -- A technical failure produces exactly one repair and keeps the global
    -- lane serialized while that repair is runnable.
    UPDATE autopilot.task
       SET status = 'FAILED_CLOSED',
           terminal_reason_code = 'TECHNICAL_TEST_FAILURE',
           safe_summary_json = jsonb_build_object(
               'status', 'BLOCKED',
               'result_code', 'TECHNICAL_TEST_FAILURE',
               'target_head_sha', repeat('a', 40),
               'summary', 'Focused technical verification failed.'
           ),
           completed_at = now()
     WHERE task_id = blocker_task;
    SELECT followup_task_id INTO repair_task
      FROM autopilot.role_dispatch_followup
     WHERE parent_task_id = blocker_task AND followup_kind = 'REPAIR';
    IF repair_task IS NULL
       OR (SELECT state FROM autopilot.project_work_item
            WHERE work_item_id = blocker_item) <> 'ACTIVE'
       OR (SELECT count(*) FROM autopilot.project_work_task
            WHERE work_item_id = blocker_item AND run_kind = 'REPAIR') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_REPAIR_NOT_TRACKED';
    END IF;
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-project-worker-2', 60);
    IF FOUND THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_FANNED_OUT_DURING_REPAIR';
    END IF;

    -- An owner-only repair result is retained, but it releases the planner to
    -- the next unrelated READY item instead of stopping the project.
    UPDATE autopilot.task
       SET status = 'FAILED_CLOSED',
           terminal_reason_code = 'OWNER_HOLDOUT_EVIDENCE_REQUIRED',
           safe_summary_json = jsonb_build_object(
               'status', 'BLOCKED',
               'result_code', 'OWNER_HOLDOUT_EVIDENCE_REQUIRED',
               'target_head_sha', repeat('a', 40),
               'summary', 'Independent holdout evidence requires owner input.'
           ),
           completed_at = now()
     WHERE task_id = repair_task;
    IF (SELECT state FROM autopilot.project_work_item
         WHERE work_item_id = blocker_item) <> 'BLOCKED' THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_BLOCKER_NOT_RETAINED';
    END IF;

    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-project-worker-3', 60);
    IF probe.work_item_id <> independent_item THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_INDEPENDENT_WORK_NOT_SELECTED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          probe.work_item_id, 'sql-project-worker-3', probe.lease_epoch,
          true, repeat('b', 40)
      );
    independent_task := materialized.task_id;
    UPDATE autopilot.task
       SET status = 'DONE', terminal_reason_code = 'KNOWLEDGE_READY',
           safe_summary_json = jsonb_build_object(
               'status', 'SUCCEEDED', 'result_code', 'KNOWLEDGE_READY',
               'target_head_sha', repeat('b', 40),
               'summary', 'Independent knowledge task completed.'
           ), completed_at = now()
     WHERE task_id = independent_task;
    IF (SELECT state FROM autopilot.project_work_item
         WHERE work_item_id = independent_item) <> 'DONE' THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_COMPLETION_NOT_RECORDED';
    END IF;

    -- Dependency gating opens only after the prerequisite is DONE.
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-project-worker-4', 60);
    IF probe.work_item_id <> dependent_item THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_DEPENDENCY_NOT_RELEASED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          probe.work_item_id, 'sql-project-worker-4', probe.lease_epoch,
          true, repeat('c', 40)
      );
    dependent_task := materialized.task_id;
    UPDATE autopilot.task
       SET status = 'DONE', terminal_reason_code = 'VIDEO_READY',
           safe_summary_json = jsonb_build_object(
               'status', 'SUCCEEDED', 'result_code', 'VIDEO_READY',
               'target_head_sha', repeat('c', 40),
               'summary', 'Dependent video task completed.'
           ), completed_at = now()
     WHERE task_id = dependent_task;

    -- The unchanged blocked head is never redelivered.  A genuinely changed
    -- head reactivates the same lane with a new exact-head task.
    UPDATE autopilot.project_work_item
       SET not_before = now() WHERE work_item_id = blocker_item;
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-project-worker-5', 60);
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          probe.work_item_id, 'sql-project-worker-5', probe.lease_epoch,
          true, repeat('a', 40)
      );
    IF materialized.task_id IS NOT NULL
       OR materialized.created IS NOT false
       OR (SELECT count(*) FROM autopilot.project_work_task
            WHERE work_item_id = blocker_item AND run_kind = 'AUDIT') <> 1 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_UNCHANGED_HEAD_REDISPATCHED';
    END IF;

    UPDATE autopilot.project_work_item
       SET not_before = now() WHERE work_item_id = blocker_item;
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-project-worker-6', 60);
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
          probe.work_item_id, 'sql-project-worker-6', probe.lease_epoch,
          true, repeat('d', 40)
      );
    changed_head_task := materialized.task_id;
    IF changed_head_task IS NULL
       OR (SELECT goal_json->>'expected_head_sha' FROM autopilot.task
            WHERE task_id = changed_head_task) <> repeat('d', 40)
       OR (SELECT generation FROM autopilot.project_work_item
            WHERE work_item_id = blocker_item) <> 2 THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_CHANGED_HEAD_NOT_REACTIVATED';
    END IF;

    IF has_table_privilege(
           'autopilot_runtime_principal',
           'autopilot.project_work_item', 'SELECT'
       )
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.claim_project_work_probe(text,integer)', 'EXECUTE'
       )
       OR NOT has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)',
           'EXECUTE'
       )
       OR has_function_privilege(
           'autopilot_runtime_principal',
           'autopilot.register_project_work_item(text,text,integer,integer,text,text,text)',
           'EXECUTE'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PLANNER_PRIVILEGE_INVALID';
    END IF;
END $$;

ROLLBACK;
