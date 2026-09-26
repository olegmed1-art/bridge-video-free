import copy
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from ops import native_maintenance_workflow_api as api
from ops.native_maintenance_workflow_pause import Refused, digest


SOURCE = 'a' * 40
ROW = dict(id=7, path='.github/workflows/database-production.yml', state='active',
           updated_at='2026-09-26T00:00:00Z')
PLAN = dict(version=1, repository=api.REPOSITORY, source=SOURCE, workflows=[ROW])


class Response(io.BytesIO):
    def __init__(self, data, url, status=200, headers=None):
        super().__init__(data)
        self.url, self.status, self.headers = url, status, headers or {}


class Guard:
    def __init__(self):
        self.calls = []
        self.reject = False

    def assert_dispatch(self, *scope):
        self.calls.append(scope)
        if self.reject:
            raise Refused('DISPATCH_REFUSED')


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = api.Transport('test-token-never-output')

    def test_only_exact_repo_paths_and_methods_are_available(self):
        with patch.object(self.transport._opener, 'open') as opened:
            for method, path in [('POST', '/actions/runs'), ('GET', '//evil.invalid'),
                                 ('GET', '/actions/workflows/7?token=x'),
                                 ('PUT', '/actions/workflows/7/cancel'),
                                 ('PUT', '/git/ref/heads/main'),
                                 ('GET', '/actions/workflows/7\n')]:
                with self.assertRaises(Refused):
                    self.transport.request(method, path)
            opened.assert_not_called()

    def test_headers_success_and_no_implicit_retry(self):
        url = api.BASE + '/actions/workflows/7'
        with patch.object(self.transport._opener, 'open', return_value=Response(b'{"id":7}', url)) as opened:
            self.assertEqual(self.transport.request('GET', '/actions/workflows/7'), {'id': 7})
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, url)
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-token-never-output')
            self.assertEqual(opened.call_args.kwargs['timeout'], 4)
        with patch.object(self.transport._opener, 'open', side_effect=OSError('private-test-token-never-output')) as opened:
            with self.assertRaises(Refused) as error:
                self.transport.request('PUT', '/actions/workflows/7/disable')
            self.assertEqual(opened.call_count, 1)
            self.assertNotIn('private', str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)

    def test_redirect_duplicate_oversize_unexpected_status_refused(self):
        path = '/actions/workflows/7'
        url = api.BASE + path
        for response in [Response(b'{}', 'https://evil.invalid'), Response(b'{}', url, 202),
                         Response(b'{"id":7,"id":8}', url),
                         Response(b'x' * (api.MAX_RESPONSE + 1), url),
                         Response(b'{}', url, headers={'Link': '<https://evil.invalid>; rel="next"'})]:
            with self.subTest(response=response):
                with patch.object(self.transport._opener, 'open', return_value=response):
                    with self.assertRaises(Refused):
                        self.transport.request('GET', path)
        with self.assertRaises(Refused):
            api.NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://evil.invalid')

    def test_put_requires_empty_204(self):
        path = '/actions/workflows/7/disable'
        for status, data in [(200, b''), (204, b'bad')]:
            with patch.object(self.transport._opener, 'open', return_value=Response(data, api.BASE + path, status)):
                with self.assertRaises(Refused):
                    self.transport.request('PUT', path)
        with patch.object(self.transport._opener, 'open', return_value=Response(b'', api.BASE + path, 204)):
            self.assertIsNone(self.transport.request('PUT', path))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.guard = Guard()
        self.client = api.WorkflowAPI('token', PLAN, digest(PLAN), mutation_guard=self.guard)
        self.calls = []
        self.remote = {**ROW, 'url': api.BASE + '/actions/workflows/7'}
        self.source = SOURCE
        self.failure = False
        self.client.transport.request = self.request

    def request(self, method, path):
        self.calls.append((method, path))
        if path == '/git/ref/heads/main':
            return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': self.source}}
        if method == 'GET':
            return copy.deepcopy(self.remote)
        if self.failure:
            raise Refused('WRITE_REPLY_LOST')

    def test_read_and_guarded_write_exact_scope(self):
        self.assertEqual(self.client.get_workflow(7), ROW)
        self.client.disable_workflow(7)
        self.client.enable_workflow(7)
        self.assertEqual(self.guard.calls, [(digest(PLAN), 'disable', 7)] * 2 + [(digest(PLAN), 'enable', 7)] * 2)
        self.assertEqual([r for r in self.calls if r[0] == 'PUT'],
                         [('PUT', '/actions/workflows/7/disable'), ('PUT', '/actions/workflows/7/enable')])

    def test_no_mutation_guard_means_read_only_even_with_valid_token(self):
        self.client.mutation_guard = None
        with self.assertRaisesRegex(Refused, 'READ_ONLY'):
            self.client.disable_workflow(7)
        self.assertEqual(self.calls, [])

    def test_scope_and_source_rejection_precede_write(self):
        self.source = 'b' * 40
        with self.assertRaisesRegex(Refused, 'SOURCE_CHANGED'):
            self.client.disable_workflow(7)
        self.assertFalse(any(m == 'PUT' for m, _ in self.calls))
        self.setUp()
        self.guard.reject = True
        with self.assertRaisesRegex(Refused, 'DISPATCH_REFUSED'):
            self.client.disable_workflow(7)
        self.assertEqual(self.calls, [])

    def test_wrong_repository_path_and_outside_plan_rejected(self):
        for key, value in [('url', 'https://api.github.com/repos/other/repo/actions/workflows/7'),
                           ('id', 8), ('path', '.github/workflows/unrelated.yml'), ('state', 'deleted')]:
            self.setUp()
            self.remote[key] = value
            with self.assertRaises(Refused):
                self.client.get_workflow(7)
            self.assertFalse(any(m == 'PUT' for m, _ in self.calls))
        self.setUp()
        with self.assertRaisesRegex(Refused, 'OUTSIDE_PLAN'):
            self.client.disable_workflow(True)
        self.assertEqual(self.calls, [])

    def test_lost_write_response_latches_and_never_retries(self):
        self.failure = True
        with self.assertRaisesRegex(Refused, 'REPLY_LOST'):
            self.client.disable_workflow(7)
        self.failure = False
        with self.assertRaisesRegex(Refused, 'ALREADY_FAILED'):
            self.client.disable_workflow(7)
        self.assertEqual(sum(m == 'PUT' for m, _ in self.calls), 1)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{**ROW, 'id': n, 'url': api.BASE + '/actions/workflows/' + str(n),
                      'path': f'.github/workflows/writer-{n}.yml'} for n in range(1, 102)]
        self.calls = []
        self.mode = None

    def request(self, method, path):
        self.assertEqual(method, 'GET')
        self.calls.append(path)
        if path == '/git/ref/heads/main':
            sha = 'b' * 40 if self.mode == 'main-change' and len(self.calls) > 1 else SOURCE
            return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': sha}}
        page = int(path.split('page=')[-1])
        rows = copy.deepcopy(self.rows[(page-1)*100:page*100])
        total = len(self.rows)
        if self.mode == 'partial':
            rows = rows[:-1]
        if self.mode == 'duplicate' and page == 2:
            rows = [self.rows[0]]
        if self.mode == 'count-drift' and page == 2:
            total += 1
        return dict(total_count=total, workflows=rows)

    def test_complete_fixed_origin_pagination_is_read_only(self):
        result = api.inventory(self, SOURCE)
        self.assertEqual(result['workflow_count'], 101)
        self.assertFalse(result['production_mutations'])
        self.assertEqual(result['writer_exclusion'], 'NOT_ESTABLISHED')
        self.assertEqual(len(self.calls), 4)

    def test_partial_duplicate_changed_count_or_source_refused(self):
        for mode in ('partial', 'duplicate', 'count-drift', 'main-change'):
            self.setUp()
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(Refused):
                api.inventory(self, SOURCE)


if __name__ == '__main__':
    unittest.main()
