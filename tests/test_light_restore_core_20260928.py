"""Offline real-journal/CAS tests; no GitHub, Oracle, or database access."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_checkpoint as cp
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import Journal, digest
from ops.incident import light_restore_core_20260928 as core


class Store:
    def __init__(self):
        self.head = None
        self.archives = {}
        self.revision = 0
        self.fail_after_cas = False

    def assert_private(self):
        pass

    def read_head(self, scope):
        return self.head

    def put_archive(self, scope, sha, data):
        assert (scope, sha) not in self.archives or self.archives[(scope, sha)] == data
        self.archives[(scope, sha)] = data

    def read_archive(self, scope, sha, limit):
        data = self.archives[(scope, sha)]
        assert len(data) <= limit
        return data

    def compare_head(self, scope, expected_revision, data):
        assert (self.head[1] if self.head else None) == expected_revision
        self.revision += 1
        self.head = (data, str(self.revision))
        if self.fail_after_cas:
            raise ConnectionError('lost CAS acknowledgement')


class Guard:
    receipt_accepted = True
    token = 'fixture-token'
    api = None
    run_identity = dict(run_id=33, attempt=1, job_id=34)

    def __init__(self, plan_digest):
        self.plan_digest = plan_digest
        self.live = self.reconciliations = 0

    def assert_current(self):
        pass

    def assert_live(self):
        self.live += 1

    def assert_reconciled(self, scope, outcome):
        assert (scope, outcome) == (core.SCOPE, 'AFTER')
        self.reconciliations += 1


class WorkflowAPI:
    original = None
    instance = None
    ambiguous = False

    def __init__(self, token, plan, digest_value, *, mutation_guard, read_api):
        assert token == 'fixture-token' and digest(plan) == digest_value
        self.guard = mutation_guard
        self.plan_digest = digest_value
        self.current = dict(self.original)
        self.puts = 0
        type(self).instance = self

    def get_workflow(self, workflow_id):
        assert workflow_id == core.WORKFLOW
        return dict(self.current)

    def enable_workflow(self, workflow_id):
        self.guard.assert_dispatch(self.plan_digest, 'enable', workflow_id)
        self.guard.assert_dispatch(self.plan_digest, 'enable', workflow_id)
        self.puts += 1
        self.current.update(state='active', updated_at='2026-09-28T10:00:00Z')
        if self.ambiguous:
            raise ConnectionError('lost PUT acknowledgement')


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name in ('operation', 'pause'):
            (root / name).mkdir(mode=0o700)
        self.op = Journal(root / 'operation')
        self.pause = Journal(root / 'pause')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.pause.close)
        self.addCleanup(self.op.close)
        self.original = dict(id=7, path='.github/workflows/recovery-registry-population.yml',
                             state='active', updated_at='2026-09-28T03:53:33Z')
        disabled = dict(self.original, state='disabled_manually', updated_at='2026-09-28T05:44:00Z')
        self.plan = dict(version=1, repository='olegmed1-art/bridge-video-free',
                         source=core.SOURCE, workflows=[self.original])
        first = dict(run_id=11, attempt=1, job_id=12)
        second = dict(run_id=21, attempt=1, job_id=22)
        self.scope = dict(source=core.SOURCE, operation='apply', execution_mode='staged_v1',
                          origin_run=first, workflow_plan_digest=digest(self.plan))
        self.scope_digest = digest(self.scope)
        self.packet = dict(stage='restore', expected_outcome='AFTER', scope=self.scope,
                           plan=self.plan, prior_units=[
                               dict(stage='prepare', source=core.SOURCE,
                                    scope_digest=self.scope_digest, run=first),
                               dict(stage='execute', source=core.SOURCE,
                                    scope_digest=self.scope_digest, run=second)])
        self.op.append(dict(kind='BOUND', scope=self.scope))
        self.pause.append(dict(kind='PLAN', digest=digest(self.plan), plan=self.plan,
                               operation_scope_digest=self.scope_digest))
        self.store = Store()
        bound = cp.JournalCheckpoint(self.store).sync(self.scope_digest, self.op, self.pause)
        self.pause.append(dict(kind='DISABLE_INTENT', id=7, observed=self.original))
        self.pause.append(dict(kind='DISABLED', id=7, observed=disabled))
        self.op.append(dict(kind='PREPARED', run=first, outcome='UNKNOWN'))
        self.head = cp.JournalCheckpoint(self.store, accepted_head_digest=bound).sync(
            self.scope_digest, self.op, self.pause)
        for kind, outcome in [('SESSION_BOUND', 'UNKNOWN'), ('SESSION_INTENT', 'UNKNOWN'),
                              ('SESSION_RESULT', 'AFTER')]:
            self.op.append(dict(kind=kind, run=second, outcome=outcome))
        self.pair = cp.sha(snapshot.capture_locked(self.op, self.pause))
        self.packet.update(accepted_head_digest=self.head, recovery_pair_digest=self.pair)
        self.guard = Guard(digest(self.plan))
        WorkflowAPI.original = disabled
        WorkflowAPI.instance = None
        WorkflowAPI.ambiguous = False
        self.patches = [patch.object(core, name, value) for name, value in
                        [('SCOPE', self.scope_digest), ('HEAD', self.head), ('PAIR', self.pair),
                         ('WORKFLOW', 7)]]
        self.patches.append(patch('ops.native_maintenance_workflow_api.WorkflowAPI', WorkflowAPI))
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_success_publishes_each_stage_and_enables_once(self):
        result = core.restore(self.packet, self.op, self.pause, self.store, self.guard)
        self.assertEqual(result['outcome'], 'AFTER')
        self.assertEqual(result['restored_workflow_ids'], [7])
        self.assertEqual(WorkflowAPI.instance.puts, 1)
        self.assertEqual([r['event']['kind'] for r in self.op.records][-2:],
                         ['RESTORE_INTENT', 'RESTORED'])
        self.assertEqual([r['event']['kind'] for r in self.pause.records][-2:],
                         ['ENABLE_INTENT', 'ENABLED'])
        self.assertEqual(cp.accepted_latest(self.store, self.scope_digest, result['head_digest']),
                         snapshot.capture_locked(self.op, self.pause))
        self.assertGreater(self.guard.reconciliations, 5)

    def test_changed_initial_pair_refuses_without_effect(self):
        self.op.append(dict(kind='RESTORE_INTENT', run=Guard.run_identity, outcome='AFTER'))
        with self.assertRaises(core.RestoreRefused) as caught:
            core.restore(self.packet, self.op, self.pause, self.store, self.guard)
        self.assertFalse(caught.exception.effects_possible)
        self.assertIsNone(WorkflowAPI.instance)
        self.assertEqual(cp.sha(self.store.head[0]), self.head)

    def test_lost_enable_ack_stops_after_checkpointed_intent(self):
        WorkflowAPI.ambiguous = True
        with self.assertRaises(core.RestoreRefused) as caught:
            core.restore(self.packet, self.op, self.pause, self.store, self.guard)
        self.assertTrue(caught.exception.effects_possible)
        self.assertEqual(WorkflowAPI.instance.puts, 1)
        self.assertEqual(self.pause.records[-1]['event']['kind'], 'ENABLE_INTENT')
        archived = cp.accepted_latest(self.store, self.scope_digest, cp.sha(self.store.head[0]))
        self.assertEqual(snapshot._parse(archived)['journals']['pause'][-1],
                         snapshot._read(self.pause)[-1])
        with self.assertRaises(core.RestoreRefused):
            core.restore(self.packet, self.op, self.pause, self.store, self.guard)
        self.assertEqual(WorkflowAPI.instance.puts, 1)

    def test_lost_suffix_cas_ack_never_enables(self):
        self.store.fail_after_cas = True
        with self.assertRaises(core.RestoreRefused) as caught:
            core.restore(self.packet, self.op, self.pause, self.store, self.guard)
        self.assertTrue(caught.exception.effects_possible)
        self.assertEqual(WorkflowAPI.instance.puts, 0)
        self.assertNotEqual(cp.sha(self.store.head[0]), self.head)

    def test_workflow_drift_refuses_before_checkpoint_effect(self):
        WorkflowAPI.original = dict(WorkflowAPI.original, updated_at='2026-09-28T09:00:00Z')
        with self.assertRaises(core.RestoreRefused) as caught:
            core.restore(self.packet, self.op, self.pause, self.store, self.guard)
        self.assertEqual(caught.exception.code, 'INCIDENT_WORKFLOW_DRIFT')
        self.assertFalse(caught.exception.effects_possible)
        self.assertEqual(cp.sha(self.store.head[0]), self.head)
        self.assertEqual(WorkflowAPI.instance.puts, 0)


if __name__ == '__main__':
    unittest.main()
