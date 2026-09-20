\set ON_ERROR_STOP on
BEGIN;

DO $prerequisite$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0361_autopilot_provider_terminal_classification'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_CONTEXT_REQUIRES_0361';
    END IF;
END $prerequisite$;

-- A non-review @codex command executes with the PR containing the command as
-- its repository context. Prefer the audited target PR, while retaining the
-- mailbox and dispatch-PR routes for already published v3 deliveries.
DO $migration$
DECLARE
    proc regprocedure;
    original text;
    patched text;
BEGIN
    proc := 'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')>0
       OR strpos(original,'outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_ACK_SOURCE_DRIFT';
    END IF;
    patched := replace(original,
        'outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer',
        'outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer');
    patched := replace(patched,
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the command to its mailbox.',
        '    -- TARGET_PR_CODEX_CONTEXT_V1: target is primary; retain legacy routes.');
    EXECUTE patched;

    proc := 'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')>0
       OR strpos(original,'p_event_pr IN (outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_TERMINAL_SOURCE_DRIFT';
    END IF;
    patched := replace(original,
        'p_event_pr IN (outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)',
        'p_event_pr IN (outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)');
    patched := replace(patched,
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the response to its mailbox.',
        '    -- TARGET_PR_CODEX_CONTEXT_V1: target is primary; retain legacy routes.');
    EXECUTE patched;

    proc := 'autopilot.authorize_codex_publication(jsonb,bigint,text)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')>0
       OR strpos(original,$$proof.command_pr IS DISTINCT FROM outbox.mailbox_pr
       AND NOT (outbox.status='CALLBACK_ACCEPTED'
                AND proof.command_pr IS NOT DISTINCT FROM outbox.target_pr)$$)=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_PUBLICATION_SOURCE_DRIFT';
    END IF;
    patched := replace(original,
        $$proof.command_pr IS DISTINCT FROM outbox.mailbox_pr
       AND NOT (outbox.status='CALLBACK_ACCEPTED'
                AND proof.command_pr IS NOT DISTINCT FROM outbox.target_pr)$$,
        $$proof.command_pr IS DISTINCT FROM outbox.target_pr
       AND proof.command_pr IS DISTINCT FROM outbox.mailbox_pr$$);
    patched := replace(patched,
        '    -- MAILBOX_V2_CODEX_PUBLICATION_V1: bind provenance to the mailbox.',
        '    -- TARGET_PR_CODEX_CONTEXT_V1: accept target or retained mailbox proof.');
    EXECUTE patched;

    proc := 'autopilot.issue_codex_publication_permit(jsonb,integer)'::regprocedure;
    original := pg_get_functiondef(proc);
    IF strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')>0
       OR strpos(original,'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_TARGET_PR_ISSUER_SOURCE_DRIFT';
    END IF;
    patched := replace(original,
        'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr',
        $$proof.command_pr IS DISTINCT FROM outbox.target_pr
       AND proof.command_pr IS DISTINCT FROM outbox.mailbox_pr$$);
    patched := replace(patched,
        '    -- MAILBOX_V2_CODEX_ISSUER_V1: bind provenance to the mailbox.',
        '    -- TARGET_PR_CODEX_CONTEXT_V1: accept target or retained mailbox proof.');
    EXECUTE patched;
END $migration$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0362_autopilot_target_pr_codex_context');
COMMIT;
