\set ON_ERROR_STOP on
BEGIN;

DO $prerequisite$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0344_autopilot_mailbox_v2_rotation'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_INBOUND_REQUIRES_0344';
    END IF;
END $prerequisite$;

-- The GitHub owner command and the Codex response live in the dispatch's
-- mailbox PR. After mailbox rotation that PR is no longer necessarily the
-- audited target PR. Bind ordinary inbound stages to mailbox_pr while
-- retaining the dispatch-PR fallback and the permit-bound target-PR terminal
-- used only by bounded publication.
DO $migration$
DECLARE
    ack_proc regprocedure :=
        'autopilot.accept_role_dispatch_codex_ack(text,text,boolean,text,integer,text,bigint,text,text,bigint,text,bigint,jsonb)'::regprocedure;
    terminal_proc regprocedure :=
        'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure;
    publication_proc regprocedure :=
        'autopilot.authorize_codex_publication(jsonb,bigint,text)'::regprocedure;
    issuer_proc regprocedure :=
        'autopilot.issue_codex_publication_permit(jsonb,integer)'::regprocedure;
    original text;
    patched text;
    old_ack_binding text :=
        'outbox.target_pr,outbox.github_dispatch_comment_id::integer';
    new_ack_binding text :=
        'outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer';
    old_terminal_binding text :=
        'OR p_event_pr<>outbox.target_pr';
    new_terminal_binding text :=
        $$OR NOT (
           p_event_pr IN (outbox.mailbox_pr,outbox.github_dispatch_comment_id::integer)
           OR (outbox.mode='REPAIR'
               AND p_body->>'status'='SUCCEEDED'
               AND p_body->>'result_code'='BOUNDED_REPAIR_PUBLISHED'
               AND p_event_pr=outbox.target_pr)
       )$$;
    old_terminal_proof_binding text :=
        'AND proof.command_pr=p_event_pr';
    new_terminal_proof_binding text :=
        $$AND (
                proof.command_pr=p_event_pr
                OR (outbox.mode='REPAIR'
                    AND p_body->>'status'='SUCCEEDED'
                    AND p_body->>'result_code'='BOUNDED_REPAIR_PUBLISHED'
                    AND p_event_pr=outbox.target_pr
                    AND proof.command_pr=outbox.mailbox_pr
                    AND EXISTS (
                        SELECT 1 FROM autopilot.codex_publication_permit AS publication_permit
                         WHERE publication_permit.dispatch_id=outbox.dispatch_id
                           AND publication_permit.command_comment_id=outbox.codex_command_comment_id
                           AND p_delivery_id='github-codex-result:'||publication_permit.publication_comment_id::text
                    ))
            )$$;
    old_publication_command_binding text :=
        $$'command_pr',outbox.target_pr$$;
    new_publication_command_binding text :=
        $$'command_pr',proof.command_pr$$;
    old_proof_binding text :=
        'proof.command_pr IS DISTINCT FROM outbox.target_pr';
    new_publication_proof_binding text :=
        $$proof.command_pr IS DISTINCT FROM outbox.mailbox_pr
       AND NOT (outbox.status='CALLBACK_ACCEPTED'
                AND proof.command_pr IS NOT DISTINCT FROM outbox.target_pr)$$;
    new_issuer_proof_binding text :=
        'proof.command_pr IS DISTINCT FROM outbox.mailbox_pr';
BEGIN
    original := pg_get_functiondef(ack_proc);
    IF original IS NULL
       OR strpos(original,'MAILBOX_V2_CODEX_INBOUND_V1')>0
       OR (length(original)-length(replace(original,old_ack_binding,'')))
          /length(old_ack_binding)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ACK_SOURCE_DRIFT';
    END IF;
    patched := replace(original,old_ack_binding,new_ack_binding);
    patched := replace(
        patched,
        'BEGIN' || chr(10) || '    IF p_signature_verified',
        'BEGIN' || chr(10) ||
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the command to its mailbox.' || chr(10) ||
        '    IF p_signature_verified'
    );
    IF patched=original OR strpos(patched,'MAILBOX_V2_CODEX_INBOUND_V1')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ACK_PATCH_FAILED';
    END IF;
    EXECUTE patched;

    original := pg_get_functiondef(terminal_proc);
    IF original IS NULL
       OR strpos(original,'MAILBOX_V2_CODEX_INBOUND_V1')>0
       OR (length(original)-length(replace(original,old_terminal_binding,'')))
          /length(old_terminal_binding)<>1
       OR (length(original)-length(replace(original,old_terminal_proof_binding,'')))
          /length(old_terminal_proof_binding)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TERMINAL_SOURCE_DRIFT';
    END IF;
    patched := replace(original,old_terminal_binding,new_terminal_binding);
    patched := replace(patched,old_terminal_proof_binding,new_terminal_proof_binding);
    patched := replace(
        patched,
        'BEGIN' || chr(10) || '    IF p_signature_verified',
        'BEGIN' || chr(10) ||
        '    -- MAILBOX_V2_CODEX_INBOUND_V1: bind the response to its mailbox.' || chr(10) ||
        '    IF p_signature_verified'
    );
    IF patched=original OR strpos(patched,'MAILBOX_V2_CODEX_INBOUND_V1')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_TERMINAL_PATCH_FAILED';
    END IF;
    EXECUTE patched;

    -- A REPAIR response uses the retained delivery proof to authorize its
    -- bounded publication. New proof names the mailbox command PR. Completed
    -- pre-0345 target-bound publication receipts retain read-only replay, so
    -- authorization derives command_pr from the immutable proof itself.
    original := pg_get_functiondef(publication_proc);
    IF original IS NULL
       OR strpos(original,'MAILBOX_V2_CODEX_PUBLICATION_V1')>0
       OR (length(original)-length(replace(original,old_publication_command_binding,'')))
          /length(old_publication_command_binding)<>1
       OR (length(original)-length(replace(original,old_proof_binding,'')))
          /length(old_proof_binding)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_PUBLICATION_SOURCE_DRIFT';
    END IF;
    patched := replace(original,old_publication_command_binding,new_publication_command_binding);
    patched := replace(patched,old_proof_binding,new_publication_proof_binding);
    patched := replace(
        patched,
        'BEGIN' || chr(10) || '    IF jsonb_typeof(p_command)',
        'BEGIN' || chr(10) ||
        '    -- MAILBOX_V2_CODEX_PUBLICATION_V1: bind provenance to the mailbox.' || chr(10) ||
        '    IF jsonb_typeof(p_command)'
    );
    IF patched=original OR strpos(patched,'MAILBOX_V2_CODEX_PUBLICATION_V1')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_PUBLICATION_PATCH_FAILED';
    END IF;
    EXECUTE patched;

    original := pg_get_functiondef(issuer_proc);
    IF original IS NULL
       OR strpos(original,'MAILBOX_V2_CODEX_ISSUER_V1')>0
       OR (length(original)-length(replace(original,old_proof_binding,'')))
          /length(old_proof_binding)<>1 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ISSUER_SOURCE_DRIFT';
    END IF;
    patched := replace(original,old_proof_binding,new_issuer_proof_binding);
    patched := replace(
        patched,
        'BEGIN' || chr(10) || '    -- Serialize all owner-only issuance',
        'BEGIN' || chr(10) ||
        '    -- MAILBOX_V2_CODEX_ISSUER_V1: bind provenance to the mailbox.' || chr(10) ||
        '    -- Serialize all owner-only issuance'
    );
    IF patched=original OR strpos(patched,'MAILBOX_V2_CODEX_ISSUER_V1')=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_MAILBOX_V2_CODEX_ISSUER_PATCH_FAILED';
    END IF;
    EXECUTE patched;
END $migration$;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0345_autopilot_mailbox_v2_codex_inbound');
COMMIT;
