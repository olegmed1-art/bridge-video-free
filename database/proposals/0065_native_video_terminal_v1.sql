-- UNEXECUTED CANDIDATE. Separate from every automatic migration runner.
-- Requires qualified0063/0064. No LOGIN, credential, role, table or GRANT.
-- Future activation needs an explicit owner EXECUTE grant for the exact new RPC.
-- DONE means persisted private processing, NOT READ_RESULT, owner ACK or delivery.
-- Rollback: before any use, revert this source/install transaction. After use,
-- preserve terminal columns/receipts; disable the caller and reconcile, never DROP.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL statement_timeout='10000ms';
SET LOCAL lock_timeout='1000ms';
SET LOCAL transaction_timeout='15000ms';
SELECT pg_advisory_xact_lock(6400640064006400::bigint);
DO $pre$
BEGIN
 IF session_user<>'neondb_owner' OR current_user<>session_user OR current_database()<>'neondb'
    OR current_setting('server_version_num')::int<170000 THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_INSTALLER'; END IF;
 PERFORM 'autopilot.native_video_task_claim_v1'::regclass,
   'video_queue.terminal_assignment_v1'::regclass,
   'autopilot._native_guard_v1(boolean)'::regprocedure;
END $pre$;

ALTER TABLE autopilot.native_video_task_claim_v1
 ADD COLUMN terminal_receipt_text text CHECK(octet_length(terminal_receipt_text) BETWEEN 2 AND 65536),
 ADD COLUMN terminal_receipt_sha256 text CHECK(terminal_receipt_sha256~'^[0-9a-f]{64}$'),
 ADD COLUMN terminal_completed_at timestamptz,
 ADD CONSTRAINT native_terminal_receipt_closed_v1 CHECK (
  (terminal_receipt_text IS NULL AND terminal_receipt_sha256 IS NULL AND terminal_completed_at IS NULL)
  OR (terminal_receipt_text IS NOT NULL AND terminal_receipt_sha256 IS NOT NULL
      AND terminal_completed_at IS NOT NULL AND assignment_text IS NOT NULL
      AND terminal_receipt_sha256=video_queue.post_finish_sha_v1(terminal_receipt_text::jsonb)));

-- Preserve ALL0064 identity/phase invariants. Only append the three terminal
-- fields to the same one-way phase list; existing values cannot change or clear.
CREATE OR REPLACE FUNCTION autopilot._native_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE k text; oldj jsonb; newj jsonb;
BEGIN
 IF TG_OP<>'UPDATE' OR TG_TABLE_NAME='native_video_intake_v1' THEN
  RAISE EXCEPTION 'NATIVE_APPEND_ONLY'; END IF;
 oldj:=to_jsonb(OLD); newj:=to_jsonb(NEW);
 IF oldj-ARRAY['task_claim_xid','task_lease_until','video_claim_xid','video_claim','binding_text','binding_sha256',
      'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at',
      'terminal_receipt_text','terminal_receipt_sha256','terminal_completed_at']
    IS DISTINCT FROM newj-ARRAY['task_claim_xid','task_lease_until','video_claim_xid','video_claim','binding_text','binding_sha256',
      'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at',
      'terminal_receipt_text','terminal_receipt_sha256','terminal_completed_at'] THEN
  RAISE EXCEPTION 'NATIVE_IMMUTABLE_IDENTITY'; END IF;
 FOREACH k IN ARRAY ARRAY['task_claim_xid','video_claim_xid','video_claim','binding_text','binding_sha256',
       'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at',
       'terminal_receipt_text','terminal_receipt_sha256','terminal_completed_at'] LOOP
  IF oldj->k<>'null'::jsonb AND oldj->k IS DISTINCT FROM newj->k THEN
   RAISE EXCEPTION 'NATIVE_IMMUTABLE_PHASE'; END IF;
 END LOOP;
 RETURN NEW;
END $$;

CREATE FUNCTION autopilot.complete_native_video_task_v1(
 p_task uuid,p_epoch bigint,p_receipt_text text,p_receipt_sha256 text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t autopilot.task%ROWTYPE; n autopilot.native_video_task_claim_v1%ROWTYPE;
 j video_queue.job%ROWTYPE; b video_queue.batch%ROWTYPE;
 registry video_queue.terminal_assignment_v1%ROWTYPE; ev video_queue.job_event%ROWTYPE;
 s video_queue.one_job_staging_admission_v1%ROWTYPE;
 d video_queue.subset_admission%ROWTYPE; ap video_queue.subset_approval%ROWTYPE;
 a jsonb; receipt jsonb; envelope jsonb; selected jsonb; selector text;
 completed timestamptz; now_stamp timestamptz;
BEGIN
 PERFORM autopilot._native_guard_v1(true);
 IF p_task IS NULL OR p_epoch IS NULL OR p_epoch<>1 OR p_receipt_text IS NULL
    OR octet_length(p_receipt_text) NOT BETWEEN 2 AND 65536
    OR p_receipt_sha256 IS NULL OR p_receipt_sha256!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_INPUT'; END IF;
 receipt:=p_receipt_text::jsonb;
 IF video_queue.post_finish_json_v1(receipt) IS DISTINCT FROM p_receipt_text
    OR video_queue.post_finish_sha_v1(receipt) IS DISTINCT FROM p_receipt_sha256 THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_RECEIPT'; END IF;

 -- Same task -> native -> job -> batch order as0064. NOWAIT never queues an
 -- unbounded competing writer or repairs an unrelated row/privilege.
 SELECT * INTO STRICT t FROM autopilot.task WHERE task_id=p_task FOR UPDATE NOWAIT;
 SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE task_id=p_task FOR UPDATE NOWAIT;
 SELECT * INTO STRICT j FROM video_queue.job WHERE job_id=n.job_id FOR UPDATE NOWAIT;
 SELECT * INTO STRICT b FROM video_queue.batch WHERE batch_id=n.batch_id FOR SHARE NOWAIT;
 IF n.assignment_text IS NULL OR n.revoked_at IS NOT NULL
    OR pg_xact_status(n.task_claim_xid) IS DISTINCT FROM 'committed'
    OR pg_xact_status(n.video_claim_xid) IS DISTINCT FROM 'committed'
    OR pg_xact_status(n.binding_xid) IS DISTINCT FROM 'committed' THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_COMMITTED_BINDING_REQUIRED'; END IF;
 a:=video_queue.post_finish_assignment_v1(n.assignment_text);
 selector:=video_queue.post_finish_selector_v1(n.assignment_text);
 IF t.goal_type IS DISTINCT FROM 'NATIVE_VIDEO_V1'
    OR t.lease_epoch IS DISTINCT FROM p_epoch OR t.attempts<>1 OR t.max_attempts<>1
    OR t.goal_json IS DISTINCT FROM jsonb_build_object('schema','native-video-goal/v1',
       'intake_sha256',n.intake_sha256,'batch_id',n.batch_id)
    OR a#>>'{autopilot,task_id}' IS DISTINCT FROM p_task::text
    OR a#>'{autopilot,lease_epoch}' IS DISTINCT FROM to_jsonb(p_epoch)
    OR a#>>'{autopilot,worker_id}' IS DISTINCT FROM 'db-login:autopilot_light_worker_login'
    OR a#>>'{video,job_id}' IS DISTINCT FROM j.job_id::text
    OR a#>>'{video,batch_id}' IS DISTINCT FROM b.batch_id::text
    OR a#>>'{video,lease_token}' IS DISTINCT FROM n.video_claim->>'lease_token'
    OR a#>>'{video,source_file_id}' IS DISTINCT FROM j.source_file_id
    OR a#>>'{video,stable_job_key}' IS DISTINCT FROM j.stable_job_key
    OR a#>>'{video,worker_key}' IS DISTINCT FROM 'db-login:autopilot_light_worker_login'
    OR a->>'admission_id' IS DISTINCT FROM n.admission_id::text
    OR a->>'source_fingerprint' IS DISTINCT FROM n.source_fingerprint
    OR n.assignment_sha256 IS DISTINCT FROM video_queue.post_finish_sha_v1(a) THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_ASSIGNMENT'; END IF;
 SELECT * INTO STRICT registry FROM video_queue.terminal_assignment_v1
  WHERE selector_sha256=selector FOR SHARE NOWAIT;
 SELECT * INTO STRICT ev FROM video_queue.job_event WHERE job_id=n.job_id
  AND details#>>'{terminal_finish_v1,selector_sha256}'=selector FOR SHARE NOWAIT;
 SELECT * INTO STRICT s FROM video_queue.one_job_staging_admission_v1
  WHERE admission_id=n.admission_id FOR SHARE NOWAIT;
 SELECT * INTO STRICT d FROM video_queue.subset_admission WHERE batch_id=n.batch_id FOR SHARE NOWAIT;
 SELECT * INTO STRICT ap FROM video_queue.subset_approval WHERE approval_id=d.approval_id FOR SHARE NOWAIT;
 IF registry.revoked_at IS NOT NULL OR s.revoked_at IS NOT NULL OR NOT s.enabled
    OR ap.revoked_at IS NOT NULL OR NOT ap.runtime_enabled OR n.revoked_at IS NOT NULL
    OR registry.assignment_text IS DISTINCT FROM n.assignment_text
    OR registry.assignment_sha256 IS DISTINCT FROM n.assignment_sha256
    OR registry.principal::text IS DISTINCT FROM 'autopilot_light_worker_login'
    OR s.job_id IS DISTINCT FROM n.job_id OR s.batch_id IS DISTINCT FROM n.batch_id
    OR s.subset_approval_id IS DISTINCT FROM ap.approval_id
    OR s.source_fingerprint IS DISTINCT FROM n.source_fingerprint
    OR d.state IS DISTINCT FROM 'FINALIZED' THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_REVOKED'; END IF;
 PERFORM video_queue.subset_assert_final(d.request_key);
 envelope:=ev.details->'terminal_finish_v1';
 selected:=(j.output-'private_staging')||jsonb_build_object(
  'result_mode','SHADOW_REVIEW_ONLY','database_persistence_allowed',false);
 IF NOT video_queue.subset_shape(receipt,ARRAY['schema','encoding','selector_sha256',
       'assignment_sha256','job_id','attempt_count','former_video_lease_token','source_file_id',
       'generation_sha256','terminal_event_id','selected_terminal_sha256','persisted_output_sha256',
       'queue_result','queue_result_sha256','private_bundle','artifact'])
    OR NOT video_queue.subset_shape(receipt->'queue_result',ARRAY['job_status','batch_status','released_jobs'])
    OR envelope->>'receipt_text' IS DISTINCT FROM p_receipt_text
    OR envelope->>'receipt_sha256' IS DISTINCT FROM p_receipt_sha256
    OR envelope->>'selector_sha256' IS DISTINCT FROM selector
    OR envelope->>'writer_backend_pid' IS NULL
    OR (envelope->>'writer_backend_pid')::integer<=0
    OR (envelope->>'writer_backend_pid')::integer IS NOT DISTINCT FROM pg_backend_pid()
    OR j.status IS DISTINCT FROM 'REVIEW_READY' OR j.batch_id IS DISTINCT FROM n.batch_id
    OR j.attempt_count<>1 OR j.lease_token IS NOT NULL OR j.lease_owner IS NOT NULL
    OR j.lease_expires_at IS NOT NULL OR NOT coalesce(b.status IN ('RUNNING','REVIEW'),false)
    OR ev.event_type IS DISTINCT FROM 'REVIEW_READY' OR ev.batch_id IS DISTINCT FROM n.batch_id
    OR ev.lease_token::text IS DISTINCT FROM n.video_claim->>'lease_token'
    OR ev.worker_key IS DISTINCT FROM 'db-login:autopilot_light_worker_login'
    OR receipt->>'schema' IS DISTINCT FROM 'diana20-terminal-finish/v1'
    OR receipt->>'encoding' IS DISTINCT FROM 'diana20-canonical-json/v1'
    OR receipt->>'selector_sha256' IS DISTINCT FROM selector
    OR receipt->>'assignment_sha256' IS DISTINCT FROM n.assignment_sha256
    OR receipt->>'job_id' IS DISTINCT FROM n.job_id::text
    OR receipt->'attempt_count' IS DISTINCT FROM '1'::jsonb
    OR receipt->>'former_video_lease_token' IS DISTINCT FROM n.video_claim->>'lease_token'
    OR receipt->>'source_file_id' IS DISTINCT FROM j.source_file_id
    OR receipt->>'generation_sha256' IS DISTINCT FROM a->>'generation_sha256'
    OR receipt->'terminal_event_id' IS DISTINCT FROM to_jsonb(ev.event_id)
    OR receipt->>'selected_terminal_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(selected)
    OR receipt->>'persisted_output_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(j.output)
    OR receipt->'private_bundle' IS DISTINCT FROM j.output->'private_staging'
    OR receipt#>>'{queue_result,job_status}' IS DISTINCT FROM 'REVIEW_READY'
    OR NOT coalesce(receipt#>>'{queue_result,batch_status}' IN ('RUNNING','REVIEW'),false)
    OR receipt#>'{queue_result,released_jobs}' IS DISTINCT FROM '0'::jsonb
    OR receipt->>'queue_result_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(receipt->'queue_result') THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_RECEIPT'; END IF;
 -- Other backends cannot see an uncommitted finish. The same backend is refused
 -- conservatively; PID inequality alone is NOT a COMMIT acknowledgement.
 IF t.status='DONE' THEN
  IF n.terminal_receipt_text IS DISTINCT FROM p_receipt_text
     OR n.terminal_receipt_sha256 IS DISTINCT FROM p_receipt_sha256
     OR n.terminal_completed_at IS NULL OR t.completed_at IS DISTINCT FROM n.terminal_completed_at
     OR t.lease_owner IS NOT NULL OR t.lease_until IS NOT NULL THEN
   RAISE EXCEPTION 'NATIVE_TERMINAL_DONE_CONFLICT'; END IF;
  completed:=n.terminal_completed_at; -- Exact duplicate: no writes/event/finish.
 ELSE
  now_stamp:=clock_timestamp();
  IF t.status IS DISTINCT FROM 'RUNNING'
     OR t.lease_owner IS DISTINCT FROM 'db-login:autopilot_light_worker_login'
     OR t.lease_until IS DISTINCT FROM n.task_lease_until OR t.lease_until<=now_stamp
     OR n.expires_at<=now_stamp OR registry.expires_at<=now_stamp
     OR s.expires_at<=now_stamp OR ap.expires_at<=now_stamp
     OR n.terminal_receipt_text IS NOT NULL THEN
   RAISE EXCEPTION 'NATIVE_TERMINAL_TASK_LEASE'; END IF;
  completed:=clock_timestamp();
  UPDATE autopilot.native_video_task_claim_v1 SET terminal_receipt_text=p_receipt_text,
   terminal_receipt_sha256=p_receipt_sha256,terminal_completed_at=completed WHERE task_id=p_task;
  UPDATE autopilot.task SET status='DONE',lease_owner=NULL,lease_until=NULL,
   terminal_reason_code='NATIVE_PRIVATE_TERMINAL_PERSISTED',completed_at=completed,updated_at=completed,
   safe_summary_json=jsonb_build_object('schema','native-video-completion/v1',
    'terminal_receipt_sha256',p_receipt_sha256,'processing_persisted',true,'delivery_state','NOT_ATTESTED')
   WHERE task_id=p_task AND status='RUNNING' AND lease_epoch=p_epoch
    AND lease_until>clock_timestamp() AND n.expires_at>clock_timestamp()
    AND registry.expires_at>clock_timestamp() AND s.expires_at>clock_timestamp()
    AND ap.expires_at>clock_timestamp();
  IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_TERMINAL_TASK_EXPIRED_DURING_COMPLETE'; END IF;
  PERFORM autopilot.record_event(p_task,'TASK_DONE','RUNNING','DONE',
   jsonb_build_object('terminal_receipt_sha256',p_receipt_sha256,'processing_persisted',true,
    'delivery_state','NOT_ATTESTED'),'SYSTEM','native-video-terminal-v1',
   'native-terminal:'||p_task::text||':'||p_epoch::text||':'||p_receipt_sha256);
 END IF;
 RETURN jsonb_build_object('schema','native-video-completion/v1','status','DONE',
  'task_id',p_task,'lease_epoch',p_epoch,'job_id',n.job_id,'selector_sha256',selector,
  'assignment_sha256',n.assignment_sha256,'terminal_receipt_sha256',p_receipt_sha256,
  'completed_at_utc',to_char(completed AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'processing_persisted',true,'delivery_state','NOT_ATTESTED');
END $$;
ALTER FUNCTION autopilot.complete_native_video_task_v1(uuid,bigint,text,text) OWNER TO neondb_owner;
REVOKE ALL ON FUNCTION autopilot.complete_native_video_task_v1(uuid,bigint,text,text) FROM PUBLIC;
DO $acl$
BEGIN
 IF EXISTS(SELECT FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(
   coalesce(p.proacl,acldefault('f',p.proowner))) x
   WHERE p.oid='autopilot.complete_native_video_task_v1(uuid,bigint,text,text)'::regprocedure
    AND x.grantee<>p.proowner) THEN
  RAISE EXCEPTION 'NATIVE_TERMINAL_UNEXPECTED_DEFAULT_ACL'; END IF;
END $acl$;
COMMIT;
