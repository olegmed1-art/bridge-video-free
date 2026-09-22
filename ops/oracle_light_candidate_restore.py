"""Restore the pinned old rehearsal snapshot into a fenced persistent candidate."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE = 'autopilot-db-rehearsal-20260922'
SOURCE_DB = 'autopilot_acl_cutf8_rehearsal'
TARGET = 'bridge-autopilot-postgres'
DATABASE = 'autopilot_candidate_20260922'
IMAGE = 'sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280'
ROOT = Path('/home/ubuntu/autopilot-db-migration-20260922')
FILES = {
    'production-autopilot.dump':'d9137d49e3584607055acc35acd65235a69b03c373174da03fe7537a1b158bd5',
    'production-migration-ledger.dump':'572042c1f6e2ce5942a68d6815b5c7d69693c930195f020eafd2a634cc3acb05',
    'production-health-view.dump':'6f50fcdc66e128a2c8ac2347f2b0464073cec3c91d4748130cf379439d32127d',
    'rehearsal-only-acl.sql':'a2171e52f2b5d2a53801910fb2cb416db6269b4cabd66550b1aa266490a12e66',
}
ROLES = ('neondb_owner','autopilot_runtime','autopilot_runtime_principal',
         'autopilot_callback','autopilot_callback_login','autopilot_light_worker_login',
         'bridge_school_worker','bridge_school_worker_principal','bridge_school_reader','bridge_school_health')


def run(*args, input=None):
    return subprocess.run(args,input=input,check=True,capture_output=True,text=True,timeout=180).stdout.strip()


def sql(container,database,statement):
    port='55432' if container==TARGET else '5432'
    return run('docker','exec','-i','--user','postgres',container,'psql','-XAt','-q',
               '-p',port,'-U','postgres','-d',database,'-v','ON_ERROR_STOP=1',input=statement)


MANIFEST = r"""
CREATE TEMP TABLE restore_manifest (k text PRIMARY KEY,n bigint,h text);
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind IN ('r','p','m') AND (n.nspname IN ('autopilot','autopilot_reconcile')
 OR (n.nspname='public' AND c.relname='schema_migration')) LOOP
  EXECUTE format('INSERT INTO restore_manifest SELECT %L,count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),'''' ORDER BY md5(to_jsonb(t)::text)),'''')) FROM %I.%I t',
    item.nspname||'.'||item.relname,item.nspname,item.relname);
 END LOOP;
END $$;
INSERT INTO restore_manifest SELECT 'function_definitions',count(*),
 md5(string_agg(pg_get_functiondef(p.oid),E'\n' ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)))
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('autopilot','autopilot_reconcile');
INSERT INTO restore_manifest SELECT 'effective_acl',count(*),
 md5(string_agg(r.rolname||':'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||
 has_function_privilege(r.oid,p.oid,'EXECUTE')::text,E'\n' ORDER BY r.rolname,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)))
 FROM pg_roles r CROSS JOIN pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND r.rolname IN ('autopilot_callback_login','autopilot_light_worker_login','bridge_school_worker_principal');
SELECT jsonb_object_agg(k,jsonb_build_array(n,h)) FROM restore_manifest;
"""


def main():
    assert run('hostname')=='autopilot-lite-vnic'
    assert os.path.ismount('/srv/autopilot-data')
    source=json.loads(run('docker','inspect',SOURCE))[0]
    target=json.loads(run('docker','inspect',TARGET))[0]
    assert source['Image']==IMAGE and source['HostConfig']['NetworkMode']=='none'
    assert target['Image']==IMAGE and target['Config']['Labels']['managed_by']=='bridge-autopilot-pg-stage-v1'
    assert target['HostConfig']['NetworkMode']=='host' and not target['HostConfig']['PortBindings']
    assert any(m['Source']=='/srv/autopilot-data/postgresql' and m['Destination']=='/var/lib/postgresql' for m in target['Mounts'])
    for name,digest in FILES.items():
        path=ROOT/name
        assert not any(p.is_symlink() for p in [path,*path.parents])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    expected=json.loads(sql(SOURCE,SOURCE_DB,MANIFEST))
    assert expected['function_definitions'][0]==97
    roles=''
    for role in ROLES:
        roles+=f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF; END $$;\n"
    sql(TARGET,'postgres',roles)
    names=','.join("'"+r+"'" for r in ROLES)
    assert sql(TARGET,'postgres',f'SELECT count(*) FROM pg_roles WHERE rolname IN ({names}) AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);')=='0'
    exists=sql(TARGET,'postgres',f"SELECT count(*) FROM pg_database WHERE datname='{DATABASE}';")=='1'
    if not exists:
        sql(TARGET,'postgres',f"CREATE DATABASE {DATABASE} OWNER neondb_owner TEMPLATE template0 LOCALE_PROVIDER builtin LOCALE 'C.UTF-8'; REVOKE ALL PRIVILEGES ON DATABASE {DATABASE} FROM PUBLIC;")
        sql(TARGET,DATABASE,'CREATE EXTENSION pgcrypto; CREATE EXTENSION btree_gist;')
        for name in FILES:
            run('docker','cp',str(ROOT/name),TARGET+':/tmp/'+name)
            run('docker','exec',TARGET,'chown','999:999','/tmp/'+name)
        for name in list(FILES)[:3]:
            run('docker','exec','--user','postgres',TARGET,'pg_restore','-p','55432','-U','postgres','--role=neondb_owner',
                '-d',DATABASE,'--no-owner','--no-privileges','--single-transaction','--exit-on-error','/tmp/'+name)
        sql(TARGET,DATABASE,(ROOT/'rehearsal-only-acl.sql').read_text())
    fence=json.loads(sql(TARGET,'postgres',f"SELECT jsonb_build_array(pg_get_userbyid(datdba),datlocprovider,datlocale,(SELECT count(*) FROM aclexplode(coalesce(datacl,acldefault('d',datdba))) a WHERE a.grantee<>datdba)) FROM pg_database WHERE datname='{DATABASE}';"))
    assert fence==['neondb_owner','b','C.UTF-8',0], 'candidate owner/locale/database ACL mismatch'
    assert sql(TARGET,'postgres',f'SELECT count(*) FROM pg_roles WHERE rolname IN ({names}) AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);')=='0'
    actual=json.loads(sql(TARGET,DATABASE,MANIFEST))
    assert actual==expected, 'candidate differs; preserve for inspection, do not retry restore'
    assert sql(TARGET,DATABASE,"SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND p.prosecdef AND r.rolsuper;")=='0'
    assert sql(TARGET,DATABASE,'SELECT count(*) FROM public.autopilot_operational_health_signal;')=='3'
    result={'candidate':'VERIFIED_FENCED','database':DATABASE,'snapshot_date':'2026-09-22T08:23Z',
            'matched_manifest_entries':len(actual),'functions':97,'all_application_roles_nologin':True,
            'production_cutover':False,'neon_changed':False}
    print(json.dumps(result))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'candidate':'NOT_CONFIRMED','error_type':type(exc).__name__}))
        sys.exit(2)
