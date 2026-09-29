import base64
import copy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
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
                    'status': 'in_progress', 'conclusion': None,
                    'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}]}

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


class CheckpointAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        profile = guard.CheckpointRunBinding
        self.run.update(path=profile.workflow, event='workflow_dispatch')
        raw = (Path(__file__).resolve().parents[1] / profile.workflow).read_bytes()
        self.file.update(path=profile.workflow, size=len(raw), content=base64.b64encode(raw).decode())
        self.jobs['jobs'][0]['name'] = 'probe'
        self.jobs['jobs'].append({**self.jobs['jobs'][0], 'id': 455, 'name': 'contract',
                                  'status': 'completed', 'conclusion': 'success'})
        self.jobs['total_count'] = 2

    def get(self, path):
        if path == '/contents/' + guard.CheckpointRunBinding.workflow + '?ref=' + self.source:
            self.calls.append(path)
            return copy.deepcopy(self.file)
        return super().get(path)


class CheckpointBindingTests(unittest.TestCase):
    def test_fixed_two_job_contract_and_exact_blob(self):
        api = CheckpointAPI()
        binding = guard.CheckpointRunBinding(api.source, 123, 2, api)
        binding.assert_running()
        self.assertEqual(binding.job_id, 456)
        self.assertEqual(hashlib.sha256(base64.b64decode(api.file['content'])).hexdigest(),
                         binding.workflow_sha256)
        # A valid relay profile cannot satisfy the production/window profile.
        with self.assertRaises(Exception):
            guard.RunBinding(api.source, 123, 2, api).assert_running()

    def test_prerequisite_attempt_source_status_duplicate_and_event_drift(self):
        for fault in ('failed_contract', 'missing_contract', 'extra_job', 'same_id',
                      'old_contract_attempt', 'old_contract_source', 'cancelled', 'push'):
            with self.subTest(fault=fault):
                api = CheckpointAPI()
                if fault == 'failed_contract':
                    api.jobs['jobs'][1]['conclusion'] = 'failure'
                elif fault == 'missing_contract':
                    api.jobs['jobs'].pop()
                elif fault == 'extra_job':
                    api.jobs['jobs'].append(dict(api.jobs['jobs'][1]))
                elif fault == 'same_id':
                    api.jobs['jobs'][1]['id'] = 456
                elif fault == 'old_contract_attempt':
                    api.jobs['jobs'][1]['run_attempt'] = 1
                elif fault == 'old_contract_source':
                    api.jobs['jobs'][1]['head_sha'] = 'b' * 40
                elif fault == 'cancelled':
                    api.jobs['jobs'][0].update(status='completed', conclusion='cancelled')
                else:
                    api.run['event'] = 'push'
                binding = guard.CheckpointRunBinding(api.source, 123, 2, api)
                with self.assertRaises(Exception):
                    binding.assert_running()
                self.assertTrue(binding.failed)


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

    def test_middle_reads_overlap_but_first_and_final_run_stay_ordered(self):
        original = self.api.get
        barrier = threading.Barrier(3)
        jobs_done, main_done = threading.Event(), threading.Event()
        lock = threading.Lock()
        events = []
        def get(path):
            kind = ('run' if path == '/actions/runs/123' else
                    'file' if path.startswith('/contents/') else
                    'main' if path == '/git/ref/heads/main' else 'jobs')
            with lock: events.append(('start', kind))
            if kind != 'run':
                barrier.wait(timeout=2)
                if kind == 'main':
                    self.assertTrue(jobs_done.wait(2))
                if kind == 'file':
                    self.assertTrue(main_done.wait(2))
            value = original(path)
            with lock: events.append(('end', kind))
            if kind == 'jobs': jobs_done.set()
            if kind == 'main': main_done.set()
            return value
        with patch.object(self.api, 'get', side_effect=get):
            self.binding.assert_running()
        self.assertEqual(self.binding.job_id, 456)
        self.assertEqual([e for e in events if e == ('start', 'run')], [('start', 'run')]*2)
        first_run_end = events.index(('end', 'run'))
        last_run_start = len(events) - 1 - events[::-1].index(('start', 'run'))
        self.assertLess(first_run_end, min(events.index(('start', k)) for k in ('file','main','jobs')))
        self.assertGreater(last_run_start, max(events.index(('end', k)) for k in ('file','main','jobs')))
        self.assertLess(events.index(('end','jobs')), events.index(('end','main')))
        self.assertLess(events.index(('end','main')), events.index(('end','file')))

    def test_failed_middle_read_joins_other_workers_and_latches(self):
        original = self.api.get
        blocked, invalid, release = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def get(path):
            if path.startswith('/contents/'):
                blocked.set()
                if not release.wait(2): raise AssertionError('CI_WORKER_NOT_RELEASED')
            if path == '/git/ref/heads/main':
                invalid.set()
                return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'b'*40}}
            return original(path)
        def check():
            try: self.binding.assert_running()
            except BaseException as exc: errors.append(exc)
        with patch.object(self.api, 'get', side_effect=get):
            worker = threading.Thread(target=check)
            worker.start()
            self.assertTrue(blocked.wait(1))
            self.assertTrue(invalid.wait(1))
            self.assertTrue(worker.is_alive())
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertTrue(self.binding.failed)
        self.assertIsNone(self.binding.job_id)
        self.assertEqual(self.api.calls.count('/actions/runs/123'), 1)
        with self.assertRaisesRegex(guard.Refused, 'ALREADY_FAILED'):
            self.binding.assert_running()

    def test_worker_exception_joins_started_jobs_without_retry_or_final_run(self):
        original = self.api.get
        file_failed, jobs_blocked, release = threading.Event(), threading.Event(), threading.Event()
        errors, file_calls = [], []
        def get(path):
            if path.startswith('/contents/'):
                file_calls.append(path)
                file_failed.set()
                raise OSError('CI_READ_FAILED')
            if path.endswith('/jobs?per_page=100'):
                jobs_blocked.set()
                if not release.wait(2): raise AssertionError('CI_WORKER_NOT_RELEASED')
            return original(path)
        def check():
            try: self.binding.assert_running()
            except BaseException as exc: errors.append(exc)
        with patch.object(self.api, 'get', side_effect=get):
            worker = threading.Thread(target=check)
            worker.start()
            self.assertTrue(file_failed.wait(1))
            self.assertTrue(jobs_blocked.wait(1))
            self.assertTrue(worker.is_alive())
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OSError)
        self.assertEqual(len(file_calls), 1)
        self.assertEqual(self.api.calls.count('/actions/runs/123'), 1)
        self.assertIsNone(self.binding.job_id)
        self.assertTrue(self.binding.failed)

    def test_deadline_expiry_during_middle_reads_never_promotes_job(self):
        original = self.api.get
        blocked, release = threading.Event(), threading.Event()
        errors = []
        def get(path):
            if path.endswith('/jobs?per_page=100'):
                blocked.set()
                if not release.wait(2): raise AssertionError('CI_WORKER_NOT_RELEASED')
            return original(path)
        def check():
            try: self.binding.assert_running()
            except BaseException as exc: errors.append(exc)
        with patch.object(self.api, 'get', side_effect=get):
            worker = threading.Thread(target=check)
            worker.start()
            self.assertTrue(blocked.wait(1))
            self.binding.deadline = time.monotonic() - 1
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertTrue(self.binding.failed)
        self.assertIsNone(self.binding.job_id)

    def test_http_gets_use_separate_no_redirect_openers(self):
        class Response:
            status = 200
            headers = {}
            def __init__(self, request): self.url = request.full_url
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, size): return b'{}'
        class Opener:
            def open(self, request, timeout):
                self.requests.append((request.full_url, timeout))
                return Response(request)
            def __init__(self): self.requests = []
        openers = []
        def build(*handlers):
            self.assertIsInstance(handlers[0], guard.NoRedirect)
            self.assertIsInstance(handlers[1], guard.urllib.request.ProxyHandler)
            self.assertEqual(handlers[1].proxies, {})
            opener = Opener()
            openers.append(opener)
            return opener
        with patch.object(guard.urllib.request, 'build_opener', side_effect=build):
            adapter = guard.API('CI-token')
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(adapter.get, path) for path in
                           ('/git/ref/heads/main', '/actions/runs/123')]
                self.assertEqual([r.result() for r in results], [{}, {}])
        self.assertEqual(len(openers), 2)
        self.assertEqual([len(o.requests) for o in openers], [1,1])


if __name__ == '__main__':
    unittest.main()
