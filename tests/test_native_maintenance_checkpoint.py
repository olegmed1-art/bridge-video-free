"""Explicit memory-store fault injection; no OCI/off-VM production claim."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_checkpoint as cp
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import Journal, Refused, digest, encoded
from ops import native_maintenance_executor as executor
from ops import native_maintenance_workflow_api as api
import test_native_maintenance_executor as executor_tests


class MemoryStore:
    def __init__(self):
        self.objects, self.heads = {}, {}
        self.revision = 0
        self.fail = None
        self.private = True

    def fault(self, name):
        if self.fail == name:
            raise ConnectionError('CI_FAULT_' + name)

    def assert_private(self):
        if not self.private:
            raise Refused('CI_PUBLIC_STORE')

    def read_head(self, scope):
        self.fault('read_head')
        return self.heads.get(scope)

    def put_archive(self, scope, sha, data):
        self.fault('before_archive')
        key = (scope, sha)
        if key in self.objects and self.objects[key] != data:
            raise Refused('CI_IMMUTABLE_CONFLICT')
        self.objects[key] = data
        self.fault('after_archive')

    def read_archive(self, scope, sha, limit):
        self.fault('read_archive')
        data = self.objects[(scope, sha)]
        if len(data) > limit:
            raise Refused('CI_OVERSIZE')
        return data

    def compare_head(self, scope, expected_revision, data):
        self.fault('before_head')
        head = self.heads.get(scope)
        if (head[1] if head else None) != expected_revision:
            raise Refused('CI_CAS_CONFLICT')
        self.revision += 1
        self.heads[scope] = (data, str(self.revision))
        self.fault('after_head')


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in snapshot.NAMES:
            (self.root / name).mkdir(mode=0o700)
        self.operation, self.pause = [Journal(self.root / name) for name in snapshot.NAMES]
        self.addCleanup(self.operation.close)
        self.addCleanup(self.pause.close)
        plan = dict(version=1, repository='olegmed1-art/bridge-video-free', source='a' * 40,
                    workflows=[dict(id=7, path='.github/workflows/ci-only.yml', state='active',
                                    updated_at='2026-09-26T00:00:00Z')])
        scope = dict(source=plan['source'], workflow_plan_digest=digest(plan), manifest_digest='f' * 64)
        self.scope = digest(scope)
        self.operation.append(dict(kind='BOUND', scope=scope))
        self.pause.append(dict(kind='PLAN', digest=digest(plan), plan=plan, operation_scope_digest=self.scope))
        self.store = MemoryStore()
        self.cp = cp.JournalCheckpoint(self.store)

    def sync(self):
        return self.cp.sync(self.scope, self.operation, self.pause)

    def test_capture_retains_exclusive_locks_and_matches_offline(self):
        data = snapshot.capture_locked(self.operation, self.pause)
        with self.assertRaises(BlockingIOError):
            Journal(self.operation.root)
        with self.assertRaises(BlockingIOError):
            snapshot.capture(self.operation.root, self.pause.root)
        self.operation.close()
        self.pause.close()
        self.assertEqual(snapshot.capture(self.root / 'operation', self.root / 'pause'), data)

    def test_sync_monotonic_head_and_restore_exact_accepted_pair(self):
        first = self.sync()
        self.assertEqual(self.sync(), first)
        self.assertEqual(self.store.revision, 1)
        self.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        second = self.sync()
        head = cp.parse_head(self.store.read_head(self.scope)[0], self.scope)
        self.assertEqual((head['sequence'], head['previous']), (2, first))
        data = cp.accepted_latest(self.store, self.scope, second)
        parent = self.root / 'recovery'
        parent.mkdir(mode=0o700)
        restored = snapshot.restore(data, cp.sha(data), self.scope, parent)
        self.assertEqual(snapshot.capture(restored / 'operation', restored / 'pause'), data)
        with self.assertRaisesRegex(Refused, 'LATEST_CHANGED'):
            cp.accepted_latest(self.store, self.scope, first)

    def test_lost_acceptance_reply_poisons_instance_but_preserves_intent(self):
        self.sync()
        self.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        self.store.fail = 'after_head'
        with self.assertRaises(ConnectionError):
            self.sync()
        head = self.store.heads[self.scope][0]
        self.store.fail = None
        with self.assertRaisesRegex(Refused, 'ALREADY_FAILED'):
            self.sync()
        data = cp.accepted_latest(self.store, self.scope, cp.sha(head))
        records = snapshot._parse(data)['journals']['operation']
        self.assertIn('SESSION_INTENT', records[-1])
        recovered = cp.JournalCheckpoint(self.store, accepted_head_digest=cp.sha(head))
        self.assertEqual(recovered.sync(self.scope, self.operation, self.pause), cp.sha(head))

    def test_each_remote_failure_stops_without_retry(self):
        for fault in ('read_head', 'before_archive', 'after_archive', 'read_archive', 'before_head', 'after_head'):
            with self.subTest(fault=fault):
                store = MemoryStore()
                checkpoint = cp.JournalCheckpoint(store)
                store.fail = fault
                with self.assertRaises(ConnectionError):
                    checkpoint.sync(self.scope, self.operation, self.pause)
                store.fail = None
                with self.assertRaisesRegex(Refused, 'ALREADY_FAILED'):
                    checkpoint.sync(self.scope, self.operation, self.pause)
                self.assertEqual(store.revision, int(fault == 'after_head'))

    def test_fresh_instance_requires_independent_existing_head_acceptance(self):
        accepted = self.sync()
        for value in (None, 'e' * 64):
            with self.subTest(value=value), self.assertRaisesRegex(Refused, 'REQUIRES_ACCEPTANCE'):
                cp.JournalCheckpoint(self.store, accepted_head_digest=value).sync(self.scope, self.operation, self.pause)
        self.assertEqual(cp.JournalCheckpoint(self.store, accepted_head_digest=accepted).sync(
            self.scope, self.operation, self.pause), accepted)

    def test_stale_local_pair_cannot_overwrite_newer_accepted_head(self):
        stale = snapshot.capture_locked(self.operation, self.pause)
        self.sync()
        self.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        accepted = self.sync()
        parent = self.root / 'stale'
        parent.mkdir(mode=0o700)
        restored = snapshot.restore(stale, cp.sha(stale), self.scope, parent)
        with Journal(restored / 'operation') as operation, Journal(restored / 'pause') as pause:
            with self.assertRaisesRegex(Refused, 'ROLLBACK_OR_FORK'):
                cp.JournalCheckpoint(self.store, accepted_head_digest=accepted).sync(self.scope, operation, pause)
        self.assertEqual(self.store.revision, 2)

    def test_competing_head_or_revision_blocks_writes(self):
        self.sync()
        data, _ = self.store.heads[self.scope]
        self.store.heads[self.scope] = (data, 'external-revision')
        with self.assertRaisesRegex(Refused, 'CONCURRENT_CHANGE'):
            self.sync()

    def test_privacy_loss_and_archive_corruption_block(self):
        accepted = self.sync()
        self.store.private = False
        with self.assertRaisesRegex(Refused, 'PUBLIC_STORE'):
            cp.accepted_latest(self.store, self.scope, accepted)
        self.store.private = True
        key = next(iter(self.store.objects))
        self.store.objects[key] = b'corrupt'
        with self.assertRaisesRegex(Refused, 'ARCHIVE_DIGEST'):
            cp.accepted_latest(self.store, self.scope, accepted)

    def test_local_change_during_upload_never_returns_success(self):
        original = self.store.compare_head
        def changed(*args):
            original(*args)
            self.operation.append(dict(kind='CI_CONCURRENT_LOCAL_APPEND'))
        with patch.object(self.store, 'compare_head', side_effect=changed):
            with self.assertRaisesRegex(Refused, 'CHANGED_DURING_SYNC'):
                self.sync()

    def test_compare_conflict_does_not_publish_head(self):
        def conflict(*args):
            raise Refused('CI_CAS_CONFLICT')
        with patch.object(self.store, 'compare_head', side_effect=conflict):
            with self.assertRaisesRegex(Refused, 'CAS_CONFLICT'):
                self.sync()
        self.assertEqual(self.store.heads, {})
        self.assertEqual(len(self.store.objects), 1)  # Preserve uncertain objects.

    def test_absent_head_cannot_bootstrap_existing_intent_history(self):
        self.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        with self.assertRaisesRegex(Refused, 'FRESH_BOUND_PAIR_REQUIRED'):
            self.sync()
        self.assertEqual(self.store.heads, {})
        self.assertEqual(self.store.objects, {})

    def test_invalid_store_and_malformed_heads_refused(self):
        with self.assertRaisesRegex(Refused, 'STORE_REQUIRED'):
            cp.JournalCheckpoint(None)
        self.sync()
        head = cp.parse_head(self.store.heads[self.scope][0], self.scope)
        for change in ({'sequence': True}, {'previous': 'a' * 64}, {'archive_digest': 'x'}, {'version': 2}):
            with self.subTest(change=change), self.assertRaises(Refused):
                cp.parse_head(encoded({**head, **change}), self.scope)


class ExecutorCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.fixture = executor_tests.ExecutorTests('test_session_is_durable_and_success_needs_separate_restore')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = MemoryStore()
        self.fixture.ex.checkpoint = cp.JournalCheckpoint(self.store)

    def latest(self):
        scope = self.fixture.ex.scope_digest
        head = self.store.heads[scope][0]
        return snapshot._parse(cp.accepted_latest(self.store, scope, cp.sha(head)))

    def test_remote_intent_exists_before_each_put_and_sql(self):
        fixture = self.fixture
        request = fixture.remote.request
        def checked_request(method, path):
            if method == 'PUT':
                rows = self.latest()['journals']['pause']
                expected = 'DISABLE_INTENT' if path.endswith('/disable') else 'ENABLE_INTENT'
                self.assertIn(expected, rows[-1])
            return request(method, path)
        def sql(*args, **kwargs):
            self.assertIn('SESSION_INTENT', self.latest()['journals']['operation'][-1])
            return fixture.successful_session(*args, **kwargs)
        with patch.object(api.Transport, 'request', side_effect=checked_request), \
                patch.object(executor, 'permission_session', side_effect=sql):
            self.assertEqual(fixture.ex.execute(None), 'AFTER')
            self.assertIn('SESSION_RESULT', self.latest()['journals']['operation'][-1])
            fixture.ex.restore(executor_tests.Release(fixture.ex.scope_digest, 'AFTER'), 'AFTER')
        self.assertIn('RESTORED', self.latest()['journals']['operation'][-1])

    def test_lost_head_reply_prevents_sql_and_preserves_unknown_on_recovery(self):
        fixture = self.fixture
        compare = self.store.compare_head
        def lose_session_acceptance(scope, revision, data):
            compare(scope, revision, data)
            archive = cp.parse_head(data, scope)['archive_digest']
            rows = snapshot._parse(self.store.objects[(scope, archive)])['journals']['operation']
            if 'SESSION_INTENT' in rows[-1]:
                raise ConnectionError('CI_LOST_SESSION_INTENT_ACK')
        with patch.object(self.store, 'compare_head', side_effect=lose_session_acceptance), \
                patch.object(executor, 'permission_session') as sql:
            with self.assertRaises(executor.ExecutionError):
                fixture.ex.execute(None)
            sql.assert_not_called()
        self.assertEqual(fixture.remote.puts, [(7, 'disable')])
        self.assertIn('SESSION_INTENT', self.latest()['journals']['operation'][-1])
        accepted = cp.sha(self.store.heads[fixture.ex.scope_digest][0])
        fixture.reopen()
        fixture.ex.checkpoint = cp.JournalCheckpoint(self.store, accepted_head_digest=accepted)
        with self.assertRaisesRegex(Refused, 'ALREADY_ATTEMPTED'):
            fixture.ex.execute(None)

    def test_lost_enable_intent_acceptance_never_enables(self):
        fixture = self.fixture
        with patch.object(executor, 'permission_session', side_effect=fixture.successful_session):
            fixture.ex.execute(None)
        compare = self.store.compare_head
        def lose_enable(scope, revision, data):
            compare(scope, revision, data)
            archive = cp.parse_head(data, scope)['archive_digest']
            rows = snapshot._parse(self.store.objects[(scope, archive)])['journals']['pause']
            if 'ENABLE_INTENT' in rows[-1]:
                raise ConnectionError('CI_LOST_ENABLE_INTENT_ACK')
        with patch.object(self.store, 'compare_head', side_effect=lose_enable):
            with self.assertRaises(executor.ExecutionError):
                fixture.ex.restore(executor_tests.Release(fixture.ex.scope_digest, 'AFTER'), 'AFTER')
        self.assertEqual(fixture.remote.puts, [(7, 'disable')])
        self.assertIn('ENABLE_INTENT', self.latest()['journals']['pause'][-1])

    def test_lifetime_expiry_during_upload_blocks_mutation(self):
        fixture = self.fixture
        compare = self.store.compare_head
        def expired(*args):
            compare(*args)
            fixture.runtime.reject = 'lifetime'
        with patch.object(self.store, 'compare_head', side_effect=expired), \
                patch.object(executor, 'permission_session') as sql:
            with self.assertRaises(executor.ExecutionError):
                fixture.ex.execute(None)
            sql.assert_not_called()
        self.assertEqual(fixture.remote.puts, [])


if __name__ == '__main__':
    unittest.main()
