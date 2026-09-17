\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
    canary_work_id uuid;
    ordinary_work_id uuid;
    probe record;
    materialized record;
    claimed record;
    dispatch record;
    assignment record;
    repair_task_id uuid;
    run_suffix text := txid_current()::text;
    expected_head text := repeat('6',40);
    original_checks jsonb := '["exact head","UI-visible delivery proof","RUNNING acknowledgement","exactly-once terminal receipt"]'::jsonb;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0346_autopilot_canary_acceptance_guard'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ACCEPTANCE_GUARD_MIGRATION_MISSING';
    END IF;
    IF pg_get_functiondef(
           'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
       ) NOT LIKE '%CANARY_ACCEPTANCE_NO_REPAIR_V1%'
       OR position(
           'controller_verifies_delivery' IN pg_get_functiondef(
               'autopilot.get_dispatch_assignment(uuid)'::regprocedure
           )
       )=0
       OR position(
           'UI-visible dispatch envelope' IN pg_get_functiondef(
               'autopilot.get_dispatch_assignment(uuid)'::regprocedure
           )
       )=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ACCEPTANCE_GUARD_DEFINITION_INVALID';
    END IF;

    SELECT work_item_id INTO canary_work_id
      FROM autopilot.register_universal_work_item(
        'sql-canary-acceptance-346-'||run_suffix,
        'AUTOPILOT','GITHUB_EVENT_RUNTIME_E2E_CANARY',
        'Controller-owned transport acceptance.',
        1150,0,jsonb_build_object(
          'repository','olegmed1-art/bridge-video-free',
          'target_pr',1150,
          'expected_head_sha',expected_head,
          'execution_mode','READ_ONLY',
          'repository_mutation',false,
          'canary','AUTOPILOT_0331_GITHUB_EVENT_RUNTIME',
          'required_checks',original_checks
        ),NULL,'database-test','SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-canary-planner-346',60);
    IF probe.work_item_id IS DISTINCT FROM canary_work_id THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_WORK_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        canary_work_id,'sql-canary-planner-346',probe.lease_epoch,true,expected_head
      );
    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-canary-worker-346',60);
    IF claimed.task_id IS DISTINCT FROM materialized.task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_TASK_NOT_CLAIMED';
    END IF;
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-canary-worker-346',claimed.lease_epoch
      );
    SELECT * INTO assignment
      FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    IF assignment.task_kind IS DISTINCT FROM 'REPOSITORY_AUDIT'
       OR assignment.task_spec_json->>'mailbox_pr' IS DISTINCT FROM '1637'
       OR assignment.task_spec_json->'required_checks'
            IS DISTINCT FROM '["exact target head","UI-visible dispatch envelope"]'::jsonb
       OR assignment.task_spec_json->'controller_postconditions'
            IS DISTINCT FROM original_checks
       OR assignment.task_spec_json->'controller_verifies_delivery'
            IS DISTINCT FROM 'true'::jsonb
       OR assignment.task_spec_json->'max_repair_attempts'
            IS DISTINCT FROM '0'::jsonb
       OR position('active mailbox PR #1637' IN assignment.objective)=0
       OR position('Do not require or attempt to pre-verify' IN assignment.objective)=0
       OR octet_length(assignment.task_spec_json::text)>4096 THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ASSIGNMENT_NOT_CONTROLLER_SPLIT: %',
            row_to_json(assignment);
    END IF;

    repair_task_id:=autopilot.materialize_role_repair(
        materialized.task_id,'RUNTIME_EVIDENCE_INCOMPLETE',
        'Task blocked. See execution details above.'
    );
    IF repair_task_id IS NOT NULL
       OR EXISTS (
           SELECT 1 FROM autopilot.role_dispatch_followup
            WHERE parent_task_id=materialized.task_id
              AND followup_kind='REPAIR'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_CREATED_REPAIR_FOLLOWUP';
    END IF;

    -- Ordinary repository work retains the existing single bounded repair.
    SELECT work_item_id INTO ordinary_work_id
      FROM autopilot.register_universal_work_item(
        'sql-ordinary-repair-346-'||run_suffix,
        'AUTOPILOT','REPOSITORY_AUDIT','Audit one bounded repository concern.',
        1150,0,jsonb_build_object(
          'repository','olegmed1-art/bridge-video-free',
          'target_pr',1150,
          'expected_head_sha',expected_head,
          'execution_mode','READ_ONLY',
          'repository_mutation',false,
          'required_checks',jsonb_build_array('bounded repository concern')
        ),NULL,'database-test','SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-ordinary-planner-346',60);
    IF probe.work_item_id IS DISTINCT FROM ordinary_work_id THEN
        RAISE EXCEPTION 'AUTOPILOT_ORDINARY_WORK_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        ordinary_work_id,'sql-ordinary-planner-346',probe.lease_epoch,true,expected_head
      );
    repair_task_id:=autopilot.materialize_role_repair(
        materialized.task_id,'BOUNDED_REPOSITORY_DEFECT',
        'One bounded repository defect is confirmed.'
    );
    IF repair_task_id IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM autopilot.role_dispatch_followup
            WHERE parent_task_id=materialized.task_id
              AND followup_task_id=repair_task_id
              AND followup_kind='REPAIR'
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ORDINARY_REPAIR_WAS_SUPPRESSED';
    END IF;

    IF has_function_privilege(
        'autopilot_runtime','autopilot.get_dispatch_assignment(uuid)','EXECUTE'
    ) OR has_function_privilege(
        'autopilot_callback','autopilot.materialize_role_repair(uuid,text,text)','EXECUTE'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CANARY_ACCEPTANCE_PRIVILEGE_WIDENED';
    END IF;
END $test$;

SELECT 2 AS cases,0 AS failures;
ROLLBACK;
