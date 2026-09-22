"""Export the fenced candidate and drill a bounded downloaded archive; no cutover."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import oracle_light_candidate_restore as candidate

ROOT=candidate.ROOT
ARCHIVE=ROOT/'candidate-backup-v2-20260922.tar.gz'
DOWNLOADED=ROOT/'candidate-backup-v2-from-object-storage-20260922.tar.gz'
DRILL='autopilot_restore_drill_v2_20260922'
MEMBERS={'autopilot.dump','ledger.dump','health.dump','acl.sql','manifest.json'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_archive(path):
    assert path.is_file() and not path.is_symlink()
    assert 0<path.stat().st_size<32*1024**2
    payloads={}
    total=0
    with tarfile.open(path,'r:gz') as archive:
        for entry in archive:
            assert entry.name in MEMBERS and entry.name not in payloads
            assert entry.isfile() and 0<=entry.size<16*1024**2
            total+=entry.size
            assert total<32*1024**2
            with archive.extractfile(entry) as source:
                payloads[entry.name]=source.read(entry.size+1)
            assert len(payloads[entry.name])==entry.size
    assert set(payloads)==MEMBERS
    assert all(payloads[name].startswith(b'PGDMP') for name in ('autopilot.dump','ledger.dump','health.dump'))
    manifest=json.loads(payloads['manifest.json'])
    assert manifest['format']==1 and manifest['database']==candidate.DATABASE
    assert manifest['snapshot_scope']=='FENCED_CANDIDATE_ONLY'
    assert manifest['roles_nologin']==list(candidate.ROLES) and manifest['locale']=='C.UTF-8'
    assert set(manifest['files'])==MEMBERS-{'manifest.json'}
    assert all(hashlib.sha256(payloads[name]).hexdigest()==sha for name,sha in manifest['files'].items())
    assert manifest['files']['acl.sql']==candidate.FILES['rehearsal-only-acl.sql']
    assert manifest['data']['function_definitions'][0]==97
    assert manifest['data']['effective_acl'][0]==291
    return payloads,manifest


def export():
    candidate.main()
    assert not ARCHIVE.exists(), 'existing archive preserved'
    with tempfile.TemporaryDirectory(prefix='candidate-export-',dir=ROOT) as directory:
        work=Path(directory)
        selections={
            'autopilot.dump':['--schema=autopilot','--schema=autopilot_reconcile'],
            'ledger.dump':['--table=public.schema_migration'],
            'health.dump':['--table=public.autopilot_operational_health_signal'],
        }
        for name,selection in selections.items():
            with (work/name).open('xb') as output:
                subprocess.run(['docker','exec','--user','postgres',candidate.TARGET,'pg_dump',
                    '-p','55432','-U','postgres','-d',candidate.DATABASE,'-Fc','--no-owner','--no-privileges',
                    *selection],stdout=output,stderr=subprocess.PIPE,check=True,timeout=180)
        (work/'acl.sql').write_bytes((ROOT/'rehearsal-only-acl.sql').read_bytes())
        manifest={'format':1,'database':candidate.DATABASE,'snapshot_scope':'FENCED_CANDIDATE_ONLY',
                  'created_at':datetime.now(timezone.utc).isoformat(),'locale':'C.UTF-8',
                  'roles_nologin':list(candidate.ROLES),
                  'data':json.loads(candidate.sql(candidate.TARGET,candidate.DATABASE,candidate.MANIFEST)),
                  'files':{p.name:digest(p) for p in work.iterdir()}}
        (work/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
        with tarfile.open(ARCHIVE,'x:gz') as archive:
            for name in sorted(MEMBERS):
                archive.add(work/name,arcname=name,recursive=False)
    ARCHIVE.chmod(0o600)
    assert ARCHIVE.stat().st_size<32*1024**2
    print(json.dumps({'candidate_backup':'EXPORTED','bytes':ARCHIVE.stat().st_size,'sha256':digest(ARCHIVE),
                      'off_vm':False,'production_backup':False}))


def restore():
    candidate.main()
    assert DOWNLOADED.is_file() and not DOWNLOADED.is_symlink()
    assert ARCHIVE.is_file() and not ARCHIVE.is_symlink()
    assert digest(DOWNLOADED)==digest(ARCHIVE)
    assert DOWNLOADED.stat().st_size<32*1024**2
    with tempfile.TemporaryDirectory(prefix='candidate-drill-',dir=ROOT) as directory:
        work=Path(directory)
        payloads,manifest=validate_archive(DOWNLOADED)
        for name,data in payloads.items():
            (work/name).write_bytes(data)
        exists=candidate.sql(candidate.TARGET,'postgres',f"SELECT count(*) FROM pg_database WHERE datname='{DRILL}';")=='1'
        if not exists:
            candidate.sql(candidate.TARGET,'postgres',f"CREATE DATABASE {DRILL} OWNER neondb_owner TEMPLATE template0 LOCALE_PROVIDER builtin LOCALE 'C.UTF-8'; REVOKE ALL PRIVILEGES ON DATABASE {DRILL} FROM PUBLIC;")
            candidate.sql(candidate.TARGET,DRILL,'CREATE EXTENSION pgcrypto; CREATE EXTENSION btree_gist;')
            for name in ('autopilot.dump','ledger.dump','health.dump'):
                dest='/tmp/drill-'+name
                candidate.run('docker','cp',str(work/name),candidate.TARGET+':'+dest)
                candidate.run('docker','exec',candidate.TARGET,'chown','999:999',dest)
                candidate.run('docker','exec','--user','postgres',candidate.TARGET,'pg_restore',
                              '-p','55432','-U','postgres','--role=neondb_owner','-d',DRILL,
                              '--no-owner','--no-privileges','--single-transaction','--exit-on-error',dest)
            candidate.sql(candidate.TARGET,DRILL,(work/'acl.sql').read_text())
        fence=json.loads(candidate.sql(candidate.TARGET,'postgres',f"SELECT jsonb_build_array(pg_get_userbyid(datdba),datlocprovider,datlocale,(SELECT count(*) FROM aclexplode(coalesce(datacl,acldefault('d',datdba))) a WHERE a.grantee<>datdba)) FROM pg_database WHERE datname='{DRILL}';"))
        assert fence==['neondb_owner','b','C.UTF-8',0]
        candidate.sql(candidate.TARGET,DRILL,candidate.SUPPORT_SQL)
        actual=json.loads(candidate.sql(candidate.TARGET,DRILL,candidate.MANIFEST))
        assert actual==manifest['data']
        names=','.join("'"+r+"'" for r in candidate.ROLES)
        assert candidate.sql(candidate.TARGET,'postgres',f'SELECT count(*) FROM pg_roles WHERE rolname IN ({names}) AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);')=='0'
        assert candidate.sql(candidate.TARGET,DRILL,"SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND p.prosecdef AND r.rolsuper;")=='0'
        assert candidate.sql(candidate.TARGET,DRILL,'SELECT count(*) FROM public.autopilot_operational_health_signal;')=='3'
    print(json.dumps({'candidate_restore_drill':'PASS','database':DRILL,'matched_manifest_entries':len(actual),
                      'archive_sha256':digest(DOWNLOADED),'production_cutover':False}))


if __name__=='__main__':
    try:
        os.umask(0o077)
        assert candidate.run('hostname')=='autopilot-lite-vnic'
        assert len(sys.argv)==2 and sys.argv[1] in ('export','restore')
        export() if sys.argv[1]=='export' else restore()
    except Exception as exc:
        print(json.dumps({'candidate_backup_drill':'NOT_CONFIRMED','error_type':type(exc).__name__}))
        sys.exit(2)
