\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0332_autopilot_codex_event_cycle'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ASSIGNMENT_COHERENCE_REQUIRES_0332';
    END IF;
END $$;

-- Keep rollback exact.  The assignment reader is the only database surface
-- changed by this migration; no queued task or retained receipt is rewritten.
CREATE TABLE autopilot.migration_0333_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);
INSERT INTO autopilot.migration_0333_function_backup(
    function_key,function_definition
)
SELECT 'get_dispatch_assignment',pg_get_functiondef(
    'autopilot.get_dispatch_assignment(uuid)'::regprocedure
);
REVOKE ALL ON TABLE autopilot.migration_0333_function_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- A project work item describes the registered concern.  It is not itself an
-- executable command: old rows may contain a stale REPAIR objective while a
-- fresh initial dispatch is necessarily READ_ONLY.  Build the executable
-- assignment exclusively from the immutable outbox mode/current-head binding,
-- and copy only bounded, non-authority focus metadata from the work item.
CREATE OR REPLACE FUNCTION autopilot.get_dispatch_assignment(p_dispatch_id uuid)
RETURNS TABLE(
    dispatch_id uuid, task_id uuid, role text, execution_scope text,
    can_repair boolean, task_kind text, objective text, task_spec_json jsonb,
    target_chat_id text, target_chat_name text, target_chat_url text,
    executor_id text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
WITH assignment_source AS (
    SELECT
        outbox.dispatch_id,outbox.task_id,outbox.role,outbox.repository,
        outbox.target_pr,outbox.expected_head_sha,outbox.mode,
        outbox.repair_attempt,outbox.blocked_result_code,
        outbox.blocked_summary,roles.execution_scope,roles.can_repair,
        mapping.run_kind,work.work_key,work.task_kind AS source_task_kind,
        COALESCE(work.task_spec_json,'{}'::jsonb) AS source_task_spec,
        chat.chat_id,chat.chat_name,chat.chat_url,chat.executor_id
    FROM autopilot.role_dispatch_outbox AS outbox
    JOIN autopilot.role_registry AS roles
      ON roles.role_id=outbox.role AND roles.enabled
    LEFT JOIN autopilot.role_chat_registry AS chat
      ON chat.role_id=outbox.role AND chat.enabled
    LEFT JOIN autopilot.project_work_task AS mapping
      ON mapping.task_id=outbox.task_id
    LEFT JOIN autopilot.project_work_item AS work
      ON work.work_item_id=mapping.work_item_id
    WHERE outbox.dispatch_id=p_dispatch_id
), focus_context AS (
    SELECT source.*,
        jsonb_strip_nulls(jsonb_build_object(
            'work_key',source.work_key,
            'source_task_kind',source.source_task_kind,
            'focus_path',source.source_task_spec->'focus_path',
            'focus_paths',source.source_task_spec->'focus_paths',
            'repair_unit',source.source_task_spec->'repair_unit',
            'required_checks',source.source_task_spec->'required_checks',
            'required_changes',source.source_task_spec->'required_changes',
            'preserve',source.source_task_spec->'preserve',
            'review_thread_id',source.source_task_spec->'review_thread_id',
            'review_thread_url',source.source_task_spec->'review_thread_url',
            'verification_kind',source.source_task_spec->'verification_kind',
            'expected_changed_files',source.source_task_spec->'expected_changed_files',
            'canary',source.source_task_spec->'canary',
            'source',source.source_task_spec->'source',
            'failed_role',source.source_task_spec->'failed_role',
            'failed_result_code',source.source_task_spec->'failed_result_code',
            'failed_dispatch_id',source.source_task_spec->'failed_dispatch_id',
            'origin_result_code',source.source_task_spec->'origin_result_code'
        )) AS candidate_focus
    FROM assignment_source AS source
), bounded_context AS (
    SELECT context.*,
        CASE
          WHEN octet_length(context.candidate_focus::text)<=2048
          THEN context.candidate_focus
          ELSE jsonb_strip_nulls(jsonb_build_object(
              'work_key',context.work_key,
              'source_task_kind',context.source_task_kind,
              'source_task_spec_sha256',encode(public.digest(
                  convert_to(context.source_task_spec::text,'UTF8'),'sha256'
              ),'hex')
          ))
        END AS bounded_focus
    FROM focus_context AS context
)
SELECT
    source.dispatch_id,source.task_id,source.role,source.execution_scope,
    source.can_repair,
    CASE source.mode
      WHEN 'REPAIR' THEN 'REPOSITORY_REPAIR'
      WHEN 'VERIFY' THEN 'REPOSITORY_VERIFY'
      ELSE 'REPOSITORY_AUDIT'
    END AS task_kind,
    CASE source.mode
      WHEN 'REPAIR' THEN format(
          'Apply one bounded repository-only repair to open PR #%s at exact head %s for blocker %s: %s. Stay within task_spec_json; do not merge, deploy, or mutate production, Canon, Neon, servers, media, paid, or external systems.',
          source.target_pr,source.expected_head_sha,
          source.blocked_result_code,source.blocked_summary
      )
      WHEN 'VERIFY' THEN format(
          'Perform a READ_ONLY verification of open PR #%s at exact head %s after the bounded repair for %s. Verify only the bounded focus in task_spec_json, report evidence, and make no changes.',
          source.target_pr,source.expected_head_sha,
          source.blocked_result_code
      )
      ELSE format(
          'Perform a READ_ONLY %s audit of open PR #%s at exact head %s. Investigate only the bounded focus in task_spec_json, report evidence and the smallest repair scope if needed, and make no changes.',
          source.role,source.target_pr,source.expected_head_sha
      )
    END AS objective,
    source.bounded_focus || jsonb_strip_nulls(jsonb_build_object(
        'assignment_schema','SLAVIK_DISPATCH_ASSIGNMENT_V1',
        'repository',source.repository,
        'target_pr',source.target_pr,
        'expected_head_sha',source.expected_head_sha,
        'execution_mode',source.mode,
        'role',source.role,
        'exact_head_binding',true,
        'repository_mutation',source.mode='REPAIR',
        'production_mutation',false,
        'canon_mutation',false,
        'neon_mutation',false,
        'server_mutation',false,
        'external_mutation',false,
        'media_execution',false,
        'paid_action',false,
        'merge',false,
        'deploy',false,
        'cost_cap_microusd',0,
        'repair_attempt',source.repair_attempt,
        'max_repair_attempts',CASE WHEN source.mode='REPAIR' THEN 1 ELSE 0 END,
        'blocked_result_code',source.blocked_result_code,
        'blocked_summary',source.blocked_summary
    )) AS task_spec_json,
    source.chat_id,source.chat_name,source.chat_url,source.executor_id
FROM bounded_context AS source
WHERE (
        source.run_kind IS NULL
        OR (source.mode='READ_ONLY' AND source.run_kind='AUDIT')
        OR (source.mode='REPAIR' AND source.run_kind='REPAIR')
        OR (source.mode='VERIFY' AND source.run_kind='VERIFY')
      )
  AND (
        source.mode<>'REPAIR'
        OR (source.execution_scope='REPOSITORY' AND source.can_repair)
      )
  AND octet_length((
        source.bounded_focus || jsonb_strip_nulls(jsonb_build_object(
            'assignment_schema','SLAVIK_DISPATCH_ASSIGNMENT_V1',
            'repository',source.repository,
            'target_pr',source.target_pr,
            'expected_head_sha',source.expected_head_sha,
            'execution_mode',source.mode,
            'role',source.role,
            'exact_head_binding',true,
            'repository_mutation',source.mode='REPAIR',
            'production_mutation',false,
            'canon_mutation',false,
            'neon_mutation',false,
            'server_mutation',false,
            'external_mutation',false,
            'media_execution',false,
            'paid_action',false,
            'merge',false,
            'deploy',false,
            'cost_cap_microusd',0,
            'repair_attempt',source.repair_attempt,
            'max_repair_attempts',CASE WHEN source.mode='REPAIR' THEN 1 ELSE 0 END,
            'blocked_result_code',source.blocked_result_code,
            'blocked_summary',source.blocked_summary
        ))
      )::text)<=4096
$$;
REVOKE ALL ON FUNCTION autopilot.get_dispatch_assignment(uuid)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0333_autopilot_dispatch_assignment_coherence');
COMMIT;
