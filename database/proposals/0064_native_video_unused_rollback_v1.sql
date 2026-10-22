\set ON_ERROR_STOP on
-- UNEXECUTED rollback for an EXACTLY UNUSED package only. Before invoking:
-- separately authorize fence/drain of exact new principals/RPCs, reconcile all
-- existing sessions/transactions and capture original-check metadata + SHA.
-- NOLOGIN/password rotation/REVOKE alone does not drain connected sessions.
-- No auto terminate, restore, retry or post-use DROP is implemented here.
-- psql requires -v expected_original_checks_sha256=<externally retained64SHA>.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL statement_timeout='10000ms';
SET LOCAL lock_timeout='1000ms';
SELECT pg_advisory_xact_lock(6400640064006400::bigint);
SELECT set_config('native_video.expected_original_checks_sha256',:'expected_original_checks_sha256',true);
LOCK TABLE autopilot.task,autopilot.native_video_intake_v1,autopilot.native_video_task_claim_v1
 IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE m text; j jsonb; k text; c record;
BEGIN
 IF session_user<>'neondb_owner' OR current_user<>session_user OR current_database()<>'neondb' THEN
  RAISE EXCEPTION 'NATIVE_ROLLBACK_INSTALLER'; END IF;
 IF EXISTS(SELECT FROM autopilot.native_video_intake_v1)
    OR EXISTS(SELECT FROM autopilot.native_video_task_claim_v1)
    OR EXISTS(SELECT FROM autopilot.task WHERE goal_type='NATIVE_VIDEO_V1') THEN
  RAISE EXCEPTION 'NATIVE_USED_OR_UNCERTAIN_PRESERVE_NO_DROP'; END IF;
 m:=obj_description('autopilot.native_video_intake_v1'::regclass,'pg_class');
 IF encode(public.digest(convert_to(m,'UTF8'),'sha256'),'hex') IS DISTINCT FROM
       current_setting('native_video.expected_original_checks_sha256')
    OR current_setting('native_video.expected_original_checks_sha256')!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'NATIVE_EXTERNAL_RESTORE_PIN_REQUIRED'; END IF;
 j:=m::jsonb;
 FOREACH k IN ARRAY ARRAY['goal_type','risk_class'] LOOP
  EXECUTE format('ALTER TABLE autopilot.task DROP CONSTRAINT %I','native_video_'||k||'_check_v1');
  EXECUTE format('ALTER TABLE autopilot.task ADD CONSTRAINT %I CHECK (%s)',j#>>ARRAY[k,'name'],
    j#>>ARRAY[k,'expression']);
 END LOOP;
 ALTER TABLE autopilot.task DROP CONSTRAINT native_video_isolation_v1;
END $$;
-- Function drops remove exactly their new explicit EXECUTE grants. No existing
-- role, CONNECT/USAGE grant or membership is guessed/revoked/restored here.
DROP FUNCTION autopilot.register_native_video_intake_v1(text,text);
DROP FUNCTION autopilot.create_native_video_task_v1(text,uuid);
DROP FUNCTION autopilot.claim_native_video_task_v1(integer);
DROP FUNCTION autopilot.heartbeat_native_video_task_v1(uuid,bigint,integer);
DROP FUNCTION video_queue.claim_native_video_job_v1(uuid,uuid,uuid,bigint,integer);
DROP FUNCTION autopilot.bind_native_video_claim_v1(uuid,bigint,text,text);
DROP FUNCTION autopilot.inspect_native_video_claim_v1(text);
DROP FUNCTION video_queue.enroll_native_video_assignment_v1(text,text);
DROP FUNCTION autopilot._native_snapshot_v1(uuid,bigint);
DROP FUNCTION autopilot._native_admission_v1(uuid,uuid);
DROP FUNCTION autopilot._native_guard_v1(boolean);
DROP TABLE autopilot.native_video_task_claim_v1;
DROP TABLE autopilot.native_video_intake_v1;
DROP FUNCTION autopilot._native_immutable_v1();
COMMIT;
