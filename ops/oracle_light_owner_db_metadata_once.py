import hashlib, json, os, subprocess, types, urllib.request, pwd
SHA = 'e12c74d58f8564a9fad2032abfadbb1ecbf1f874'
ROOT = '/opt/bridge-school/school-autopilot-production-light'
def current_main():
    with urllib.request.urlopen('https://api.github.com/repos/olegmed1-art/bridge-video-free/branches/main', timeout=20) as r:
        data = r.read(65537)
    if len(data)>65536 or json.loads(data)['commit']['sha'] != SHA:
        raise RuntimeError('MAIN_CHANGED')
if os.geteuid()!=0 or os.uname().nodename!='autopilot-lite-vnic':
    raise RuntimeError('HOST_IDENTITY')
current_main()
url=f'https://raw.githubusercontent.com/olegmed1-art/bridge-video-free/{SHA}/ops/oracle_light_active_hold_attest.py'
with urllib.request.urlopen(url, timeout=20) as r:
    code=r.read(65537)
if len(code)>65536 or hashlib.sha256(code).hexdigest()!='d4a6b43ed6d8207b8b1ee8d63b4acd41a2aea806a33cebd041746bafd8186f94':
    raise RuntimeError('SOURCE_HASH')
hold=types.ModuleType('verified_hold')
exec(compile(code,'<verified_hold>','exec'),hold.__dict__)
hold.main()
before=hold.service()
child = 'import hashlib, json, os, psycopg\nfrom psycopg.rows import dict_row\ntry:\n with psycopg.connect(os.environ[\'AUDIT_DATABASE_URL\'],autocommit=False,connect_timeout=10,options=\'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000\',row_factory=dict_row) as c:\n  identity=c.execute("SELECT current_user AS role,session_user AS session_role,current_database() AS database,current_setting(\'transaction_read_only\') AS read_only").fetchone()\n  if identity != dict(role=\'autopilot_light_worker_login\',session_role=\'autopilot_light_worker_login\',database=\'neondb\',read_only=\'on\'): raise ValueError()\n  schema=c.execute("SELECT nspname,pg_get_userbyid(nspowner) AS owner,nspacl::text AS acl,has_schema_privilege(current_user,oid,\'USAGE\') AS usage,has_schema_privilege(current_user,oid,\'CREATE\') AS create_allowed FROM pg_namespace WHERE nspname=\'autopilot\'").fetchall()\n  functions=c.execute("SELECT p.proname AS name,pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_userbyid(p.proowner) AS owner,p.prosecdef AS security_definer,p.proacl::text AS acl,p.proconfig,has_function_privilege(current_user,p.oid,\'EXECUTE\') AS execute_allowed,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=\'autopilot\' AND p.proname LIKE \'native_cli_%\' AND p.prokind=\'f\' ORDER BY 1,2 LIMIT 31").fetchall()\n  if len(functions)>30: raise ValueError()\n  for f in functions:\n   f[\'definition_sha256\']=hashlib.sha256(f.pop(\'definition\').encode()).hexdigest()\n   config=f.pop(\'proconfig\') or []\n   f[\'search_path\']=[x for x in config if x.startswith(\'search_path=\')]\n   f[\'config_keys\']=sorted(x.split(\'=\',1)[0] for x in config)\n  roles=c.execute("SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolbypassrls,pg_has_role(current_user,oid,\'USAGE\') AS privileges_inherited FROM pg_roles WHERE oid=(SELECT oid FROM pg_roles WHERE rolname=current_user) OR pg_has_role(current_user,oid,\'MEMBER\') ORDER BY rolname LIMIT 101").fetchall()\n  if len(roles)>100: raise ValueError()\n  tables=c.execute("SELECT c.relname AS name,pg_get_userbyid(c.relowner) AS owner,c.relacl::text AS acl,c.relrowsecurity,c.relforcerowsecurity,has_table_privilege(current_user,c.oid,\'SELECT\') AS select_allowed,has_table_privilege(current_user,c.oid,\'INSERT,UPDATE,DELETE,TRUNCATE\') AS any_write_allowed FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=\'autopilot\' AND c.relname IN (\'native_cli_config\',\'native_cli_receipt\') AND c.relkind=\'r\' ORDER BY 1").fetchall()\n  enabled=\'UNKNOWN_NO_TABLE_ACCESS\'\n  if any(t[\'name\']==\'native_cli_config\' and t[\'select_allowed\'] for t in tables):\n   enabled=[x[\'enabled\'] for x in c.execute(\'SELECT enabled FROM autopilot.native_cli_config LIMIT 2\').fetchall()]\n  c.rollback()\n print(json.dumps(dict(audit=\'NATIVE_DB_METADATA\',read_only=True,tasks_started=False,identity=identity,schema=schema,functions=functions,roles=roles,tables=tables,enabled=enabled),sort_keys=True))\nexcept BaseException:\n print(json.dumps(dict(audit=\'NATIVE_DB_METADATA_BLOCKED\')))\n raise SystemExit(2)\n'
user=pwd.getpwnam('school-autopilot')
def identity():
 os.setgroups([])
 os.setgid(user.pw_gid)
 os.setuid(user.pw_uid)
env_before=hold.read(hold.ENV,0o600,262144)
dsn=hold.env(env_before)['AUTOPILOT_DATABASE_URL']
current_main()
try:
 result=subprocess.run(['/opt/bridge-school/school-autopilot/.venv/bin/python','-I','-B','-c',child],env={'AUDIT_DATABASE_URL':dsn,'PATH':'/usr/bin:/bin'},cwd='/',preexec_fn=identity,capture_output=True,text=True,timeout=60)
finally:
 hold.main()
 if hold.service()!=before: raise RuntimeError('SERVICE_CHANGED')
if hold.read(hold.ENV,0o600,262144)!=env_before: raise RuntimeError('ENV_CHANGED')
current_main()
if result.returncode!=0 or len(result.stdout)>65536: raise RuntimeError('CATALOG_READ_FAILED')
report=json.loads(result.stdout)
if report.get('audit')!='NATIVE_DB_METADATA' or report.get('read_only') is not True or report.get('tasks_started') is not False: raise RuntimeError('OUTPUT_INVALID')
print(json.dumps(report,sort_keys=True))
