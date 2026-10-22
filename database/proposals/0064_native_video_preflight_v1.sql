-- UNEXECUTED catalog/privilege preflight; no rows from application tables.
BEGIN READ ONLY;
SET LOCAL search_path=pg_catalog;
SET LOCAL statement_timeout='3000ms';
SET LOCAL lock_timeout='1000ms';
SELECT current_database(),session_user,current_user,current_setting('server_version_num'),
 current_setting('server_encoding');
SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner) AS owner,c.relacl
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE (n.nspname='autopilot' AND c.relname IN ('task','native_video_intake_v1','native_video_task_claim_v1'))
 OR (n.nspname='video_queue' AND c.relname IN ('job','batch','source_reservation','subset_admission',
    'subset_approval','one_job_staging_admission_v1','terminal_assignment_v1')) ORDER BY 1,2;
SELECT conname,pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint WHERE conrelid='autopilot.task'::regclass
 ORDER BY conname;
-- NULL before installation, exact retained restore metadata after installation.
SELECT obj_description(to_regclass('autopilot.native_video_intake_v1'),'pg_class') AS exact_original_checks;
SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls
 FROM pg_catalog.pg_roles WHERE rolname IN ('neondb_owner','video_native_owner_login','autopilot_light_worker_login');
-- Runtime privileges plus definer helper/table closure must be reviewed against
-- exact accepted base profile. Missing privileges stop; do not add hidden GRANTs.
SELECT q.sig,has_function_privilege('neondb_owner',q.sig,'EXECUTE') AS definer_execute
 FROM unnest(ARRAY['video_queue.subset_assert_final(text)',
 'video_queue.subset_claim_job(uuid,text,integer,text,text)',
 'video_queue.post_finish_assignment_v1(text)','video_queue.post_finish_json_v1(jsonb,integer)',
 'video_queue.post_finish_sha_v1(jsonb)','video_queue.post_finish_selector_v1(text)']) q(sig);
SELECT c.oid::regclass AS object,has_table_privilege('neondb_owner',c.oid,'SELECT') AS definer_select,
 has_table_privilege('neondb_owner',c.oid,'INSERT') AS definer_insert,
 has_table_privilege('neondb_owner',c.oid,'UPDATE') AS definer_update
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname IN ('autopilot','video_queue') AND c.relkind IN ('r','p') ORDER BY 1 LIMIT 100;
SELECT p.oid::regprocedure,p.prosecdef,p.proconfig,pg_get_userbyid(p.proowner),p.proacl
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname IN ('autopilot','video_queue') AND p.proname LIKE '%native%v1' ORDER BY 1 LIMIT 40;
ROLLBACK;
