"""Real local duplex pipes with simulated remote store; no live OCI claim."""
import os
import threading
import unittest
from unittest.mock import patch

from ops import native_maintenance_checkpoint as cp
from ops import native_maintenance_checkpoint_transport as rpc
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import encoded
import test_native_maintenance_checkpoint as fixtures


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CheckpointTests('test_capture_retains_exclusive_locks_and_matches_offline')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        first_read, first_write = os.pipe()
        second_read, second_write = os.pipe()
        self.fds = [first_read, first_write, second_read, second_write]
        self.client = rpc.Channel(second_read, first_write, 'a' * 64, seconds=5)
        self.channel = rpc.Channel(first_read, second_write, 'a' * 64, seconds=5)
        self.store = fixtures.MemoryStore()
        self.calls, self.errors = [], []
        self.server = rpc.StoreServer(self.channel, self.fixture.scope, self.store,
                                      lambda: self.calls.append(True))
        self.proxy = rpc.ProxyStore(self.client, self.fixture.scope)
        self.thread = None
        self.addCleanup(self.close)

    def close(self):
        # Closing the client writer causes the blocked server read to see EOF.
        os.close(self.client.writer)
        self.fds.remove(self.client.writer)
        if self.thread:
            self.thread.join(timeout=2)
            self.assertFalse(self.thread.is_alive())
        for fd in self.fds:
            os.close(fd)
        self.fds = []

    def start(self):
        def serve():
            try:
                while True:
                    self.server.once()
            except BaseException as exc:
                self.errors.append(type(exc).__name__)
                # A real runner closes its SSH input on refusal/exit.
                # This local test closes just the response writer to deliver EOF.
                os.close(self.channel.writer)
                self.fds.remove(self.channel.writer)
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def sync(self):
        return self.checkpoint.sync(self.fixture.scope, self.fixture.operation, self.fixture.pause)

    def test_real_pipe_checkpoint_order_and_private_restore(self):
        self.start()
        self.checkpoint = cp.JournalCheckpoint(self.proxy)
        first = self.sync()
        self.assertEqual(self.sync(), first)
        self.fixture.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        second = self.sync()
        self.assertNotEqual(first, second)
        data = cp.accepted_latest(self.proxy, self.fixture.scope, second)
        self.assertEqual(data, snapshot.capture_locked(self.fixture.operation, self.fixture.pause))
        self.assertGreater(self.server.sequence, 10)
        self.assertEqual(self.calls, [True] * (self.server.sequence * 2))

    def test_lost_head_write_response_keeps_store_head_and_poisons_both(self):
        self.start()
        self.checkpoint = cp.JournalCheckpoint(self.proxy)
        self.sync()
        self.fixture.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        self.store.fail = 'after_head'
        with self.assertRaises(Exception):
            self.sync()
        self.assertTrue(self.checkpoint.failed and self.proxy.failed and self.server.failed)
        self.assertEqual(self.store.revision, 2)
        self.store.fail = None
        head = self.store.heads[self.fixture.scope][0]
        archived = cp.accepted_latest(self.store, self.fixture.scope, cp.sha(head))
        self.assertIn('SESSION_INTENT', snapshot._parse(archived)['journals']['operation'][-1])
        with self.assertRaises(Exception):
            self.sync()
        self.assertEqual(self.store.revision, 2)

    def test_wrong_source_binding_scope_sequence_method_and_extra_refused_before_store(self):
        original = dict(version=1, binding='a' * 64, scope=self.fixture.scope,
                        sequence=1, method='assert_private', args=[])
        for key, value in [('binding', 'b' * 64), ('scope', 'b' * 64), ('sequence', 0),
                           ('sequence', True), ('method', 'delete_object'), ('extra', 1)]:
            self.server.failed = self.channel.failed = False
            request = {**original, key: value}
            self.client.send(request)
            with self.assertRaises(Exception):
                self.server.once()
            self.assertTrue(self.server.failed and self.channel.failed)
            self.assertFalse(self.calls)
            self.assertFalse(self.store.objects)

    def test_duplicate_or_noncanonical_or_oversize_frame(self):
        for raw in (b'{"a":1,"a":1}', b'{"a": 1}'):
            self.channel.failed = False
            os.write(self.client.writer, len(raw).to_bytes(4, 'big') + raw)
            with self.assertRaises(Exception):
                self.channel.receive()
        self.channel.failed = False
        os.write(self.client.writer, (rpc.MAX_FRAME + 1).to_bytes(4, 'big'))
        with self.assertRaises(Exception):
            self.channel.receive()

    def test_expired_pipe_and_malformed_reply_latch(self):
        self.client.deadline = 0
        with self.assertRaises(Exception):
            self.proxy.assert_private()
        self.assertTrue(self.proxy.failed and self.client.failed)

    def test_malformed_result_latches_outside_rpc_call(self):
        with patch.object(self.proxy, '_call', return_value=['bad base64', 'etag']):
            with self.assertRaises(Exception):
                self.proxy.read_head(self.fixture.scope)
        self.assertTrue(self.proxy.failed and self.client.failed)

    def test_wrong_expected_cas_is_preserved_and_never_retried(self):
        self.start()
        self.checkpoint = cp.JournalCheckpoint(self.proxy)
        self.sync()
        original = self.store.heads[self.fixture.scope]
        with self.assertRaises(Exception):
            self.proxy.compare_head(self.fixture.scope, 'wrong-etag', original[0])
        self.assertEqual(self.store.heads[self.fixture.scope], original)
        self.assertTrue(self.proxy.failed and self.server.failed)

    def test_postoperation_guard_failure_suppresses_ack(self):
        request = dict(version=1, binding='a' * 64, scope=self.fixture.scope,
                       sequence=1, method='assert_private', args=[])
        self.client.send(request)
        with patch.object(self.server, 'guard', side_effect=[None, RuntimeError('private')]), \
                patch.object(self.channel, 'send') as send, self.assertRaises(RuntimeError):
            self.server.once()
        send.assert_not_called()
        self.assertTrue(self.server.failed and self.channel.failed)


if __name__ == '__main__':
    unittest.main()
