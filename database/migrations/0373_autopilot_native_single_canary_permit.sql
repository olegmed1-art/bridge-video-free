\set ON_ERROR_STOP on
BEGIN;

DO $preflight$ BEGIN
 IF NOT EXISTS(SELECT FROM public.schema_migration
               WHERE migration_key='0339_autopilot_native_cli_receipts') THEN
  RAISE EXCEPTION 'NATIVE_CANARY_REQUIRES_0339';
 END IF;
END $preflight$;

-- Owner-issued, empty by default. A permit is tied to a real broker-created
-- PUBLISHED dispatch PR, its unchanged assignment, exact target and head.
-- This table does not grant any login or activate native_cli_config.
-- Config cutover_at must precede a NEW broker publication; no backdating of
-- an older dispatch is authorized, and the delivery window still applies.
CREATE TABLE autopilot.native_cli_single_canary_permit (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 dispatch_id uuid NOT NULL UNIQUE REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
 target_pr integer NOT NULL CHECK(target_pr BETWEEN 1 AND 1000000),
 expected_head_sha text NOT NULL CHECK(expected_head_sha ~ '^[0-9a-f]{40}$'),
 mode text NOT NULL DEFAULT 'READ_ONLY' CHECK(mode='READ_ONLY'),
 expires_at timestamptz NOT NULL,
 revoked boolean NOT NULL DEFAULT false,
 reserved_at timestamptz,
 started_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(started_at IS NULL OR reserved_at IS NOT NULL),
 CHECK(reserved_at IS NULL OR expires_at>created_at)
);
-- One permit for this pilot, ever. Revocation never frees a second slot while
-- a lost provider create may still be running or its response may be unknown.
REVOKE ALL ON TABLE autopilot.native_cli_single_canary_permit
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;

-- Do not grant the legacy unrestricted native_cli_reserve RPC to the canary
-- identity. The checked wrapper locks the permit and original reservation in
-- one transaction; on lost response, its exact replay returns the same row.
CREATE FUNCTION autopilot.native_cli_reserve_canary(
 p_dispatch_id uuid,p_assignment jsonb,p_branch text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE permit autopilot.native_cli_single_canary_permit;
 o autopilot.role_dispatch_outbox; snapshot jsonb;
BEGIN
 SELECT * INTO permit FROM autopilot.native_cli_single_canary_permit
 WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 IF permit.dispatch_id IS NULL OR permit.revoked OR permit.expires_at<=clock_timestamp()
 OR permit.started_at IS NOT NULL THEN RAISE EXCEPTION 'NATIVE_CANARY_PERMIT_INVALID'; END IF;
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 IF o.dispatch_id IS NULL OR o.status IS DISTINCT FROM 'PUBLISHED'
 OR o.delivery_contract_version IS DISTINCT FROM
    CASE WHEN permit.reserved_at IS NULL THEN 3 ELSE 4 END
 OR o.github_dispatch_comment_id IS NULL
 OR o.target_pr IS DISTINCT FROM permit.target_pr
 OR o.expected_head_sha IS DISTINCT FROM permit.expected_head_sha
 OR o.mode IS DISTINCT FROM 'READ_ONLY' THEN
  RAISE EXCEPTION 'NATIVE_CANARY_BINDING_CHANGED';
 END IF;
 snapshot:=autopilot.native_cli_reserve(p_dispatch_id,p_assignment,p_branch);
 IF snapshot->>'state' IS DISTINCT FROM 'RESERVED'
 OR snapshot->'request'->>'expected_head_sha' IS DISTINCT FROM permit.expected_head_sha
 OR (snapshot->'request'->>'target_pr')::integer IS DISTINCT FROM permit.target_pr THEN
  RAISE EXCEPTION 'NATIVE_CANARY_RESERVATION_CONFLICT';
 END IF;
 UPDATE autopilot.native_cli_single_canary_permit
 SET reserved_at=COALESCE(reserved_at,clock_timestamp()) WHERE dispatch_id=p_dispatch_id;
 RETURN snapshot;
END $$;

-- This is the one-shot creation boundary. A lost response must remain UNKNOWN;
-- expiry or revocation cannot authorize another provider create. The existing
-- native_cli_begin journals submission_started_at under the same transaction.
CREATE FUNCTION autopilot.native_cli_begin_canary(p_request jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE permit autopilot.native_cli_single_canary_permit; allowed boolean;
 id uuid;
BEGIN
 IF jsonb_typeof(p_request) IS DISTINCT FROM 'object'
 OR COALESCE(p_request->>'dispatch_id','') !~
  '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
  RAISE EXCEPTION 'NATIVE_CANARY_REQUEST_INVALID';
 END IF;
 id:=(p_request->>'dispatch_id')::uuid;
 SELECT * INTO permit FROM autopilot.native_cli_single_canary_permit
 WHERE dispatch_id=id FOR UPDATE;
 IF permit.dispatch_id IS NULL OR permit.revoked
 OR permit.expires_at<=clock_timestamp() OR permit.reserved_at IS NULL
 OR permit.started_at IS NOT NULL
 OR p_request->>'expected_head_sha' IS DISTINCT FROM permit.expected_head_sha
 OR (p_request->>'target_pr')::integer IS DISTINCT FROM permit.target_pr
 OR p_request->>'mode' IS DISTINCT FROM 'READ_ONLY' THEN
  RAISE EXCEPTION 'NATIVE_CANARY_PERMIT_INVALID';
 END IF;
 allowed:=autopilot.native_cli_begin(p_request);
 IF allowed THEN
  UPDATE autopilot.native_cli_single_canary_permit SET started_at=clock_timestamp()
  WHERE dispatch_id=id;
 END IF;
 RETURN allowed;
END $$;

-- A revocation read before the external call can stop it. A race after the
-- final read remains possible: SQL begin is the irreversible one-shot boundary.
-- Submitted tasks may still be collected and closed on their original host.
CREATE FUNCTION autopilot.native_cli_canary_current(p_request jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE id uuid; receipt autopilot.native_cli_receipt;
 permit autopilot.native_cli_single_canary_permit;
BEGIN
 IF jsonb_typeof(p_request) IS DISTINCT FROM 'object'
 OR COALESCE(p_request->>'dispatch_id','') !~
  '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN RETURN false; END IF;
 id:=(p_request->>'dispatch_id')::uuid;
 SELECT * INTO receipt FROM autopilot.native_cli_receipt
 WHERE dispatch_id=id AND owner_name=SESSION_USER AND request=p_request;
 IF receipt.dispatch_id IS NULL THEN RETURN false; END IF;
 IF receipt.state='SUBMITTED' THEN RETURN autopilot.native_cli_current(p_request); END IF;
 SELECT * INTO permit FROM autopilot.native_cli_single_canary_permit
 WHERE dispatch_id=id;
 RETURN COALESCE(receipt.state='RESERVED' AND permit.reserved_at IS NOT NULL
  AND NOT permit.revoked AND permit.expires_at>clock_timestamp()
  AND autopilot.native_cli_current(p_request),false);
END $$;

REVOKE ALL ON FUNCTION autopilot.native_cli_reserve_canary(uuid,jsonb,text),
 autopilot.native_cli_begin_canary(jsonb),autopilot.native_cli_canary_current(jsonb)
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
-- The separately reviewed activation must grant only these wrappers and the
-- existing snapshot/current/ack/finish RPCs to a dedicated, restricted login.
INSERT INTO public.schema_migration(migration_key)
 VALUES('0373_autopilot_native_single_canary_permit');
COMMIT;
