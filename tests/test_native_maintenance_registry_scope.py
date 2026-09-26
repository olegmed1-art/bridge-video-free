"""No credentials or network: exact URI semantics and closed read-only catalog."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from ops import native_maintenance_registry_scope as subject

HOST = subject.EXPECTED_TARGET['neon']['host']
URI = ('postgresql://neondb_owner:synthetic-test-password@' + HOST
       + '/neondb?sslmode=require&channel_binding=require')


class RegistryScopeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(subject.os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_original_uri_effective_target_preserved_and_session_read_only(self):
        for host in (HOST, HOST.replace('.c-5.', '-pooler.c-5.')):
            p = subject.parameters(URI.replace(HOST, host))
            self.assertEqual(p['host'], host)
            self.assertEqual(p['user'], 'neondb_owner')
            self.assertEqual(p['sslmode'], 'verify-full')
            self.assertEqual(p['options'], '')

    def test_original_overrides_and_ambient_libpq_settings_refused(self):
        for suffix in ('&options=-c%20role=other', '&hostaddr=127.0.0.1',
                       '&service=other', '&dbname=other', '&user=other',
                       '&host=other.neon.tech', '&port=5433', '&sslmode=disable'):
            with self.subTest(suffix=suffix), self.assertRaises(Exception):
                subject.parameters(URI + suffix)
        for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS'):
            with patch.dict(subject.os.environ, {key: 'private'}), self.assertRaises(Exception):
                subject.parameters(URI)
        for raw in ('', URI.replace(HOST, 'other.neon.tech'), 'host=' + HOST):
            with self.assertRaises(Exception):
                subject.parameters(raw)

    def observe(self, row, session=('on', 'neondb', 'neondb_owner', 'neondb_owner'),
                host=HOST, drift=None):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.info = NS(host=host, port=5432, hostaddr='192.0.2.1')
        params = dict(host=host, hostaddr='192.0.2.1', sslmode='verify-full',
                      gssencmode='disable', channel_binding='require', options='')
        if drift:
            params.update(drift)
        conn.pgconn.info = [NS(keyword=k.encode(), val=v.encode()) for k, v in params.items()]
        binding = subject.EXPECTED_TARGET['neon']
        tags = [('neon.' + key, binding[key], context, 'configuration file', binding[key], False)
                for key, context in [('project_id', 'postmaster'), ('branch_id', 'postmaster'),
                                     ('endpoint_id', 'superuser')]]
        def execute(sql, *args):
            if sql == subject.CLOSURE:
                self.assertTrue(conn.read_only)
                return NS(fetchone=lambda: row)
            if 'pg_catalog.pg_settings' in sql:
                return NS(fetchall=lambda: tags)
            return NS(fetchone=lambda: session)
        conn.execute.side_effect = execute
        connect = MagicMock(return_value=conn)
        report = subject.observe(connect, URI.replace(HOST, host))
        self.assertTrue(conn.read_only)
        self.assertTrue(connect.call_args.kwargs['autocommit'])
        self.assertNotIn('synthetic-test-password', str(report))
        self.assertEqual(conn.execute.call_args_list[-1].args[1], (list(subject.TABLES),))
        return report

    @staticmethod
    def row():
        return ([[n, 'r', False, False, 'heap'] for n in subject.TABLES],) + (0,) * 11

    def test_closed_catalog_report_does_not_grant_pause_exemption(self):
        report = self.observe(self.row())
        self.assertEqual(report['audit'], 'NATIVE_REGISTRY_RUNTIME_SCOPE_PASS')
        self.assertFalse(report['workflow_pause_exempted'])
        self.assertFalse(report['production_mutations'])
        self.observe(self.row(), host=HOST.replace('.c-5.', '-pooler.c-5.'))

    def test_actual_identity_path_rejects_effective_connection_drift(self):
        for drift in ({'host': 'other.neon.tech'}, {'sslmode': 'require'},
                      {'gssencmode': 'prefer'}, {'channel_binding': 'prefer'},
                      {'options': '-c role=other'}, {'hostaddr': '192.0.2.2'}):
            with self.subTest(drift=drift), self.assertRaises(Exception):
                self.observe(self.row(), drift=drift)

    def test_every_dependency_missing_table_and_session_drift_refused(self):
        for i in range(1, 12):
            row = list(self.row())
            row[i] = 1
            with self.subTest(column=i), self.assertRaises(Exception):
                self.observe(tuple(row))
        with self.assertRaises(Exception):
            self.observe(([],) + (0,) * 11)
        with self.assertRaises(Exception):
            self.observe(self.row(), session=('off', 'neondb', 'neondb_owner', 'neondb_owner'))

    def test_private_exception_never_serialized(self):
        output = io.StringIO()
        with patch.object(subject, 'PHASE', 'connection'), \
                patch.object(subject, 'main', side_effect=RuntimeError('private URI')), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            subject.entrypoint()
        self.assertEqual(json.loads(output.getvalue()),
                         dict(audit='NATIVE_REGISTRY_RUNTIME_SCOPE_REFUSED', phase='connection',
                              production_mutations=False))
        self.assertNotIn('private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
