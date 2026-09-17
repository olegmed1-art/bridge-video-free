\set ON_ERROR_STOP on
BEGIN;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.schema_migration
                   WHERE migration_key='0339_autopilot_native_cli_receipts') THEN
        RAISE EXCEPTION 'PUBLICATION_ISSUER_REQUIRES_0339';
    END IF;
END $$;

-- Owner-only issuer. No runtime/callback/Actions principal receives EXECUTE.
-- The Python verifier supplies hashes of freshly re-fetched GitHub records;
-- this function independently rebinds them to the locked canonical dispatch.
CREATE FUNCTION autopilot.issue_codex_publication_permit(
    p_evidence jsonb,
    p_ttl_seconds integer DEFAULT 600
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $$
DECLARE
    keys text[];
    v_dispatch_id uuid;
    outbox autopilot.role_dispatch_outbox;
    task_row autopilot.task;
    proof autopilot.role_dispatch_codex_delivery_proof;
    work_row autopilot.project_work_item;
    existing autopilot.codex_publication_permit;
    expiry timestamptz;
    expected jsonb;
BEGIN
    IF jsonb_typeof(p_evidence) IS DISTINCT FROM 'object'
       OR octet_length(COALESCE(p_evidence::text,''))>16384
       OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 180 AND 900 THEN
        RAISE EXCEPTION 'PUBLICATION_ISSUER_INPUT_INVALID';
    END IF;
    SELECT array_agg(key ORDER BY key) INTO keys FROM jsonb_object_keys(p_evidence) key;
    IF keys IS DISTINCT FROM ARRAY[
        'approval_comment_id','command_comment_id','dispatch_epoch','dispatch_id',
        'expected_head_sha','payload_sha256','provenance_evidence_sha256',
        'publication_comment_id','role','target_pr','task_fingerprint'
    ]::text[]
       OR jsonb_typeof(p_evidence->'dispatch_epoch') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'target_pr') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'command_comment_id') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'publication_comment_id') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_evidence->'approval_comment_id') IS DISTINCT FROM 'number'
       OR COALESCE(p_evidence->>'dispatch_id','') !~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(p_evidence->>'dispatch_epoch','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_evidence->>'target_pr','') !~ '^[1-9][0-9]{0,6}$'
       OR COALESCE(p_evidence->>'command_comment_id','') !~ '^[1-9][0-9]{0,18}$'
       OR COALESCE(p_evidence->>'publication_comment_id','') !~ '^[1-9][0-9]{0,18}$'
       OR COALESCE(p_evidence->>'approval_comment_id','') !~ '^[1-9][0-9]{0,18}$'
       OR COALESCE(p_evidence->>'role','') !~ '^[A-Z][A-Z0-9_]{0,63}$'
       OR COALESCE(p_evidence->>'task_fingerprint','') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_evidence->>'expected_head_sha','') !~ '^[0-9a-f]{40}$'
       OR COALESCE(p_evidence->>'payload_sha256','') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_evidence->>'provenance_evidence_sha256','') !~ '^[0-9a-f]{64}$'
       OR p_evidence->>'command_comment_id'=p_evidence->>'publication_comment_id'
       OR p_evidence->>'approval_comment_id'=p_evidence->>'command_comment_id'
       OR p_evidence->>'approval_comment_id'=p_evidence->>'publication_comment_id' THEN
        RAISE EXCEPTION 'PUBLICATION_ISSUER_INPUT_INVALID';
    END IF;

    v_dispatch_id:=(p_evidence->>'dispatch_id')::uuid;
    -- Same lock order as the callback publisher: outbox -> task -> evidence.
    SELECT * INTO outbox FROM autopilot.role_dispatch_outbox
     WHERE dispatch_id=v_dispatch_id FOR UPDATE;
    IF outbox.dispatch_id IS NULL THEN
        RAISE EXCEPTION 'PUBLICATION_ISSUER_BINDING_INVALID';
    END IF;
    SELECT * INTO task_row FROM autopilot.task WHERE task_id=outbox.task_id FOR UPDATE;
    SELECT * INTO proof FROM autopilot.role_dispatch_codex_delivery_proof
     WHERE dispatch_id=v_dispatch_id FOR SHARE;
    PERFORM 1 FROM autopilot.project_work_task WHERE task_id=outbox.task_id FOR SHARE;
    SELECT work.* INTO work_row FROM autopilot.project_work_item work
     JOIN autopilot.project_work_task mapping USING(work_item_id)
     WHERE mapping.task_id=outbox.task_id FOR SHARE OF work;
    PERFORM 1 FROM autopilot.role_registry
     WHERE role_id=outbox.role AND enabled AND can_repair
       AND execution_scope='REPOSITORY' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'PUBLICATION_ISSUER_ROLE_INVALID'; END IF;

    expected:=jsonb_build_object(
        'dispatch_id',outbox.dispatch_id::text,
        'dispatch_epoch',outbox.dispatch_epoch,
        'role',outbox.role,
        'task_fingerprint',outbox.task_fingerprint,
        'target_pr',outbox.target_pr,
        'expected_head_sha',outbox.expected_head_sha,
        'command_comment_id',outbox.codex_command_comment_id,
        'publication_comment_id',(p_evidence->>'publication_comment_id')::bigint,
        'approval_comment_id',(p_evidence->>'approval_comment_id')::bigint,
        'payload_sha256',p_evidence->>'payload_sha256',
        'provenance_evidence_sha256',p_evidence->>'provenance_evidence_sha256'
    );
    IF p_evidence IS DISTINCT FROM expected
       OR proof.dispatch_id IS NULL
       OR proof.command_comment_id IS DISTINCT FROM outbox.codex_command_comment_id
       OR proof.command_pr IS DISTINCT FROM outbox.target_pr
       OR outbox.repository IS DISTINCT FROM 'olegmed1-art/bridge-video-free'
       OR outbox.mode IS DISTINCT FROM 'REPAIR'
       OR outbox.delivery_contract_version IS DISTINCT FROM 3
       OR outbox.status IS DISTINCT FROM 'SENT'
       OR task_row.status IS DISTINCT FROM 'WAITING_EXTERNAL'
       OR work_row.work_item_id IS NULL
       OR work_row.state IS DISTINCT FROM 'ACTIVE'
       OR work_row.last_task_id IS DISTINCT FROM outbox.task_id
       OR work_row.repository IS DISTINCT FROM outbox.repository
       OR work_row.target_pr IS DISTINCT FROM outbox.target_pr
       OR work_row.role IS DISTINCT FROM outbox.role
       OR outbox.callback_deadline_at IS NULL
       OR outbox.callback_deadline_at<=clock_timestamp()+interval '180 seconds' THEN
        RAISE EXCEPTION 'PUBLICATION_ISSUER_BINDING_INVALID';
    END IF;

    SELECT * INTO existing FROM autopilot.codex_publication_permit
     WHERE dispatch_id=v_dispatch_id FOR UPDATE;
    IF existing.dispatch_id IS NOT NULL THEN
        IF existing.command_comment_id IS DISTINCT FROM (p_evidence->>'command_comment_id')::bigint
           OR existing.publication_comment_id IS DISTINCT FROM (p_evidence->>'publication_comment_id')::bigint
           OR existing.approval_comment_id IS DISTINCT FROM (p_evidence->>'approval_comment_id')::bigint
           OR existing.payload_sha256 IS DISTINCT FROM p_evidence->>'payload_sha256'
           OR existing.provenance_evidence_sha256 IS DISTINCT FROM p_evidence->>'provenance_evidence_sha256'
           OR existing.revoked IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'PUBLICATION_PERMIT_REUSE_CONFLICT';
        END IF;
        IF existing.expires_at<=clock_timestamp()+interval '180 seconds' THEN
            RAISE EXCEPTION 'PUBLICATION_PERMIT_EXPIRED';
        END IF;
        RETURN jsonb_build_object(
            'state','ISSUED','dispatch_id',existing.dispatch_id,
            'publication_comment_id',existing.publication_comment_id,
            'approval_comment_id',existing.approval_comment_id,
            'payload_sha256',existing.payload_sha256,'expires_at',existing.expires_at);
    END IF;

    expiry:=LEAST(outbox.callback_deadline_at,
                  clock_timestamp()+make_interval(secs=>p_ttl_seconds));
    IF expiry<=clock_timestamp()+interval '180 seconds' THEN
        RAISE EXCEPTION 'PUBLICATION_PERMIT_WINDOW_TOO_SHORT';
    END IF;
    INSERT INTO autopilot.codex_publication_permit(
        dispatch_id,command_comment_id,publication_comment_id,approval_comment_id,
        payload_sha256,provenance_evidence_sha256,expires_at)
    VALUES(
        v_dispatch_id,(p_evidence->>'command_comment_id')::bigint,
        (p_evidence->>'publication_comment_id')::bigint,
        (p_evidence->>'approval_comment_id')::bigint,
        p_evidence->>'payload_sha256',p_evidence->>'provenance_evidence_sha256',expiry);
    RETURN jsonb_build_object(
        'state','ISSUED','dispatch_id',v_dispatch_id,
        'publication_comment_id',(p_evidence->>'publication_comment_id')::bigint,
        'approval_comment_id',(p_evidence->>'approval_comment_id')::bigint,
        'payload_sha256',p_evidence->>'payload_sha256','expires_at',expiry);
END $$;

REVOKE ALL ON FUNCTION autopilot.issue_codex_publication_permit(jsonb,integer)
FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

INSERT INTO public.schema_migration(migration_key)
VALUES ('0340_autopilot_publication_permit_issuer');
COMMIT;
