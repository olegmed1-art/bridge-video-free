\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('autopilot.codex_command_send_intent.v1',0));

DO $pre$
BEGIN
 IF NOT EXISTS (SELECT FROM public.schema_migration
   WHERE migration_key='0369_autopilot_reconcile_gateway') THEN
   RAISE EXCEPTION 'CODEX_SEND_INTENT_REQUIRES_0369';
 END IF;
END $pre$;

-- Owner-only, durable, one-shot intent. This is NOT an ACK or a task result.
-- No TTL, release, retry or takeover: an ambiguous send cannot be replayed.
-- Keep this ledger across rollback/reapply; deleting it reopens send authority.
CREATE TABLE IF NOT EXISTS autopilot.codex_command_send_intent (
 dispatch_id uuid PRIMARY KEY REFERENCES autopilot.role_dispatch_outbox,
 attempt_id uuid NOT NULL UNIQUE,
 command_sha256 text NOT NULL CHECK (command_sha256 ~ '^[0-9a-f]{64}$'),
 binding jsonb NOT NULL CHECK (jsonb_typeof(binding)='object'),
 consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT codex_send_binding_bound CHECK (octet_length(binding::text)<=16384)
);
REVOKE ALL ON autopilot.codex_command_send_intent FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;

-- Rollback retains the ledger. Do not silently adopt a drifted/precreated
-- replacement whose uniqueness, FK, owner, RLS or triggers could break safety.
DO $ledger$
DECLARE columns text[]; checks text[];
BEGIN
 SELECT array_agg(a.attname||':'||format_type(a.atttypid,a.atttypmod)||':'||a.attnotnull ORDER BY a.attnum)
 INTO columns FROM pg_attribute a WHERE a.attrelid='autopilot.codex_command_send_intent'::regclass
 AND a.attnum>0 AND NOT a.attisdropped;
 IF columns IS DISTINCT FROM ARRAY['dispatch_id:uuid:true','attempt_id:uuid:true',
   'command_sha256:text:true','binding:jsonb:true','consumed_at:timestamp with time zone:true']
 OR NOT EXISTS(SELECT FROM pg_class WHERE oid='autopilot.codex_command_send_intent'::regclass
   AND relkind='r' AND NOT relrowsecurity AND NOT relforcerowsecurity
   AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user))
 OR EXISTS(SELECT FROM pg_trigger WHERE tgrelid='autopilot.codex_command_send_intent'::regclass AND NOT tgisinternal)
 OR EXISTS(SELECT FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
   WHERE c.oid='autopilot.codex_command_send_intent'::regclass AND a.grantee<>c.relowner)
 OR (SELECT count(*) FROM pg_constraint WHERE conrelid='autopilot.codex_command_send_intent'::regclass AND contype<>'n')<>6
 OR NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid='autopilot.codex_command_send_intent'::regclass
   AND contype='p' AND conkey=ARRAY[1]::smallint[] AND NOT condeferrable AND convalidated)
 OR NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid='autopilot.codex_command_send_intent'::regclass
   AND contype='u' AND conkey=ARRAY[2]::smallint[] AND NOT condeferrable AND convalidated)
 OR NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid='autopilot.codex_command_send_intent'::regclass
   AND contype='f' AND conkey=ARRAY[1]::smallint[] AND confkey=ARRAY[1]::smallint[]
   AND confrelid='autopilot.role_dispatch_outbox'::regclass AND convalidated
   AND confupdtype='a' AND confdeltype='a' AND NOT condeferrable)
 THEN RAISE EXCEPTION 'CODEX_SEND_LEDGER_SHAPE_INVALID'; END IF;
 SELECT array_agg(regexp_replace(pg_get_expr(conbin,conrelid),'[[:space:]()]','','g') ORDER BY
   regexp_replace(pg_get_expr(conbin,conrelid),'[[:space:]()]','','g')) INTO checks
 FROM pg_constraint WHERE conrelid='autopilot.codex_command_send_intent'::regclass AND contype='c' AND convalidated;
 IF checks IS DISTINCT FROM ARRAY[
   'command_sha256~''^[0-9a-f]{64}$''::text',
   'jsonb_typeofbinding=''object''::text',
   'octet_lengthbinding::text<=16384'] THEN
   RAISE EXCEPTION 'CODEX_SEND_LEDGER_CHECKS_INVALID';
 END IF;
END $ledger$;

-- A read-only snapshot of the exact command authority. NULL never authorizes
-- a send. No SECURITY DEFINER and no new grants to a runtime or connector role.
CREATE FUNCTION autopilot.codex_command_send_binding(p_dispatch_id uuid)
RETURNS jsonb LANGUAGE sql VOLATILE SECURITY INVOKER
SET search_path TO 'pg_catalog'
AS $f$
 SELECT jsonb_build_object(
   'repository',o.repository,'dispatch_id',o.dispatch_id,'task_id',o.task_id,
   'work_item_id',w.work_item_id,'dispatch_pr',o.github_dispatch_comment_id,'mailbox_pr',o.mailbox_pr,
   'dispatch_epoch',o.dispatch_epoch,'role',o.role,
   'task_fingerprint',o.task_fingerprint,'target_pr',o.target_pr,
   'expected_head_sha',o.expected_head_sha,'mode',o.mode,
   'execution_scope',a.execution_scope,'can_repair',a.can_repair,
   'task_kind',a.task_kind,'objective',a.objective,'task_spec_json',a.task_spec_json,
   'delivery_deadline_at',o.delivery_deadline_at)
 FROM autopilot.role_dispatch_outbox o
 JOIN autopilot.task t ON t.task_id=o.task_id
 JOIN autopilot.project_work_task m ON m.task_id=t.task_id
 JOIN autopilot.project_work_item w ON w.work_item_id=m.work_item_id
 JOIN autopilot.role_registry r ON r.role_id=o.role
 CROSS JOIN LATERAL autopilot.get_dispatch_assignment(o.dispatch_id) a
 WHERE o.dispatch_id=p_dispatch_id
   AND a.dispatch_id=o.dispatch_id AND a.task_id=o.task_id AND a.role=o.role
   AND (SELECT count(*) FROM autopilot.get_dispatch_assignment(o.dispatch_id))=1
   AND o.repository='olegmed1-art/bridge-video-free'
   AND o.status='PUBLISHED' AND o.delivery_contract_version=3
   AND o.github_dispatch_comment_id IS NOT NULL
   AND o.codex_command_comment_id IS NULL AND o.codex_ack_at IS NULL
   AND o.delivery_deadline_at>clock_timestamp()+interval '180 seconds'
   AND t.status='WAITING_EXTERNAL'
   AND t.goal_type IN ('CHATGPT_ROLE_DISPATCH_V1','CHATGPT_ROLE_FOLLOWUP_V1')
   AND w.state='ACTIVE' AND w.last_task_id=t.task_id
   AND w.repository=o.repository AND w.role=o.role AND w.target_pr=o.target_pr
   AND w.hold_reason IS NULL AND w.not_before<=clock_timestamp()
   AND (w.depends_on_work_item_id IS NULL OR EXISTS (
     SELECT FROM autopilot.project_work_item parent
     WHERE parent.work_item_id=w.depends_on_work_item_id AND parent.state='DONE'))
   AND r.enabled AND r.execution_scope='REPOSITORY'
   AND (o.mode='READ_ONLY' OR (o.mode='REPAIR' AND r.can_repair))
$f$;

CREATE FUNCTION autopilot.claim_codex_command_send(
 p_dispatch_id uuid,p_attempt_id uuid,p_binding jsonb,p_command_sha256 text
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
SET search_path TO 'pg_catalog'
AS $f$
DECLARE current_binding jsonb; inserted integer; outbox autopilot.role_dispatch_outbox;
 work_id uuid;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'CODEX_SEND_READ_COMMITTED_REQUIRED';
 END IF;
 IF p_dispatch_id IS NULL OR p_attempt_id IS NULL OR p_binding IS NULL
    OR jsonb_typeof(p_binding) IS DISTINCT FROM 'object'
    OR octet_length(p_binding::text)>16384
    OR p_attempt_id::text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    OR p_command_sha256 IS NULL OR p_command_sha256 !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'CODEX_SEND_INTENT_INVALID';
 END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('autopilot.codex_command_send_intent.v1',0));
 IF NOT EXISTS(SELECT FROM public.schema_migration WHERE migration_key='0370_autopilot_codex_send_intent') THEN
   RETURN false;
 END IF;
 -- Lock the authority rows in explicit order. Lock timeouts,
 -- deadlocks and lost replies are failures, NEVER permission to POST.
 SELECT * INTO outbox FROM autopilot.role_dispatch_outbox o
 WHERE o.dispatch_id=p_dispatch_id FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM autopilot.task WHERE task_id=outbox.task_id FOR UPDATE;
 SELECT work_item_id INTO work_id FROM autopilot.project_work_task
 WHERE task_id=outbox.task_id FOR SHARE;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM autopilot.project_work_item WHERE work_item_id=work_id FOR UPDATE;
 PERFORM 1 FROM autopilot.role_registry WHERE role_id=outbox.role FOR SHARE;
 current_binding:=autopilot.codex_command_send_binding(p_dispatch_id);
 IF current_binding IS NULL OR current_binding IS DISTINCT FROM p_binding THEN
   RETURN false;
 END IF;
 INSERT INTO autopilot.codex_command_send_intent(
   dispatch_id,attempt_id,command_sha256,binding)
 VALUES(p_dispatch_id,p_attempt_id,p_command_sha256,current_binding)
 ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS inserted=ROW_COUNT;
 -- Even the identical attempt never gets a second true response. A stored
 -- intent proves only that the right to try was consumed, not that POST ran.
 RETURN inserted=1;
END $f$;

REVOKE ALL ON FUNCTION autopilot.codex_command_send_binding(uuid),
 autopilot.claim_codex_command_send(uuid,uuid,jsonb,text) FROM PUBLIC,
 autopilot_runtime,autopilot_runtime_principal,autopilot_callback,bridge_school_worker;

INSERT INTO public.schema_migration(migration_key)
VALUES('0370_autopilot_codex_send_intent');
COMMIT;
