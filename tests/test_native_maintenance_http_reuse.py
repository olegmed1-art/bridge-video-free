"""Real stdlib HTTP parser over controlled sockets; no network or credentials."""
import io
import json
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from ops import native_maintenance_run_guard as guard


def response(body=b'{"ok":true}', status=200, extra=b'', length=None):
    return (b'HTTP/1.1 ' + str(status).encode() + b' Test\r\nContent-Length: '
            + str(len(body) if length is None else length).encode() + b'\r\n' + extra + b'\r\n' + body)


class HTTPReuseTests(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        self.wire = []
        self.next_response = lambda: response()
        self.barrier = None
        self.response_for_socket = None
        test = self
        class Socket:
            def __init__(self, connection):
                self.connection = connection
                self.closed = False
            def sendall(self, data):
                self.last_request = bytes(data)
                test.wire.append(bytes(data))
            def makefile(self, *args, **kwargs):
                if test.barrier is not None:
                    test.barrier.wait(timeout=2)
                value = test.response_for_socket(self) if test.response_for_socket else test.next_response()
                if isinstance(value, Exception):
                    raise value
                return io.BytesIO(value)
            def close(self):
                self.closed = True
        def connect(connection):
            connection.sock = Socket(connection)
            test.sockets.append(connection.sock)
        self.patch = patch.object(guard.http.client.HTTPSConnection, 'connect', new=connect)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.api = guard.PersistentAPI('synthetic-token')
        self.addCleanup(self.api.close)

    def test_live_connection_reused_but_gets_and_headers_are_always_fresh(self):
        counter = iter((response(b'{"value":1}'), response(b'{"value":2}')))
        self.next_response = lambda: next(counter)
        self.assertEqual(self.api.get('/actions/runs/123')['value'], 1)
        self.assertEqual(self.api.get('/actions/runs/123')['value'], 2)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(len(self.wire), 2)
        connection = self.sockets[0].connection
        self.assertEqual((connection.host, connection.port, connection.timeout), ('api.github.com', 443, 4))
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        for raw in self.wire:
            self.assertIn(b'GET /repos/olegmed1-art/bridge-video-free/actions/runs/123 HTTP/1.1', raw)
            self.assertIn(b'Host: api.github.com', raw)
            self.assertIn(b'Cache-Control: no-cache', raw)
            self.assertIn(b'Authorization: Bearer synthetic-token', raw)

    def test_three_middle_lanes_overlap_and_persist_across_new_worker_pools(self):
        paths = ('/contents/.github/workflows/test.yml?ref='+'a'*40,
                 '/git/ref/heads/main', '/actions/runs/123/attempts/1/jobs?per_page=100')
        self.api.get('/actions/runs/123')
        self.barrier = threading.Barrier(3)
        for _ in range(2):
            with ThreadPoolExecutor(max_workers=3) as pool:
                self.assertEqual(list(pool.map(self.api.get, paths)), [{'ok': True}]*3)
        self.barrier = None
        self.api.get('/actions/workflows/456/runs?status=queued&per_page=100')
        self.assertEqual(len(self.sockets), 4)
        self.assertEqual(len(self.wire), 8)
        self.assertEqual(len({id(c) for c in self.api._connections}), 4)

    def test_clean_server_close_reconnects_only_for_next_distinct_get(self):
        self.next_response = lambda: response(extra=b'Connection: close\r\n')
        self.api.get('/actions/runs/123')
        self.assertEqual(len(self.wire), 1)
        self.assertTrue(self.sockets[0].closed)
        self.api.get('/actions/runs/123')
        self.assertEqual(len(self.wire), 2)
        self.assertEqual(len(self.sockets), 2)

    def test_stale_socket_eof_is_never_retried(self):
        self.api.get('/actions/runs/123')
        self.next_response = lambda: b''
        with self.assertRaises(guard.http.client.RemoteDisconnected):
            self.api.get('/actions/runs/123')
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(len(self.wire), 2)
        with self.assertRaisesRegex(guard.Refused, 'TRANSPORT_FAILED'):
            self.api.get('/git/ref/heads/main')
        self.assertEqual(len(self.wire), 2)
        self.assertTrue(self.sockets[0].closed)

    def test_bad_responses_latch_without_redirect_retry_or_reuse(self):
        cases = [response(status=302, extra=b'Location: https://example.com/\r\n'),
                 response(status=500), response(extra=b'Link: next-page\r\n'),
                 response(b'{"ok":1,"ok":2}'), response(b'{}', length=100),
                 response(b'{}', extra=b'Transfer-Encoding: chunked\r\n'),
                 response(b'x'*(guard.MAX_RESPONSE+1)), TimeoutError('simulated-timeout')]
        for raw in cases:
            with self.subTest(kind=type(raw).__name__, size=len(raw) if isinstance(raw, bytes) else 0):
                api = guard.PersistentAPI('synthetic-token')
                self.next_response = lambda: raw
                before = len(self.wire)
                with self.assertRaises(Exception):
                    api.get('/actions/runs/123')
                self.assertTrue(api.failed)
                with self.assertRaises(guard.Refused):
                    api.get('/actions/runs/123')
                self.assertEqual(len(self.wire), before+1)
                self.assertTrue(self.sockets[-1].closed)
                api.close()

    def test_no_environment_proxy_and_invalid_path_never_sends(self):
        with patch.dict(guard.os.environ, {'HTTPS_PROXY': 'http://invalid.test:9999'}):
            self.api.get('/actions/runs/123')
        self.assertEqual(self.sockets[0].connection.host, 'api.github.com')
        with self.assertRaises(guard.Refused):
            self.api.get('/actions/runs/123\r\nInjected: yes')
        self.assertEqual(len(self.wire), 1)
        self.assertTrue(self.api.failed)

    def test_close_is_idempotent_and_prevents_new_requests(self):
        self.api.get('/actions/runs/123')
        self.api.close()
        self.api.close()
        self.assertTrue(all(s.closed for s in self.sockets))
        with self.assertRaises(guard.Refused):
            self.api.get('/actions/runs/123')
        self.assertEqual(len(self.wire), 1)

    def test_joined_read_failure_never_retries_or_allows_later_observations(self):
        paths = ('/contents/.github/workflows/test.yml?ref='+'a'*40,
                 '/git/ref/heads/main', '/actions/runs/123/attempts/1/jobs?per_page=100')
        self.barrier = threading.Barrier(3)
        self.response_for_socket = lambda sock: b'' if b'/contents/' in sock.last_request else response()
        with self.assertRaises(guard.http.client.RemoteDisconnected):
            guard.read_observations(self.api, paths)
        self.assertTrue(self.api.failed)
        self.assertEqual(len(self.wire), 3)
        self.barrier = None
        with self.assertRaises(guard.Refused):
            self.api.get('/actions/runs/123')
        self.api.close()
        self.assertTrue(all(sock.closed for sock in self.sockets))
        self.assertEqual(len(self.wire), 3)

    def test_cleanup_error_does_not_mask_original_failure(self):
        self.api.get('/actions/runs/123')
        self.next_response = lambda: response(status=500)
        with patch.object(self.sockets[0], 'close', side_effect=RuntimeError('cleanup')):
            with self.assertRaisesRegex(guard.Refused, 'API_RESPONSE_INCOMPLETE'):
                self.api.get('/actions/runs/123')
        self.assertTrue(self.api.failed)
        self.assertEqual(len(self.wire), 2)
