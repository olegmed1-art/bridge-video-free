\set ON_ERROR_STOP on
BEGIN;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.schema_migration
                   WHERE migration_key='0337_autopilot_role_repair_admission') THEN
        RAISE EXCEPTION 'PUBLICATION_REQUIRES_0337';
    END IF;
END $$;

-- Intentionally EMPTY. Bot/app identity is not proof that an output originates
-- from the acknowledged owner command. Only a separately reviewed provenance
-- verifier / owner-bound issuer may populate this capability ledger after a
-- separate owner gate. No automatic backfill or runtime writer is installed.
CREATE TABLE autopilot.codex_publication_permit (
    dispatch_id uuid PRIMARY KEY REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
    command_comment_id bigint NOT NULL CHECK (command_comment_id > 0),
    publication_comment_id bigint NOT NULL UNIQUE CHECK (publication_comment_id > 0),
    approval_comment_id bigint NOT NULL UNIQUE CHECK (approval_comment_id > 0),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    provenance_evidence_sha256 text NOT NULL CHECK (provenance_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    expires_at timestamptz NOT NULL,
    revoked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (expires_at > created_at),
    CHECK (approval_comment_id <> command_comment_id
           AND approval_comment_id <> publication_comment_id)
);
REVOKE ALL ON TABLE autopilot.codex_publication_permit
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

CREATE FUNCTION autopilot.authorize_codex_publication(
    p_command jsonb, p_publication_comment_id bigint, p_payload_sha256 text
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    outbox autopilot.role_dispatch_outbox;
    task_row autopilot.task;
    proof autopilot.role_dispatch_codex_delivery_proof;
    permit autopilot.codex_publication_permit;
    work_row autopilot.project_work_item;
    assignment record;
    expected_command jsonb;
    receipt jsonb;
BEGIN
    IF jsonb_typeof(p_command) IS DISTINCT FROM 'object'
       OR octet_length(p_command::text)>16384
       OR (COALESCE(p_command->>'dispatch_id','') ~
           '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') IS NOT TRUE
       OR p_publication_comment_id IS NULL OR p_publication_comment_id<=0
       OR (p_payload_sha256 ~ '^[0-9a-f]{64}$') IS NOT TRUE THEN
        RAISE EXCEPTION 'PUBLICATION_INPUT_INVALID';
    END IF;
    -- Same outbox -> task lock order as existing terminal/reconciliation RPCs.
    -- The caller retains this transaction through GitHub CAS and SQL receipt.
    SELECT * INTO outbox FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id=(p_command->>'dispatch_id')::uuid FOR UPDATE;
    SELECT * INTO task_row FROM autopilot.task
     WHERE task_id=outbox.task_id FOR UPDATE;
    SELECT * INTO proof FROM autopilot.role_dispatch_codex_delivery_proof
     WHERE dispatch_id=outbox.dispatch_id FOR SHARE;
    SELECT * INTO permit FROM autopilot.codex_publication_permit
     WHERE dispatch_id=outbox.dispatch_id FOR SHARE;
    IF outbox.dispatch_id IS NULL OR task_row.task_id IS NULL
       OR proof.dispatch_id IS NULL OR permit.dispatch_id IS NULL
       OR permit.revoked IS DISTINCT FROM false
       OR permit.command_comment_id IS DISTINCT FROM outbox.codex_command_comment_id
       OR permit.publication_comment_id IS DISTINCT FROM p_publication_comment_id
       OR permit.payload_sha256 IS DISTINCT FROM p_payload_sha256 THEN
        RAISE EXCEPTION 'PUBLICATION_PROVENANCE_PERMIT_REQUIRED';
    END IF;
    PERFORM 1 FROM autopilot.role_registry
     WHERE role_id=outbox.role AND enabled AND can_repair
       AND execution_scope='REPOSITORY' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'PUBLICATION_ROLE_REVOKED'; END IF;
    PERFORM 1 FROM autopilot.project_work_task
     WHERE task_id=outbox.task_id FOR SHARE;
    SELECT work.* INTO work_row FROM autopilot.project_work_item AS work
     JOIN autopilot.project_work_task AS mapping USING (work_item_id)
     WHERE mapping.task_id=outbox.task_id FOR SHARE OF work;
    SELECT * INTO assignment FROM autopilot.get_dispatch_assignment(outbox.dispatch_id);
    IF NOT FOUND THEN RAISE EXCEPTION 'PUBLICATION_ASSIGNMENT_MISSING'; END IF;
    expected_command:=jsonb_build_object(
        'comment_id',outbox.codex_command_comment_id,
        'command_pr',outbox.target_pr,
        'created_at',proof.proof_body->'command_created_at',
        'dispatch_id',outbox.dispatch_id::text,
        'dispatch_pr',outbox.github_dispatch_comment_id,
        'dispatch_epoch',outbox.dispatch_epoch,
        'role',outbox.role,'task_fingerprint',outbox.task_fingerprint,
        'target_pr',outbox.target_pr,'expected_head_sha',outbox.expected_head_sha,
        'mode',outbox.mode,'execution_scope',assignment.execution_scope,
        'can_repair',assignment.can_repair,'task_kind',assignment.task_kind,
        'objective',assignment.objective,'task_spec',assignment.task_spec_json
    );
    IF p_command IS DISTINCT FROM expected_command
       OR outbox.repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
       OR outbox.mode IS DISTINCT FROM 'REPAIR'
       OR outbox.delivery_contract_version IS DISTINCT FROM 3
       OR proof.command_comment_id IS DISTINCT FROM outbox.codex_command_comment_id
       OR proof.command_pr IS DISTINCT FROM outbox.target_pr
       OR task_row.goal_type NOT IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
       OR jsonb_typeof(assignment.task_spec_json->'expected_changed_files') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'PUBLICATION_ASSIGNMENT_CHANGED';
    END IF;
    IF outbox.status='CALLBACK_ACCEPTED' THEN
        SELECT callback_body INTO receipt
          FROM autopilot.role_dispatch_codex_terminal_receipt
         WHERE dispatch_id=outbox.dispatch_id
           AND delivery_id='github-codex-result:'||p_publication_comment_id::text;
        IF receipt IS NULL OR receipt->>'result_code' IS DISTINCT FROM 'BOUNDED_REPAIR_PUBLISHED' THEN
            RAISE EXCEPTION 'PUBLICATION_ALREADY_COMPLETED';
        END IF;
        -- Expired but already completed permits allow read-only replay only.
        RETURN jsonb_build_object('state','CALLBACK_ACCEPTED','terminal_body',receipt);
    END IF;
    IF outbox.status IS DISTINCT FROM 'SENT'
       OR task_row.status IS DISTINCT FROM 'WAITING_EXTERNAL'
       OR work_row.work_item_id IS NULL
       OR work_row.state IS DISTINCT FROM 'ACTIVE'
       OR work_row.last_task_id IS DISTINCT FROM outbox.task_id
       OR work_row.repository IS DISTINCT FROM outbox.repository
       OR work_row.target_pr IS DISTINCT FROM outbox.target_pr
       OR work_row.role IS DISTINCT FROM outbox.role
       OR outbox.callback_deadline_at IS NULL
       OR outbox.callback_deadline_at<=clock_timestamp()+interval '45 seconds'
       OR permit.expires_at<=clock_timestamp()+interval '45 seconds' THEN
        RAISE EXCEPTION 'PUBLICATION_ASSIGNMENT_NOT_ACTIVE';
    END IF;
    RETURN jsonb_build_object('state','SENT');
END $$;
REVOKE ALL ON FUNCTION autopilot.authorize_codex_publication(jsonb,bigint,text)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.authorize_codex_publication(jsonb,bigint,text)
TO autopilot_callback;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0338_autopilot_bounded_publication_permit');
COMMIT;
