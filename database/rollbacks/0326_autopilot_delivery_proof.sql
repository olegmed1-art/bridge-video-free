\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.role_dispatch_outbox WHERE delivery_contract_version=2)
       OR EXISTS (SELECT 1 FROM autopilot.role_dispatch_delivery_proof)
       OR EXISTS (SELECT 1 FROM autopilot.dispatcher_wake_outbox) THEN
        RAISE EXCEPTION 'AUTOPILOT_DELIVERY_PROOF_ROLLBACK_REQUIRES_EMPTY_V2_STATE';
    END IF;
END $$;

DROP FUNCTION autopilot.accept_role_dispatch_terminal_v2(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb);
DROP FUNCTION autopilot.accept_role_dispatch_delivery_proof(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb);
DROP FUNCTION autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text);
DROP FUNCTION autopilot.get_dispatch_assignment(uuid);

GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_callback(
    text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb
) TO autopilot_callback;

CREATE OR REPLACE FUNCTION autopilot.mark_role_dispatch_sent(
    p_dispatch_id uuid, p_publisher_id text, p_claim_epoch bigint,
    p_github_comment_id bigint, p_dispatch_body_sha256 text
)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE affected integer;
BEGIN
    IF p_github_comment_id<=0 OR p_dispatch_body_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_SENT_INVALID';
    END IF;
    UPDATE autopilot.role_dispatch_outbox
       SET status='SENT',claim_owner=NULL,claim_until=NULL,
           github_dispatch_comment_id=p_github_comment_id,
           dispatch_body_sha256=p_dispatch_body_sha256,
           sent_at=now(),callback_deadline_at=now()+interval '15 minutes',
           updated_at=now(),last_error_code=NULL
     WHERE dispatch_id=p_dispatch_id AND status='CLAIMED'
       AND claim_owner=p_publisher_id AND claim_epoch=p_claim_epoch AND claim_until>now();
    GET DIAGNOSTICS affected=ROW_COUNT;
    RETURN affected=1;
END $$;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_sent(uuid,text,bigint,bigint,text)
TO autopilot_runtime;

ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_outbox_status_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_outbox_status_check
 CHECK (status IN ('PENDING','CLAIMED','RETRY','SENT','CALLBACK_ACCEPTED','FAILED_CLOSED'));
ALTER TABLE autopilot.role_dispatch_outbox
    DROP CONSTRAINT role_dispatch_delivery_contract_version_check,
    DROP COLUMN delivery_contract_version,
    DROP COLUMN target_chat_id,
    DROP COLUMN target_chat_name,
    DROP COLUMN executor_id,
    DROP COLUMN published_at,
    DROP COLUMN delivery_deadline_at,
    DROP COLUMN delivered_at;
DROP TABLE autopilot.dispatcher_wake_outbox;
DROP TABLE autopilot.role_dispatch_delivery_proof;
DROP TABLE autopilot.role_chat_registry;

CREATE FUNCTION autopilot.get_dispatch_assignment(p_dispatch_id uuid)
RETURNS TABLE(dispatch_id uuid,task_id uuid,role text,execution_scope text,
    can_repair boolean,task_kind text,objective text,task_spec_json jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
 SELECT outbox.dispatch_id,outbox.task_id,outbox.role,roles.execution_scope,
        roles.can_repair,COALESCE(work.task_kind,'REPOSITORY_AUDIT'),
        COALESCE(work.objective,'Audit the exact target head and report a bounded result.'),
        COALESCE(work.task_spec_json,'{}'::jsonb)
 FROM autopilot.role_dispatch_outbox outbox
 JOIN autopilot.role_registry roles ON roles.role_id=outbox.role AND roles.enabled
 LEFT JOIN autopilot.project_work_task map ON map.task_id=outbox.task_id
 LEFT JOIN autopilot.project_work_item work ON work.work_item_id=map.work_item_id
 WHERE outbox.dispatch_id=p_dispatch_id
$$;
REVOKE ALL ON FUNCTION autopilot.get_dispatch_assignment(uuid)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

DELETE FROM public.schema_migration WHERE migration_key='0326_autopilot_delivery_proof';
COMMIT;
