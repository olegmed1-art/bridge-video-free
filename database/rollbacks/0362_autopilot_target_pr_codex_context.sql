\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_codex_delivery_proof IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM autopilot.role_dispatch_outbox outbox
          JOIN autopilot.role_dispatch_codex_delivery_proof proof USING (dispatch_id)
         WHERE proof.command_pr=outbox.target_pr
           AND outbox.status IN ('PUBLISHED','SENT')
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_CONTEXT_ROLLBACK_HAS_ACTIVE_TARGET_PROOF';
    END IF;
END $guard$;

DO $rollback$
DECLARE
    proc regprocedure;
    original text;
    restored text;
BEGIN
    proc := 'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')=0 THEN RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_ACK_ROLLBACK_DRIFT'; END IF;
    restored := replace(original,
        'outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer',
        'outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer');
    restored := replace(restored,
        '    -- TARGET_PR_CODEX_CONTEXT_V1: target is primary; retain legacy routes.',
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the command to its mailbox.');
    EXECUTE restored;

    proc := 'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')=0 THEN RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_TERMINAL_ROLLBACK_DRIFT'; END IF;
    restored := replace(original,
        'p_event_pr IN (outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)',
        'p_event_pr IN (outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)');
    restored := replace(restored,
        '    -- TARGET_PR_CODEX_CONTEXT_V1: target is primary; retain legacy routes.',
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the response to its mailbox.');
    EXECUTE restored;

    proc := 'autopilot.authorize_codex_publication(jsonb,bigint,text)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')=0 THEN RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_PUBLICATION_ROLLBACK_DRIFT'; END IF;
    restored := replace(original,
        $$proof.command_pr IS DISTINCT FROM outbox.target_pr
       AND proof.command_pr IS DISTINCT FROM outbox.mailbox_pr$$,
        $$proof.command_pr IS DISTINCT FROM outbox.mailbox_pr
       AND NOT (outbox.status='CALLBACK_ACCEPTED'
                AND proof.command_pr IS NOT DISTINCT FROM outbox.target_pr)$$);
    restored := replace(restored,
        '    -- TARGET_PR_CODEX_CONTEXT_V1: accept target or retained mailbox proof.',
        '    -- MAILBOX_V2_CODEX_PUBLICATION_V1: bind provenance to the mailbox.');
    EXECUTE restored;

    proc := 'autopilot.issue_codex_publication_permit(jsonb,integer)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')=0 THEN RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_ISSUER_ROLLBACK_DRIFT'; END IF;
    restored := replace(original,
        $$proof.command_pr IS DISTINCT FROM outbox.target_pr
       AND proof.command_pr IS DISTINCT FROM outbox.mailbox_pr$$,
        'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr');
    restored := replace(restored,
        '    -- TARGET_PR_CODEX_CONTEXT_V1: accept target or retained mailbox proof.',
        '    -- MAILBOX_V2_CODEX_ISSUER_V1: bind provenance to the mailbox.');
    EXECUTE restored;
END $rollback$;

DELETE FROM public.schema_migration
 WHERE migration_key='0362_autopilot_target_pr_codex_context';
COMMIT;
