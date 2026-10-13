"""Narrow object-level ordering/cleanup tests; no real client or network.

The pinned driver's implicit BEGIN behavior is checked separately against its
cached upstream source. This fixture does not pretend to observe wire commands.
"""
import types
import unittest
from unittest.mock import patch

import test_incident_app_connect_once as baseline

SECRET = baseline.SECRET

SET = 'SET LOCAL statement_timeout = 5000'
SELECT = "SELECT current_user, current_database(), current_setting('transaction_read_only')"


class OrderedConnection:
    def __init__(self, driver, events, row, faults=()):
        self.driver, self.events, self.row, self.faults = driver, events, row, faults
        self.info = types.SimpleNamespace(hostaddr='192.0.2.10')
        self._read_only = None

    def event(self, name):
        self.events.append(name)
        if name in self.faults:
            raise self.driver.OperationalError(SECRET)

    @property
    def read_only(self):
        return self._read_only

    @read_only.setter
    def read_only(self, value):
        if value is not True:
            raise AssertionError('read_only must be true')
        self.event('read_only')
        self._read_only = value

    def cursor(self):
        self.event('cursor')
        connection = self

        class Cursor:
            def __enter__(self):
                connection.event('cursor_enter')
                return self

            def __exit__(self, *args):
                connection.event('cursor_exit')
                return False

            def execute(self, sql):
                if connection.read_only is not True:
                    raise AssertionError('statement before read_only')
                if sql not in (SET, SELECT):
                    raise AssertionError('unexpected SQL')
                connection.event('set_local' if sql == SET else 'select')

            def fetchone(self):
                connection.event('fetch')
                return connection.row

        return Cursor()

    def rollback(self):
        self.event('rollback')

    def close(self):
        self.event('close')

    def commit(self):
        raise AssertionError('commit forbidden')


class TransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline.ProbeTests.setUpClass()
        cls.probe, cls.driver = baseline.ProbeTests.probe, baseline.ProbeTests.driver

    setUp = baseline.ProbeTests.setUp
    run_probe = baseline.ProbeTests.run_probe

    def run_case(self, faults=(), row=None):
        events = []
        if row is None:
            row = (self.params['user'], self.params['dbname'], 'on')
        conn = OrderedConnection(self.driver, events, row, faults)

        def connect(*args, **kwargs):
            conn.event('connect')
            return conn

        with patch.object(self.driver, 'connect', side_effect=connect) as call:
            result, output = self.run_probe(True)
        call.assert_called_once_with(self.dsn, connect_timeout=10,
                                    application_name='incident-1911-readonly-probe')
        return result, events

    def test_success_order_and_no_startup_options(self):
        result, events = self.run_case()
        self.assertEqual(events, ['connect', 'read_only', 'cursor', 'cursor_enter',
                                  'set_local', 'select', 'fetch', 'cursor_exit', 'rollback', 'close'])
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['probe'], 'incident-1911-v5')
        self.assertTrue(all(result['session_checks'].values()))

    def test_connect_failure_has_parser_and_no_transaction_or_cleanup(self):
        # Startup rejection is only synthetic input here, never a live diagnosis.
        events = []
        error = self.driver.OperationalError('unsupported startup parameter in options: statement_timeout')
        def connect(*args, **kwargs):
            events.append('connect')
            raise error
        with patch.object(self.driver, 'connect', side_effect=connect) as call:
            result, output = self.run_probe(True)
        call.assert_called_once()
        self.assertEqual(events, ['connect'])
        self.assertFalse(result['connected'])
        self.assertEqual(result['phase'], 'connect')
        self.assertEqual(result['status'], 'connection_failed')
        self.assertEqual(result['error_detail']['message_hints']['startup_parameters'], ['statement_timeout'])

    def test_setup_failure_never_reaches_select_and_always_cleans_up(self):
        for fault in ('read_only', 'cursor', 'cursor_enter', 'set_local'):
            with self.subTest(fault=fault):
                result, events = self.run_case((fault,))
                self.assertEqual(result['phase'], 'transaction_setup')
                self.assertEqual(result['status'], 'transaction_setup_failed')
                self.assertTrue(result['connected'])
                self.assertNotIn('select', events)
                self.assertNotIn('fetch', events)
                self.assertEqual(events[-2:], ['rollback', 'close'])
                self.assertEqual(events.count('rollback'), 1)
                self.assertEqual(events.count('close'), 1)
                self.assertNotIn('error_detail', result)

    def test_select_and_fetch_failure_preserve_query_phase(self):
        for fault in ('select', 'fetch', 'cursor_exit'):
            with self.subTest(fault=fault):
                result, events = self.run_case((fault,))
                self.assertEqual(result['phase'], 'query')
                self.assertEqual(result['status'], 'query_failed')
                self.assertEqual(events[-2:], ['rollback', 'close'])

    def test_setup_failure_is_primary_when_both_cleanup_operations_fail(self):
        result, events = self.run_case(('set_local', 'rollback', 'close'))
        self.assertEqual(result['phase'], 'transaction_setup')
        self.assertEqual([x['phase'] for x in result['cleanup_errors']], ['rollback', 'close'])
        self.assertNotIn('select', events)
        self.assertEqual(events.count('rollback'), 1)
        self.assertEqual(events.count('close'), 1)

    def test_cleanup_failure_after_success_never_commits_or_reports_pass(self):
        for faults, phase, secondary in [(('rollback',), 'rollback', []),
                                         (('close',), 'close', []),
                                         (('rollback', 'close'), 'rollback', ['close'])]:
            with self.subTest(faults=faults):
                result, events = self.run_case(faults)
                self.assertEqual(result['phase'], phase)
                self.assertEqual(result['status'], phase + '_failed')
                self.assertEqual([x['phase'] for x in result['cleanup_errors']], secondary)
                self.assertEqual(events[-2:], ['rollback', 'close'])

    def test_read_only_off_is_a_contract_failure_with_cleanup(self):
        result, events = self.run_case(row=(self.params['user'], self.params['dbname'], 'off'))
        self.assertEqual(result['status'], 'session_contract_failed')
        self.assertFalse(result['session_checks']['read_only'])
        self.assertEqual(events[-2:], ['rollback', 'close'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
