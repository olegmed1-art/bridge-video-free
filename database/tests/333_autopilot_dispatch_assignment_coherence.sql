\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    work_id uuid;
    probe record;
    materialized record;
    claimed record;
    dispatch record;
    assignment record;
    repair_task_id uuid;
    verify_task_id uuid;
    current_head text:=repeat('c',40);
    repaired_head text:=repeat('d',40);
    stale_head text:=repeat('a',40);
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0333_autopilot_dispatch_assignment_coherence'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_COHERENCE_MIGRATION_MISSING';
    END IF;

    -- Reproduce the production defect: the registered concern is a stale
    -- direct REPAIR, while every newly materialized origin dispatch is
    -- READ_ONLY and bound to the newly observed head.
    SELECT work_item_id INTO work_id
      FROM autopilot.register_universal_work_item(
        'sql-assignment-coherence-333','AUTOPILOT','REPOSITORY_REPAIR',
        'Repair the stale target at exact head '||stale_head||'.',
        1150,0,jsonb_build_object(
          'repository','olegmed1-art/bridge-video-free',
          'target_pr',1150,
          'expected_head_sha',stale_head,
          'execution_mode','REPAIR',
          'repository_mutation',true,
          'required_changes',jsonb_build_array('repair one bounded file'),
          'focus_paths',jsonb_build_array('oracle_autopilot/worker.py')
        ),NULL,'database-test','SQL_TEST'
      );
    SELECT * INTO probe
      FROM autopilot.claim_project_work_probe('sql-assignment-planner-333',60);
    IF probe.work_item_id IS DISTINCT FROM work_id THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_WORK_NOT_CLAIMED';
    END IF;
    SELECT * INTO materialized
      FROM autopilot.materialize_project_work_probe(
        work_id,'sql-assignment-planner-333',probe.lease_epoch,true,current_head
      );
    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-assignment-worker-333-audit',60);
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-assignment-worker-333-audit',claimed.lease_epoch
      );
    SELECT * INTO assignment
      FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    IF assignment.dispatch_id IS DISTINCT FROM dispatch.dispatch_id
       OR assignment.task_kind IS DISTINCT FROM 'REPOSITORY_AUDIT'
       OR position('READ_ONLY' IN assignment.objective)=0
       OR position(current_head IN assignment.objective)=0
       OR position(stale_head IN assignment.objective)>0
       OR assignment.task_spec_json->>'execution_mode'
            IS DISTINCT FROM 'READ_ONLY'
       OR assignment.task_spec_json->>'expected_head_sha'
            IS DISTINCT FROM current_head
       OR assignment.task_spec_json->>'source_task_kind'
            IS DISTINCT FROM 'REPOSITORY_REPAIR'
       OR assignment.task_spec_json->'repository_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR assignment.task_spec_json->'production_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR assignment.task_spec_json->'neon_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR assignment.task_spec_json->'canon_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR assignment.task_spec_json->'required_changes'
            IS DISTINCT FROM jsonb_build_array('repair one bounded file')
       OR octet_length(assignment.task_spec_json::text)>4096 THEN
        RAISE EXCEPTION 'AUTOPILOT_READ_ONLY_ASSIGNMENT_NOT_CANONICAL: %',
            row_to_json(assignment);
    END IF;

    -- A repair is never inferred from the registered concern.  It is exposed
    -- only through the one-attempt follow-up lineage and a repair-capable role.
    repair_task_id:=autopilot.materialize_role_repair(
        materialized.task_id,'BOUNDED_DEFECT','One bounded defect is confirmed.'
    );
    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-assignment-worker-333-repair',60);
    IF claimed.task_id IS DISTINCT FROM repair_task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_FOLLOWUP_NOT_CLAIMED';
    END IF;
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-assignment-worker-333-repair',claimed.lease_epoch
      );
    SELECT * INTO assignment
      FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    IF assignment.task_kind IS DISTINCT FROM 'REPOSITORY_REPAIR'
       OR assignment.task_spec_json->>'execution_mode'
            IS DISTINCT FROM 'REPAIR'
       OR assignment.task_spec_json->>'expected_head_sha'
            IS DISTINCT FROM current_head
       OR assignment.task_spec_json->'repository_mutation'
            IS DISTINCT FROM 'true'::jsonb
       OR assignment.task_spec_json->>'blocked_result_code'
            IS DISTINCT FROM 'BOUNDED_DEFECT'
       OR position('One bounded defect is confirmed.' IN assignment.objective)=0
       OR octet_length(assignment.task_spec_json::text)>4096 THEN
        RAISE EXCEPTION 'AUTOPILOT_REPAIR_ASSIGNMENT_NOT_CANONICAL';
    END IF;

    -- A mismatched work-lineage mode must disappear from the dispatch surface.
    UPDATE autopilot.project_work_task SET run_kind='VERIFY'
     WHERE task_id=repair_task_id;
    IF EXISTS (
        SELECT 1 FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id)
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_LINEAGE_MISMATCH_EXPOSED';
    END IF;
    UPDATE autopilot.project_work_task SET run_kind='REPAIR'
     WHERE task_id=repair_task_id;

    -- Verification is separately current-head-bound and mutation-free.
    verify_task_id:=autopilot.materialize_role_verification(
        repair_task_id,repaired_head
    );
    SELECT * INTO claimed
      FROM autopilot.claim_next_task('sql-assignment-worker-333-verify',60);
    IF claimed.task_id IS DISTINCT FROM verify_task_id THEN
        RAISE EXCEPTION 'AUTOPILOT_VERIFY_FOLLOWUP_NOT_CLAIMED';
    END IF;
    SELECT * INTO dispatch
      FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-assignment-worker-333-verify',claimed.lease_epoch
      );
    SELECT * INTO assignment
      FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    IF assignment.task_kind IS DISTINCT FROM 'REPOSITORY_VERIFY'
       OR assignment.task_spec_json->>'execution_mode'
            IS DISTINCT FROM 'VERIFY'
       OR assignment.task_spec_json->>'expected_head_sha'
            IS DISTINCT FROM repaired_head
       OR assignment.task_spec_json->'repository_mutation'
            IS DISTINCT FROM 'false'::jsonb
       OR position(repaired_head IN assignment.objective)=0
       OR octet_length(assignment.task_spec_json::text)>4096 THEN
        RAISE EXCEPTION 'AUTOPILOT_VERIFY_ASSIGNMENT_NOT_CANONICAL';
    END IF;

    IF has_function_privilege(
        'autopilot_runtime',
        'autopilot.get_dispatch_assignment(uuid)','EXECUTE'
    ) OR has_function_privilege(
        'autopilot_callback',
        'autopilot.get_dispatch_assignment(uuid)','EXECUTE'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_READER_PRIVILEGE_WIDENED';
    END IF;
END $$;

ROLLBACK;
