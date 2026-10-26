"""Bounded native PostgreSQL qualification for the isolated Books 0500 component."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
KEY = '0500_books_authority_core'
DB = 'bridge_school_books0500_ci'
OWNER = 'bridge_ci_owner'
ADMIN_URL = 'postgresql://postgres:postgres@localhost:5432/bridge_school_ci'
OWNER_URL = f'postgresql://{OWNER}:{OWNER}@localhost:5432/{DB}'
ADMIN_FIXTURE_URL = f'postgresql://postgres:postgres@localhost:5432/{DB}'
WORKER = 'bridge_school_worker_principal'
TABLES = ('books_authority_object', 'books_authority_state',
          'books_authority_state_audit', 'books_authority_attempt', 'books_authority_phase')


def fixture_guard():
    required = {
        'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Linux',
        'GITHUB_REPOSITORY': 'olegmed1-art/bridge-video-free',
        'ADMIN_DATABASE_URL': ADMIN_URL,
        'DATABASE_URL': f'postgresql://{OWNER}:{OWNER}@localhost:5432/bridge_school_ci',
    }
    if any(os.environ.get(k) != v for k, v in required.items()):
        raise RuntimeError('EXACT_DISPOSABLE_BOOKS0500_CI_REQUIRED')
    if not (ROOT / 'database/migrations' / f'{KEY}.sql').is_file():
        raise RuntimeError('BOOKS0500_SOURCE_MISSING')


def command(argv, *, input_text=None, extra_env=None, timeout=20):
    env = {'PATH': os.environ['PATH'], 'LANG': 'C', 'LC_ALL': 'C'}
    env.update(extra_env or {})
    try:
        result = subprocess.run(argv, cwd=ROOT, input=input_text, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError('BOOKS0500_COMMAND_TIMEOUT') from exc
    # Never emit libpq error text or a connection string into a CI log.
    return result.returncode, result.stdout.strip()


def psql(sql, *, admin=False, admin_fixture=False, success=True):
    url = ADMIN_FIXTURE_URL if admin_fixture else (ADMIN_URL if admin else OWNER_URL)
    code, output = command(['psql', url, '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1'],
                           input_text=sql)
    if success and code:
        raise AssertionError('BOOKS0500_SQL_UNEXPECTED_FAILURE')
    if not success and not code:
        raise AssertionError('BOOKS0500_SQL_UNEXPECTED_SUCCESS')
    return output


def checksum(migrations):
    wrapper = migrations / f'{KEY}.sql'
    parts = migrations / KEY
    chunks = [f'FILE:{wrapper.name}\n'.encode(), wrapper.read_bytes()]
    for part in sorted(parts.glob('*.sql'), key=lambda p: p.name):
        chunks += [f'\nFILE:{KEY}/{part.name}\n'.encode(), part.read_bytes()]
    return hashlib.sha256(b''.join(chunks)).hexdigest()


class Books0500Postgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_guard()
        if psql(f"SELECT count(*) FROM pg_database WHERE datname='{DB}';", admin=True) != '0':
            raise RuntimeError('BOOKS0500_FOREIGN_FIXTURE_DATABASE_HOLD')
        if psql("SELECT count(*) FROM pg_roles WHERE rolname='bridge_school_worker_principal';",
                admin=True) != '1':
            raise RuntimeError('BOOKS0500_WORKER_PRINCIPAL_MISSING')
        if psql("SELECT count(*) FROM pg_roles WHERE rolname='bridge_school_reader';",
                admin=True) != '1':
            raise RuntimeError('BOOKS0500_READER_ROLE_MISSING')
        psql(f'CREATE DATABASE {DB} TEMPLATE template0 OWNER {OWNER};', admin=True)
        cls.created_oid = psql(f"SELECT oid FROM pg_database WHERE datname='{DB}';", admin=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='books0500-ci-')
        cls.stage = Path(cls.temp.name)
        shutil.copytree(ROOT / 'database/migrations' / KEY,
                        cls.stage / 'migrations' / KEY)
        shutil.copy2(ROOT / 'database/migrations' / f'{KEY}.sql',
                     cls.stage / 'migrations' / f'{KEY}.sql')
        (cls.stage / 'migration_checks').mkdir()
        shutil.copy2(ROOT / 'database/migration_checks' / f'{KEY}.sql',
                     cls.stage / 'migration_checks' / f'{KEY}.sql')

    @classmethod
    def tearDownClass(cls):
        if not hasattr(cls, 'created_oid'):
            return
        try:
            if psql(f"SELECT oid FROM pg_database WHERE datname='{DB}';",
                    admin=True) != cls.created_oid:
                raise AssertionError('BOOKS0500_FIXTURE_IDENTITY_CHANGED')
            if psql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{DB}';",
                    admin=True) != '0':
                raise AssertionError('BOOKS0500_FIXTURE_HAS_SESSIONS')
            psql(f'DROP DATABASE {DB};', admin=True)
            if psql(f"SELECT count(*) FROM pg_database WHERE datname='{DB}';",
                    admin=True) != '0':
                raise AssertionError('BOOKS0500_FIXTURE_CLEANUP_UNVERIFIED')
        finally:
            if hasattr(cls, 'temp'):
                cls.temp.cleanup()

    def setUp(self):
        psql('DROP SCHEMA IF EXISTS public CASCADE; '
             f'CREATE SCHEMA public AUTHORIZATION {OWNER}; '
             'CREATE TABLE public.schema_migration('
             'migration_key text PRIMARY KEY,checksum text,applied_at timestamptz DEFAULT clock_timestamp()); '
             'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bridge_school_reader;')
        self.sum = checksum(self.stage / 'migrations')

    def wrapper(self, success=True):
        code, _ = command(['psql', OWNER_URL, '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                           '-v', f'migration_checksum={self.sum}',
                           '-v', f'books_expected_database={DB}',
                           '-v', f'books_expected_owner={OWNER}',
                           '-f', str(self.stage / 'migrations' / f'{KEY}.sql')])
        self.assertEqual(code == 0, success, 'BOOKS0500_WRAPPER_OUTCOME')

    def checker(self, success=True):
        code, _ = command(['psql', OWNER_URL, '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                           '-v', f'migration_checksum={self.sum}',
                           '-v', f'books_expected_database={DB}',
                           '-v', f'books_expected_owner={OWNER}',
                           '-f', str(self.stage / 'migration_checks' / f'{KEY}.sql')])
        self.assertEqual(code == 0, success, 'BOOKS0500_CHECKER_OUTCOME')

    def runner(self, success=True):
        env = {'DATABASE_URL': OWNER_URL,
               'MIGRATIONS_DIR': str(self.stage / 'migrations'),
               'MIGRATION_CHECKS_DIR': str(self.stage / 'migration_checks'),
               'EXTERNAL_HISTORY_FILE': str(self.stage / 'absent_external_history.tsv'),
               'BOOKS_AUTHORITY_EXPECTED_DATABASE': DB,
               'BOOKS_AUTHORITY_EXPECTED_OWNER': OWNER}
        code, _ = command(['bash', str(ROOT / 'database/scripts/migrate.sh')],
                           extra_env=env, timeout=30)
        self.assertEqual(code == 0, success, 'BOOKS0500_RUNNER_OUTCOME')

    def counts(self):
        return tuple(map(int, psql("SELECT "
             "(SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace "
             "AND relname LIKE 'books_authority_%' AND relkind='r'),"
             "(SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace "
             "AND proname LIKE 'books_authority_%'),"
             "(SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
             "WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal),"
             "(SELECT count(*) FROM public.schema_migration "
             f"WHERE migration_key='{KEY}');").split('|')))

    def test_empty_install_and_exact_replay(self):
        defaults_before = psql("SELECT defaclacl::text FROM pg_default_acl WHERE "
                               "defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
                               "AND defaclnamespace='public'::regnamespace AND defaclobjtype='r';")
        self.runner()
        self.assertEqual(self.counts(), (5, 3, 5, 1))
        self.assertEqual(psql("SELECT count(*) FROM pg_class c WHERE "
                              "c.relnamespace='public'::regnamespace AND c.relname LIKE 'books_authority_%' "
                              "AND has_table_privilege('bridge_school_reader',c.oid,'SELECT');"), '0')
        self.assertEqual(psql("SELECT count(*) FROM information_schema.role_table_grants "
                              "WHERE grantee='bridge_school_worker_principal' "
                              "AND table_name LIKE 'books_authority_%';"), '0')
        self.runner()
        self.assertEqual(self.counts(), (5, 3, 5, 1))
        self.assertEqual(psql("SELECT count(*) FROM pg_class c WHERE "
                              "c.relnamespace='public'::regnamespace AND c.relname LIKE 'books_authority_%' "
                              "AND has_table_privilege('bridge_school_reader',c.oid,'SELECT');"), '0')
        self.assertEqual(psql("SELECT defaclacl::text FROM pg_default_acl WHERE "
                              "defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
                              "AND defaclnamespace='public'::regnamespace AND defaclobjtype='r';"), defaults_before)

    def test_partial_or_unknown_provenance_is_hold(self):
        psql('CREATE TABLE public.books_authority_object(object_id text PRIMARY KEY);')
        self.assertEqual(psql("SELECT has_table_privilege('bridge_school_reader', "
                              "'public.books_authority_object', 'SELECT');"), 't')
        self.wrapper(False)
        self.assertEqual(self.counts(), (1, 0, 0, 0))
        self.assertEqual(psql("SELECT has_table_privilege('bridge_school_reader', "
                              "'public.books_authority_object', 'SELECT');"), 't')
        psql('DROP TABLE public.books_authority_object;')
        psql((self.stage / 'migrations' / KEY / '001_core.sql').read_text())
        self.wrapper(False)
        self.assertEqual(self.counts(), (5, 3, 5, 0))

    def test_rollback_after_first_create(self):
        part = self.stage / 'migrations' / KEY / '001_core.sql'
        original = part.read_text()
        first_end = original.index(');') + 2
        part.write_text(original[:first_end] + '\nSELECT 1/0;\n' + original[first_end:])
        try:
            self.sum = checksum(self.stage / 'migrations')
            self.wrapper(False)
            self.assertEqual(self.counts(), (0, 0, 0, 0))
        finally:
            part.write_text(original)

    def test_column_acl_check_owner_and_missing_checker(self):
        self.wrapper()
        psql('GRANT UPDATE(body) ON public.books_authority_object TO bridge_school_worker_principal;')
        self.checker(False)
        psql('REVOKE UPDATE(body) ON public.books_authority_object FROM bridge_school_worker_principal;')
        self.checker()
        psql('ALTER TABLE public.books_authority_state DROP CONSTRAINT books_authority_state_status_check; '
             'ALTER TABLE public.books_authority_attempt DROP CONSTRAINT books_authority_attempt_status_check; '
             "ALTER TABLE public.books_authority_attempt ADD CONSTRAINT books_authority_state_status_check CHECK (status IN ('active','revoked')); "
             "ALTER TABLE public.books_authority_state ADD CONSTRAINT books_authority_attempt_status_check CHECK (status IN ('unresolved','success'));")
        self.checker(False)
        check = self.stage / 'migration_checks' / f'{KEY}.sql'
        check.rename(check.with_suffix('.held'))
        try:
            self.runner(False)
        finally:
            check.with_suffix('.held').rename(check)

    def test_read_grant_partial_acl_and_logical_revoke(self):
        self.wrapper()
        four = [t for t in TABLES if t != 'books_authority_state_audit']
        psql('GRANT SELECT ON ' + ','.join('public.' + t for t in four) +
             ' TO bridge_school_worker_principal;')
        self.checker()
        self.assertEqual(psql("SELECT count(*) FROM pg_class c WHERE "
                              "c.relnamespace='public'::regnamespace AND c.relkind='r' "
                              "AND c.relname LIKE 'books_authority_%' "
                              "AND (has_table_privilege('bridge_school_worker_principal',c.oid,'INSERT') "
                              "OR has_table_privilege('bridge_school_worker_principal',c.oid,'UPDATE') "
                              "OR has_table_privilege('bridge_school_worker_principal',c.oid,'DELETE') "
                              "OR has_table_privilege('bridge_school_worker_principal',c.oid,'TRUNCATE'));"), '0')
        psql('REVOKE SELECT ON public.books_authority_phase FROM bridge_school_worker_principal;')
        self.checker(False)
        psql('REVOKE SELECT ON ' + ','.join('public.' + t for t in four) +
             ' FROM bridge_school_worker_principal;')
        self.checker()
        self.runner()
        self.assertEqual(psql("SELECT count(*) FROM information_schema.role_table_grants "
                              "WHERE grantee='bridge_school_worker_principal' "
                              "AND table_name LIKE 'books_authority_%';"), '0')

    def test_bad_checksum_and_default_acl_hold(self):
        psql(f"INSERT INTO public.schema_migration(migration_key,checksum) VALUES ('{KEY}',repeat('0',64));")
        self.wrapper(False)
        self.assertEqual(self.counts(), (0, 0, 0, 1))
        psql(f"DELETE FROM public.schema_migration WHERE migration_key='{KEY}';")
        psql('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bridge_school_worker_principal;')
        self.wrapper(False)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_state_audit_and_immutable_boundaries(self):
        self.wrapper()
        psql("INSERT INTO public.books_authority_object VALUES ('fixture','grant','{}'); "
             "INSERT INTO public.books_authority_state(object_id,revision,status,audit_actor) "
             "VALUES ('fixture',0,'active','fixture');")
        self.assertEqual(psql("SELECT count(*) FROM public.books_authority_state_audit "
                              "WHERE object_id='fixture';"), '1')
        psql("UPDATE public.books_authority_state SET revision=1,status='revoked' "
             "WHERE object_id='fixture';")
        self.assertEqual(psql("SELECT count(*) FROM public.books_authority_state_audit "
                              "WHERE object_id='fixture';"), '2')
        psql("UPDATE public.books_authority_state SET status='active' WHERE object_id='fixture';",
             success=False)
        psql("UPDATE public.books_authority_object SET kind='policy' WHERE object_id='fixture';",
             success=False)
        self.assertEqual(psql("SELECT status FROM public.books_authority_state "
                              "WHERE object_id='fixture';"), 'revoked')

    def test_invoker_audit_failure_rolls_back_state(self):
        self.wrapper()
        psql("INSERT INTO public.books_authority_object VALUES ('fixture','grant','{}'); "
             "GRANT USAGE ON SCHEMA public TO bridge_school_worker_principal; "
             "GRANT INSERT ON public.books_authority_state TO bridge_school_worker_principal;")
        insertion = ("SET ROLE bridge_school_worker_principal; "
                     "INSERT INTO public.books_authority_state(object_id,revision,status,audit_actor) "
                     "VALUES ('fixture',0,'active','synthetic');")
        psql(insertion, admin_fixture=True, success=False)
        self.assertEqual(psql("SELECT count(*) FROM public.books_authority_state "
                              "WHERE object_id='fixture';"), '0')
        psql('GRANT INSERT ON public.books_authority_state_audit TO bridge_school_worker_principal;')
        psql(insertion, admin_fixture=True)
        self.assertEqual(psql("SELECT count(*) FROM public.books_authority_state_audit "
                              "WHERE object_id='fixture';"), '1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
