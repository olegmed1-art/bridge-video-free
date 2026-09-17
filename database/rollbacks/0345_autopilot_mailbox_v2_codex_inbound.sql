\set ON_ERROR_STOP on
BEGIN;

LOCK TABLE autopilot.task IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_outbox IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.role_dispatch_codex_delivery_proof IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE autopilot.codex_publication_permit IN SHARE ROW EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF (SELECT enabled FROM autopilot.project_planner_state WHERE singleton)
       OR EXISTS (
           SELECT 1
             FROM autopilot.role_dispatch_outbox
            WHERE mailbox_pr=1637
              AND status IN ('PUBLISHED','SENT')
       )
       OR EXISTS (
           SELECT 1
             FROM autopilot.task
            WHERE goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
              AND goal_json->>'mailbox_pr'='1637'
              AND status NOT IN (
                  'OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED'
              )
       ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_INBOUND_ROLLBACK_REQUIRES_PAUSE';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM autopilot.role_dispatch_outbox AS outbox
          JOIN autopilot.role_dispatch_codex_delivery_proof AS proof
            USING (dispatch_id)
          JOIN autopilot.codex_publication_permit AS permit
            USING (dispatch_id)
         WHERE outbox.mailbox_pr=1637
           AND outbox.mode='REPAIR'
           AND outbox.status='CALLBACK_ACCEPTED'
           AND proof.command_pr=outbox.mailbox_pr
           AND permit.command_comment_id=proof.command_comment_id
    ) THEN
        RAISE EXCEPTION
            'AUTOPILOT_MAILBOX_V2_CODEX_INBOUND_ROLLBACK_RETAINED_PUBLICATION';
    END IF;
END $guard$;

DO $rollback$
DECLARE
    ack_proc regprocedure :=
        'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure;
    terminal_proc regprocedure :=
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure;
    publication_proc regprocedure :=
        'autopilot.authorize_codex_publication(jsonb,bigint,text)'::regprocedure;
    issuer_proc regprocedure :=
        'autopilot.issue_codex_publication_permit(jsonb,integer)'::regprocedure;
    current_definition text;
    restored text;
    old_ack_binding text :=
        'outbox.target_pr,outbox.github_dispatch_comment_id::integer';
    new_ack_binding text :=
        'outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer';
    old_terminal_binding text :=
        'OR p_event_pr<>outbox.target_pr';
    new_terminal_binding text :=
        'OR p_event_pr NOT IN (outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)';
    old_publication_command_binding text :=
        $$'command_pr',outbox.target_pr$$;
    new_publication_command_binding text :=
        $$'command_pr',outbox.mailbox_pr$$;
    old_proof_binding text :=
        'proof.command_pr IS DISTINCT FROM outbox.target_pr';
    new_proof_binding text :=
        'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr';
BEGIN
    current_definition := pg_get_functiondef(ack_proc);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,new_ack_binding,'')))
          /length(new_ack_binding)<>1
       OR (length(current_definition)-length(replace(
              current_definition,'MAILBOX_V2_CODEX_INBOUND_V1',''
          )))/length('MAILBOX_V2_CODEX_INBOUND_V1')<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ACK_ROLLBACK_DRIFT';
    END IF;
    restored := replace(current_definition,new_ack_binding,old_ack_binding);
    restored := replace(
        restored,
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the command to its mailbox.' || chr(10),
        ''
    );
    EXECUTE restored;

    current_definition := pg_get_functiondef(terminal_proc);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,new_terminal_binding,'')))
          /length(new_terminal_binding)<>1
       OR (length(current_definition)-length(replace(
              current_definition,'MAILBOX_V2_CODEX_INBOUND_V1',''
          )))/length('MAILBOX_V2_CODEX_INBOUND_V1')<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TERMINAL_ROLLBACK_DRIFT';
    END IF;
    restored := replace(current_definition,new_terminal_binding,old_terminal_binding);
    restored := replace(
        restored,
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the response to its mailbox.' || chr(10),
        ''
    );
    EXECUTE restored;

    current_definition := pg_get_functiondef(publication_proc);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,new_publication_command_binding,'')))
          /length(new_publication_command_binding)<>1
       OR (length(current_definition)-length(replace(current_definition,new_proof_binding,'')))
          /length(new_proof_binding)<>1
       OR (length(current_definition)-length(replace(
              current_definition,'MAILBOX_V2_CODEX_PUBLICATION_V1',''
          )))/length('MAILBOX_V2_CODEX_PUBLICATION_V1')<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_PUBLICATION_ROLLBACK_DRIFT';
    END IF;
    restored := replace(current_definition,new_publication_command_binding,old_publication_command_binding);
    restored := replace(restored,new_proof_binding,old_proof_binding);
    restored := replace(
        restored,
        '    -- MAILBOX_V2_CODEX_PUBLICATION_V1: bind provenance to the mailbox.' || chr(10),
        ''
    );
    EXECUTE restored;

    current_definition := pg_get_functiondef(issuer_proc);
    IF current_definition IS NULL
       OR (length(current_definition)-length(replace(current_definition,new_proof_binding,'')))
          /length(new_proof_binding)<>1
       OR (length(current_definition)-length(replace(
              current_definition,'MAILBOX_V2_CODEX_ISSUER_V1',''
          )))/length('MAILBOX_V2_CODEX_ISSUER_V1')<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ISSUER_ROLLBACK_DRIFT';
    END IF;
    restored := replace(current_definition,new_proof_binding,old_proof_binding);
    restored := replace(
        restored,
        '    -- MAILBOX_V2_CODEX_ISSUER_V1: bind provenance to the mailbox.' || chr(10),
        ''
    );
    EXECUTE restored;
END $rollback$;

DELETE FROM public.schema_migration
 WHERE migration_key='0345_autopilot_mailbox_v2_codex_inbound';
COMMIT;
