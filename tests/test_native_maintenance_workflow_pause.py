import copy
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops.native_maintenance_workflow_pause import Journal, Refused, WorkflowPause, digest


def plan():
    return {'version': 1, 'repository': 'olegmed1-art/bridge-video-free', 'source': 'a' * 40,
            'workflows': [dict(id=i, path=f'.github/workflows/writer-{i}.yml',
                               state='disabled_inactivity' if i == 3 else 'active',
                               updated_at='2026-09-26T00:00:00Z') for i in (1, 2, 3)]}


class API:
    def __init__(self, source):
        self.rows = {row['id']: copy.deepcopy(row) for row in source['workflows']}
        self.writes = []
        self.fail_at = None
        self.lost_reply = False

    def get_workflow(self, workflow):
        return copy.deepcopy(self.rows[workflow])

    def disable_workflow(self, workflow):
        self.writes.append(('disable', workflow))
        if self.fail_at == workflow:
            raise OSError('network unavailable')
        self.rows[workflow].update(state='disabled_manually', updated_at='2026-09-26T01:00:00Z')
        if self.lost_reply:
            raise OSError('reply lost after accepted disable')

    def enable_workflow(self, workflow):
        self.writes.append(('enable', workflow))
        self.rows[workflow].update(state='active', updated_at='2026-09-26T02:00:00Z')
        if self.lost_reply:
            raise OSError('reply lost after accepted enable')


class Coordination:
    def __init__(self):
        self.allowed = True

    def assert_scope(self, _):
        if not self.allowed:
            raise Refused('COORDINATION_LOST')


class Release:
    def __init__(self):
        self.remote_complete = True
        self.database_outcome = 'AFTER'

    def assert_reconciled(self, _):
        if not self.remote_complete or self.database_outcome not in ('BEFORE', 'AFTER'):
            raise Refused('REMOTE_OR_DB_UNRECONCILED')


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = plan()
        self.api = API(self.source)
        self.coordination = Coordination()
        self.journal = Journal(self.root)
        self.pause = self.rebuild()

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def rebuild(self):
        return WorkflowPause(self.source, digest(self.source), self.api, self.journal, self.coordination)

    def restart(self):
        self.journal.close()
        self.journal = Journal(self.root)
        self.pause = self.rebuild()

    def test_disable_restore_survives_controller_restart_preserves_prior_disabled(self):
        self.pause.pause()
        self.assertEqual(self.api.writes, [('disable', 1), ('disable', 2)])
        self.restart()
        self.pause.assert_paused()
        self.pause.restore(Release())
        self.assertEqual(self.api.writes, [('disable', 1), ('disable', 2), ('enable', 1), ('enable', 2)])
        self.assertEqual(self.api.rows[3], self.source['workflows'][2])
        self.restart()
        self.pause.restore(Release())
        self.assertEqual(len(self.api.writes), 4)

    def test_lost_disable_reply_is_not_retried_or_automatically_reversed(self):
        self.api.lost_reply = True
        with self.assertRaises(OSError):
            self.pause.pause()
        self.assertEqual(self.api.rows[1]['state'], 'disabled_manually')
        self.restart()
        for action in (self.pause.pause, lambda: self.pause.restore(Release())):
            with self.assertRaises(Refused):
                action()
        self.assertEqual(self.api.writes, [('disable', 1)])

    def test_partial_disable_error_keeps_completed_disable(self):
        self.api.fail_at = 2
        with self.assertRaises(OSError):
            self.pause.pause()
        self.assertEqual(self.api.rows[1]['state'], 'disabled_manually')
        self.assertEqual(self.api.rows[2]['state'], 'active')
        self.restart()
        with self.assertRaisesRegex(Refused, 'AMBIGUOUS'):
            self.pause.restore(Release())
        self.assertFalse(any(kind == 'enable' for kind, _ in self.api.writes))

    def test_cancellation_after_remote_start_does_not_restore(self):
        self.pause.pause()
        self.restart()
        release = Release()
        release.remote_complete = False
        with self.assertRaisesRegex(Refused, 'UNRECONCILED'):
            self.pause.restore(release)
        self.assertEqual(self.api.writes, [('disable', 1), ('disable', 2)])

    def test_lost_commit_ack_requires_fresh_classified_database_outcome(self):
        self.pause.pause()
        release = Release()
        release.database_outcome = 'UNKNOWN'
        with self.assertRaisesRegex(Refused, 'UNRECONCILED'):
            self.pause.restore(release)
        self.assertEqual(len(self.api.writes), 2)
        self.restart()
        release.database_outcome = 'AFTER'
        self.pause.restore(release)
        self.assertEqual(len(self.api.writes), 4)

    def test_lost_enable_reply_stops_remaining_restore(self):
        self.pause.pause()
        self.api.lost_reply = True
        with self.assertRaises(OSError):
            self.pause.restore(Release())
        self.assertEqual(self.api.rows[2]['state'], 'disabled_manually')
        self.restart()
        with self.assertRaisesRegex(Refused, 'AMBIGUOUS'):
            self.pause.restore(Release())
        self.assertEqual(self.api.writes[-1], ('enable', 1))

    def test_external_change_latches_failure(self):
        self.pause.pause()
        self.api.rows[1]['state'] = 'active'
        with self.assertRaisesRegex(Refused, 'DRIFT'):
            self.pause.assert_paused()
        self.api.rows[1]['state'] = 'disabled_manually'
        with self.assertRaisesRegex(Refused, 'ALREADY_FAILED'):
            self.pause.assert_paused()

    def test_scope_loss_before_dispatch_leaves_durable_intent_without_api_write(self):
        original = self.journal.append
        def append(event):
            original(event)
            if event['kind'] == 'DISABLE_INTENT':
                self.coordination.allowed = False
        with patch.object(self.journal, 'append', side_effect=append):
            with self.assertRaisesRegex(Refused, 'COORDINATION_LOST'):
                self.pause.pause()
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.journal.records[-1]['event']['kind'], 'DISABLE_INTENT')

    def test_journal_write_failure_prevents_dispatch(self):
        with patch.object(self.journal, 'append', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.pause.pause()
        self.assertEqual(self.api.writes, [])

    def test_journal_exclusive_lock_is_enforced(self):
        with self.assertRaises(BlockingIOError):
            Journal(self.root)

    def test_corrupt_tail_blocks_recovery(self):
        self.journal.close()
        (self.root / '000001.json').write_text('{"previous":')
        (self.root / '000001.json').chmod(0o600)
        with self.assertRaises(ValueError):
            Journal(self.root)

    def test_fifo_and_symlink_refused_without_blocking(self):
        self.journal.close()
        os.mkfifo(self.root / '000001.json', 0o600)
        with self.assertRaisesRegex(Refused, 'REGULAR'):
            Journal(self.root)
        (self.root / '000001.json').unlink()
        (self.root / '000001.json').symlink_to('000000.json')
        with self.assertRaises(OSError):
            Journal(self.root)

    def test_tampered_plan_unknown_id_and_injected_restore_refused(self):
        with self.assertRaisesRegex(Refused, 'DIGEST'):
            WorkflowPause(self.source, '0' * 64, self.api, self.journal, self.coordination)
        self.journal.append({'kind': 'ENABLED', 'id': 1, 'observed': self.source['workflows'][0]})
        with self.assertRaisesRegex(Refused, 'PHASE'):
            self.rebuild()

    def test_disable_intent_is_readable_from_disk_before_request(self):
        # Readback observes intent before the mocked network side effect.
        import json
        original = self.api.disable_workflow
        def disable(workflow):
            record = json.loads((self.root / f'{len(self.journal.records)-1:06d}.json').read_text())
            self.assertEqual(record['event']['kind'], 'DISABLE_INTENT')
            self.assertEqual(record['event']['id'], workflow)
            original(workflow)
        self.api.disable_workflow = disable
        self.pause.pause()

    def test_replaced_lock_stops_api_writes(self):
        (self.root / 'lock').unlink()
        (self.root / 'lock').touch(mode=0o600)
        with self.assertRaisesRegex(Refused, 'LOCK_CHANGED'):
            self.pause.pause()
        self.assertEqual(self.api.writes, [])


def crash_after_intent(root):
    source = plan()
    with Journal(root) as journal:
        api = API(source)
        api.disable_workflow = lambda _: os._exit(73)
        WorkflowPause(source, digest(source), api, journal, Coordination()).pause()


class CrashTests(unittest.TestCase):
    def test_real_process_exit_keeps_intent_and_releases_local_lock(self):
        with tempfile.TemporaryDirectory() as root:
            child = multiprocessing.Process(target=crash_after_intent, args=(root,))
            child.start()
            child.join(5)
            self.assertEqual(child.exitcode, 73)
            with Journal(root) as journal:
                source = plan()
                api = API(source)
                pause = WorkflowPause(source, digest(source), api, journal, Coordination())
                with self.assertRaisesRegex(Refused, 'AMBIGUOUS'):
                    pause.restore(Release())
                self.assertEqual(api.writes, [])


if __name__ == '__main__':
    unittest.main()
