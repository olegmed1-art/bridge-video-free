"""Narrow synthetic tests: never load a real driver or connect to any endpoint."""
import ast
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'synthetic-secret-must-never-escape'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        driver = types.ModuleType('psycopg')
        class Error(Exception):
            pass
        class DatabaseError(Error):
            pass
        class OperationalError(DatabaseError):
            pass
        class InterfaceError(Error):
            pass
        class ConnectionTimeout(OperationalError):
            pass
        driver.Error, driver.DatabaseError = Error, DatabaseError
        driver.OperationalError, driver.InterfaceError = OperationalError, InterfaceError
        driver.errors = types.SimpleNamespace(ConnectionTimeout=ConnectionTimeout)
        driver.__version__ = '3.3.4'
        driver.pq = types.SimpleNamespace(__impl__='binary', __build_version__=180000,
                                         version=lambda: 180000)
        driver.connect = MagicMock(side_effect=AssertionError('real driver forbidden'))
        conninfo = types.ModuleType('psycopg.conninfo')
        conninfo.conninfo_to_dict = MagicMock(side_effect=AssertionError('explicit fixture required'))
        rows = types.ModuleType('psycopg.rows')
        rows.dict_row = object()
        with patch.dict(sys.modules, {'psycopg': driver, 'psycopg.conninfo': conninfo,
                                      'psycopg.rows': rows}):
            db = load_module('_isolated_db', ROOT / 'bridge_school_api/db.py')
            with patch.dict(sys.modules, {'bridge_school_api.db': db}):
                cls.probe = load_module('_isolated_incident', ROOT / 'bridge_school_api/incident_db_probe.py')
        cls.driver = driver

    def setUp(self):
        self.driver.connect.reset_mock()
        self.params = dict(user=self.probe.EXPECTED_PRINCIPAL, password=SECRET,
                           dbname=self.probe.EXPECTED_DATABASE, host=self.probe.PRODUCTION_HOST,
                           port='5432', sslmode='require', channel_binding='require')
        self.dsn = (f'postgresql://{self.params["user"]}:{SECRET}@{self.params["host"]}/'
                    f'{self.params["dbname"]}?sslmode=require&channel_binding=require')

    def run_probe(self, connect, metadata=True, extra_env=None):
        out = io.StringIO()
        env = {'VERCEL_ENV': 'production', 'BRIDGE_APP_DATABASE_URL': self.dsn,
               **(extra_env or {})}
        with patch.dict(os.environ, env, clear=True), \
             patch.dict(sys.modules, {'psycopg': self.driver}), \
             patch.object(self.probe, 'conninfo_to_dict', return_value=self.params), \
             patch.object(self.probe.sys, 'version_info', (3, 12, 15)), \
             redirect_stdout(out):
            result = self.probe.probe(connect, runtime_metadata=metadata)
        rendered = out.getvalue() + json.dumps(result)
        for forbidden in (SECRET, self.params['host'], 'postgresql://', '192.0.2.10'):
            self.assertNotIn(forbidden, rendered)
        return result, out.getvalue()

    def connection(self):
        conn = MagicMock()
        conn.info.hostaddr = '192.0.2.10'
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (
            self.params['user'], self.params['dbname'], 'on')
        return conn

    def test_single_connect_query_cleanup_and_metadata_before_connect(self):
        conn = self.connection()
        observed = []
        def connect(*args, **kwargs):
            observed.append(json.loads(sys.stdout.getvalue())['schema'])
            return conn
        with patch.object(self.driver, 'connect', side_effect=connect) as call:
            result, output = self.run_probe(True)
        self.assertEqual(observed, ['incident-1911-runtime-v1'])
        call.assert_called_once_with(self.dsn, connect_timeout=10,
            application_name='incident-1911-readonly-probe',
            options='-c default_transaction_read_only=on -c statement_timeout=5000')
        conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "SELECT current_user, current_database(), current_setting('transaction_read_only')")
        conn.rollback.assert_called_once_with()
        conn.close.assert_called_once_with()
        conn.commit.assert_not_called()
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['address_family'], 'ipv4')
        self.assertTrue(all(result['session_checks'].values()))
        self.assertEqual(len(output.splitlines()), 1)

    def test_rejected_config_and_default_never_connect_or_emit_metadata(self):
        for connect, env in [(False, {}), (True, {'PGHOST': 'unreviewed'})]:
            with self.subTest(connect=connect):
                result, output = self.run_probe(connect, extra_env=env)
                self.assertFalse(result['connected'])
                self.assertEqual(output, '')
        self.driver.connect.assert_not_called()
        self.params['sslmode'] = 'disable'
        self.assertEqual(self.run_probe(True)[0]['status'], 'effective_parameters_rejected')
        self.driver.connect.assert_not_called()

    def test_failed_connect_sanitizes_aggregate_hints_without_retry(self):
        err = self.driver.OperationalError('connection refused; connection reset by peer ' + SECRET + ' 192.0.2.10')
        err.sqlstate = SECRET
        with patch.object(self.driver, 'connect', side_effect=err) as call:
            result, _ = self.run_probe(True)
        call.assert_called_once()
        self.assertEqual(result['tcp_subtypes'], ['refused', 'reset'])
        self.assertEqual(result['diagnostic_markers'], ['tcp'])
        self.assertTrue(result['sqlstate_filtered'])
        self.assertIsNone(result['sqlstate'])

    def test_mismatched_session_returns_only_booleans_and_still_cleans_up(self):
        conn = self.connection()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (SECRET, SECRET, SECRET)
        with patch.object(self.driver, 'connect', return_value=conn):
            result, _ = self.run_probe(True)
        self.assertEqual(result['status'], 'session_contract_failed')
        self.assertFalse(any(result['session_checks'].values()))
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_query_failure_and_cleanup_errors_are_redacted(self):
        conn = self.connection()
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = self.driver.Error(SECRET)
        conn.rollback.side_effect = self.driver.Error(SECRET)
        conn.close.side_effect = self.driver.Error(SECRET)
        with patch.object(self.driver, 'connect', return_value=conn):
            result, _ = self.run_probe(True)
        self.assertEqual(result['phase'], 'query')
        self.assertEqual([x['phase'] for x in result['cleanup_errors']], ['rollback', 'close'])
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_runtime_strings_and_versions_fail_closed_without_connect(self):
        for field, value in [('__version__', SECRET)]:
            with patch.object(self.driver, field, value):
                result, output = self.run_probe(True)
                self.assertEqual(result['status'], 'runtime_metadata_unavailable')
                self.assertEqual(output, '')
        for field, value in [('__impl__', SECRET), ('__build_version__', SECRET)]:
            with patch.object(self.driver.pq, field, value):
                result, output = self.run_probe(True)
                self.assertEqual(result['status'], 'runtime_metadata_unavailable')
                self.assertEqual(output, '')
        self.driver.connect.assert_not_called()

    def test_both_existing_ssl_modes_are_observed_without_changes(self):
        for mode in ('require', 'verify-full'):
            self.params['sslmode'] = mode
            self.dsn = self.dsn.replace('sslmode=require', 'sslmode=' + mode)
            with patch.object(self.driver, 'connect', return_value=self.connection()) as call:
                _, output = self.run_probe(True)
            self.assertEqual(json.loads(output)['sslmode'], mode)
            self.assertEqual(call.call_args.args[0], self.dsn)

    def test_family_does_not_emit_address_or_fail_session(self):
        for address, expected in [('2001:db8::1', 'ipv6'), (SECRET, 'unknown')]:
            conn = self.connection()
            conn.info.hostaddr = address
            self.assertEqual(self.probe._address_family(conn), expected)

    def test_opt_in_off_preserves_single_result_behavior(self):
        with patch.object(self.driver, 'connect', return_value=self.connection()):
            result, output = self.run_probe(True, metadata=False)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(output, '')
        self.assertNotIn('session_checks', result)
        self.assertNotIn('address_family', result)

    def test_workflow_bounds_and_no_production_followup(self):
        workflow = (ROOT / '.github/workflows/incident-app-connect-once.yml').read_text()
        self.assertIn('timeout-minutes: 1', workflow)
        self.assertIn('github.run_number == 1 && github.run_attempt == 1', workflow)
        self.assertIn('github.workflow_sha == github.sha', workflow)
        self.assertEqual(workflow.count('${{ secrets.'), 1)
        self.assertIn('timeout --kill-after=5s 20s', workflow)
        self.assertNotIn('workflow_dispatch', workflow)
        self.assertNotIn('uvicorn', workflow)
        self.assertNotIn('curl ', workflow)
        self.assertIn('--require-hashes --retries 0', workflow)
        tree = ast.parse((ROOT / 'bridge_school_api/incident_db_probe.py').read_text())
        calls = [x for x in ast.walk(tree) if isinstance(x, ast.Call) and
                 isinstance(x.func, ast.Attribute) and isinstance(x.func.value, ast.Name) and
                 x.func.value.id == 'psycopg' and x.func.attr == 'connect']
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
