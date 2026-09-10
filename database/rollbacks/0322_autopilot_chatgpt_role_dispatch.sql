\set ON_ERROR_STOP on
BEGIN;

-- This rollback is intentionally available only before the bridge has carried
-- production state.  Deleting dispatch or callback evidence would make an
-- apparent rollback unsafe and non-auditable.
DO $$
BEGIN
    IF to_regclass('autopilot.role_dispatch_callback_receipt') IS NOT NULL
       AND EXISTS (SELECT 1 FROM autopilot.role_dispatch_callback_receipt) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_DISPATCH_ROLLBACK_CALLBACKS_PRESENT';
    END IF;
    IF to_regclass('autopilot.role_dispatch_outbox') IS NOT NULL
       AND EXISTS (SELECT 1 FROM autopilot.role_dispatch_outbox) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_DISPATCH_ROLLBACK_OUTBOX_PRESENT';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.task
         WHERE goal_type = 'CHATGPT_ROLE_DISPATCH_V1'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_DISPATCH_ROLLBACK_TASKS_PRESENT';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.evidence
         WHERE metadata_json->>'broker_policy_version' = 'physical-no-merge-v2'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_ROLE_DISPATCH_ROLLBACK_V2_EVIDENCE_PRESENT';
    END IF;
END $$;

DO $migration$
DECLARE
    function_sql text;
    old_validation text := $old$
           OR COALESCE(p_summary->>'broker_policy_version', '') NOT IN (
              'physical-no-merge-v1', 'physical-no-merge-v2'
           )
           OR COALESCE(p_summary->>'broker_source_sha', '') !~$old$;
    new_validation text := $new$
           OR p_summary->>'broker_policy_version' IS DISTINCT FROM 'physical-no-merge-v1'
           OR COALESCE(p_summary->>'broker_source_sha', '') !~$new$;
BEGIN
    SELECT pg_get_functiondef(
        'autopilot.complete_task(uuid,text,bigint,text,text,jsonb)'::regprocedure
    ) INTO function_sql;
    IF function_sql IS NULL OR strpos(function_sql, old_validation) = 0 THEN
        RAISE EXCEPTION 'AUTOPILOT_COMPLETE_TASK_0322_DEFINITION_UNEXPECTED';
    END IF;
    EXECUTE replace(function_sql, old_validation, new_validation);
END
$migration$;

REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_callback(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) FROM autopilot_callback, autopilot_runtime, autopilot_runtime_principal, PUBLIC;
REVOKE ALL ON FUNCTION autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text)
    FROM autopilot_runtime, autopilot_runtime_principal, PUBLIC;
REVOKE ALL ON FUNCTION autopilot.prepare_role_dispatch(uuid,text,bigint)
    FROM autopilot_runtime, PUBLIC;
REVOKE ALL ON FUNCTION autopilot.claim_role_dispatch_outbox(text,integer)
    FROM autopilot_runtime, PUBLIC;
REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text)
    FROM autopilot_runtime, PUBLIC;
REVOKE ALL ON FUNCTION autopilot.fail_role_dispatch_outbox(uuid,text,bigint,text,integer)
    FROM autopilot_runtime, PUBLIC;
REVOKE ALL ON FUNCTION autopilot.reconcile_role_dispatch_callbacks()
    FROM autopilot_runtime, PUBLIC;

DROP FUNCTION autopilot.accept_role_dispatch_callback(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
);
DROP FUNCTION autopilot.create_chatgpt_role_dispatch_task(text,jsonb,integer,text,text);
DROP FUNCTION autopilot.prepare_role_dispatch(uuid,text,bigint);
DROP FUNCTION autopilot.claim_role_dispatch_outbox(text,integer);
DROP FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text);
DROP FUNCTION autopilot.fail_role_dispatch_outbox(uuid,text,bigint,text,integer);
DROP FUNCTION autopilot.reconcile_role_dispatch_callbacks();

DROP TABLE autopilot.role_dispatch_callback_receipt;
DROP TABLE autopilot.role_dispatch_outbox;

ALTER TABLE autopilot.task DROP CONSTRAINT task_goal_type_check;
ALTER TABLE autopilot.task ADD CONSTRAINT task_goal_type_check CHECK (goal_type IN (
    'AUTOPILOT_SMOKE_V1',
    'EXTERNAL_WAIT_SHADOW_V1',
    'OWNER_BOUNDARY_V1',
    'GITHUB_PR_READ_ONLY_V1',
    'GITHUB_CI_READ_ONLY_V1',
    'GITHUB_DRAFT_REPAIR_V1',
    'IBF_READ_ONLY_ANALYSIS'
));

ALTER TABLE autopilot.step_attempt DROP CONSTRAINT step_attempt_capability_name_check;
ALTER TABLE autopilot.step_attempt ADD CONSTRAINT step_attempt_capability_name_check CHECK (
    capability_name IN (
        'shadow.noop', 'shadow.wait', 'policy.owner_boundary',
        'github.pr.snapshot', 'github.ci.snapshot', 'github.draft_repair',
        'ibf.read_only_analysis'
    )
);

ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_evidence_class_check;
ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_evidence_class_check CHECK (
    evidence_class IN (
        'SYNTHETIC_SHADOW_COMPLETION',
        'SYNTHETIC_SHADOW_RESUME',
        'OWNER_BOUNDARY_PROOF',
        'GITHUB_PR_READ_ONLY_SNAPSHOT',
        'GITHUB_CI_READ_ONLY_SNAPSHOT',
        'GITHUB_DRAFT_REPAIR_EVIDENCE',
        'IBF_READ_ONLY_ANALYSIS_EVIDENCE'
    )
);

ALTER TABLE autopilot.evidence DROP CONSTRAINT evidence_provider_check;
ALTER TABLE autopilot.evidence ADD CONSTRAINT evidence_provider_check CHECK (
    provider IN ('ORACLE_RESIDENT', 'NEON_STATE_MACHINE')
);

-- Keep the NOLOGIN group role.  A separately provisioned login may be a
-- member, and silently dropping memberships is outside a schema rollback.
REVOKE USAGE ON SCHEMA autopilot FROM autopilot_callback;

DELETE FROM public.schema_migration
WHERE migration_key = '0322_autopilot_chatgpt_role_dispatch';

COMMIT;
