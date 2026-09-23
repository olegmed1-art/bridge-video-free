\set ON_ERROR_STOP on
BEGIN;

DO $pre$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM public.schema_migration
                WHERE migration_key='0371_autopilot_provider_retry_budget') THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_READBACK_REQUIRES_0371';
 END IF;
 IF EXISTS (SELECT 1 FROM public.schema_migration
            WHERE migration_key='0372_autopilot_codex_terminal_readback_candidates') THEN
   RAISE EXCEPTION 'AUTOPILOT_TERMINAL_READBACK_ALREADY_APPLIED';
 END IF;
END $pre$;

CREATE FUNCTION autopilot.codex_terminal_readback_candidates()
RETURNS TABLE (
 dispatch_id uuid, target_pr integer, dispatch_epoch bigint,
 role text, task_fingerprint text, mode text, since_at timestamptz,
 deadline_at timestamptz
)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, autopilot
AS $body$
 SELECT outbox.dispatch_id,outbox.target_pr,outbox.dispatch_epoch,
        outbox.role,outbox.task_fingerprint,outbox.mode,
        outbox.published_at,
        CASE WHEN outbox.status='SENT' THEN outbox.callback_deadline_at
             ELSE outbox.delivery_deadline_at END
 FROM autopilot.role_dispatch_outbox AS outbox
 JOIN autopilot.task AS task ON task.task_id=outbox.task_id
 WHERE outbox.repository='olegmed1-art/bridge-video-free'
   AND outbox.delivery_contract_version=3
   AND outbox.status IN ('SENT','PUBLISHED')
   AND task.status='WAITING_EXTERNAL'
   AND outbox.target_pr BETWEEN 1 AND 1000000
   AND outbox.published_at IS NOT NULL
   AND (CASE WHEN outbox.status='SENT' THEN outbox.callback_deadline_at
             ELSE outbox.delivery_deadline_at END)>clock_timestamp()+interval '60 seconds'
 ORDER BY 8, outbox.dispatch_id
 LIMIT 7
$body$;

REVOKE ALL ON FUNCTION autopilot.codex_terminal_readback_candidates()
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.codex_terminal_readback_candidates()
 TO autopilot_callback;

CREATE TABLE autopilot.codex_rest_readback_diagnostic (
 dispatch_id uuid NOT NULL REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
 reason_code text NOT NULL CHECK (reason_code ~ '^CODEX_[A-Z0-9_]{1,63}$'),
 target_pr integer NOT NULL CHECK (target_pr BETWEEN 1 AND 1000000),
 comment_id bigint CHECK (comment_id IS NULL OR comment_id>0),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(dispatch_id,reason_code)
);
REVOKE ALL ON TABLE autopilot.codex_rest_readback_diagnostic
 FROM PUBLIC,autopilot_callback,autopilot_runtime,autopilot_runtime_principal;

CREATE FUNCTION autopilot.record_codex_rest_readback_diagnostic(
 p_dispatch_id uuid,p_reason_code text,p_comment_id bigint DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $record$
DECLARE target integer;
BEGIN
 IF p_reason_code IS NULL OR p_reason_code !~ '^CODEX_[A-Z0-9_]{1,63}$'
 OR p_reason_code NOT IN (
  'CODEX_READBACK_INVALID_RESULT','CODEX_READBACK_DUPLICATE_TERMINAL',
  'CODEX_READBACK_PAGE_LIMIT','CODEX_READBACK_PAGE_UNAVAILABLE',
  'CODEX_READBACK_COMMENT_CHANGED','CODEX_READBACK_COMMENT_OUTSIDE_WINDOW',
  'CODEX_READBACK_DEADLINE_EXPIRED','CODEX_PR_HEAD_MISMATCH',
  'CODEX_REPAIR_HEAD_NOT_APPLIED','CODEX_READBACK_RPC_REJECTED'
 ) OR (p_comment_id IS NOT NULL AND p_comment_id<=0) THEN
  RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_DIAGNOSTIC_INVALID';
 END IF;
 SELECT target_pr INTO target FROM autopilot.role_dispatch_outbox
 WHERE dispatch_id=p_dispatch_id AND repository='olegmed1-art/bridge-video-free'
   AND delivery_contract_version=3
   AND status IN ('SENT','PUBLISHED','FAILED_CLOSED');
 IF target IS NULL THEN
  RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_DIAGNOSTIC_DISPATCH_INVALID';
 END IF;
 INSERT INTO autopilot.codex_rest_readback_diagnostic(
   dispatch_id,reason_code,target_pr,comment_id)
 VALUES(p_dispatch_id,p_reason_code,target,p_comment_id)
 ON CONFLICT (dispatch_id,reason_code) DO NOTHING;
 RETURN FOUND;
END $record$;
REVOKE ALL ON FUNCTION autopilot.record_codex_rest_readback_diagnostic(uuid,text,bigint)
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.record_codex_rest_readback_diagnostic(uuid,text,bigint)
 TO autopilot_callback;

-- REST readback has separate provenance from the GitHub Actions event receiver.
ALTER TABLE autopilot.role_dispatch_codex_terminal_receipt
 ADD COLUMN ingress_provenance text NOT NULL DEFAULT 'ISSUE_COMMENT_EVENT'
 CHECK (ingress_provenance IN ('ISSUE_COMMENT_EVENT','GITHUB_REST_DOUBLE_READ'));

DO $source$
DECLARE original text; copied text;
BEGIN
 SELECT pg_get_functiondef(
   'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
 ) INTO original;
 IF original IS NULL
 OR strpos(original,'TARGET_PR_CODEX_CONTEXT_V1')=0
 OR strpos(original,'p_signature_verified IS DISTINCT FROM true')=0
 OR (length(original)-length(replace(original,'p_signature_verified','')))
      <>2*length('p_signature_verified')
 OR strpos(original,'CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_codex_terminal(')=0 THEN
   RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_TERMINAL_SOURCE_DRIFT';
 END IF;
 copied:=replace(original,
   'CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_codex_terminal(',
   'CREATE OR REPLACE FUNCTION autopilot.codex_rest_readback_terminal_core(');
 copied:=replace(copied,'p_signature_verified','p_api_readback_verified');
 EXECUTE copied;
END $source$;
REVOKE ALL ON FUNCTION autopilot.codex_rest_readback_terminal_core(
 text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)
 FROM PUBLIC,autopilot_callback,autopilot_runtime,autopilot_runtime_principal;

CREATE FUNCTION autopilot.codex_rest_readback_core_parity()
RETURNS boolean LANGUAGE sql SECURITY DEFINER
SET search_path=pg_catalog,autopilot
AS $parity$
 SELECT pg_get_functiondef(
   'autopilot.codex_rest_readback_terminal_core(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
 )=replace(replace(pg_get_functiondef(
   'autopilot.accept_role_dispatch_codex_terminal(text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)'::regprocedure
 ),'CREATE OR REPLACE FUNCTION autopilot.accept_role_dispatch_codex_terminal(',
   'CREATE OR REPLACE FUNCTION autopilot.codex_rest_readback_terminal_core('),
   'p_signature_verified','p_api_readback_verified')
$parity$;
REVOKE ALL ON FUNCTION autopilot.codex_rest_readback_core_parity()
 FROM PUBLIC,autopilot_callback,autopilot_runtime,autopilot_runtime_principal;

CREATE FUNCTION autopilot.accept_role_dispatch_codex_rest_readback(
 p_delivery_id text,p_payload_fingerprint text,p_api_readback_verified boolean,
 p_repository text,p_event_pr integer,p_actor_login text,p_actor_id bigint,
 p_author_association text,p_app_slug text,p_app_id bigint,p_body jsonb
)
RETURNS TABLE(accepted boolean,duplicate boolean,task_id uuid,
 successor_task_id uuid,resulting_state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot
AS $wrapper$
DECLARE result record;
BEGIN
 IF autopilot.codex_rest_readback_core_parity() IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_CORE_DRIFT';
 END IF;
 FOR result IN SELECT * FROM autopilot.codex_rest_readback_terminal_core(
   p_delivery_id,p_payload_fingerprint,p_api_readback_verified,p_repository,
   p_event_pr,p_actor_login,p_actor_id,p_author_association,p_app_slug,p_app_id,p_body
 ) LOOP
   IF result.accepted THEN
     UPDATE autopilot.role_dispatch_codex_terminal_receipt
        SET ingress_provenance='GITHUB_REST_DOUBLE_READ'
      WHERE delivery_id=p_delivery_id AND ingress_provenance='ISSUE_COMMENT_EVENT';
     IF NOT FOUND THEN
       RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_PROVENANCE_MISSING';
     END IF;
   END IF;
   RETURN QUERY SELECT result.accepted,result.duplicate,result.task_id,
                       result.successor_task_id,result.resulting_state;
 END LOOP;
END $wrapper$;
REVOKE ALL ON FUNCTION autopilot.accept_role_dispatch_codex_rest_readback(
 text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal;
GRANT EXECUTE ON FUNCTION autopilot.accept_role_dispatch_codex_rest_readback(
 text,text,boolean,text,integer,text,bigint,text,text,bigint,jsonb)
 TO autopilot_callback;
DO $verify$
BEGIN
 IF autopilot.codex_rest_readback_core_parity() IS DISTINCT FROM true THEN
   RAISE EXCEPTION 'AUTOPILOT_REST_READBACK_CORE_INITIAL_MISMATCH';
 END IF;
END $verify$;

INSERT INTO public.schema_migration(migration_key)
VALUES('0372_autopilot_codex_terminal_readback_candidates');
COMMIT;
