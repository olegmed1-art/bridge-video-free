"""Offline recovery of real executor journals with explicitly simulated runtime."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_maintenance_executor as fixture
from ops import native_maintenance_executor as executor
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import Journal, Refused, digest, encoded


class SnapshotTests(unittest.TestCase):
    setUp = fixture.ExecutorTests.setUp
    open_journals = fixture.ExecutorTests.open_journals
    build = fixture.ExecutorTests.build

    def capture(self):
        self.pause.close()
        self.operation.close()
        return snapshot.capture(self.paths[1], self.paths[0])

    def restore(self, data):
        parent = Path(self.tmp.name) / 'recovery'
        parent.mkdir(mode=0o700, exist_ok=True)
        return snapshot.restore(data, hashlib.sha256(data).hexdigest(), self.ex.scope_digest, parent)

    def test_actual_lost_session_roundtrip_preserves_replay_prohibition(self):
        with patch.object(executor, 'permission_session', side_effect=ConnectionError('private')):
            with self.assertRaises(executor.ExecutionError):
                self.ex.execute(None)
        data = self.capture()
        root = self.restore(data)
        self.assertEqual(snapshot.capture(root / 'operation', root / 'pause'), data)
        self.paths = [root / 'pause', root / 'operation']
        self.open_journals()
        self.runtime.run_id += 1
        self.runtime.job_id += 1
        recovered = self.build()
        with patch.object(executor, 'permission_session') as sql:
            with self.assertRaisesRegex(Refused, 'ALREADY_ATTEMPTED'):
                recovered.execute(None)
            sql.assert_not_called()
        self.assertEqual(self.remote.puts, [(7, 'disable')])
        self.assertEqual(recovered.state, 'session_recorded')
        self.assertTrue((root / 'RECOVERY_ONLY').is_file())

    def test_active_journals_refuse_and_partial_lock_is_released(self):
        with self.assertRaises(BlockingIOError):
            snapshot.capture(self.paths[1], self.paths[0])
        self.operation.close()
        with self.assertRaises(BlockingIOError):
            snapshot.capture(self.paths[1], self.paths[0])
        with Journal(self.paths[1]):
            pass

    def test_same_plan_different_operation_cannot_be_mixed(self):
        old_pause = self.paths[0]
        self.capture()
        other = Path(self.tmp.name) / 'other'
        other.mkdir(mode=0o700)
        for name in ('operation', 'pause'):
            (other / name).mkdir(mode=0o700)
        self.paths = [other / 'pause', other / 'operation']
        self.open_journals()
        self.runtime.run_id += 1
        self.runtime.job_id += 1
        second = self.build()
        self.assertNotEqual(second.scope_digest, self.ex.scope_digest)
        self.capture()
        with self.assertRaisesRegex(Refused, 'PAIR_BINDING'):
            snapshot.capture(other / 'operation', old_pause)

    def test_corruption_scope_and_digest_rejected_before_destination_creation(self):
        data = self.capture()
        parent = Path(self.tmp.name) / 'empty'
        parent.mkdir(mode=0o700)
        changed = json.loads(data)
        changed['journals']['operation'][0] += ' '
        for payload, expected_digest, scope in (
            (data, '0' * 64, self.ex.scope_digest),
            (data, hashlib.sha256(data).hexdigest(), '0' * 64),
            (encoded(changed), hashlib.sha256(encoded(changed)).hexdigest(), self.ex.scope_digest),
        ):
            with self.assertRaises(Refused):
                snapshot.restore(payload, expected_digest, scope, parent)
            self.assertEqual(list(parent.iterdir()), [])

    def test_oversize_valid_journal_rejected_before_restore_output(self):
        data = self.capture()
        value = json.loads(data)
        rows = value['journals']['operation']
        previous = digest(json.loads(rows[-1]))
        for _ in range(18):
            record = {'previous': previous, 'event': {'payload': 'x' * 250000}}
            rows.append(encoded(record).decode())
            previous = digest(record)
        data = encoded(value)
        self.assertLess(len(data), snapshot.MAX_BYTES)
        parent = Path(self.tmp.name) / 'empty'
        parent.mkdir(mode=0o700)
        with self.assertRaisesRegex(Refused, 'JOURNAL_BYTES'):
            snapshot.restore(data, hashlib.sha256(data).hexdigest(), self.ex.scope_digest, parent)
        self.assertEqual(list(parent.iterdir()), [])

    def test_no_overwrite_and_uncertain_restore_retains_remnants(self):
        data = self.capture()
        parent = Path(self.tmp.name) / 'recovery'
        parent.mkdir(mode=0o700)
        with patch.object(snapshot, '_write', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.restore(data)
        remnants = list(parent.iterdir())
        self.assertEqual(len(remnants), 1)
        self.assertFalse((remnants[0] / 'RECOVERY_ONLY').exists())
        with self.assertRaises(FileExistsError):
            self.restore(data)
        self.assertEqual(list(parent.iterdir()), remnants)

    def test_symlink_and_public_parent_refused(self):
        data = self.capture()
        public = Path(self.tmp.name) / 'public'
        public.mkdir(mode=0o755)
        linked = Path(self.tmp.name) / 'linked'
        linked.symlink_to(public, target_is_directory=True)
        for parent in (public, linked):
            with self.assertRaises(Refused):
                snapshot.restore(data, hashlib.sha256(data).hexdigest(), self.ex.scope_digest, parent)
        self.assertEqual(list(public.iterdir()), [])

    def test_oversize_corrupt_and_extra_source_files_refused(self):
        data = self.capture()
        with patch.object(snapshot, 'MAX_BYTES', 16):
            with self.assertRaises(Refused):
                snapshot.capture(self.paths[1], self.paths[0])
        path = self.paths[1] / '000000.json'
        old = path.read_bytes()
        path.write_bytes(b'{')
        with self.assertRaises((Refused, ValueError)):
            snapshot.capture(self.paths[1], self.paths[0])
        path.write_bytes(old)
        (self.paths[1] / 'unexpected').write_text('partial')
        with self.assertRaises(Refused):
            snapshot.capture(self.paths[1], self.paths[0])

    def test_legacy_unbound_pause_rejected_without_repair(self):
        self.capture()
        path = self.paths[0] / '000000.json'
        record = json.loads(path.read_bytes())
        del record['event']['operation_scope_digest']
        path.write_bytes(encoded(record))
        old = path.read_bytes()
        with self.assertRaisesRegex(Refused, 'PAIR_BINDING'):
            snapshot.capture(self.paths[1], self.paths[0])
        self.assertEqual(path.read_bytes(), old)
        self.open_journals()
        with self.assertRaisesRegex(Refused, 'JOURNAL_PLAN_MISMATCH'):
            self.build()


if __name__ == '__main__':
    unittest.main()
