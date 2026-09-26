"""No real database credentials: fail-closed read-only owner-path contracts."""
from contextlib import ExitStack
import contextlib
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from ops import native_maintenance_owner_attest as subject

URI = ('postgresql://neondb_owner:synthetic-test-password@' + subject.EXPECTED_TARGET['neon']['host']
       + '/neondb?sslmode=require&channel_binding=require')


class OwnerAttestTests(unittest.TestCase):
    def test_failure_reports_only_fixed_phase_and_no_exception(self):
        output = io.StringIO()
        with patch.object(subject, 'PHASE', 'connection'), \
                patch.object(subject, 'main', side_effect=RuntimeError('private credential')), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exc:
                subject.entrypoint()
        self.assertEqual(exc.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue()),
                         dict(audit='NATIVE_OWNER_READ_ONLY_REFUSED', phase='connection',
                              reason='unclassified',
                              production_mutations=False))
        self.assertNotIn('private', output.getvalue())

    def test_connection_error_classification_never_returns_private_message(self):
        for message, reason in (
            ('password authentication failed for user private', 'authentication'),
            ('private password could not translate host name', 'dns'),
            ('private password certificate refused', 'tls_certificate'),
            ('private password channel binding refused', 'channel_binding'),
            ('private password timeout', 'timeout'),
            ('private password connection refused', 'connection_refused'),
            ('private password', 'unclassified'),
        ):
            self.assertEqual(subject.failure_reason(RuntimeError(message)), reason)

    def test_broken_exception_stringification_cannot_escape_safe_handler(self):
        class BrokenError(Exception):
            def __str__(self):
                raise RuntimeError('private credential in secondary error')
        output = io.StringIO()
        with patch.object(subject, 'main', side_effect=BrokenError()), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                subject.entrypoint()
        self.assertEqual(result.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'unclassified')
        self.assertNotIn('private', output.getvalue())

    def test_rebuilds_direct_verified_connection_without_uri_overrides(self):
        p = subject.parameters(URI + '&options=project%3Dother&hostaddr=127.0.0.1&gssencmode=require')
        self.assertEqual(p['host'], subject.EXPECTED_TARGET['neon']['host'])
        self.assertEqual(p['sslmode'], 'verify-full')
        self.assertEqual(p['gssencmode'], 'disable')
        self.assertEqual(p['options'], '')
        self.assertNotIn('hostaddr', p)
        self.assertNotIn('service', p)

    def test_wrong_target_or_role_refused(self):
        for uri in (URI.replace('neondb_owner:', 'other:'), URI.replace('/neondb?', '/other?'),
                    URI.replace(subject.EXPECTED_TARGET['neon']['host'], 'example.com'), ''):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                subject.parameters(uri)

    def test_pooler_credential_rebuilt_as_exact_direct_host(self):
        host = subject.EXPECTED_TARGET['neon']['host']
        pooled = host.replace('.c-5.', '-pooler.c-5.')
        self.assertEqual(subject.parameters(URI.replace(host, pooled))['host'], host)

    def fixture(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = ('on',)
        connect = MagicMock(return_value=connection)
        return connect, connection

    def test_read_only_before_snapshot_and_no_write_entrypoints(self):
        connect, conn = self.fixture()
        with ExitStack() as stack:
            checks = {}
            for name in ('snapshot', 'dormant', 'privileges', 'expected_after', 'prepare', 'change'):
                checks[name] = stack.enter_context(patch.object(subject.engine, name))
            def capture(_conn, target):
                self.assertTrue(_conn.read_only)
                self.assertEqual(target.neon.branch_id, 'br-aged-mud-b1i64914')
                return {'fixture': True}
            checks['snapshot'].side_effect = capture
            report = subject.observe(connect, URI)
            checks['privileges'].assert_called_once()
            self.assertIs(checks['privileges'].call_args.args[-1], False)
            checks['prepare'].assert_not_called()
            checks['change'].assert_not_called()
            self.assertFalse(report['snapshot_approved'])
            self.assertNotIn('synthetic-test-password', str(report))
        self.assertTrue(connect.call_args.kwargs['autocommit'])
        self.assertEqual(len(conn.execute.call_args_list), 4)

    def test_each_database_guard_failure_propagates(self):
        for failing in ('snapshot', 'dormant', 'privileges', 'expected_after'):
            connect, conn = self.fixture()
            with self.subTest(failing=failing), ExitStack() as stack:
                for name in ('snapshot', 'dormant', 'privileges', 'expected_after'):
                    mock = stack.enter_context(patch.object(subject.engine, name, return_value={'fixture': True}))
                    if name == failing:
                        mock.side_effect = RuntimeError('CI guard rejection')
                with self.assertRaises(RuntimeError):
                    subject.observe(connect, URI)
                conn.__exit__.assert_called_once()

    def test_read_write_session_refused_before_snapshot(self):
        connect, conn = self.fixture()
        conn.execute.return_value.fetchone.return_value = ('off',)
        with patch.object(subject.engine, 'snapshot') as snapshot:
            with self.assertRaises(subject.engine.Refused):
                subject.observe(connect, URI)
            snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
