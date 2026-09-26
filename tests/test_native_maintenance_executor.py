"""Contract fixtures use simulated GitHub/host/operator state; never a live permit."""
import copy
from dataclasses import dataclass
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import native_maintenance_executor as executor
from ops import native_maintenance_workflow_api as api
from ops import native_permission_hold_guard as hold_guard
from ops.native_maintenance_workflow_pause import Journal, Refused, digest

SOURCE = 'a' * 40
PLAN = dict(version=1, repository=api.REPOSITORY, source=SOURCE, workflows=[
    dict(id=7, path='.github/workflows/writer.yml', state='active', updated_at='2026-09-26T00:00:00Z'),
    dict(id=8, path='.github/workflows/disabled.yml', state='disabled_manually', updated_at='2026-09-25T00:00:00Z'),
])
ROUTE = dict(version=1, backend='neon', database='autopilot', epoch=1)
HOLD = hold_guard.hold.HoldIdentity('CI-fixture-only', 123, 'fixture', 'fixture', 'fixture')


@dataclass
class Target:
    database: str
    session_owner: str
    owner: str
    recipient: str
    neon: dict


TARGET = Target(**copy.deepcopy(hold_guard.EXPECTED_TARGET))


class Runtime:
    """Explicit CI stubs: no real supervisor, owner coordination or authenticated run."""
    source = SOURCE
    run_id, attempt, job_id = 11, 1, 22

    def __init__(self):
        self.reject = None
        self.scopes = []

    def check(self, kind):
        if kind == self.reject:
            raise Refused('CI_' + kind)

    def assert_running(self): self.check('run')
    def assert_alive(self): self.check('lifetime')
    def assert_held(self, scope):
        self.check('owner')
        self.scopes.append(scope)
    def assert_drained(self, scope): self.check('drain')


class Remote:
    def __init__(self):
        self.rows = {row['id']: copy.deepcopy(row) for row in PLAN['workflows']}
        self.puts = []
        self.lost = None

    def request(self, method, path):
        if path == '/git/ref/heads/main':
            return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': SOURCE}}
        number = int(path.split('/')[3])
        row = self.rows[number]
        if method == 'GET':
            return {**row, 'url': api.BASE + '/actions/workflows/' + str(number)}
        action = path.split('/')[-1]
        self.puts.append((number, action))
        row['state'] = 'disabled_manually' if action == 'disable' else 'active'
        row['updated_at'] = '2026-09-26T00:00:01Z' if action == 'disable' else '2026-09-26T00:00:02Z'
        if self.lost == action:
            raise ConnectionError('CI_PRIVATE_LOST_REPLY')


class Release:
    def __init__(self, scope, outcome):
        self.scope, self.outcome, self.calls = scope, outcome, 0
        self.reject = False

    def assert_reconciled(self, scope, outcome):
        if self.reject or (scope, outcome) != (self.scope, self.outcome):
            raise Refused('CI_UNRECONCILED')
        self.calls += 1


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name in ('pause', 'operation'):
            (root / name).mkdir(mode=0o700)
        self.paths = [root / 'pause', root / 'operation']
        self.runtime = Runtime()
        self.remote = Remote()
        self.transport_patch = patch.object(api.Transport, 'request', side_effect=self.remote.request)
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)
        self.attest = patch.object(hold_guard.hold, 'attest', return_value=HOLD).start()
        self.addCleanup(patch.stopall)
        self.open_journals()
        self.ex = self.build()

    def open_journals(self):
        self.pause, self.operation = [Journal(p) for p in self.paths]
        self.addCleanup(self.pause.close)
        self.addCleanup(self.operation.close)

    def build(self, **overrides):
        kwargs = dict(target=TARGET, operation='apply', manifest_path=Path(self.tmp.name) / 'manifest',
                      manifest_digest='f' * 64, expected_route=ROUTE, approved_hold=HOLD,
                      workflow_plan=PLAN, plan_digest=digest(PLAN), api_token='CI-token',
                      pause_journal=self.pause, operation_journal=self.operation,
                      run=self.runtime, operator=self.runtime, lifetime=self.runtime)
        return executor.MaintenanceExecutor(**{**kwargs, **overrides})

    def reopen(self, *, new_run=True):
        self.pause.close()
        self.operation.close()
        self.open_journals()
        self.runtime.reject = None
        if new_run:
            self.runtime.run_id += 1
            self.runtime.job_id += 1
        self.ex = self.build()

    def record_kinds(self):
        return [r['event']['kind'] for r in self.operation.records]

    def successful_session(self, *args, **kwargs):
        self.assertEqual(self.record_kinds()[-1], 'SESSION_INTENT')
        self.assertEqual(self.remote.rows[7]['state'], 'disabled_manually')
        kwargs['external_guard'].assert_held(args[0], 'rollback' if kwargs['rollback'] else 'apply')
        return 'BEFORE' if kwargs['rollback'] else 'AFTER'

    def test_session_is_durable_and_success_needs_separate_restore(self):
        with patch.object(executor, 'permission_session', side_effect=self.successful_session) as sql:
            self.assertEqual(self.ex.execute(None), 'AFTER')
            self.assertEqual(sql.call_count, 1)
        self.assertEqual(self.record_kinds(), ['BOUND', 'SESSION_INTENT', 'SESSION_RESULT'])
        self.assertEqual(self.remote.puts, [(7, 'disable')])
        release = Release(self.ex.scope_digest, 'AFTER')
        self.ex.restore(release, 'AFTER')
        self.assertEqual(self.remote.puts, [(7, 'disable'), (7, 'enable')])
        self.assertEqual(self.remote.rows[8]['state'], 'disabled_manually')
        self.assertEqual(self.record_kinds()[-2:], ['RESTORE_INTENT', 'RESTORED'])
        self.assertGreater(release.calls, 2)

    def test_recorded_or_interrupted_session_never_repeats_even_new_run(self):
        with patch.object(executor, 'permission_session', side_effect=ConnectionError('private')) as sql:
            with self.assertRaises(executor.ExecutionError) as error:
                self.ex.execute(None)
            self.assertNotIn('private', str(error.exception))
            self.assertEqual(error.exception.outcome, 'UNKNOWN')
            self.assertEqual(self.remote.puts, [(7, 'disable')])
            for new_run in (False, True):
                self.reopen(new_run=new_run)
                with self.assertRaisesRegex(Refused, 'ALREADY_ATTEMPTED'):
                    self.ex.execute(None)
            self.assertEqual(sql.call_count, 1)
        release = Release(self.ex.scope_digest, 'AFTER')
        self.ex.restore(release, 'AFTER')
        self.assertEqual(self.remote.puts[-1], (7, 'enable'))

    def test_crash_after_intent_before_session_forbids_retry(self):
        self.ex._event('SESSION_INTENT', 'UNKNOWN')
        self.reopen(new_run=False)
        with patch.object(executor, 'permission_session') as sql:
            with self.assertRaisesRegex(Refused, 'ALREADY_ATTEMPTED'):
                self.ex.execute(None)
            sql.assert_not_called()

    def test_new_run_cannot_start_original_bound_session(self):
        self.reopen()
        with self.assertRaisesRegex(Refused, 'ALREADY_ATTEMPTED'):
            self.ex.execute(None)
        self.assertEqual(self.remote.puts, [])

    def test_unreconciled_release_never_enables(self):
        with patch.object(executor, 'permission_session', side_effect=self.successful_session):
            self.ex.execute(None)
        release = Release(self.ex.scope_digest, 'AFTER')
        release.reject = True
        with self.assertRaises(executor.ExecutionError):
            self.ex.restore(release, 'AFTER')
        self.assertEqual(self.remote.puts, [(7, 'disable')])
        self.assertNotIn('RESTORE_INTENT', self.record_kinds())

    def test_each_missing_continuity_condition_blocks_session(self):
        for condition in ('run', 'lifetime', 'owner', 'drain'):
            with self.subTest(condition=condition):
                self.runtime.reject = condition
                with patch.object(executor, 'permission_session') as sql:
                    with self.assertRaises((Refused, executor.ExecutionError)):
                        self.ex.execute(None)
                    sql.assert_not_called()
                self.reopen(new_run=False)
        self.attest.side_effect = hold_guard.hold.Blocked('CI_HOLD_LOST')
        with patch.object(executor, 'permission_session') as sql:
            with self.assertRaises(executor.ExecutionError):
                self.ex.execute(None)
            sql.assert_not_called()
        self.assertFalse(any(action == 'enable' for _, action in self.remote.puts))

    def test_lost_disable_or_enable_reply_never_blindly_retried(self):
        self.remote.lost = 'disable'
        with patch.object(executor, 'permission_session') as sql:
            with self.assertRaises(executor.ExecutionError):
                self.ex.execute(None)
            sql.assert_not_called()
        self.reopen()
        with self.assertRaises(executor.ExecutionError):
            self.ex.restore(Release(self.ex.scope_digest, 'BEFORE'), 'BEFORE')
        self.assertEqual(self.remote.puts, [(7, 'disable')])

    def test_lost_enable_reply_blocks_further_restore(self):
        with patch.object(executor, 'permission_session', side_effect=self.successful_session):
            self.ex.execute(None)
        self.remote.lost = 'enable'
        with self.assertRaises(executor.ExecutionError):
            self.ex.restore(Release(self.ex.scope_digest, 'AFTER'), 'AFTER')
        self.reopen()
        with self.assertRaises(executor.ExecutionError):
            self.ex.restore(Release(self.ex.scope_digest, 'AFTER'), 'AFTER')
        self.assertEqual(self.remote.puts, [(7, 'disable'), (7, 'enable')])

    def test_post_session_guard_loss_records_unknown_and_never_restores(self):
        def lost(*args, **kwargs):
            self.runtime.reject = 'lifetime'
            return 'AFTER'
        with patch.object(executor, 'permission_session', side_effect=lost):
            with self.assertRaises(executor.ExecutionError):
                self.ex.execute(None)
        self.assertEqual(self.operation.records[-1]['event']['outcome'], 'UNKNOWN')
        self.assertEqual(self.remote.puts, [(7, 'disable')])

    def test_manifest_route_hold_and_source_cannot_change_on_recovery(self):
        for overrides in [dict(manifest_digest='e' * 64), dict(expected_route={**ROUTE, 'epoch': 2}),
                          dict(approved_hold=hold_guard.hold.HoldIdentity('changed', 123, 'fixture', 'fixture', 'fixture'))]:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(Refused, 'SCOPE_CHANGED'):
                self.build(**overrides)
        self.runtime.source = 'b' * 40
        with self.assertRaisesRegex(Refused, 'SOURCE_MISMATCH'):
            self.build()

    def test_no_missing_operator_lifetime_run_or_shared_journal_defaults(self):
        for overrides in [dict(operator=None), dict(lifetime=None), dict(run=None), dict(pause_journal=self.operation)]:
            with self.subTest(overrides=overrides), self.assertRaises(Refused):
                self.build(**overrides)

    def test_dispatch_outside_phase_is_refused(self):
        with self.assertRaisesRegex(Refused, 'DISPATCH_PHASE'):
            self.ex.assert_dispatch(digest(PLAN), 'disable', 7)
        self.assertEqual(self.remote.puts, [])


if __name__ == '__main__':
    unittest.main()
