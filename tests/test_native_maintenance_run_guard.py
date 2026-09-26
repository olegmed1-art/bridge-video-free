import base64
import copy
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from ops import native_maintenance_run_guard as guard


class FakeAPI:
    def __init__(self):
        self.source = 'a' * 40
        self.calls = []
        principal = {'login': guard.OWNER, 'id': guard.OWNER_ID}
        self.run = {'id': 123, 'run_attempt': 2, 'head_sha': self.source,
                    'path': guard.WORKFLOW, 'head_branch': 'main', 'event': 'push',
                    'repository': {'full_name': guard.REPOSITORY},
                    'head_repository': {'full_name': guard.REPOSITORY},
                    'actor': principal.copy(), 'triggering_actor': principal.copy(),
                    'status': 'in_progress', 'conclusion': None}
        raw = (Path(__file__).resolve().parents[1] / guard.WORKFLOW).read_bytes()
        self.file = {'type': 'file', 'path': guard.WORKFLOW, 'encoding': 'base64',
                     'size': len(raw), 'content': base64.b64encode(raw).decode()}
        self.main = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': self.source}}
        self.jobs = {'total_count': 1, 'jobs': [{'id': 456, 'name': guard.JOB,
                    'run_id': 123, 'run_attempt': 2, 'head_sha': self.source,
                    'status': 'in_progress', 'conclusion': None}]}

    def get(self, path):
        self.calls.append(path)
        if path == '/actions/runs/123':
            return copy.deepcopy(self.run)
        if path == '/contents/' + guard.WORKFLOW + '?ref=' + self.source:
            return copy.deepcopy(self.file)
        if path == '/git/ref/heads/main':
            return copy.deepcopy(self.main)
        if path == '/actions/runs/123/attempts/2/jobs?per_page=100':
            return copy.deepcopy(self.jobs)
        raise AssertionError('UNEXPECTED_API_PATH')


class RunBindingTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.binding = guard.RunBinding(self.api.source, 123, 2, self.api)

    def refuse_and_latch(self):
        with self.assertRaises(Exception):
            self.binding.assert_running()
        self.assertTrue(self.binding.failed)
        self.binding.api = FakeAPI()
        with self.assertRaisesRegex(guard.Refused, 'ALREADY_FAILED'):
            self.binding.assert_running()

    def test_valid_exact_attempt_and_stable_job_identity(self):
        self.binding.assert_running()
        self.binding.assert_running()
        self.assertEqual(self.binding.job_id, 456)
        self.assertIn('/actions/runs/123/attempts/2/jobs?per_page=100', self.api.calls)
        self.assertNotIn('/actions/runs/123/jobs', self.api.calls)
        self.assertFalse(hasattr(self.binding, 'assert_held'))

    def test_workflow_digest_pins_real_file(self):
        raw = (Path(__file__).resolve().parents[1] / guard.WORKFLOW).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), guard.WORKFLOW_SHA256)
        self.api.file['content'] = base64.b64encode(raw.replace(b'queue: max', b'queue: single')).decode()
        self.api.file['size'] = len(base64.b64decode(self.api.file['content']))
        self.refuse_and_latch()

    def test_run_field_drift_and_nonrunning_states(self):
        for key, value in [('id', 124), ('run_attempt', 3), ('head_sha', 'b' * 40),
                           ('path', 'other.yml'), ('head_branch', 'feature'), ('event', 'pull_request'),
                           ('status', 'queued'), ('status', 'completed'), ('conclusion', 'cancelled')]:
            with self.subTest(key=key, value=value):
                self.setUp()
                self.api.run[key] = value
                self.refuse_and_latch()

    def test_principal_and_repository_drift(self):
        for field, key, value in [('actor', 'login', 'someone'), ('triggering_actor', 'id', 7),
                                  ('repository', 'full_name', 'someone/repo'),
                                  ('head_repository', 'full_name', 'someone/fork')]:
            with self.subTest(field=field):
                self.setUp()
                self.api.run[field][key] = value
                self.refuse_and_latch()

    def test_job_duplicates_missing_partial_and_attempt_drift(self):
        for mode in ('duplicate', 'empty', 'partial', 'attempt', 'name', 'status', 'source'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'duplicate':
                    self.api.jobs['jobs'] *= 2
                    self.api.jobs['total_count'] = 2
                elif mode == 'empty':
                    self.api.jobs = {'total_count': 0, 'jobs': []}
                elif mode == 'partial':
                    self.api.jobs['total_count'] = 101
                else:
                    key, value = {'attempt': ('run_attempt', 3), 'name': ('name', 'unrelated'),
                                  'status': ('status', 'completed'), 'source': ('head_sha', 'b' * 40)}[mode]
                    self.api.jobs['jobs'][0][key] = value
                self.refuse_and_latch()

    def test_job_id_cannot_rebind_after_valid_observation(self):
        self.binding.assert_running()
        self.api.jobs['jobs'][0]['id'] = 789
        self.refuse_and_latch()

    def test_main_drift_and_deadline(self):
        self.api.main['object']['sha'] = 'b' * 40
        self.refuse_and_latch()
        self.setUp()
        self.binding.deadline = time.monotonic() - 1
        self.refuse_and_latch()

    def test_network_failure_is_sticky_and_late_cancel_is_seen(self):
        with patch.object(self.api, 'get', side_effect=OSError('unavailable')):
            self.refuse_and_latch()
        self.setUp()
        original = self.api.get
        calls = 0
        def get(path):
            nonlocal calls
            calls += 1
            if calls == 5:
                self.api.run['status'] = 'completed'
            return original(path)
        with patch.object(self.api, 'get', side_effect=get):
            self.refuse_and_latch()

    def test_boolean_ids_and_duplicate_json_keys_refused(self):
        with self.assertRaises(guard.Refused):
            guard.RunBinding(self.api.source, True, 2, self.api)
        with self.assertRaises(guard.Refused):
            json.loads('{"id":1,"id":2}', object_pairs_hook=guard.unique)
        self.api.jobs['jobs'][0]['run_attempt'] = True
        self.refuse_and_latch()


if __name__ == '__main__':
    unittest.main()
