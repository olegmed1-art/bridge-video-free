-- UNEXECUTED, SEPARATELY APPROVABLE ACL DELTA. No LOGIN/password/role creation.
-- Requires exact current preflight, approved roles and base 0060..0063 closure.
-- A role creation/credential-delivery approval is a DISTINCT installation step.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL lock_timeout='1000ms';
SET LOCAL statement_timeout='5000ms';
SELECT pg_advisory_xact_lock(6400640064006400::bigint);
DO $$
DECLARE r record; who text;
BEGIN
 IF session_user<>'neondb_owner' OR current_user<>session_user OR current_database()<>'neondb' THEN
  RAISE EXCEPTION 'NATIVE_ACL_INSTALLER'; END IF;
 FOREACH who IN ARRAY ARRAY['video_native_owner_login','autopilot_light_worker_login'] LOOP
  SELECT * INTO STRICT r FROM pg_catalog.pg_roles WHERE rolname=who;
  IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls
     OR r.rolreplication OR pg_has_role(who,'neondb_owner','MEMBER') THEN
   RAISE EXCEPTION 'NATIVE_ACL_UNSAFE_ROLE'; END IF;
  IF who='video_native_owner_login' AND (r.rolinherit OR EXISTS(SELECT FROM pg_catalog.pg_auth_members WHERE member=r.oid)) THEN
   RAISE EXCEPTION 'NATIVE_OWNER_MEMBERSHIP'; END IF;
  IF EXISTS(SELECT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname!~'^pg_(toast|temp)'
       AND c.relkind IN ('r','p','v','m')
       AND (has_table_privilege(who,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')
          OR (who='video_native_owner_login' AND has_table_privilege(who,c.oid,'SELECT')))) THEN
   RAISE EXCEPTION 'NATIVE_AMBIENT_TABLE_PRIVILEGE_REFUSE_NO_GLOBAL_REPAIR'; END IF;
  IF EXISTS(SELECT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname!~'^pg_(toast|temp)'
       AND c.relkind='S'
       AND has_sequence_privilege(who,c.oid,'USAGE,SELECT,UPDATE')) THEN
   RAISE EXCEPTION 'NATIVE_AMBIENT_SEQUENCE_PRIVILEGE'; END IF;
  IF has_schema_privilege(who,'autopilot','CREATE') OR has_schema_privilege(who,'video_queue','CREATE')
     OR has_schema_privilege(who,'public','CREATE') OR has_database_privilege(who,current_database(),'CREATE') THEN
   RAISE EXCEPTION 'NATIVE_AMBIENT_SCHEMA_CREATE'; END IF;
 END LOOP;
 -- Effective rights include PUBLIC and inherited ACLs. No silent global repair.
 IF EXISTS(SELECT FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    WHERE (n.nspname IN ('autopilot','video_queue') OR (n.nspname='public'
      AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend d
        WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')))
    AND has_function_privilege('video_native_owner_login',p.oid,'EXECUTE')
    AND p.oid NOT IN (
      'autopilot.register_native_video_intake_v1(text,text)'::regprocedure,
      'autopilot.create_native_video_task_v1(text,uuid)'::regprocedure,
      'autopilot.bind_native_video_claim_v1(uuid,bigint,text,text)'::regprocedure,
      'autopilot.inspect_native_video_claim_v1(text)'::regprocedure,
      'video_queue.enroll_native_video_assignment_v1(text,text)'::regprocedure)) THEN
  RAISE EXCEPTION 'NATIVE_OWNER_UNEXPECTED_EFFECTIVE_APPLICATION_EXECUTE'; END IF;
 IF NOT pg_has_role('autopilot_light_worker_login','video_subset_worker_v2','MEMBER') THEN
  RAISE EXCEPTION 'NATIVE_SEPARATE_BASE_WORKER_PREREQUISITE'; END IF;
END $$;
GRANT CONNECT ON DATABASE neondb TO video_native_owner_login;
GRANT USAGE ON SCHEMA autopilot,video_queue TO video_native_owner_login;
GRANT USAGE ON SCHEMA autopilot,video_queue TO autopilot_light_worker_login;
GRANT EXECUTE ON FUNCTION
 autopilot.register_native_video_intake_v1(text,text),
 autopilot.create_native_video_task_v1(text,uuid),
 autopilot.bind_native_video_claim_v1(uuid,bigint,text,text),
 autopilot.inspect_native_video_claim_v1(text),
 video_queue.enroll_native_video_assignment_v1(text,text) TO video_native_owner_login;
GRANT EXECUTE ON FUNCTION
 autopilot.claim_native_video_task_v1(integer),
 autopilot.heartbeat_native_video_task_v1(uuid,bigint,integer),
 video_queue.claim_native_video_job_v1(uuid,uuid,uuid,bigint,integer) TO autopilot_light_worker_login;
COMMIT;
