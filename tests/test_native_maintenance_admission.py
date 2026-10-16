"""Full composite observation over explicit fake infrastructure, never a permit."""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import copy
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops import native_maintenance_executor as executor
from ops import native_maintenance_coordination as coordination
from ops import native_maintenance_workflow_api as workflow_api
from ops import native_permission_hold_guard as hold_guard
from ops.native_maintenance_run_guard import RunBinding
from ops.native_maintenance_checkpoint import JournalCheckpoint
from ops.native_maintenance_workflow_pause import Journal, Refused, digest
from database import native_cli_permission_engine as engine
from database.native_cli_maintenance_session import HeldResources
from test_native_maintenance_run_guard import FakeAPI
from test_native_maintenance_checkpoint import MemoryStore
from test_native_maintenance_coordination import Database, START, agreement_record
from test_native_maintenance_executor import HOLD, TARGET, ROUTE, SOURCE


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths = [Path(self.tmp.name) / name for name in ('operation', 'pause')]
        for path in paths:
            path.mkdir(mode=0o700)
        self.operation, self.pause = [Journal(path) for path in paths]
        self.addCleanup(self.operation.close)
        self.addCleanup(self.pause.close)
        row = dict(id=7, path='.github/workflows/ci-only.yml', state='disabled_manually',
                   updated_at='2026-09-26T00:00:00Z')
        self.plan = dict(version=1, repository=workflow_api.REPOSITORY, source=SOURCE, workflows=[row])
        self.row = copy.deepcopy(row)
        self.workflow_reads = 0
        self.puts = []
        def request(method, path):
            if method != 'GET':
                self.puts.append(path)
                raise AssertionError('ADMISSION_MUST_NOT_WRITE')
            if path == '/git/ref/heads/main':
                return self.api.main
            self.assertEqual(path, '/actions/workflows/7')
            self.workflow_reads += 1
            return {**self.row, 'url': workflow_api.BASE + path}
        self.transport = patch.object(workflow_api.Transport, 'request', side_effect=request)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.api = FakeAPI()
        self.run = RunBinding(SOURCE, 123, 2, self.api)
        self.scope = dict(version=1, target=asdict(TARGET), operation='apply', manifest_digest='f'*64,
                          workflow_plan_digest=digest(self.plan), source=SOURCE, route=ROUTE,
                          hold=asdict(HOLD), execution_mode='staged_v1', admission_mode='observed_v1',
                          origin_run=dict(run_id=123, attempt=2, job_id=456))
        record = agreement_record(self.scope)
        self.agreement = coordination.Agreement(record, digest(record), self.scope)
        self.db = Database()
        self.db.foreign = [(99, START, TARGET.recipient, 'client backend', 'idle', None, None, None)]
        self.identity = patch.object(engine, 'identity').start()
        self.attest = patch.object(hold_guard.hold, 'attest', return_value=HOLD).start()
        self.addCleanup(patch.stopall)
        self.reader = Mock()
        self.reader.get.return_value = dict(total_count=0, workflow_runs=[])
        self.connections = coordination.OwnedConnections(self.db.connect, TARGET, approved_hold=HOLD)
        self.operator = coordination.Operator(scope=self.scope, agreement=self.agreement,
            workflows=coordination.WorkflowDrain(self.reader, self.plan, digest(self.plan)),
            prior_hosts=coordination.PriorSupervisors([], digest([])), connections=self.connections,
            source_transport=workflow_api.Transport('CI-only'), run=self.run)
        self.life = SimpleNamespace(assert_alive=Mock())
        self.ex = executor.MaintenanceExecutor(target=TARGET, operation='apply',
            manifest_path=Path(self.tmp.name)/'unused-manifest', manifest_digest='f'*64,
            expected_route=ROUTE, approved_hold=HOLD, workflow_plan=self.plan, plan_digest=digest(self.plan),
            api_token='CI-only', operation_journal=self.operation, pause_journal=self.pause,
            run=self.run, operator=self.operator, lifetime=self.life,
            checkpoint=JournalCheckpoint(MemoryStore()), staged=True, observed_admission=True)
        self.assertEqual(self.ex.scope, self.scope)
        self.api.calls.clear()

    def assert_refuses_sticky(self):
        with self.assertRaises(Exception):
            self.ex.hold.assert_held(TARGET, 'apply')
        self.assertTrue(self.ex.failed)
        self.assertTrue(self.ex.hold.failed)
        with self.assertRaises(Exception):
            self.ex.hold.assert_held(TARGET, 'apply')
        self.assertEqual(self.puts, [])
        self.assertEqual(len(self.operation.records), 1)
        self.assertEqual(len(self.pause.records), 1)

    def test_each_call_has_fresh_observations_without_recursive_reads(self):
        for _ in range(2):
            self.ex.hold.assert_held(TARGET, 'apply')
        # Five run GETs, five status GETs and two workflow GETs per call.
        # This is a call-graph assertion, NOT a live timing proof.
        self.assertEqual(len(self.api.calls), 10)
        self.assertEqual(self.reader.get.call_count, 10)
        self.assertEqual(self.workflow_reads, 4)
        self.assertEqual(self.attest.call_count, 6)
        self.assertEqual(self.puts, [])
        self.assertEqual(self.connections.live, {})

    def test_reenabled_workflow_during_drain_is_reobserved(self):
        def drain(path):
            self.row['state'] = 'active'
            return dict(total_count=0, workflow_runs=[])
        self.reader.get.side_effect = drain
        self.assert_refuses_sticky()
        self.assertTrue(self.ex.pause.failed)
        self.assertEqual(self.api.calls, [])

    def test_cancellation_after_hold_is_seen_before_admission(self):
        def attest():
            self.api.run.update(status='completed', conclusion='cancelled')
            return HOLD
        self.attest.side_effect = attest
        self.assert_refuses_sticky()
        self.assertTrue(self.run.failed)

    def test_main_drift_after_hold_is_seen_before_admission(self):
        def attest():
            self.api.main['object']['sha'] = 'b'*40
            return HOLD
        self.attest.side_effect = attest
        self.assert_refuses_sticky()
        self.assertTrue(self.run.failed)

    def test_agreement_expiry_during_final_run_observation_refuses(self):
        get = self.api.get
        def expired(path):
            result = get(path)
            self.agreement.deadline = 0
            return result
        self.api.get = expired
        self.assert_refuses_sticky()
        self.assertTrue(self.operator.failed)

    def test_hold_drift_refuses(self):
        self.attest.return_value = None
        self.assert_refuses_sticky()
        self.assertEqual(self.api.calls, [])

    def test_foreign_backend_refuses(self):
        self.db.foreign.append((100, START, 'other', 'client backend', 'idle', None, None, None))
        self.assert_refuses_sticky()
        self.assertEqual(self.api.calls, [])

    def test_route_and_database_fences_still_follow_observation(self):
        route = SimpleNamespace(assert_held=Mock())
        fence = SimpleNamespace(assert_held=Mock())
        guard = HeldResources(TARGET, 'apply', route, fence, self.ex.hold)
        route.assert_held.side_effect = Refused('ROUTE_LOST')
        with self.assertRaisesRegex(Refused, 'ROUTE_LOST'):
            guard.assert_held(TARGET, 'apply')
        fence.assert_held.assert_not_called()
        route.assert_held.side_effect = None
        fence.assert_held.side_effect = Refused('FENCE_LOST')
        with self.assertRaisesRegex(Refused, 'FENCE_LOST'):
            guard.assert_held(TARGET, 'apply')
        self.assertEqual(len(self.api.calls), 10)

    def test_same_named_method_does_not_replace_legacy_writer_brackets(self):
        writer = SimpleNamespace(assert_held=Mock(), observe_hold=Mock())
        guard = hold_guard.HoldMaintenanceGuard(TARGET, 'apply', HOLD, writer)
        guard.assert_held(TARGET, 'apply')
        self.assertEqual(writer.assert_held.call_count, 2)
        writer.observe_hold.assert_not_called()

    def test_local_unobserved_or_expired_run_never_confers_authority(self):
        unobserved = RunBinding(SOURCE, 123, 2, FakeAPI())
        with self.assertRaises(Exception):
            unobserved.assert_current()
        self.assertTrue(unobserved.failed)
        self.run.deadline = 0
        self.assert_refuses_sticky()
        self.assertTrue(self.run.failed)

    def permission_boundary(self, cancelled_call):
        """Run the actual engine ordering over a simulated transaction/SQL sink."""
        events, commands = [], []
        @contextmanager
        def transaction():
            events.append('BEGIN')
            try:
                yield
            except BaseException:
                events.append('ROLLBACK')
                raise
            else:
                events.append('COMMIT')
        conn = SimpleNamespace(autocommit=True, transaction=transaction,
                               execute=lambda command, *args: commands.append(str(command)))
        before = dict(config=[dict(enabled=False)], receipts=0, nonterminal_tasks=0)
        after = {**before, 'CI_EXPECTED_PERMISSION_DELTA': True}
        manifest = dict(version=1, target=asdict(TARGET), before=before, after=after)
        calls = 0
        def observe(target, operation):
            nonlocal calls
            calls += 1
            if calls == cancelled_call:
                self.api.run.update(status='completed', conclusion='cancelled')
            self.ex.hold.assert_held(target, operation)
        with patch.object(engine, 'load_manifest', return_value=manifest), \
             patch.object(engine, 'expected_after', return_value=after), \
             patch.object(engine, 'snapshot', side_effect=[before, after]), \
             patch.object(engine, 'privileges'):
            with self.assertRaises(Exception):
                engine.change(conn, TARGET, 'CI_ONLY', 'f'*64,
                              SimpleNamespace(assert_held=observe))
        self.assertEqual(events, ['BEGIN', 'ROLLBACK'])
        self.assertTrue(self.ex.hold.failed)
        return [command for command in commands if 'GRANT EXECUTE' in command]

    def test_cancel_before_grants_prevents_all_six_statements(self):
        self.assertEqual(self.permission_boundary(2), [])

    def test_cancel_before_commit_rolls_back_uncommitted_statements(self):
        self.assertEqual(len(self.permission_boundary(3)), 6)


if __name__ == '__main__':
    unittest.main()
