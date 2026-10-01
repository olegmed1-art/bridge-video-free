"""Explicit synthetic, loopback-only PostgreSQL 18 encrypted restore rehearsal.

Run from the repository root with --bin-dir and --work-dir. All databases,
credentials, rows and large objects are generated here; no environment secrets,
external services or production connection strings are used. Only the validation
helper's statistics SQL is imported; its connection/execution code is not run.
The fresh cluster is stopped in finally and retained for inspection.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.neon_backup_validate_once import STATS


def verify_checksum(path, expected):
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError('LOCAL_ARTIFACT_CHECKSUM_MISMATCH')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', required=True)
    parser.add_argument('--work-dir', required=True)
    parser.add_argument('--openssl', default='C:/Program Files/Git/usr/bin/openssl.exe')
    args = parser.parse_args()
    binaries = Path(args.bin_dir).resolve()
    openssl = Path(args.openssl).resolve()
    root = Path(args.work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cluster = Path(tempfile.mkdtemp(prefix='backup-roundtrip-', dir=root))
    suffix = '.exe' if os.name == 'nt' else ''
    # Explicit OS plumbing allowlist: never inherit a connection or secret.
    env = {key: os.environ[key] for key in
           ('SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT')
           if key in os.environ}
    env.update(LC_ALL='C', PATH=os.pathsep.join((str(binaries), str(openssl.parent))))
    hidden = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def run(tool, argv, *, sql=None, encryption=False):
        executable = openssl if tool == 'openssl' else binaries / (tool + suffix)
        child_env = dict(env)
        if encryption:
            child_env['LOCAL_FIXTURE_PASSPHRASE'] = 'synthetic-local-backup-fixture-only-2026'
        if tool == 'pg_ctl':
            # A Windows daemon may keep inherited pipe handles open.
            with (cluster / 'control.log').open('a', encoding='utf-8') as output:
                result = subprocess.run([str(executable), *argv],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    env=child_env, timeout=30, creationflags=hidden)
        else:
            result = subprocess.run([str(executable), *argv], input=sql,
                text=True, capture_output=True, env=child_env,
                timeout=60, creationflags=hidden)
        if result.returncode != 0:
            raise RuntimeError('LOCAL_' + tool.upper() + '_FAILED')
        return result

    postgres_version = run('postgres', ['--version']).stdout.strip()
    assert postgres_version.startswith('postgres (PostgreSQL) 18.')
    run('openssl', ['version'])
    run('initdb', ['-D', str(cluster / 'data'), '--username=postgres',
                   '--auth=trust', '--encoding=UTF8', '--locale=C'])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    connection = ['--host=127.0.0.1', '--port=' + str(port), '--username=postgres']

    def sql(statement, database='postgres'):
        return run('psql', [*connection, '--dbname=' + database, '-XqAt',
                           '-v', 'ON_ERROR_STOP=1'], sql=statement).stdout.strip()

    source = 'fixture_source'
    restored = 'fixture_restored'
    try:
        run('pg_ctl', ['-D', str(cluster / 'data'), '-l', str(cluster / 'server.log'),
                      '-o', f'-h 127.0.0.1 -p {port} -F', '-w', 'start'])
        sql('CREATE DATABASE fixture_source; CREATE DATABASE fixture_restored;')
        sql('''CREATE SCHEMA assistant_lab;
            CREATE SCHEMA fixture;
            CREATE TABLE assistant_lab.job(id integer PRIMARY KEY, state text NOT NULL);
            CREATE TABLE assistant_lab.research_job(id integer PRIMARY KEY, label text NOT NULL);
            INSERT INTO assistant_lab.job VALUES (1,'synthetic-ready'),(2,'synthetic-done');
            INSERT INTO assistant_lab.research_job VALUES (1,'synthetic-research');
            CREATE TABLE public.recovery_checkpoint(id integer PRIMARY KEY);
            CREATE TABLE public.recovery_verification(id integer PRIMARY KEY);
            INSERT INTO public.recovery_checkpoint VALUES (1);
            INSERT INTO public.recovery_verification VALUES (1);
            CREATE SEQUENCE fixture.seq START 7;
            SELECT nextval('fixture.seq');
            CREATE TABLE fixture.sample(id integer PRIMARY KEY, payload text);
            INSERT INTO fixture.sample VALUES (1,'synthetic backup data');
            SELECT lo_from_bytea(730001, decode('00010203aabbccdd','hex'));
            ''', source)

        counts = '''SELECT json_build_object(
            'tables', (SELECT count(*) FROM information_schema.tables
                       WHERE table_schema NOT IN ('pg_catalog','information_schema')),
            'schemas', (SELECT count(*) FROM information_schema.schemata
                        WHERE schema_name !~ '^pg_' AND schema_name <> 'information_schema'),
            'jobs', (SELECT count(*) FROM assistant_lab.job),
            'research_jobs', (SELECT count(*) FROM assistant_lab.research_job),
            'checkpoint', to_regclass('public.recovery_checkpoint') IS NOT NULL,
            'verification', to_regclass('public.recovery_verification') IS NOT NULL,
            'sequence_value', (SELECT last_value FROM fixture.seq),
            'sample', (SELECT payload FROM fixture.sample WHERE id=1),
            'large_object', encode(lo_get(730001), 'hex'));
            '''
        expected = json.loads(sql(counts, source))
        assert sql(STATS, source) == '5|3|2|1', 'LOCAL_SOURCE_VALIDATION_STATS_MISMATCH'
        assert expected == dict(tables=5, schemas=3, jobs=2, research_jobs=1,
            checkpoint=True, verification=True, sequence_value=7,
            sample='synthetic backup data', large_object='00010203aabbccdd')

        dump = cluster / 'neon.dump'
        encrypted = cluster / 'neon.dump.enc'
        decrypted = cluster / 'neon.restore.dump'
        run('pg_dump', [*connection, '--dbname=' + source, '--format=custom',
            '--compress=9', '--no-owner', '--no-privileges', '--file=' + str(dump)])
        assert dump.stat().st_size > 0
        dump_digest = hashlib.sha256(dump.read_bytes()).hexdigest()
        cipher = ['-aes-256-cbc', '-pbkdf2', '-iter', '250000', '-md', 'sha256',
                  '-pass', 'env:LOCAL_FIXTURE_PASSPHRASE']
        run('openssl', ['enc', *cipher, '-salt', '-in', str(dump), '-out', str(encrypted)],
            encryption=True)
        dump.unlink()  # Same plaintext-removal boundary as the workflow.
        encrypted_digest = hashlib.sha256(encrypted.read_bytes()).hexdigest()
        downloaded = cluster / 'downloaded.dump.enc'
        downloaded.write_bytes(encrypted.read_bytes())
        verify_checksum(downloaded, encrypted_digest)

        corrupt = cluster / 'corrupt.dump.enc'
        damaged = bytearray(downloaded.read_bytes())
        damaged[-1] ^= 1
        corrupt.write_bytes(damaged)
        try:
            verify_checksum(corrupt, encrypted_digest)
        except ValueError as failure:
            assert str(failure) == 'LOCAL_ARTIFACT_CHECKSUM_MISMATCH'
        else:
            raise AssertionError('LOCAL_CORRUPT_ARTIFACT_NOT_REFUSED')

        run('openssl', ['enc', '-d', *cipher, '-in', str(downloaded), '-out', str(decrypted)],
            encryption=True)
        verify_checksum(decrypted, dump_digest)
        run('pg_restore', [*connection, '--dbname=' + restored,
            '--no-owner', '--no-privileges', '--exit-on-error', str(decrypted)])
        decrypted.unlink()
        assert json.loads(sql(counts, restored)) == expected, 'LOCAL_RESTORE_CONTENT_MISMATCH'
        assert sql(STATS, restored) == '5|3|2|1', 'LOCAL_RESTORE_VALIDATION_STATS_MISMATCH'
        print(json.dumps(dict(
            result='LOCAL_PG18_ENCRYPTED_ROUNDTRIP_PASS', postgres_major=18,
            postgres_version=postgres_version, validation_stats_sql_verified=True,
            restored_tables=expected['tables'], restored_schemas=expected['schemas'],
            restored_jobs=expected['jobs'], restored_research_jobs=expected['research_jobs'],
            sequence_and_large_object_verified=True, corrupt_artifact_refused=True,
            encrypted_bytes=encrypted.stat().st_size,
            production_connections=False, environment_secrets=False), sort_keys=True))
    finally:
        run('pg_ctl', ['-D', str(cluster / 'data'), '-m', 'immediate', '-w', 'stop'])


if __name__ == '__main__':
    main()
