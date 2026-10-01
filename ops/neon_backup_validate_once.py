"""Review-only local roundtrip. Never uploads data; never restores to Neon.

All child diagnostics are private. Only allowlisted gates reach job summary.
Linux/Docker execution only; local tests inject synthetic commands and clocks.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from ops import neon_backup_source as source
from ops import neon_backup_preflight_once as context_guard

MAX_BYTES = 200 * 1024 * 1024
WORK_SECONDS = 1080
TOKEN = 'LOCAL_VALIDATION_NO_UPLOAD_V1'
# Catalog metadata is role independent, unlike information_schema visibility.
# Compare identical user-object sets; never weaken critical row-count equality.
STATS = """SELECT
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
     AND c.relkind IN ('r','p','v','m','f'))::text || '|' ||
 (SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_'
   AND nspname <> 'information_schema')::text || '|' ||
 (SELECT count(*) FROM assistant_lab.job)::text || '|' ||
 (SELECT count(*) FROM assistant_lab.research_job)::text;
"""
VERIFY = STATS + "SELECT to_regclass('public.recovery_checkpoint') IS NOT NULL AND to_regclass('public.recovery_verification') IS NOT NULL;"


class Refused(Exception):
    pass


def need(value):
    if not value:
        raise Refused()


def minimal_env():
    return {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}


def hash_file(path):
    need(path.is_file() and 0 < path.stat().st_size <= MAX_BYTES)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.digest()


def stats(value):
    need(re.fullmatch(r'[0-9]+\|[0-9]+\|[0-9]+\|[0-9]+', value) is not None)
    return value


class Runner:
    def __init__(self, root, run_id, deadline):
        self.root = root
        self.names = ['backup-validation-' + run_id + '-' + x for x in ('source', 'restore')]
        self.deadline = deadline

    def run(self, args, *, seconds=30, env=None, sql=None, stdin=None, output=False):
        remaining = self.deadline - time.monotonic()
        need(remaining > 0)
        result = subprocess.run(args, input=sql, stdin=stdin,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=env or minimal_env(),
            timeout=min(seconds, remaining))
        need(result.returncode == 0)
        return result.stdout.decode('utf-8').strip() if output else ''

    def source_client(self, pg, args, *, sql=None, dump=False):
        cmd = ['docker', 'run', '--rm', '--pull=never', '--name', self.names[0],
               '--log-driver=none', '--cpus=1', '--memory=512m', '--memory-swap=512m',
               '--read-only', '--tmpfs', '/tmp:rw,size=16777216,mode=1777',
               '--pids-limit=64', '--ulimit', 'core=0:0',
               '--security-opt=no-new-privileges', '--cap-drop=ALL',
               '--user', str(os.getuid()) + ':' + str(os.getgid()), '-i',
               '--ulimit', 'fsize=' + str(MAX_BYTES) + ':' + str(MAX_BYTES),
               '--mount', 'type=bind,source=/etc/ssl/certs/ca-certificates.crt,target=/backup-ca.crt,readonly']
        for key in pg:
            cmd.extend(['-e', key])
        if dump:
            cmd.extend(['--mount', 'type=bind,source=' + str(self.root) + ',target=/data'])
        cmd.extend(['-e', 'LC_ALL=C', 'postgres:18', *args])
        return self.run(cmd, seconds=360 if dump else 45,
            env={**minimal_env(), **pg}, sql=sql.encode() if sql else None, output=not dump)

    def crypt(self, phrase, decrypt=False):
        # prlimit imposes the cap while writing, including CBC expansion.
        cmd = ['prlimit', '--fsize=' + str(MAX_BYTES) + ':' + str(MAX_BYTES), '--',
               'openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '250000', '-md', 'sha256',
               '-pass', 'env:BACKUP_PASSPHRASE']
        cmd += ['-d'] if decrypt else ['-salt']
        cmd += ['-in', str(self.root / ('encrypted' if decrypt else 'dump')),
                '-out', str(self.root / ('restored' if decrypt else 'encrypted'))]
        self.run(cmd, seconds=60, env={**minimal_env(), 'BACKUP_PASSPHRASE': phrase})

    def restore(self, expected):
        self.run(['docker', 'run', '-d', '--pull=never', '--name', self.names[1],
            '--network=none', '--log-driver=none', '--cpus=2', '--memory=2g', '--memory-swap=2g',
            '--read-only', '--tmpfs', '/tmp:rw,size=16777216,mode=1777',
            '--tmpfs', '/var/run/postgresql:rw,size=16777216,mode=3775',
            '--pids-limit=256', '--ulimit', 'core=0:0',
            '--security-opt=no-new-privileges',
            '--tmpfs', '/var/lib/postgresql:rw,size=1073741824',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=recovery',
            'postgres:18', '-c', 'listen_addresses=', '-c', 'statement_timeout=300000'])
        # Readiness observations target only isolated local PostgreSQL, not production retries.
        ready_until = min(self.deadline, time.monotonic() + 45)
        while True:
            try:
                self.run(['docker', 'exec', self.names[1], 'sh', '-c',
                    'test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres -d recovery'], seconds=3)
                break
            except Refused:
                need(time.monotonic() < ready_until)
                time.sleep(1)
        with (self.root / 'restored').open('rb') as stream:
            self.run(['docker', 'exec', '-i', self.names[1], 'pg_restore', '-U', 'postgres',
                '-d', 'recovery', '--no-owner', '--no-privileges', '--exit-on-error'],
                seconds=300, stdin=stream)
        value = self.run(['docker', 'exec', '-i', self.names[1], 'psql', '-U', 'postgres',
            '-d', 'recovery', '-XqAt', '-v', 'ON_ERROR_STOP=1'], sql=VERIFY.encode(), output=True)
        need(value == expected + '\nt')

    def cleanup(self):
        # Use a separate short cleanup budget even when the work deadline expired.
        ok = True
        for name in self.names:
            try:
                removed = subprocess.run(['docker', 'rm', '-f', '-v', name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=minimal_env(), timeout=8)
                # rm also fails for an already-removed --rm container; verify absence.
                checked = subprocess.run(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'],
                    capture_output=True, env=minimal_env(), timeout=3)
                ok = ok and checked.returncode == 0 and not checked.stdout.strip()
            except BaseException:
                ok = False
        try:
            shutil.rmtree(self.root)
            ok = ok and not self.root.exists()
        except FileNotFoundError:
            pass
        except BaseException:
            ok = False
        return ok


def execute():
    row = dict(schema='local-backup-validation-v1', status='FAIL', phase='context',
               source_sha=None, upload=False, production_writes=False, durable_backup=False,
               dump=False, encryption=False, hash=False, restore=False, cleanup=False,
               gates=dict(auth='NOT_PROVEN', identity='NOT_PROVEN', acl='NOT_PROVEN',
                          rls='NOT_PROVEN', readonly='NOT_PROVEN'))
    runner = None
    phrase = os.environ.pop('BACKUP_PASSPHRASE', '')
    raw = os.environ.pop('DATABASE_URL', '')
    try:
        need(os.environ.get('VALIDATION_OPERATION') == TOKEN)
        row['source_sha'] = context_guard.context()
        wall = time.time()
        started = int(os.environ['VALIDATION_STARTED'])
        approved = int(os.environ['VALIDATION_DEADLINE'])
        need(0 <= wall - started < 1200 and wall < approved <= started + 1200)
        seconds = min(WORK_SECONDS, started + 1140 - wall, approved - wall - 40)
        need(seconds > 0)
        def expired(signum, frame):
            raise Refused()
        signal.signal(signal.SIGALRM, expired)
        signal.signal(signal.SIGTERM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        context_guard.check_main()
        need(len(phrase) >= 24 and '\n' not in phrase and '\r' not in phrase)
        pg = source.parameters(raw)
        raw = ''
        root = Path(os.environ['RUNNER_TEMP']).resolve() / ('backup-validation-' + os.environ['GITHUB_RUN_ID'])
        root.mkdir(mode=0o700)  # Existing directory is refused, never reused.
        runner = Runner(root, os.environ['GITHUB_RUN_ID'], time.monotonic() + seconds)
        row['phase'] = 'tools'
        runner.run(['docker', 'pull', 'postgres:18'], seconds=90)
        runner.run(['prlimit', '--version'], seconds=5)
        runner.run(['openssl', 'version'], seconds=5)
        row['phase'] = 'preflight'
        source.preflight(pg, gates=row['gates'], run_client=runner.source_client)
        row['phase'] = 'source_stats'
        expected = stats(runner.source_client(pg, ['psql', '-XqAt', '-v', 'ON_ERROR_STOP=1'], sql=STATS))
        row['phase'] = 'dump'
        runner.source_client(pg, ['pg_dump', '--format=custom', '--compress=9', '--no-owner',
            '--no-privileges', '--lock-wait-timeout=5s', '--file=/data/dump'], dump=True)
        pg.clear()
        original = hash_file(root / 'dump')
        row['dump'] = True
        row['phase'] = 'encrypt'
        runner.crypt(phrase)
        encrypted = hash_file(root / 'encrypted')
        (root / 'dump').unlink()
        row['encryption'] = True
        row['phase'] = 'hash_decrypt'
        need(hash_file(root / 'encrypted') == encrypted)
        runner.crypt(phrase, decrypt=True)
        phrase = ''
        need(hash_file(root / 'restored') == original)
        row['hash'] = True
        (root / 'encrypted').unlink()
        row['phase'] = 'restore'
        runner.restore(expected)
        row['restore'] = True
        row['phase'] = 'main_after'
        context_guard.check_main()
        row['phase'] = 'complete'
        row['status'] = 'PASS'
    except BaseException:
        row['status'] = 'FAIL'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if runner:
            row['cleanup'] = runner.cleanup()
        if not row['cleanup']:
            row['status'] = 'FAIL'
    return row


def main():
    os.umask(0o077)
    row = execute()
    # No stdout, files, manifest, counts, hashes, SQL, exception text or artifacts.
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
        stream.write('```json\n' + json.dumps(row, sort_keys=True) + '\n```\n')
    return 0 if row['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
