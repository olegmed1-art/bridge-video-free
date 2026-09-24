\set ON_ERROR_STOP on
BEGIN;

DO $assert$
DECLARE
    definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0362_autopilot_target_pr_codex_context'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0362_MIGRATION_MISSING';
    END IF;

    definition:=pg_get_functiondef('autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure);
    IF strpos(definition,'TARGET_PR_CODEX_CONTEXT_V1')=0
       OR strpos(definition,'outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer')=0 THEN
        RAISE EXCEPTION 'TARGET_PR_ACK_ROUTE_MISSING';
    END IF;

    definition:=pg_get_functiondef('autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure);
    IF strpos(definition,'p_event_pr IN (outbox.target_pr,outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)')=0
       OR strpos(definition,'proof.command_pr=p_event_pr')=0 THEN
        RAISE EXCEPTION 'TARGET_PR_TERMINAL_ROUTE_OR_PROOF_BINDING_MISSING';
    END IF;

    definition:=pg_get_functiondef('autopilot.authorize_codex_publication(jsonb,bigint,text)'::regprocedure);
    IF strpos(definition,'proof.command_pr IS DISTINCT FROM outbox.target_pr')=0
       OR strpos(definition,'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr')=0 THEN
        RAISE EXCEPTION 'PUBLICATION_DUAL_ROUTE_PROOF_MISSING';
    END IF;

    definition:=pg_get_functiondef('autopilot.issue_codex_publication_permit(jsonb,integer)'::regprocedure);
    IF strpos(definition,'proof.command_pr IS DISTINCT FROM outbox.target_pr')=0
       OR strpos(definition,'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr')=0 THEN
        RAISE EXCEPTION 'ISSUER_DUAL_ROUTE_PROOF_MISSING';
    END IF;
END $assert$;

SELECT 4 AS cases,0 AS failures;
ROLLBACK;
