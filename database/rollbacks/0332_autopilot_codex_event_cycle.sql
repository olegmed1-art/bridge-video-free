\set ON_ERROR_STOP on
BEGIN;

SELECT pg_advisory_xact_lock(
    hashtextextended('autopilot.role-worker-capacity-v1',0)
);
LOCK TABLE autopilot.role_dispatch_outbox IN ACCESS EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_codex_delivery_proof IN ACCESS EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_codex_terminal_receipt IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF (SELECT count(*) FROM autopilot.migration_0332_function_backup)<>5 THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ROLLBACK_BACKUP_INVALID';
    END IF;
    IF EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_outbox
         WHERE delivery_contract_version=3
    ) OR EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_codex_delivery_proof
    ) OR EXISTS (
        SELECT 1 FROM autopilot.role_dispatch_codex_terminal_receipt
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_ROLLBACK_REQUIRES_EMPTY_V3_STATE';
    END IF;
END $$;

DROP FUNCTION autopilot.accept_role_dispatch_codex_terminal(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
);
DROP FUNCTION autopilot.accept_role_dispatch_codex_ack(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb
);
DROP TABLE autopilot.role_dispatch_codex_terminal_receipt;
DROP TABLE autopilot.role_dispatch_codex_delivery_proof;

ALTER TABLE autopilot.role_dispatch_outbox
    DROP CONSTRAINT role_dispatch_delivery_contract_version_check,
    ALTER COLUMN delivery_contract_version SET DEFAULT 2,
    ADD CONSTRAINT role_dispatch_delivery_contract_version_check
        CHECK (delivery_contract_version IN (1,2)),
    DROP COLUMN codex_command_pr,
    DROP COLUMN codex_command_comment_id,
    DROP COLUMN codex_ack_reaction_id,
    DROP COLUMN codex_ack_at;

DO $$
DECLARE
    backed_up record;
BEGIN
    FOR backed_up IN
        SELECT function_definition
          FROM autopilot.migration_0332_function_backup
         ORDER BY function_key
    LOOP
        EXECUTE backed_up.function_definition;
    END LOOP;
END $$;

-- Restore the exact least-privilege boundaries that existed after 0331.
REVOKE ALL ON FUNCTION autopilot.get_dispatch_assignment(uuid)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) TO autopilot_runtime;
REVOKE ALL ON FUNCTION autopilot.claim_project_work_probe(text,integer)
FROM PUBLIC,autopilot_callback;
GRANT EXECUTE ON FUNCTION autopilot.claim_project_work_probe(text,integer)
TO autopilot_runtime,autopilot_runtime_principal;
REVOKE ALL ON FUNCTION autopilot.project_work_transport_retryable(text)
FROM PUBLIC;
REVOKE ALL ON FUNCTION autopilot.reconcile_role_dispatch_callbacks()
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.reconcile_role_dispatch_callbacks()
TO autopilot_runtime;

DROP TABLE autopilot.migration_0332_function_backup;
DELETE FROM public.schema_migration
 WHERE migration_key='0332_autopilot_codex_event_cycle';

COMMIT;
