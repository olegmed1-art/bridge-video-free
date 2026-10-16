from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import os
import unittest
from unittest.mock import patch

from ops import native_database_activity_probe as probe

START = datetime(2026, 9, 27, tzinfo=timezone.utc)
OBSERVER = (1, START)


def row(pid=42, start=START, role='owner'):
    return (pid, start, role, 'client', 'idle', False, False, False, 'lt10m',
            'owner_attest', 'different')


class Connection:
    def __init__(self):
        self.read_only = False
        self.in_transaction = False
        self.closed = False
        self.snapshots = 0
        self.sql = ''

    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True

    @contextmanager
    def transaction(self):
        assert self.read_only and not self.in_transaction
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    def execute(self, sql, params=None):
        assert self.read_only and self.in_transaction
        assert sql.strip().startswith(('SELECT', 'SET '))
        self.sql = sql
        return self

    def fetchone(self):
        return ('on',) if 'current_setting' in self.sql else OBSERVER

    def fetchall(self):
        self.snapshots += 1
        return [row(), row(43, role='light')]


class ActivityTests(unittest.TestCase):
    def test_correlation_handles_pid_reuse_and_never_emits_identity(self):
        before = probe.validate_rows([row(), row(43)], OBSERVER)
        after = probe.validate_rows([row(), row(43, START + timedelta(seconds=1))], OBSERVER)
        report = probe.summarize(before, after)
        self.assertEqual((report['persistent'], report['appeared'], report['disappeared']), (1, 1, 1))
        self.assertEqual(report['persistent_owner'], 1)
        serialized = json.dumps(report)
        self.assertNotIn('2026', serialized)
        self.assertNotIn('pid', serialized)
        self.assertTrue(report['no_admission_authority'])
        self.assertFalse(report['origin_attribution'])
        self.assertEqual(report['persistent_after'][0]['application_family_hint'], 'owner_attest')
        self.assertTrue(report['hints_are_caller_controlled_or_shared'])

    def test_raw_application_names_or_addresses_refuse(self):
        for index, value in ((9, 'private-arbitrary-name'), (10, '192.0.2.1')):
            bad = list(row()); bad[index] = value
            with self.assertRaises(Exception):
                probe.validate_rows([tuple(bad)], OBSERVER)

    def test_refuses_invisible_malformed_duplicate_own_and_excess_activity(self):
        bad = list(row()); bad[4] = None
        for rows in ([tuple(bad)], [row(), row()], [row(1)], [row(i+2) for i in range(65)],
                     [('secret-query',)], [row(start=None)]):
            with self.subTest(rows=len(rows)), self.assertRaises(Exception):
                probe.validate_rows(rows, OBSERVER)

    def test_read_only_before_sql_and_no_transaction_during_interval(self):
        conn = Connection()
        def interval(seconds):
            self.assertEqual(seconds, 2)
            self.assertFalse(conn.in_transaction)
        with patch.object(probe.owner, 'parameters', return_value={}), \
             patch.object(probe.engine, 'identity'), patch.object(probe.time, 'sleep', side_effect=interval):
            report = probe.observe(lambda **kwargs: conn, 'private-uri')
        self.assertTrue(conn.closed)
        self.assertEqual(conn.snapshots, 2)
        self.assertEqual(report['persistent_owner'], 1)

    def test_connection_closes_on_snapshot_failure(self):
        conn = Connection()
        with patch.object(probe.owner, 'parameters', return_value={}), \
             patch.object(probe.engine, 'identity'), \
             patch.object(probe, 'snapshot', side_effect=RuntimeError('private')), \
             self.assertRaises(RuntimeError):
            probe.observe(lambda **kwargs: conn, 'private-uri')
        self.assertTrue(conn.closed)

    def test_source_drift_and_wrong_actor_never_output_observation(self):
        env = dict(GITHUB_TRIGGERING_ACTOR='olegmed1-art', EXPECTED_MAIN='a'*40,
                   NATIVE_OWNER_DATABASE_URL='private-uri')
        with patch.dict(os.environ, env, clear=True), \
             patch.object(probe, 'source_check', side_effect=[None, RuntimeError('changed')]), \
             patch.object(probe, 'observe', return_value={}), patch('builtins.print') as output, \
             self.assertRaises(RuntimeError):
            probe.main()
        output.assert_not_called()
        with patch.dict(os.environ, {'GITHUB_TRIGGERING_ACTOR':'other'}, clear=True), \
             patch.object(probe, 'observe') as observe, self.assertRaises(Exception):
            probe.main()
        observe.assert_not_called()

    def test_failure_is_redacted(self):
        with patch.object(probe, 'main', side_effect=RuntimeError('private-uri sql pid address')), \
             patch('builtins.print') as output, self.assertRaises(SystemExit):
            probe.entrypoint()
        self.assertNotIn('private-uri', output.call_args.args[0])
        self.assertTrue(json.loads(output.call_args.args[0])['no_admission_authority'])


if __name__ == '__main__': unittest.main()
