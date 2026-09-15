\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0334_autopilot_github_probe_retry'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_CODEX_DELIVERY_WINDOW_REQUIRES_0334';
    END IF;
END $$;

-- Keep an exact rollback source and retain the deadlines of the small set of
-- in-flight rows extended during cutover.  Terminal and acknowledged rows are
-- never rewritten.
CREATE TABLE autopilot.migration_0335_function_backup (
    function_key text PRIMARY KEY,
    function_definition text NOT NULL
        CHECK (length(function_definition) BETWEEN 100 AND 100000)
);
INSERT INTO autopilot.migration_0335_function_backup(
    function_key,function_definition
)
SELECT 'mark_role_dispatch_published',pg_get_functiondef(
    'autopilot.mark_role_dispatch_published(uuid,text,bigint,bigint,text)'::regprocedure
);

CREATE TABLE autopilot.migration_0335_deadline_backup (
    dispatch_id uuid PRIMARY KEY,
    previous_delivery_deadline_at timestamptz NOT NULL,
    extended_delivery_deadline_at timestamptz NOT NULL
);
INSERT INTO autopilot.migration_0335_deadline_backup(
    dispatch_id,previous_delivery_deadline_at,extended_delivery_deadline_at
)
SELECT dispatch_id,delivery_deadline_at,published_at+interval '30 minutes'
  FROM autopilot.role_dispatch_outbox
 WHERE delivery_contract_version=3
   AND status='PUBLISHED'
   AND published_at IS NOT NULL
   AND delivery_deadline_at IS NOT NULL
   AND delivery_deadline_at<published_at+interval '30 minutes';

REVOKE ALL ON TABLE autopilot.migration_0335_function_backup,
    autopilot.migration_0335_deadline_backup
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- Six lanes remain concurrent after delivery.  Only the event-delivery
-- admission window grows: observed ChatGPT webhook runs can be queued longer
-- than ten minutes, while successful acknowledgements remain identity-pinned.
CREATE OR REPLACE FUNCTION autopilot.mark_role_dispatch_published(
    p_dispatch_id uuid, p_publisher_id text, p_claim_epoch bigint,
    p_github_pull_request bigint, p_dispatch_body_sha256 text
)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    affected integer;
    contract_version smallint;
    target autopilot.role_chat_registry;
BEGIN
    IF p_github_pull_request <= 0
       OR p_dispatch_body_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AUTOPILOT_OUTBOX_PUBLISHED_INVALID';
    END IF;

    SELECT outbox.delivery_contract_version INTO contract_version
      FROM autopilot.role_dispatch_outbox AS outbox
     WHERE outbox.dispatch_id=p_dispatch_id;
    IF contract_version IS NULL THEN
        RETURN false;
    END IF;
    IF contract_version = 2 THEN
        SELECT chat.* INTO target
          FROM autopilot.role_dispatch_outbox AS outbox
          JOIN autopilot.role_chat_registry AS chat
            ON chat.role_id=outbox.role AND chat.enabled
         WHERE outbox.dispatch_id=p_dispatch_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'AUTOPILOT_CHAT_TARGET_UNREGISTERED';
        END IF;
    END IF;

    UPDATE autopilot.role_dispatch_outbox
       SET status='PUBLISHED', claim_owner=NULL, claim_until=NULL,
           github_dispatch_comment_id=p_github_pull_request,
           dispatch_body_sha256=p_dispatch_body_sha256,
           target_chat_id=CASE WHEN contract_version=2 THEN target.chat_id ELSE NULL END,
           target_chat_name=CASE WHEN contract_version=2 THEN target.chat_name ELSE NULL END,
           executor_id=CASE WHEN contract_version=2 THEN target.executor_id ELSE NULL END,
           published_at=now(),
           delivery_deadline_at=now()+CASE WHEN contract_version=3
               THEN interval '30 minutes' ELSE interval '5 minutes' END,
           sent_at=NULL, callback_deadline_at=NULL, delivered_at=NULL,
           codex_command_pr=NULL, codex_command_comment_id=NULL,
           codex_ack_reaction_id=NULL, codex_ack_at=NULL,
           updated_at=now(), last_error_code=NULL
     WHERE dispatch_id=p_dispatch_id AND status='CLAIMED'
       AND claim_owner=p_publisher_id AND claim_epoch=p_claim_epoch
       AND claim_until>now() AND delivery_contract_version IN (2,3);
    GET DIAGNOSTICS affected=ROW_COUNT;
    RETURN affected=1;
END $$;

UPDATE autopilot.role_dispatch_outbox AS outbox
   SET delivery_deadline_at=backup.extended_delivery_deadline_at,
       updated_at=now()
  FROM autopilot.migration_0335_deadline_backup AS backup
 WHERE outbox.dispatch_id=backup.dispatch_id
   AND outbox.status='PUBLISHED'
   AND outbox.delivery_deadline_at=backup.previous_delivery_deadline_at;

REVOKE ALL ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION autopilot.mark_role_dispatch_published(
    uuid,text,bigint,bigint,text
) TO autopilot_runtime;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0335_autopilot_codex_delivery_window');
COMMIT;
