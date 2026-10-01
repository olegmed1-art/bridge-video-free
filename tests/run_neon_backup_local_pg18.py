"""Explicit local-only PG18 integration: synthetic fresh cluster, no secrets.

Usage: python -m tests.run_neon_backup_local_pg18 is NOT required by production.
Run as a script from repo root, with --bin-dir and --work-dir inside a test area.
No production helper connection parameters are used; every psql host is literal
127.0.0.1. Cluster is stopped in finally; files are retained for inspection.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops import neon_backup_source as backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', required=True)
    parser.add_argument('--work-dir', required=True)
    args = parser.parse_args()
    binaries = Path(args.bin_dir).resolve()
    root = Path(args.work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cluster = Path(tempfile.mkdtemp(prefix='backup-fixture-', dir=root))
    suffix = '.exe' if os.name == 'nt' else ''
    env = {k:v for k,v in os.environ.items() if not k.startswith('PG')
           and k not in ('DATABASE_URL','BACKUP_PASSPHRASE')}
    env['LC_ALL'] = 'C'
    hidden = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def run(tool, argv, sql=None, pg=None):
        if tool == 'pg_ctl':
            # Windows background server can inherit pipe handles. Regular files
            # avoid communicate() waiting for the daemon to close those pipes.
            with (cluster/'control.log').open('a', encoding='utf-8') as output:
                return subprocess.run([str(binaries/(tool+suffix)), *argv],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    env=env, timeout=30, creationflags=hidden)
        return subprocess.run([str(binaries/(tool+suffix)), *argv], input=sql,
            text=True, capture_output=True, env={**env, **(pg or {})}, timeout=30,
            creationflags=hidden)

    assert run('postgres', ['--version']).stdout.startswith('postgres (PostgreSQL) 18.')
    assert run('initdb', ['-D', str(cluster/'data'), '--username=postgres',
                          '--auth=trust', '--encoding=UTF8', '--locale=C']).returncode == 0
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); port = s.getsockname()[1]
    pg = dict(PGHOST='127.0.0.1', PGPORT=str(port), PGDATABASE='postgres',
              PGUSER='postgres', PGSSLMODE='disable', PGCHANNELBINDING='prefer', PGCONNECT_TIMEOUT='2')

    def sql(value, role='postgres', options='', expected=0):
        result = run('psql', ['-XqAt', '-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose'],
                     value, {**pg, 'PGUSER':role, 'PGOPTIONS':options})
        assert result.returncode == expected, 'LOCAL_SQL_FIXTURE_FAILED'
        return result

    try:
        started = run('pg_ctl', ['-D',str(cluster/'data'), '-l',str(cluster/'server.log'),
            '-o',f'-h 127.0.0.1 -p {port} -F', '-w','start'])
        assert started.returncode == 0, 'LOCAL_SERVER_START_FAILED'
        sql('''CREATE ROLE backup_fixture LOGIN;
            CREATE SCHEMA fixture;
            GRANT USAGE ON SCHEMA fixture TO backup_fixture;
            CREATE TABLE fixture.sample(id int);
            CREATE SEQUENCE fixture.seq;
            SELECT lo_create(730001);
            GRANT SELECT ON fixture.sample, fixture.seq TO backup_fixture;
            GRANT SELECT ON LARGE OBJECT 730001 TO backup_fixture;''')

        # Reproduce the unsafe legacy AND predicate: SQL evaluation order is
        # not left-to-right, so the sequence-only function can see a table OID.
        legacy = backup.PREFLIGHT.replace(
            "CASE WHEN c.relkind='S' THEN NOT has_sequence_privilege(c.oid,'SELECT') ELSE false END",
            "c.relkind='S' AND NOT has_sequence_privilege(c.oid,'SELECT')")
        failed = sql(legacy, 'backup_fixture', backup.OPTIONS, expected=3)
        assert '42809' in failed.stderr and 'not a sequence' in failed.stderr
        assert backup.classify_client_failure(failed.returncode, failed.stderr)=='SQL_FAILED'

        def catalog():
            row = json.loads(sql(backup.PREFLIGHT, 'backup_fixture', backup.OPTIONS).stdout)
            assert row['version']==18 and row['session'][3]=='on'
            assert row['identity'] is None  # Vanilla PG cannot prove Neon identity.
            return row

        row = catalog()
        for key in ('denied_schemas','denied_tables','denied_sequences','denied_largeobjects','denied_rls'):
            assert row[key]==0, key
        for revoke, grant, key in (
            ('REVOKE USAGE ON SCHEMA fixture FROM backup_fixture','GRANT USAGE ON SCHEMA fixture TO backup_fixture','denied_schemas'),
            ('REVOKE SELECT ON fixture.sample FROM backup_fixture','GRANT SELECT ON fixture.sample TO backup_fixture','denied_tables'),
            ('REVOKE SELECT ON fixture.seq FROM backup_fixture','GRANT SELECT ON fixture.seq TO backup_fixture','denied_sequences'),
            ('REVOKE SELECT ON LARGE OBJECT 730001 FROM backup_fixture','GRANT SELECT ON LARGE OBJECT 730001 TO backup_fixture','denied_largeobjects')):
            sql(revoke); assert catalog()[key]>0; sql(grant)
        sql('ALTER TABLE fixture.sample ENABLE ROW LEVEL SECURITY')
        assert catalog()['denied_rls']==1
        sql('ALTER TABLE fixture.sample OWNER TO backup_fixture; ALTER TABLE fixture.sample FORCE ROW LEVEL SECURITY')
        assert catalog()['denied_rls']==1
        sql('ALTER ROLE backup_fixture BYPASSRLS')
        assert catalog()['denied_rls']==0
        for query, options, code in (
            ('SELECT this_is_not_a_function()', '', 'SQL_FAILED'),
            ('SELECT pg_sleep(1)', '-c statement_timeout=10', 'STATEMENT_TIMEOUT'),
            ('INSERT INTO fixture.sample VALUES(1)', backup.OPTIONS, 'READ_ONLY_REFUSED')):
            result=sql(query,options=options,expected=3)
            assert backup.classify_client_failure(result.returncode,result.stderr)==code
        result=run('psql',['-XqAt','-c','SELECT 1'],pg={**pg,'PGSSLMODE':'require'})
        assert result.returncode!=0 and backup.classify_client_failure(result.returncode,result.stderr)=='TLS_FAILED'
        print('LOCAL_PG18_CATALOG_ACL_RLS_SQL_TIMEOUT_TLS_READONLY_PASS')
    finally:
        stopped=run('pg_ctl',['-D',str(cluster/'data'),'-m','immediate','-w','stop'])
        assert stopped.returncode==0, 'LOCAL_SERVER_STOP_NOT_CONFIRMED'


if __name__ == '__main__': main()
