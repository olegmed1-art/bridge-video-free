"""Offline mailbox contract tests. No credentials, network or database."""
import contextlib
import io
import os
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops import github_autopilot_db_route as target


def neon_dsn():
    return ('postgresql://autopilot_callback_login:synthetic%40%3A@' + target.SOURCE +
            '/neondb?sslmode=require&channel_binding=require')


class MailboxContractTests(unittest.TestCase):
    def test_neon_direct_callback_identity_is_preserved(self):
        with patch.dict(os.environ, {'AUTOPILOT_DB_BACKEND': 'neon'}):
            self.assertEqual(target.mailbox_dsn('  ' + neon_dsn() + '  '), neon_dsn())

    def test_neon_pooler_mailbox_identity_is_preserved(self):
        # Readiness is a single read-only query, not callback ingestion.
        raw = neon_dsn().replace(target.SOURCE, target.SOURCE.replace('.c-5.', '-pooler.c-5.'))
        with patch.dict(os.environ, {'AUTOPILOT_DB_BACKEND': 'neon'}):
            self.assertEqual(target.mailbox_dsn(raw), raw)

    def test_no_alternate_destination_principal_or_libpq_route(self):
        dsn = neon_dsn()
        invalid = [
            dsn.replace(target.SOURCE, target.SOURCE.replace('.c-5.', '-pooler.c-6.')),
            dsn.replace(target.SOURCE, 'legacy.neon.tech'),
            dsn.replace(target.SOURCE, 'attacker.invalid'),
            dsn.replace(target.SOURCE, target.SOURCE + ':9999'),
            dsn.replace('callback_login', 'worker_login'),
            dsn.replace('/neondb', '/other'),
            dsn.replace('sslmode=require', 'sslmode=disable'),
            dsn.replace('channel_binding=require', 'channel_binding=prefer'),
            dsn + '&host=attacker.invalid',
            dsn + '&options=-c%20role%3Dother',
            dsn + '&sslmode=require', dsn + '#fragment',
        ]
        with patch.dict(os.environ, {'AUTOPILOT_DB_BACKEND': 'neon'}):
            for raw in invalid:
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    target.mailbox_dsn(raw)

    def test_existing_postgresql_lease_identity_remains_supported(self):
        environment = {'AUTOPILOT_DB_BACKEND': 'postgresql',
                       'AUTOPILOT_PG_HOST': '127.0.0.1',
                       'AUTOPILOT_PG_PORT': '55432',
                       'AUTOPILOT_PG_DATABASE': 'autopilot'}
        raw = target.target_dsn(neon_dsn(), 'autopilot_callback_login', '/synthetic/ca.crt')
        with patch.dict(os.environ, environment):
            self.assertEqual(target.mailbox_dsn(raw), raw)
            with self.assertRaises(ValueError):
                target.mailbox_dsn(raw + '&host=attacker.invalid')

    def test_readiness_query_is_in_one_read_only_bounded_transaction(self):
        statements, connection_calls = [], []
        class Connection:
            active = False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            @contextlib.contextmanager
            def transaction(self):
                self.active = True
                try: yield
                finally: self.active = False
            def cursor(self): return self
            def execute(self, statement):
                if not self.active:
                    raise AssertionError('Query escaped transaction')
                statements.append(statement)
            def fetchall(self): return [(1703, 40, 40, 'ROTATION_REQUIRED')]
        conn = Connection()
        def connect(dsn, **kwargs):
            connection_calls.append((dsn, kwargs))
            return conn
        output = io.StringIO()
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connect)}), \
             patch.dict(os.environ, {'DATABASE_URL': neon_dsn(), 'AUTOPILOT_DB_BACKEND': 'neon'}), \
             contextlib.redirect_stdout(output):
            target.mailbox_read()
        self.assertEqual(output.getvalue(), '1703|40|40|ROTATION_REQUIRED\n')
        self.assertEqual(statements, ['SET TRANSACTION READ ONLY',
                                     'SET LOCAL statement_timeout = 10000',
                                     'SELECT mailbox_pr,used_dispatches,max_dispatches,readiness FROM autopilot.mailbox_rotation_readiness()'])
        self.assertEqual(connection_calls[0][0], neon_dsn())
        self.assertEqual(connection_calls[0][1], {'autocommit': True, 'connect_timeout': 10})

    def test_invalid_dsn_never_reaches_driver_connect(self):
        calls = []
        fake = types.SimpleNamespace(connect=lambda *a, **k: calls.append(a))
        error = io.StringIO()
        with patch.dict(sys.modules, {'psycopg': fake}), \
             patch.dict(os.environ, {'DATABASE_URL': 'invalid-synthetic-input', 'AUTOPILOT_DB_BACKEND': 'neon'}), \
             patch.object(sys, 'argv', ['route', '--mailbox-read']), contextlib.redirect_stderr(error):
            self.assertEqual(target.cli(), 1)
        self.assertEqual(calls, [])
        self.assertIn('stage=MAILBOX_DSN error_type=ValueError sqlstate=NONE', error.getvalue())
        self.assertNotIn('invalid-synthetic-input', error.getvalue())

    def test_connect_diagnostic_never_echoes_exception_text(self):
        OperationalError = type('OperationalError', (Exception,), {'sqlstate': '28P01'})
        def connect(*args, **kwargs):
            raise OperationalError('synthetic-private-message ' + neon_dsn())
        error = io.StringIO()
        with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace(connect=connect)}), \
             patch.dict(os.environ, {'DATABASE_URL': neon_dsn(), 'AUTOPILOT_DB_BACKEND': 'neon'}), \
             patch.object(sys, 'argv', ['route', '--mailbox-read']), contextlib.redirect_stderr(error):
            self.assertEqual(target.cli(), 1)
        self.assertEqual(error.getvalue(), 'AUTOPILOT_DATABASE_ROUTING_FAILED stage=MAILBOX_CONNECT error_type=OperationalError sqlstate=28P01\n')

    def test_unknown_diagnostics_are_fixed_tokens(self):
        target._failure_stage = target.RoutingStage.MAILBOX_CONNECT
        exc = type('UnreviewedSyntheticClass', (Exception,), {'sqlstate': 'synthetic-private-state'})('synthetic-private-text')
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            target.report_routing_failure(exc)
        self.assertEqual(output.getvalue(), 'AUTOPILOT_DATABASE_ROUTING_FAILED stage=MAILBOX_CONNECT error_type=OTHER sqlstate=NONE\n')

    def test_capacity_monitor_only_reads_and_never_creates_rotation_prs(self):
        source = (Path(__file__).resolve().parents[1] / '.github/workflows/autopilot-mailbox-pre-rotation.yml').read_text()
        self.assertIn('contents: read', source)
        self.assertIn('persist-credentials: false', source)
        self.assertIn('ops.github_autopilot_db_route mailbox', source)
        self.assertIn('SSH_PRIVATE_KEY', source)
        for forbidden in ('gh pr create', 'git push', 'pull-requests: write', 'GH_TOKEN'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
