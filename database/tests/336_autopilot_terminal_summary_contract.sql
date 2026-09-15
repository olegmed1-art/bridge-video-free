\set ON_ERROR_STOP on
BEGIN;
-- Isolated SQL CI fixture only; all fixture changes roll back.
DO $test$
DECLARE work_id uuid; probe record; claimed record; dispatch record; assignment record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.schema_migration
                   WHERE migration_key='0336_autopilot_terminal_summary_contract') THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_MIGRATION_MISSING';
    END IF;
    SELECT work_item_id INTO work_id FROM autopilot.register_universal_work_item(
        'sql-summary-contract-336','AUTOPILOT','REPOSITORY_AUDIT',
        'Read-only bounded assignment test.',1150,0,
        jsonb_build_object(
            'required_checks',jsonb_build_array(repeat('c',1800)),
            'terminal_summary_contract',jsonb_build_object('SUCCEEDED','password')
        ),NULL,'database-test','SQL_TEST'
    );
    SELECT * INTO probe FROM autopilot.claim_project_work_probe('sql-summary-planner-336',60);
    IF probe.work_item_id IS DISTINCT FROM work_id THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_FIXTURE_NOT_CLAIMED';
    END IF;
    PERFORM autopilot.materialize_project_work_probe(
        work_id,'sql-summary-planner-336',probe.lease_epoch,true,repeat('c',40)
    );
    SELECT * INTO claimed FROM autopilot.claim_next_task('sql-summary-worker-336',60);
    SELECT * INTO dispatch FROM autopilot.prepare_role_dispatch(
        claimed.task_id,'sql-summary-worker-336',claimed.lease_epoch
    );
    SELECT * INTO assignment FROM autopilot.get_dispatch_assignment(dispatch.dispatch_id);
    IF assignment.dispatch_id IS DISTINCT FROM dispatch.dispatch_id
       OR assignment.task_spec_json->'terminal_summary_contract'->>'SUCCEEDED'
          IS DISTINCT FROM 'Task completed. See execution details above.'
       OR assignment.task_spec_json->'terminal_summary_contract'->>'BLOCKED'
          IS DISTINCT FROM 'Task blocked. See execution details above.'
       OR assignment.task_spec_json->>'execution_mode' IS DISTINCT FROM 'READ_ONLY'
       OR assignment.task_spec_json->>'expected_head_sha' IS DISTINCT FROM repeat('c',40)
       OR assignment.task_spec_json->'repository_mutation' IS DISTINCT FROM 'false'::jsonb
       OR assignment.task_spec_json->'production_mutation' IS DISTINCT FROM 'false'::jsonb
       OR assignment.task_spec_json->'external_mutation' IS DISTINCT FROM 'false'::jsonb
       OR octet_length(assignment.task_spec_json::text)>4096 THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_NOT_CANONICAL_OR_BOUND_EXCEEDED';
    END IF;
    IF has_function_privilege('autopilot_runtime',
           'autopilot.get_dispatch_assignment(uuid)','EXECUTE')
       OR has_function_privilege('autopilot_callback',
           'autopilot.get_dispatch_assignment(uuid)','EXECUTE') THEN
        RAISE EXCEPTION 'SUMMARY_CONTRACT_READER_PRIVILEGE_WIDENED';
    END IF;
END $test$;
ROLLBACK;
