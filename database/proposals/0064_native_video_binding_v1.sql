-- UNEXECUTED MODEL A CANDIDATE. Not part of an automatic migration runner.
-- PG >= 17 (transaction_timeout); pg_xact_status(xid8); 0300 + 0060..0063.
-- Apply only as the verified existing object definer, after separate approval.
-- Does not create LOGINs, credentials, memberships, services or READ_RESULT grants.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL statement_timeout='10000ms';
SET LOCAL lock_timeout='1000ms';
SET LOCAL transaction_timeout='15000ms';
SELECT pg_advisory_xact_lock(6400640064006400::bigint);
DO $pre$
BEGIN
 IF session_user<>'neondb_owner' OR current_user<>session_user
    OR current_database()<>'neondb' OR current_setting('server_version_num')::int<170000
    OR current_setting('server_encoding')<>'UTF8' THEN
  RAISE EXCEPTION 'NATIVE_INSTALLER_TARGET_OR_DEFINER'; END IF;
 IF to_regclass('autopilot.native_video_intake_v1') IS NOT NULL
    OR to_regclass('autopilot.native_video_task_claim_v1') IS NOT NULL THEN
  RAISE EXCEPTION 'NATIVE_ALREADY_INSTALLED_RECONCILE_NO_RETRY'; END IF;
 -- These prerequisites must already be approved/qualified, never silently installed.
 PERFORM 'autopilot.task'::regclass,'video_queue.one_job_staging_admission_v1'::regclass,
   'video_queue.terminal_assignment_v1'::regclass,
   'video_queue.post_finish_assignment_v1(text)'::regprocedure,
   'video_queue.subset_claim_job(uuid,text,integer,text,text)'::regprocedure;
END $pre$;

CREATE TABLE autopilot.native_video_intake_v1 (
 intake_sha256 text PRIMARY KEY CHECK(intake_sha256='5edc60affb18ddae38ec7c55fbedf1d6bb4cb7ea71293cf270698d9270b847c5'),
 descriptor_text text NOT NULL CHECK(octet_length(descriptor_text)=533),
 accepted_by name NOT NULL CHECK(accepted_by='video_native_owner_login'),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(intake_sha256=video_queue.post_finish_sha_v1(descriptor_text::jsonb))
);
CREATE TABLE autopilot.native_video_task_claim_v1 (
 task_id uuid PRIMARY KEY REFERENCES autopilot.task,
 intake_sha256 text NOT NULL UNIQUE REFERENCES autopilot.native_video_intake_v1,
 job_id uuid NOT NULL UNIQUE REFERENCES video_queue.job,
 batch_id uuid NOT NULL UNIQUE REFERENCES video_queue.batch,
 admission_id uuid NOT NULL REFERENCES video_queue.one_job_staging_admission_v1,
 source_fingerprint text NOT NULL CHECK(source_fingerprint~'^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 task_claim_xid xid8,
 task_lease_until timestamptz,
 video_claim_xid xid8,
 video_claim jsonb,
 binding_text text CHECK(octet_length(binding_text) BETWEEN 2 AND 32768),
 binding_sha256 text UNIQUE CHECK(binding_sha256~'^[0-9a-f]{64}$'),
 claim_ack_sha256 text CHECK(claim_ack_sha256~'^[0-9a-f]{64}$'),
 binding_xid xid8,
 expires_at timestamptz,
 assignment_text text CHECK(octet_length(assignment_text) BETWEEN 2 AND 16384),
 assignment_sha256 text UNIQUE CHECK(assignment_sha256~'^[0-9a-f]{64}$'),
 revoked_at timestamptz,
 CHECK((video_claim_xid IS NULL)=(video_claim IS NULL)),
 CHECK((task_claim_xid IS NULL)=(task_lease_until IS NULL)),
 CHECK(video_claim IS NULL OR task_claim_xid IS NOT NULL),
 CHECK((binding_text IS NULL AND binding_sha256 IS NULL AND claim_ack_sha256 IS NULL
          AND binding_xid IS NULL AND expires_at IS NULL)
    OR (binding_text IS NOT NULL AND binding_sha256 IS NOT NULL AND claim_ack_sha256 IS NOT NULL
          AND binding_xid IS NOT NULL AND expires_at IS NOT NULL AND video_claim_xid IS NOT NULL
          AND binding_sha256=video_queue.post_finish_sha_v1(binding_text::jsonb))),
 CHECK((assignment_text IS NULL)=(assignment_sha256 IS NULL)),
 CHECK(assignment_text IS NULL OR binding_text IS NOT NULL)
);

-- Preserve original CHECK expressions for every old row; never edit old goals,
-- leases, receipts or RPC bodies. Record exact restoration metadata externally
-- before any future rollback. Only native NEW rows enter the new claim RPC.
LOCK TABLE autopilot.task IN ACCESS EXCLUSIVE MODE;
DO $checks$
DECLARE c record; names text[]:=ARRAY['goal_type','risk_class']; col text; n int;
 meta jsonb:='{}';
BEGIN
 FOREACH col IN ARRAY names LOOP
  SELECT count(*) INTO n FROM pg_catalog.pg_constraint q JOIN pg_catalog.pg_attribute a
    ON a.attrelid=q.conrelid AND q.conkey=ARRAY[a.attnum]::smallint[]
    WHERE q.conrelid='autopilot.task'::regclass AND q.contype='c' AND a.attname=col;
  IF n<>1 THEN RAISE EXCEPTION 'NATIVE_ORIGINAL_CHECK_NOT_EXACT'; END IF;
  SELECT q.conname,pg_get_expr(q.conbin,q.conrelid) AS expr INTO STRICT c
    FROM pg_catalog.pg_constraint q JOIN pg_catalog.pg_attribute a ON a.attrelid=q.conrelid
      AND q.conkey=ARRAY[a.attnum]::smallint[]
    WHERE q.conrelid='autopilot.task'::regclass AND q.contype='c' AND a.attname=col;
  meta:=meta||jsonb_build_object(col,jsonb_build_object('name',c.conname,'expression',c.expr));
  EXECUTE format('ALTER TABLE autopilot.task DROP CONSTRAINT %I',c.conname);
  EXECUTE format('ALTER TABLE autopilot.task ADD CONSTRAINT %I CHECK ((%s) OR %I=%L)',
    'native_video_'||col||'_check_v1',c.expr,col,
    CASE col WHEN 'goal_type' THEN 'NATIVE_VIDEO_V1' ELSE 'NATIVE_VIDEO_BOUND_V1' END);
 END LOOP;
 EXECUTE format('COMMENT ON TABLE autopilot.native_video_intake_v1 IS %L',meta::text);
END $checks$;
ALTER TABLE autopilot.task ADD CONSTRAINT native_video_isolation_v1 CHECK (
 (goal_type='NATIVE_VIDEO_V1')=(risk_class='NATIVE_VIDEO_BOUND_V1')
 AND (goal_type<>'NATIVE_VIDEO_V1' OR (
   status IN ('NEW','RUNNING','FAILED_CLOSED','CANCELLED','DONE')
   AND max_attempts=1 AND attempts BETWEEN 0 AND 1
   AND model_turn_cap=0 AND cost_cap_microusd=0
   AND allowed_capabilities_json='[]'::jsonb
   AND (status<>'NEW' OR (attempts=0 AND lease_epoch=0))
   AND (status<>'RUNNING' OR (attempts=1 AND lease_epoch=1
       AND lease_owner='db-login:autopilot_light_worker_login')))));

CREATE FUNCTION autopilot._native_guard_v1(p_owner boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE r record;
BEGIN
 IF p_owner IS NULL OR current_user<>'neondb_owner' OR current_database()<>'neondb'
    OR session_user::text IS DISTINCT FROM (CASE WHEN p_owner THEN 'video_native_owner_login'
       ELSE 'autopilot_light_worker_login' END) THEN RAISE EXCEPTION 'NATIVE_SESSION_IDENTITY'; END IF;
 SELECT * INTO STRICT r FROM pg_catalog.pg_roles WHERE rolname=session_user;
 IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
    OR r.rolreplication OR r.rolbypassrls OR pg_has_role(session_user,'neondb_owner','MEMBER')
    OR (p_owner AND (r.rolinherit OR EXISTS(SELECT FROM pg_catalog.pg_auth_members WHERE member=r.oid))) THEN
  RAISE EXCEPTION 'NATIVE_SESSION_CAPABILITY'; END IF;
 IF NOT pg_try_advisory_xact_lock(6400640064006400::bigint) THEN
  RAISE EXCEPTION 'NATIVE_MAINTENANCE_OR_CONCURRENT_HOLD'; END IF;
 -- Bound server work even for direct callers, not just the Python driver.
 PERFORM set_config('lock_timeout','1000ms',true);
END $$;

CREATE FUNCTION autopilot._native_immutable_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE k text; oldj jsonb; newj jsonb;
BEGIN
 IF TG_OP<>'UPDATE' OR TG_TABLE_NAME='native_video_intake_v1' THEN
  RAISE EXCEPTION 'NATIVE_APPEND_ONLY'; END IF;
 oldj:=to_jsonb(OLD); newj:=to_jsonb(NEW);
 IF oldj-ARRAY['task_claim_xid','task_lease_until','video_claim_xid','video_claim','binding_text','binding_sha256',
      'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at']
    IS DISTINCT FROM newj-ARRAY['task_claim_xid','task_lease_until','video_claim_xid','video_claim','binding_text','binding_sha256',
      'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at'] THEN
  RAISE EXCEPTION 'NATIVE_IMMUTABLE_IDENTITY'; END IF;
 FOREACH k IN ARRAY ARRAY['task_claim_xid','video_claim_xid','video_claim','binding_text','binding_sha256',
       'claim_ack_sha256','binding_xid','expires_at','assignment_text','assignment_sha256','revoked_at'] LOOP
  IF oldj->k<>'null'::jsonb AND oldj->k IS DISTINCT FROM newj->k THEN
   RAISE EXCEPTION 'NATIVE_IMMUTABLE_PHASE'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER native_intake_immutable BEFORE UPDATE OR DELETE ON autopilot.native_video_intake_v1
 FOR EACH ROW EXECUTE FUNCTION autopilot._native_immutable_v1();
CREATE TRIGGER native_claim_immutable BEFORE UPDATE OR DELETE ON autopilot.native_video_task_claim_v1
 FOR EACH ROW EXECUTE FUNCTION autopilot._native_immutable_v1();
CREATE TRIGGER native_intake_no_truncate BEFORE TRUNCATE ON autopilot.native_video_intake_v1
 EXECUTE FUNCTION autopilot._native_immutable_v1();
CREATE TRIGGER native_claim_no_truncate BEFORE TRUNCATE ON autopilot.native_video_task_claim_v1
 EXECUTE FUNCTION autopilot._native_immutable_v1();

-- Existing approval/source closure, SAME locks as old private queue (job -> batch).
CREATE FUNCTION autopilot._native_admission_v1(p_job uuid,p_batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j video_queue.job%ROWTYPE; b video_queue.batch%ROWTYPE;
 s video_queue.one_job_staging_admission_v1%ROWTYPE; a video_queue.subset_approval%ROWTYPE;
 d video_queue.subset_admission%ROWTYPE; r video_queue.source_reservation%ROWTYPE;
BEGIN
 SELECT * INTO STRICT j FROM video_queue.job WHERE job_id=p_job FOR UPDATE NOWAIT;
 SELECT * INTO STRICT b FROM video_queue.batch WHERE batch_id=p_batch FOR SHARE NOWAIT;
 SELECT * INTO STRICT s FROM video_queue.one_job_staging_admission_v1 WHERE job_id=p_job FOR SHARE NOWAIT;
 SELECT * INTO STRICT d FROM video_queue.subset_admission WHERE batch_id=p_batch FOR SHARE NOWAIT;
 SELECT * INTO STRICT a FROM video_queue.subset_approval WHERE approval_id=d.approval_id FOR SHARE NOWAIT;
 SELECT * INTO STRICT r FROM video_queue.source_reservation WHERE job_id=p_job FOR SHARE NOWAIT;
 IF j.batch_id<>p_batch OR s.batch_id<>p_batch OR s.subset_approval_id<>a.approval_id
    OR NOT s.enabled OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp()
    OR NOT a.runtime_enabled OR a.revoked_at IS NOT NULL OR a.expires_at<=clock_timestamp()
    OR d.state<>'FINALIZED' OR d.contract_sha256<>a.contract_sha256
    OR d.queue_manifest_sha256<>a.queue_manifest_sha256
    OR s.contract_sha256<>d.contract_sha256 OR s.queue_manifest_sha256<>d.queue_manifest_sha256
    OR s.source_file_id<>j.source_file_id OR s.stable_job_key<>j.stable_job_key
    OR s.algorithm_revision<>b.algorithm_revision
    OR r.source_fingerprint IS DISTINCT FROM s.source_fingerprint OR r.batch_id<>p_batch
    OR r.admission_request_key<>d.request_key
    OR (SELECT count(*) FROM video_queue.job WHERE batch_id=p_batch)<>1
    OR j.source_file_id<>'1IKeVsCKRQJ24sPzaCXi3KfPs6ERU7lRU'
    OR j.source_size_bytes IS DISTINCT FROM 636352854::bigint
    OR j.source_mime_type<>'video/mp4' OR b.status NOT IN ('RUNNING','QUEUED_CANARY') THEN
  RAISE EXCEPTION 'NATIVE_APPROVAL_OR_SOURCE_HOLD'; END IF;
 PERFORM video_queue.subset_assert_final(d.request_key);
 IF (d.binding->'jobs')->(j.sequence-1)->>'source_fingerprint' IS DISTINCT FROM s.source_fingerprint THEN
  RAISE EXCEPTION 'NATIVE_SOURCE_RESERVATION'; END IF;
 RETURN jsonb_build_object('admission_id',s.admission_id,'source_fingerprint',s.source_fingerprint,
   'expires_at',least(a.expires_at,s.expires_at));
END $$;

-- Internal fresh proof. SQL proves real session/rows/committed XIDs, NOT Linux
-- UID, original RC2 objects or local journal ACK from a supplied receipt hash.
CREATE FUNCTION autopilot._native_snapshot_v1(p_task uuid,p_epoch bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t autopilot.task%ROWTYPE; n autopilot.native_video_task_claim_v1%ROWTYPE;
 j video_queue.job%ROWTYPE; b video_queue.batch%ROWTYPE; a jsonb; e timestamptz;
BEGIN
 SELECT * INTO STRICT t FROM autopilot.task WHERE task_id=p_task FOR UPDATE NOWAIT;
 SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE task_id=p_task FOR UPDATE NOWAIT;
 a:=autopilot._native_admission_v1(n.job_id,n.batch_id);
 SELECT * INTO STRICT j FROM video_queue.job WHERE job_id=n.job_id;
 SELECT * INTO STRICT b FROM video_queue.batch WHERE batch_id=n.batch_id;
 IF t.goal_type<>'NATIVE_VIDEO_V1' OR t.status<>'RUNNING'
    OR t.lease_owner<>'db-login:autopilot_light_worker_login' OR t.lease_epoch IS DISTINCT FROM p_epoch
    OR t.lease_until IS DISTINCT FROM n.task_lease_until
    OR t.goal_json IS DISTINCT FROM jsonb_build_object('schema','native-video-goal/v1',
       'intake_sha256',n.intake_sha256,'batch_id',n.batch_id)
    OR t.lease_until<=clock_timestamp() OR n.revoked_at IS NOT NULL
    OR n.task_claim_xid IS NULL OR pg_xact_status(n.task_claim_xid) IS DISTINCT FROM 'committed'
    OR n.video_claim_xid IS NULL OR pg_xact_status(n.video_claim_xid) IS DISTINCT FROM 'committed'
    OR j.status<>'LEASED' OR j.lease_owner<>t.lease_owner OR j.attempt_count<>1
    OR j.lease_expires_at<=clock_timestamp()
    OR n.video_claim->>'lease_token' IS DISTINCT FROM j.lease_token::text
    OR a->>'admission_id' IS DISTINCT FROM n.admission_id::text
    OR a->>'source_fingerprint' IS DISTINCT FROM n.source_fingerprint
    OR n.video_claim->>'stable_job_key' IS DISTINCT FROM j.stable_job_key
    OR n.video_claim->>'output_folder_id' IS DISTINCT FROM b.output_folder_id
    OR n.video_claim->>'algorithm_revision' IS DISTINCT FROM b.algorithm_revision THEN
  RAISE EXCEPTION 'NATIVE_FRESH_COMMITTED_LEASE_REQUIRED'; END IF;
 e:=least(t.lease_until,j.lease_expires_at,(a->>'expires_at')::timestamptz,
          coalesce(n.expires_at,'infinity'::timestamptz));
 IF e<=clock_timestamp() THEN RAISE EXCEPTION 'NATIVE_EXPIRED'; END IF;
 RETURN jsonb_build_object('schema','native-video-db-snapshot/v1','task_id',p_task,
   'lease_epoch',t.lease_epoch,'worker_id',t.lease_owner,'intake_sha256',n.intake_sha256,
   'claim',n.video_claim,'admission_id',n.admission_id,'source_fingerprint',n.source_fingerprint,
   'expires_at_utc',to_char(e AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'binding_sha256',n.binding_sha256,'assignment_sha256',n.assignment_sha256);
END $$;

-- RPC 1: exact retained Diana20 content descriptor (data provenance, not run permission).
CREATE FUNCTION autopilot.register_native_video_intake_v1(p_text text,p_sha text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v jsonb; r autopilot.native_video_intake_v1%ROWTYPE;
BEGIN
 PERFORM autopilot._native_guard_v1(true);
 IF p_text IS NULL OR octet_length(p_text)<>533 OR p_sha IS DISTINCT FROM
   '5edc60affb18ddae38ec7c55fbedf1d6bb4cb7ea71293cf270698d9270b847c5' THEN
  RAISE EXCEPTION 'NATIVE_PINNED_INTAKE_ONLY'; END IF;
 v:=p_text::jsonb;
 IF video_queue.post_finish_json_v1(v) IS DISTINCT FROM p_text
    OR video_queue.post_finish_sha_v1(v) IS DISTINCT FROM p_sha THEN
  RAISE EXCEPTION 'NATIVE_CANONICAL_SOURCE'; END IF;
 SELECT * INTO r FROM autopilot.native_video_intake_v1 WHERE intake_sha256=p_sha FOR SHARE NOWAIT;
 IF FOUND THEN
  IF r.descriptor_text<>p_text THEN RAISE EXCEPTION 'NATIVE_INTAKE_CONFLICT'; END IF;
  RETURN jsonb_build_object('intake_sha256',r.intake_sha256,'status','ALREADY_ACCEPTED');
 END IF;
 INSERT INTO autopilot.native_video_intake_v1(intake_sha256,descriptor_text,accepted_by)
 VALUES(p_sha,p_text,session_user);
 RETURN jsonb_build_object('intake_sha256',p_sha,'status','ACCEPTED');
END $$;

-- RPC 2: supported native task in the actual task table; never visible as READY.
CREATE FUNCTION autopilot.create_native_video_task_v1(p_intake text,p_batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE j uuid; t uuid; a jsonb;
BEGIN
 PERFORM autopilot._native_guard_v1(true);
 PERFORM 1 FROM autopilot.native_video_intake_v1 WHERE intake_sha256=p_intake FOR SHARE NOWAIT;
 IF NOT FOUND OR p_batch IS NULL THEN RAISE EXCEPTION 'NATIVE_ACCEPTED_INTAKE_REQUIRED'; END IF;
 IF EXISTS(SELECT FROM autopilot.native_video_task_claim_v1 WHERE intake_sha256=p_intake) THEN
  RAISE EXCEPTION 'NATIVE_CREATE_CONSUMED_RECONCILE_NO_RETRY'; END IF;
 SELECT job_id INTO STRICT j FROM video_queue.job WHERE batch_id=p_batch;
 a:=autopilot._native_admission_v1(j,p_batch);
 IF NOT EXISTS(SELECT FROM video_queue.job WHERE job_id=j AND status='QUEUED' AND attempt_count=0) THEN
  RAISE EXCEPTION 'NATIVE_FIRST_VIDEO_ATTEMPT_ONLY'; END IF;
 t:=gen_random_uuid();
 INSERT INTO autopilot.task(task_id,task_key,goal_type,goal_json,status,risk_class,max_attempts,
   created_by,source,acceptance_contract_json)
 VALUES(t,'native-video:'||p_intake,'NATIVE_VIDEO_V1',
   jsonb_build_object('schema','native-video-goal/v1','intake_sha256',p_intake,'batch_id',p_batch),
   'NEW','NATIVE_VIDEO_BOUND_V1',1,session_user::text,'NATIVE_OWNER_V1',
   jsonb_build_object('schema','native-video-acceptance/v1','automatic_replay',false));
 INSERT INTO autopilot.native_video_task_claim_v1(task_id,intake_sha256,job_id,batch_id,admission_id,source_fingerprint)
 VALUES(t,p_intake,j,p_batch,(a->>'admission_id')::uuid,a->>'source_fingerprint');
 RETURN jsonb_build_object('task_id',t,'intake_sha256',p_intake,'batch_id',p_batch,'job_id',j);
END $$;

-- RPC 6: server-authenticated, one-shot claim. Must COMMIT before RPC8.
CREATE FUNCTION autopilot.claim_native_video_task_v1(p_seconds integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t autopilot.task%ROWTYPE; n autopilot.native_video_task_claim_v1%ROWTYPE;
BEGIN
 PERFORM autopilot._native_guard_v1(false);
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 60 AND 300 THEN RAISE EXCEPTION 'NATIVE_LEASE_BOUND'; END IF;
 SELECT q.* INTO STRICT t FROM autopilot.task q JOIN autopilot.native_video_task_claim_v1 x USING(task_id)
 WHERE q.goal_type='NATIVE_VIDEO_V1' AND q.status='NEW' AND q.attempts=0 AND x.revoked_at IS NULL
 FOR UPDATE OF q NOWAIT;
 SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE task_id=t.task_id FOR UPDATE NOWAIT;
 PERFORM autopilot._native_admission_v1(n.job_id,n.batch_id);
 IF n.task_claim_xid IS NOT NULL THEN RAISE EXCEPTION 'NATIVE_TASK_ALREADY_CONSUMED'; END IF;
 UPDATE autopilot.task SET status='RUNNING',attempts=1,lease_epoch=1,
   lease_owner='db-login:'||session_user::text,lease_until=clock_timestamp()+make_interval(secs=>p_seconds),
   started_at=clock_timestamp(),updated_at=clock_timestamp() WHERE task_id=t.task_id RETURNING * INTO t;
 UPDATE autopilot.native_video_task_claim_v1 SET task_claim_xid=pg_current_xact_id(),
   task_lease_until=t.lease_until WHERE task_id=t.task_id;
 RETURN jsonb_build_object('task_id',t.task_id,'lease_epoch',t.lease_epoch,'worker_id',t.lease_owner,
   'batch_id',n.batch_id,'job_id',n.job_id,'intake_sha256',n.intake_sha256);
END $$;

-- RPC 7: own current task only; renewal cannot resurrect expiry or a consumed binding.
CREATE FUNCTION autopilot.heartbeat_native_video_task_v1(p_task uuid,p_epoch bigint,p_seconds integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t autopilot.task%ROWTYPE; n autopilot.native_video_task_claim_v1%ROWTYPE;
BEGIN
 PERFORM autopilot._native_guard_v1(false);
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 60 AND 300 THEN RAISE EXCEPTION 'NATIVE_LEASE_BOUND'; END IF;
 SELECT * INTO STRICT t FROM autopilot.task WHERE task_id=p_task FOR UPDATE NOWAIT;
 SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE task_id=p_task FOR UPDATE NOWAIT;
 IF t.status<>'RUNNING' OR t.goal_type<>'NATIVE_VIDEO_V1' OR t.lease_epoch IS DISTINCT FROM p_epoch
    OR t.lease_owner IS DISTINCT FROM 'db-login:'||session_user::text OR t.lease_until<=clock_timestamp()
    OR t.lease_until IS DISTINCT FROM n.task_lease_until
    OR n.revoked_at IS NOT NULL OR n.binding_text IS NOT NULL
    OR pg_xact_status(n.task_claim_xid) IS DISTINCT FROM 'committed' THEN
  RAISE EXCEPTION 'NATIVE_TASK_LEASE_LOST'; END IF;
 PERFORM autopilot._native_admission_v1(n.job_id,n.batch_id);
 UPDATE autopilot.task SET lease_until=least(started_at+interval '5 minutes',
     clock_timestamp()+make_interval(secs=>p_seconds)),
   updated_at=clock_timestamp() WHERE task_id=p_task AND lease_until>clock_timestamp() RETURNING * INTO t;
 IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_TASK_EXPIRED_DURING_HEARTBEAT'; END IF;
 UPDATE autopilot.native_video_task_claim_v1 SET task_lease_until=t.lease_until WHERE task_id=p_task;
 RETURN jsonb_build_object('task_id',t.task_id,'lease_epoch',t.lease_epoch,'renewed',true);
END $$;

-- RPC 8: reuse exact private queue claim implementation; no alternative video queue.
CREATE FUNCTION video_queue.claim_native_video_job_v1(p_task uuid,p_batch uuid,p_job uuid,p_epoch bigint,p_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE t autopilot.task%ROWTYPE; n autopilot.native_video_task_claim_v1%ROWTYPE;
 b video_queue.batch%ROWTYPE; c jsonb;
BEGIN
 PERFORM autopilot._native_guard_v1(false);
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 60 AND 300 THEN RAISE EXCEPTION 'NATIVE_VIDEO_LEASE_BOUND'; END IF;
 SELECT * INTO STRICT t FROM autopilot.task WHERE task_id=p_task FOR UPDATE NOWAIT;
 SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE task_id=p_task FOR UPDATE NOWAIT;
 IF t.goal_type<>'NATIVE_VIDEO_V1' OR t.status<>'RUNNING' OR t.lease_epoch IS DISTINCT FROM p_epoch
    OR t.lease_owner IS DISTINCT FROM 'db-login:'||session_user::text OR t.lease_until<=clock_timestamp()
    OR t.lease_until IS DISTINCT FROM n.task_lease_until
    OR n.job_id IS DISTINCT FROM p_job OR n.batch_id IS DISTINCT FROM p_batch
    OR n.revoked_at IS NOT NULL OR n.task_claim_xid IS NULL
    OR pg_xact_status(n.task_claim_xid) IS DISTINCT FROM 'committed'
    OR n.video_claim_xid IS NOT NULL THEN RAISE EXCEPTION 'NATIVE_TASK_COMMIT_OR_CLAIM_HOLD'; END IF;
 PERFORM autopilot._native_admission_v1(p_job,p_batch);
 IF NOT EXISTS(SELECT FROM video_queue.job WHERE job_id=p_job AND status='QUEUED' AND attempt_count=0) THEN
  RAISE EXCEPTION 'NATIVE_VIDEO_ALREADY_CONSUMED'; END IF;
 SELECT * INTO STRICT b FROM video_queue.batch WHERE batch_id=p_batch;
 SELECT to_jsonb(q) INTO STRICT c FROM video_queue.subset_claim_job(p_batch,
   'db-login:'||session_user::text,p_seconds,b.processing_profile,b.algorithm_revision) q;
 IF c->>'job_id' IS DISTINCT FROM p_job::text OR c->>'batch_id' IS DISTINCT FROM p_batch::text
    OR c->>'source_file_id'<>'1IKeVsCKRQJ24sPzaCXi3KfPs6ERU7lRU'
    OR c->>'attempt_count'<>'1' OR t.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'NATIVE_PRIVATE_CLAIM_CHANGED'; END IF;
 UPDATE autopilot.native_video_task_claim_v1 SET video_claim=c,video_claim_xid=pg_current_xact_id()
   WHERE task_id=p_task;
 RETURN c;
END $$;

-- RPC 3: original UID1001 callsite attests local ACK/native receipt only AFTER
-- its actual peer/scope/RC2 checks. SQL separately verifies both committed XIDs.
CREATE FUNCTION autopilot.bind_native_video_claim_v1(p_task uuid,p_epoch bigint,p_text text,p_ack text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s jsonb; r jsonb; a jsonb; d jsonb; c jsonb; e timestamptz; h text;
BEGIN
 PERFORM autopilot._native_guard_v1(true);
 s:=autopilot._native_snapshot_v1(p_task,p_epoch);
 IF p_text IS NULL OR octet_length(p_text) NOT BETWEEN 2 AND 32768 THEN RAISE EXCEPTION 'NATIVE_BINDING_SIZE'; END IF;
 r:=p_text::jsonb;
 IF video_queue.post_finish_json_v1(r) IS DISTINCT FROM p_text
    OR NOT video_queue.subset_shape(r,ARRAY['schema','assignment','admission_receipt','claim_sha256',
       'journal_sha256','scope_nonce_hash','policy_sha256','expires_at_utc'])
    OR r->>'schema' IS DISTINCT FROM 'native-video-binding/v1'
    OR p_ack IS NULL OR p_ack!~'^[0-9a-f]{64}$' OR r->>'journal_sha256' IS DISTINCT FROM p_ack
    OR NOT coalesce(r->>'scope_nonce_hash'~'^[0-9a-f]{64}$',false)
    OR NOT coalesce(r->>'policy_sha256'~'^[0-9a-f]{64}$',false)
    OR r->>'claim_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(s->'claim') THEN
  RAISE EXCEPTION 'NATIVE_BINDING_CANONICAL_ACK'; END IF;
 a:=video_queue.post_finish_assignment_v1(video_queue.post_finish_json_v1(r->'assignment'));
 d:=r->'admission_receipt'; c:=s->'claim';
 IF NOT video_queue.subset_shape(d,ARRAY['state','durability_confirmed','record_sha256','claim_sha256',
       'anchor_sha256','request_sha256'])
    OR d->>'state' IS DISTINCT FROM 'COMMITTED' OR d->'durability_confirmed' IS DISTINCT FROM 'true'::jsonb
    OR NOT coalesce(d->>'record_sha256'~'^[0-9a-f]{64}$',false)
    OR NOT coalesce(d->>'anchor_sha256'~'^[0-9a-f]{64}$',false)
    OR d->>'request_sha256' IS DISTINCT FROM a->>'request_sha256'
    OR d->>'claim_sha256' IS DISTINCT FROM video_queue.post_finish_sha_v1(jsonb_build_object(
       'job_id',c->'job_id','batch_id',c->'batch_id','lease_token',c->'lease_token',
       'attempt_count',c->'attempt_count','source_file_id',c->'source_file_id'))
    OR a#>>'{autopilot,task_id}' IS DISTINCT FROM p_task::text
    OR a#>'{autopilot,lease_epoch}' IS DISTINCT FROM to_jsonb(p_epoch)
    OR a#>>'{autopilot,worker_id}' IS DISTINCT FROM s->>'worker_id'
    OR a#>>'{autopilot,intake_source_revision}' IS DISTINCT FROM
       'sha256:e5a600d8d532a4f10adef191a9bfdfbb962854429849cd55821266ad71d6868f'
    OR a->>'source_sha256'<>'e5a600d8d532a4f10adef191a9bfdfbb962854429849cd55821266ad71d6868f'
    OR a->>'admission_id' IS DISTINCT FROM s->>'admission_id'
    OR a->>'source_fingerprint' IS DISTINCT FROM s->>'source_fingerprint'
    OR a->'video' IS DISTINCT FROM (c-ARRAY['sequence','source_folder_id','work_folder_id',
       'processing_profile','source_name','source_mime_type','source_size_bytes','source_checksum','is_canary']
       ||jsonb_build_object('worker_key',s->>'worker_id')) THEN
  RAISE EXCEPTION 'NATIVE_ASSIGNMENT_OR_ADMISSION_BINDING'; END IF;
 e:=(r->>'expires_at_utc')::timestamptz;
 IF to_char(e AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') IS DISTINCT FROM r->>'expires_at_utc'
    OR e<=clock_timestamp() OR e>(s->>'expires_at_utc')::timestamptz THEN RAISE EXCEPTION 'NATIVE_BINDING_EXPIRY'; END IF;
 h:=video_queue.post_finish_sha_v1(r);
 -- Repeat issuance NEVER succeeds, even identical bytes. Inspect is separate.
 UPDATE autopilot.native_video_task_claim_v1 SET binding_text=p_text,binding_sha256=h,
   claim_ack_sha256=p_ack,binding_xid=pg_current_xact_id(),expires_at=e
 WHERE task_id=p_task AND binding_text IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_BIND_ALREADY_CONSUMED_RECONCILE'; END IF;
 RETURN jsonb_build_object('binding_sha256',h,'task_id',p_task);
END $$;

-- RPC 4 selectors: "task:<canonical uuid>" before bind; lowercase64 binding SHA
-- after bind. Exact row only, fresh lease/approval; no success-to-retry conversion.
CREATE FUNCTION autopilot.inspect_native_video_claim_v1(p_selector text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE n autopilot.native_video_task_claim_v1%ROWTYPE; t autopilot.task%ROWTYPE; s jsonb;
 r video_queue.terminal_assignment_v1%ROWTYPE; enrolled jsonb;
BEGIN
 PERFORM autopilot._native_guard_v1(true);
 IF p_selector~'^task:[0-9a-f-]{36}$' AND 'task:'||substring(p_selector FROM 6)::uuid::text=p_selector THEN
  SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE task_id=substring(p_selector FROM 6)::uuid;
 ELSIF p_selector~'^[0-9a-f]{64}$' THEN
  SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE binding_sha256=p_selector;
 ELSE RAISE EXCEPTION 'NATIVE_EXACT_SELECTOR'; END IF;
 SELECT * INTO STRICT t FROM autopilot.task WHERE task_id=n.task_id;
 s:=autopilot._native_snapshot_v1(n.task_id,t.lease_epoch);
 IF n.binding_xid IS NOT NULL AND pg_xact_status(n.binding_xid) IS DISTINCT FROM 'committed' THEN
  RAISE EXCEPTION 'NATIVE_BIND_COMMIT_REQUIRED'; END IF;
 IF n.assignment_text IS NOT NULL THEN
  SELECT * INTO STRICT r FROM video_queue.terminal_assignment_v1
    WHERE assignment_sha256=n.assignment_sha256 FOR SHARE NOWAIT;
  IF r.assignment_text IS DISTINCT FROM n.assignment_text OR r.revoked_at IS NOT NULL
     OR r.expires_at<=clock_timestamp() OR r.principal<>'autopilot_light_worker_login' THEN
   RAISE EXCEPTION 'NATIVE_ENROLLED_READBACK_HOLD'; END IF;
  enrolled:=jsonb_build_object('selector_sha256',r.selector_sha256,'assignment_sha256',r.assignment_sha256,
   'assignment_text',r.assignment_text,'principal',r.principal,
   'issued_at_utc',to_char(r.issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'expires_at_utc',to_char(r.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
 END IF;
 RETURN s||jsonb_build_object('enrollment',enrolled);
END $$;

-- RPC 5: exact immutable registry INSERT, no caller direct DML or READ_RESULT.
CREATE FUNCTION video_queue.enroll_native_video_assignment_v1(p_binding text,p_assignment text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE n autopilot.native_video_task_claim_v1%ROWTYPE; t autopilot.task%ROWTYPE;
 s jsonb; a jsonb; r video_queue.terminal_assignment_v1%ROWTYPE;
BEGIN
 PERFORM autopilot._native_guard_v1(true);
 SELECT * INTO STRICT n FROM autopilot.native_video_task_claim_v1 WHERE binding_sha256=p_binding;
 SELECT * INTO STRICT t FROM autopilot.task WHERE task_id=n.task_id;
 s:=autopilot._native_snapshot_v1(n.task_id,t.lease_epoch);
 IF n.binding_xid IS NULL OR pg_xact_status(n.binding_xid) IS DISTINCT FROM 'committed'
    OR n.assignment_text IS NOT NULL OR n.expires_at<=clock_timestamp()
    OR video_queue.post_finish_json_v1(n.binding_text::jsonb->'assignment') IS DISTINCT FROM p_assignment THEN
  RAISE EXCEPTION 'NATIVE_ENROLL_COMMIT_OR_REPLAY_HOLD'; END IF;
 a:=video_queue.post_finish_assignment_v1(p_assignment);
 INSERT INTO video_queue.terminal_assignment_v1(selector_sha256,assignment_sha256,assignment_text,
   principal,issued_at,expires_at) VALUES(video_queue.post_finish_selector_v1(p_assignment),
   video_queue.post_finish_sha_v1(a),p_assignment,'autopilot_light_worker_login',clock_timestamp(),n.expires_at)
 RETURNING * INTO r;
 UPDATE autopilot.native_video_task_claim_v1 SET assignment_text=p_assignment,assignment_sha256=r.assignment_sha256
 WHERE task_id=n.task_id;
 RETURN jsonb_build_object('selector_sha256',r.selector_sha256,'assignment_sha256',r.assignment_sha256,
   'assignment_text',r.assignment_text,'principal',r.principal,
   'issued_at_utc',to_char(r.issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'expires_at_utc',to_char(r.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
END $$;

-- Explicit private ownership/ACL for ALL new functions, including helpers.
-- Runtime ACL grant is a distinct, unexecuted file. Nothing is PUBLIC executable.
DO $acl$
DECLARE q record;
BEGIN
 FOR q IN SELECT x::regprocedure AS sig FROM unnest(ARRAY[
  'autopilot._native_guard_v1(boolean)','autopilot._native_immutable_v1()',
  'autopilot._native_admission_v1(uuid,uuid)','autopilot._native_snapshot_v1(uuid,bigint)',
  'autopilot.register_native_video_intake_v1(text,text)','autopilot.create_native_video_task_v1(text,uuid)',
  'autopilot.claim_native_video_task_v1(integer)','autopilot.heartbeat_native_video_task_v1(uuid,bigint,integer)',
  'autopilot.bind_native_video_claim_v1(uuid,bigint,text,text)','autopilot.inspect_native_video_claim_v1(text)',
  'video_queue.claim_native_video_job_v1(uuid,uuid,uuid,bigint,integer)',
  'video_queue.enroll_native_video_assignment_v1(text,text)']) AS f(x) LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO neondb_owner',q.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',q.sig);
  IF EXISTS(SELECT FROM pg_catalog.pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,
       acldefault('f',p.proowner))) x WHERE p.oid=q.sig::oid AND x.grantee<>p.proowner) THEN
   RAISE EXCEPTION 'NATIVE_UNEXPECTED_DEFAULT_FUNCTION_ACL'; END IF;
 END LOOP;
END $acl$;
REVOKE ALL ON autopilot.native_video_intake_v1,autopilot.native_video_task_claim_v1 FROM PUBLIC;
DO $tables$
BEGIN
 IF EXISTS(SELECT FROM pg_catalog.pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,
      acldefault('r',c.relowner))) x WHERE c.oid IN ('autopilot.native_video_intake_v1'::regclass,
      'autopilot.native_video_task_claim_v1'::regclass) AND x.grantee<>c.relowner) THEN
  RAISE EXCEPTION 'NATIVE_UNEXPECTED_DEFAULT_TABLE_ACL'; END IF;
END $tables$;
COMMIT;
