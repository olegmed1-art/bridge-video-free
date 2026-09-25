-- Read-only catalog inventory. No native RPCs, grants, DDL or row changes.
-- Run the whole block in one SQL Editor execution or psql -X -v ON_ERROR_STOP=1.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
SET LOCAL search_path=pg_catalog;

WITH RECURSIVE role_closure(oid) AS (
 SELECT oid FROM pg_roles WHERE rolname='autopilot_light_worker_login'
 UNION
 SELECT m.roleid FROM pg_auth_members m JOIN role_closure r ON r.oid=m.member
), functions AS (
 SELECT p.oid, n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')' AS signature,
  encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex') AS definition_sha256,
  p.prosecdef AS security_definer,
  (SELECT array_agg(v ORDER BY v) FROM unnest(p.proconfig) v WHERE v LIKE 'search_path=%') AS search_path
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='autopilot' AND p.prokind='f'
), direct_dependencies AS (
 SELECT f.* FROM functions f JOIN pg_proc p ON p.oid=f.oid
 WHERE p.proname IN ('get_dispatch_assignment','record_event',
  'on_role_task_terminal','on_project_work_task_terminal','reconcile_role_dispatch_callbacks')
), triggers AS (
 SELECT n.nspname||'.'||c.relname AS relation, t.tgname AS name,
  t.tgenabled AS enabled, f.signature AS function_signature,
  f.definition_sha256 AS function_sha256,
  encode(sha256(convert_to(pg_get_triggerdef(t.oid,false),'UTF8')),'hex') AS trigger_sha256
 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN functions f ON f.oid=t.tgfoid
 WHERE n.nspname='autopilot' AND NOT t.tgisinternal
  AND c.relname IN ('task','step_attempt','role_dispatch_outbox','project_work_item',
   'project_work_task','native_cli_receipt','native_cli_config')
)
SELECT jsonb_build_object(
 'audit','NATIVE_DEPENDENCY_INVENTORY_V1',
 'identity',jsonb_build_object('database',current_database(),'current_role',current_user,
  'session_role',session_user,'read_only',current_setting('transaction_read_only'),
  'server_version_num',current_setting('server_version_num')),
 'function_count',(SELECT count(*) FROM functions),
 'function_catalog_sha256',(SELECT encode(sha256(convert_to(
  coalesce(string_agg(signature||'='||definition_sha256,E'\n' ORDER BY signature COLLATE "C"),''),'UTF8')),'hex') FROM functions),
 'dependencies',(SELECT coalesce(jsonb_agg(to_jsonb(d)-'oid' ORDER BY d.signature COLLATE "C"),'[]') FROM direct_dependencies d),
 'triggers',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.relation COLLATE "C",t.name COLLATE "C"),'[]') FROM triggers t),
 'runtime_roles',(SELECT coalesce(jsonb_agg(jsonb_build_object(
  'name',r.rolname,'superuser',r.rolsuper,'create_role',r.rolcreaterole,
  'create_db',r.rolcreatedb,'login',r.rolcanlogin,'inherit',r.rolinherit,
  'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) ORDER BY r.rolname COLLATE "C"),'[]')
  FROM pg_roles r JOIN role_closure c ON c.oid=r.oid),
 'runtime_memberships',(SELECT coalesce(jsonb_agg(jsonb_build_object(
  'role',pg_get_userbyid(m.roleid),'member',pg_get_userbyid(m.member),
  'grantor',pg_get_userbyid(m.grantor),'admin',m.admin_option,
  'inherit',m.inherit_option,'set',m.set_option) ORDER BY m.roleid,m.member,m.grantor),'[]')
  FROM pg_auth_members m JOIN role_closure c ON c.oid=m.member),
 'visible_owner_sessions',(SELECT coalesce(jsonb_agg(jsonb_build_object(
  'pid',a.pid,'role',a.usename,'is_this_session',a.pid=pg_backend_pid()) ORDER BY a.pid),'[]')
  FROM pg_stat_activity a JOIN pg_roles r ON r.rolname=a.usename
  WHERE a.datname=current_database() AND a.backend_type='client backend'
   AND (r.rolsuper OR r.rolcreaterole OR a.usename=current_user
    OR r.oid IN (SELECT proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='autopilot'))),
 'tasks_started',false
) AS report;
ROLLBACK;
