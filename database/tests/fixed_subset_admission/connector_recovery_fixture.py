"""One disposable localhost PG18 rehearsal. Never accepts a DSN or remote target.

Exercises the exact generated SQL list in one psycopg transaction, the same
transaction contract documented by Neon run_sql_transaction. Neon transport and
provider permission internals are not emulated; target GUCs below are synthetic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
CODE = ROOT / 'database/recovery/fixed_subset'
sys.path.insert(0, str(CODE))
import build_recovery as r
import connector_packet as c

DATABASE = 'neondb'
ADMIN = 'postgresql://postgres:postgres@127.0.0.1:5432/neondb'
OWNER = 'postgresql://neondb_owner:neondb_owner@127.0.0.1:5432/neondb'
TARGET = dict(project_id='synthetic-project', branch_id='synthetic-recovery',
              endpoint_id='synthetic-localhost', database_name=DATABASE)
OPTIONS = ('-c statement_timeout=60000 -c lock_timeout=5000 -c timezone=UTC '
           '-c datestyle=ISO,YMD -c search_path=pg_catalog,public '
           '-c neon.project_id=synthetic-project -c neon.branch_id=synthetic-recovery '
           '-c neon.endpoint_id=synthetic-localhost')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True)+'\n')


def connect(*, admin=False):
    import psycopg
    return psycopg.connect(ADMIN if admin else OWNER, options=OPTIONS, connect_timeout=5)


def query(sql, *, admin=False):
    with connect(admin=admin) as db:
        return db.execute(sql).fetchone()[0]


def execute(statements, *, admin=False, fail_after=None):
    """Return receipt only AFTER successful commit/context exit."""
    last = None
    with connect(admin=admin) as db:
        for i, statement in enumerate(statements):
            cursor = db.execute(statement)
            if cursor.description:
                last = cursor.fetchone()
            if fail_after == i:
                db.execute("DO $$ BEGIN RAISE EXCEPTION 'SYNTHETIC_INJECTED_FAILURE'; END $$")
    return last[0] if last else None


def command(args, evidence, *, env=None, expected=True):
    proc = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=100)
    evidence.write_text(proc.stdout+proc.stderr)
    if expected:
        assert proc.returncode == 0, evidence.name + ': ' + proc.stderr[-3000:]
    else:
        assert proc.returncode != 0, 'expected rejection: '+evidence.name
    return proc


def psql(path, evidence, *, expected=True):
    return command(['psql', OWNER, '-X', '-v', 'ON_ERROR_STOP=1', '-f', str(path)],
                   evidence, expected=expected)


CATALOG = """SELECT jsonb_build_object(
 'schemas',(SELECT jsonb_agg(jsonb_build_object('nspname',nspname,'owner',pg_get_userbyid(nspowner),'acl',nspacl::text)) FROM pg_namespace WHERE nspname='video_queue'),
 'relations',(SELECT jsonb_agg(jsonb_build_object('relname',relname,'owner',pg_get_userbyid(relowner),'acl',relacl::text) ORDER BY relname) FROM pg_class x JOIN pg_namespace n ON n.oid=x.relnamespace WHERE n.nspname='video_queue' AND x.relname IN ('batch','job','job_event','job_event_event_id_seq')),
 'functions',(SELECT jsonb_agg(jsonb_build_object('signature',p.oid::regprocedure::text,'owner',pg_get_userbyid(proowner),'acl',proacl::text,'definition_md5',md5(pg_get_functiondef(p.oid)),'security_definer',prosecdef,'config',proconfig) ORDER BY p.oid::regprocedure::text) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue' AND proname IN ('canonical_json_text','claim_job','enqueue_drive_batch','finish_job','heartbeat_job','precanary_idle_snapshot','retry_job')))
"""


def snapshot():
    data = {'catalog_md5': query('SELECT '+c.catalog_digest(), admin=True),
            'legacy': query(CATALOG, admin=True),
            'registry': query("SELECT jsonb_agg(to_jsonb(m) ORDER BY migration_key) FROM schema_migration m", admin=True),
            'roles': query("SELECT jsonb_agg(jsonb_build_array(oid,rolname,rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolinherit,rolbypassrls,rolreplication,rolconfig) ORDER BY oid) FROM pg_roles", admin=True),
            'memberships': query('SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY oid),\'[]\') FROM pg_auth_members a', admin=True),
            'default_acls': query('SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY oid),\'[]\') FROM pg_default_acl d', admin=True),
            'function_acl_owner': query("SELECT jsonb_agg(jsonb_build_object('signature',p.oid::regprocedure::text,'owner',pg_get_userbyid(p.proowner),'acl',p.proacl::text,'definition_md5',md5(pg_get_functiondef(p.oid))) ORDER BY p.oid::regprocedure::text) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue'", admin=True),
            'table_acl_owner': query("SELECT jsonb_agg(jsonb_build_object('name',c.relname,'owner',pg_get_userbyid(c.relowner),'acl',c.relacl::text) ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'", admin=True),
            'sequence': query('SELECT jsonb_build_array(last_value,is_called) FROM video_queue.job_event_event_id_seq', admin=True)}
    for table, key in [('batch','batch_id'),('job','job_id'),('job_event','event_id'),
                       ('subset_approval','approval_id'),('source_observation','observation_id'),
                       ('subset_admission','request_key'),('source_reservation','source_identity')]:
        exists = query(f"SELECT to_regclass('video_queue.{table}') IS NOT NULL", admin=True)
        data[table] = query(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY {key}),'[]') FROM video_queue.{table} t", admin=True) if exists else None
    return data


def checksum(path):
    parts = path.with_suffix('')
    if not parts.is_dir():
        return digest(path.read_bytes())
    raw = ('FILE:'+path.name+'\n').encode()+path.read_bytes()
    for part in sorted(parts.glob('*.sql')):
        raw += ('\nFILE:'+parts.name+'/'+part.name+'\n').encode()+part.read_bytes()
    return digest(raw)


def prepare(stage, out):
    state = query("SELECT jsonb_build_object('version',current_setting('server_version_num'),'owner',current_user,'schemas',(SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')),'public_tables',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),'roles',(SELECT count(*) FROM pg_roles WHERE rolname !~ '^pg_' AND rolname<>'postgres'))", admin=True)
    assert 180000 <= int(state['version']) < 190000 and state['owner']=='postgres'
    assert state['schemas']==state['public_tables']==state['roles']==0
    execute(["CREATE EXTENSION pgcrypto", "CREATE ROLE neondb_owner LOGIN PASSWORD 'neondb_owner' CREATEROLE NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS",
             'ALTER DATABASE neondb OWNER TO neondb_owner', 'ALTER SCHEMA public OWNER TO neondb_owner'],admin=True)
    execute(['CREATE ROLE bridge_school_reader NOLOGIN', 'CREATE ROLE bridge_school_app NOLOGIN',
             'CREATE ROLE bridge_school_worker NOLOGIN', 'CREATE ROLE synthetic_unlisted NOLOGIN',
             'GRANT bridge_school_reader TO bridge_school_app', 'GRANT bridge_school_app TO bridge_school_worker',
             'ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
             'CREATE TABLE public.schema_migration(migration_key text PRIMARY KEY,applied_at timestamptz NOT NULL DEFAULT now(),checksum text)'])
    write(out/'server-before.json',state)
    migration_dir=stage/'source/database/migrations'
    migration_dir.mkdir(parents=True)
    for number in ('0056','0057','0058','0059','0060'):
        path=next((ROOT/'database/migrations').glob(number+'_*.sql'))
        shutil.copyfile(path,migration_dir/path.name)
        if path.with_suffix('').is_dir():
            shutil.copytree(path.with_suffix(''),migration_dir/path.stem)
        if number in ('0056','0057','0058'):
            psql(path,out/(number+'-baseline.log'))
            execute([f"UPDATE schema_migration SET checksum='{checksum(path)}' WHERE migration_key='{path.stem}'"])
    (stage/'source/database/tests').mkdir()
    shutil.copyfile(ROOT/'database/tests/041_universal_video_queue.sql',stage/'source/database/tests/041_universal_video_queue.sql')
    # Duplicate source across different closed legacy batches, mixed terminal states.
    for i in range(3):
        file_id='synthetic-source-shared' if i<2 else 'synthetic-source-unique'
        execute([f"""SELECT * FROM video_queue.enqueue_drive_batch(
          'synthetic-history-{i}','synthetic-source-folder','synthetic-output-folder','synthetic-work-folder',
          'bridge_3_1_free','3.1-free-r25.16','{file_id}',repeat('a',64),
          jsonb_build_array(jsonb_build_object('sequence',1,'file_id','{file_id}','name','Synthetic.mp4',
          'mime_type','video/mp4','size_bytes',2000000,'checksum',NULL)))"""])
    execute(["UPDATE video_queue.job SET status=CASE WHEN source_file_id='synthetic-source-unique' THEN 'AMBIGUOUS' ELSE 'FAILED' END, completed_at=now()",
             "UPDATE video_queue.batch SET status='CANARY_BLOCKED',completed_at=now()"])
    registry=query('SELECT jsonb_agg(jsonb_build_object(\'migration_key\',migration_key,\'checksum\',checksum) ORDER BY migration_key) FROM schema_migration')
    catalog=[{'registry':registry},query(CATALOG)]
    write(stage/'catalog-baseline.json',catalog)
    claim=query("SELECT pg_get_functiondef('video_queue.claim_job(text,integer,text,text)'::regprocedure)")
    write(stage/'claim-baseline.json',dict(claim_definition=claim,claim_definition_md5=hashlib.md5(claim.encode()).hexdigest()))
    (stage/'restore_claim_job.sql').write_text(claim)
    write(stage/'target.json',TARGET)
    r.ROOT=c.ROOT=stage
    write(stage/'semantic-catalog-baseline.json',{'catalog_md5':query('SELECT '+c.catalog_digest())})
    r.main()
    assert (stage/'recovery'/f'{r.KEY}.sql').read_bytes()==(CODE/f'{r.KEY}.sql').read_bytes(), 'synthetic restore differs from pinned repair'


def failure_case(name, statements, out, *, fail_after=None, error='SYNTHETIC_INJECTED_FAILURE'):
    import psycopg
    before=snapshot()
    try:
        execute(statements, fail_after=fail_after)
    except psycopg.Error as exc:
        assert exc.sqlstate=='P0001' and str(exc).splitlines()[0]==error, str(exc)
        detail=dict(name=name,status='PASS',sqlstate=exc.sqlstate,message=error,
                    inject_after=fail_after,before_sha256=digest(json.dumps(before,sort_keys=True).encode()))
    else:
        raise AssertionError(name+' did not fail')
    after=snapshot()
    assert before==after, name+' leaked catalog/rights/rows/sequence/history'
    detail['after_sha256']=digest(json.dumps(after,sort_keys=True).encode())
    detail['exact_snapshot_equal']=True
    write(out/(name+'.json'),detail)
    return detail


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--evidence',type=Path)
    args=parser.parse_args(argv)
    if not args.execute:
        print('Offline: one localhost PG18 installer/verifier/recovery rehearsal; --execute required')
        return
    assert sys.platform=='linux' and args.evidence
    assert not any(os.environ.get(k) for k in ('DATABASE_URL','NEON_API_KEY','PGSERVICE','PGHOST','PGPASSWORD'))
    out=args.evidence.resolve(); out.mkdir(parents=True,exist_ok=True)
    result={'status':'FAIL_OR_UNKNOWN','scope':'DISPOSABLE_SYNTHETIC_ONLY','neon_transport_tested':False,'negative_cases':[]}
    try:
        with tempfile.TemporaryDirectory(prefix='connector-recovery-') as tmp:
            stage=Path(tmp)
            prepare(stage,out)
            before=snapshot(); write(out/'before-install.json',before)
            install=c.install_payload()['sql_statements']
            write(out/'install-envelope.json',install)
            grant=next(i for i,s in enumerate(install) if 'GRANT video_subset_owner TO CURRENT_USER' in s)
            transfer=next(i for i,s in enumerate(install) if 'END $privileges$' in s)
            for name,index in [('install-after-grant',grant),('install-after-owner-transfer',transfer),('install-before-commit',len(install)-1)]:
                result['negative_cases'].append(failure_case(name,install,out,fail_after=index))
            # Deliberately bless the changed initial digest: the complete post-ACL
            # guard, not the opaque initial hash, must reject this new recipient.
            execute(['ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO synthetic_unlisted'])
            write(stage/'semantic-catalog-baseline.json',{'catalog_md5':query('SELECT '+c.catalog_digest())})
            result['negative_cases'].append(failure_case('unknown-default-function-recipient',c.install_payload()['sql_statements'],out,error='INSTALL_EXACT_FUNCTION_ACL'))
            execute(['ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM synthetic_unlisted'])
            write(stage/'semantic-catalog-baseline.json',{'catalog_md5':query('SELECT '+c.catalog_digest())})
            install=c.install_payload()['sql_statements']
            receipt=execute(install); write(out/'committed-install-receipt.json',receipt)
            installed=snapshot(); write(out/'after-install.json',installed)
            for key in ('batch','job','job_event','sequence'):
                assert installed[key]==before[key]
            assert sorted(len(x['historical_job_ids']) for x in installed['source_reservation'])==[1,2]
            psql(ROOT/'database/tests/041_universal_video_queue.sql',out/'041-installed.log')
            verify=c.verify_payload(receipt)['sql_statements']
            write(out/'verify-envelope.json',verify)
            index=verify.index(c.TEMP_GRANT)
            result['negative_cases'].append(failure_case('verify-after-grant',verify,out,fail_after=index))
            assert execute(verify)=='COMMITTED_INSTALL_RECEIPT_MATCHES'
            assert snapshot()==installed, 'verifier changed persistent state'
            recover=c.recover_payload(receipt)['sql_statements']
            write(out/'recovery-envelope.json',recover)
            for name,index in [('recovery-after-grant',recover.index(c.TEMP_GRANT)),
                               ('recovery-after-owner-restore',next(i for i,s in enumerate(recover) if c.bare(s).startswith('ALTER FUNCTION '+r.CLAIM+' OWNER TO')))]:
                result['negative_cases'].append(failure_case(name,recover,out,fail_after=index))
            recovery_receipt=execute(recover); write(out/'committed-recovery-receipt.json',recovery_receipt)
            recovered=snapshot(); write(out/'after-recovery.json',recovered)
            for key in ('batch','job','job_event','sequence','source_reservation','subset_approval','source_observation','subset_admission','roles','memberships'):
                assert recovered[key]==installed[key], key+' not preserved'
            assert recovered['legacy']==before['legacy'], 'legacy ACL/owner/definition mismatch'
            prior={x['migration_key']:x for x in installed['registry']}
            current={x['migration_key']:x for x in recovered['registry']}
            assert all(current[k]==v for k,v in prior.items()) and set(current)-set(prior)=={r.KEY}
            # Ordinary migrator sees only the affected five baseline migrations;
            # all are already applied. No unrelated full-preflight is performed.
            env=os.environ.copy(); env.update(DATABASE_URL=OWNER,MIGRATIONS_DIR=str(stage/'source/database/migrations'),EXTERNAL_HISTORY_FILE=str(ROOT/'database/external_migration_history.tsv'))
            rerun=command(['bash','database/scripts/migrate.sh'],out/'migrator-history.log',env=env)
            assert rerun.stdout.count('Skipping already-applied migration ')==5
            assert 'Verified audited external migration history: '+r.KEY in rerun.stdout
            assert 'Applying migration ' not in rerun.stdout and 'Bootstrapping checksum' not in rerun.stdout
            assert snapshot()==recovered
            for number in ('041','042','043'):
                path=next((ROOT/'database/tests').glob(number+'_*.sql'))
                psql(path,out/(number+'-recovered.log'))
            # Each consumer must reject a forged checksum, a missing recovery
            # marker, and an active new capability in a claimed recovered state.
            repair_row=next(x for x in recovered['registry'] if x['migration_key']==r.KEY)
            for case, mutation in [('bad-repair-checksum',f"UPDATE schema_migration SET checksum=repeat('0',64) WHERE migration_key='{r.KEY}'"),
                                   ('missing-repair-marker',f"DELETE FROM schema_migration WHERE migration_key='{r.KEY}'"),
                                   ('recovered-api-enabled','GRANT EXECUTE ON FUNCTION video_queue.subset_disabled() TO synthetic_unlisted')]:
                for number in ('041','042','043'):
                    path=next((ROOT/'database/tests').glob(number+'_*.sql'))
                    original=snapshot()
                    execute([mutation],admin=True)
                    try:
                        failed=psql(path,out/(case+'-'+number+'.log'),expected=False)
                        expected='SUBSET_EXACT_FUNCTION_ACL' if case=='missing-repair-marker' else 'LEGACY_RECOVERY_STATE_INVALID'
                        assert expected in failed.stderr, failed.stderr
                    finally:
                        # Synthetic fault cleanup only, exact old marker restored.
                        with connect(admin=True) as db:
                            db.execute('INSERT INTO schema_migration(migration_key,applied_at,checksum) VALUES(%s,%s,%s) ON CONFLICT(migration_key) DO UPDATE SET applied_at=excluded.applied_at,checksum=excluded.checksum',
                                       (repair_row['migration_key'],repair_row['applied_at'],repair_row['checksum']))
                            db.execute('REVOKE EXECUTE ON FUNCTION video_queue.subset_disabled() FROM synthetic_unlisted')
                    assert snapshot()==original
                    result['negative_cases'].append(dict(name=case+'-'+number,status='PASS',scope='full-psql-consumer',exact_snapshot_equal=True))
            result.update(status='PASS_ISOLATED_CONNECTOR_RECOVERY',invariants={'041':'PASS_INSTALLED_AND_RECOVERED','042':'PASS_RECOVERED_LEGACY','043':'PASS_RECOVERED_SIZE_BOUNDARIES'},
                old_rows_preserved=True,duplicate_reservations_preserved=True,sequence_preserved_by_recovery=True,
                legacy_acl_owner_definition_equal=True,retained_0059=True,retained_tables=4,retained_nologin_roles=5,
                migrator='FIVE_SKIPS_EXTERNAL_REPAIR_RECOGNIZED',temporary_grants_persist=False,
                repair_sha256=digest((CODE/f'{r.KEY}.sql').read_bytes()))
    finally:
        write(out/'results.json',result)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
