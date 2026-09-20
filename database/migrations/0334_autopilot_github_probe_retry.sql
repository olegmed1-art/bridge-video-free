\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0333_autopilot_dispatch_assignment_coherence'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_GITHUB_PROBE_RETRY_REQUIRES_0333';
    END IF;
END $$;

-- Preserve the exact function body and the retry windows changed below.  A
-- rollback restores a work row only while no worker has advanced its lineage.
CREATE TABLE autopilot.migration_0334_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);
INSERT INTO autopilot.migration_0334_function_backup(
    function_key,function_definition
)
SELECT 'project_work_transport_retryable',pg_get_functiondef(
    'autopilot.project_work_transport_retryable(text)'::regprocedure
);

CREATE TABLE autopilot.migration_0334_work_rearm_backup (
    work_item_id uuid PRIMARY KEY,
    previous_not_before timestamptz NOT NULL,
    previous_updated_at timestamptz NOT NULL,
    generation integer NOT NULL,
    last_task_id uuid
);
INSERT INTO autopilot.migration_0334_work_rearm_backup(
    work_item_id,previous_not_before,previous_updated_at,generation,last_task_id
)
SELECT work_item_id,not_before,updated_at,generation,last_task_id
  FROM autopilot.project_work_item
 WHERE state='BLOCKED'
   AND result_code='GITHUB_API_TRANSIENT_ERROR';

REVOKE ALL ON TABLE autopilot.migration_0334_function_backup,
    autopilot.migration_0334_work_rearm_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- The worker deliberately classifies a bounded project-head broker failure as
-- retryable.  The planner must retain that classification after the next
-- successful head probe; otherwise a previously BLOCKED lane is mistaken for
-- a semantic same-head blocker and is postponed forever.
CREATE OR REPLACE FUNCTION autopilot.project_work_transport_retryable(
    p_result_code text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT COALESCE(p_result_code IN (
        'STALE_RETRY_BUDGET_EXHAUSTED',
        'ROLE_DISPATCH_CLAIM_EXPIRED',
        'ROLE_DISPATCH_DELIVERY_EXHAUSTED',
        'ROLE_CALLBACK_DEADLINE_EXCEEDED',
        'AUTOPILOT_TRANSIENT_DATABASE_ERROR',
        'GITHUB_API_TRANSIENT_ERROR',
        'CODEX_ACK_DEADLINE_EXCEEDED',
        'CODEX_RESULT_DEADLINE_EXCEEDED'
    ),false)
$$;
REVOKE ALL ON FUNCTION autopilot.project_work_transport_retryable(text)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- Capability restoration is an event.  Re-arm only the rows carrying this
-- exact transport code; semantic blockers, active tasks, and completed work
-- are untouched.  Six-worker admission remains enforced by the claim RPC.
UPDATE autopilot.project_work_item
   SET not_before=LEAST(not_before,now()),
       updated_at=now()
 WHERE state='BLOCKED'
   AND result_code='GITHUB_API_TRANSIENT_ERROR';

SELECT pg_notify('autopilot_ready','github-project-head-transport-restored')
 WHERE EXISTS (
    SELECT 1 FROM autopilot.migration_0334_work_rearm_backup
 );

INSERT INTO public.schema_migration(migration_key)
VALUES ('0334_autopilot_github_probe_retry');
COMMIT;
