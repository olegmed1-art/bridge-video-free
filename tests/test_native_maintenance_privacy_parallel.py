"""Concurrency and fail-closed parity at the four-read privacy barrier."""
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from ops import native_maintenance_checkpoint_oci as adapter
from ops.native_maintenance_workflow_pause import Refused
from test_native_maintenance_checkpoint_oci import FakeClient, ServiceError, OCIAdapterTests


class ParallelPrivacyTests(OCIAdapterTests):
    # Inherited checkpoint fault tests exercise the isolated path as well.
    def new_store(self):
        readers = tuple(FakeClient() for _ in range(4))
        for client in (self.client,) + readers:
            client.base_client = NS(session=object(), signer=object())
        facade = adapter.IsolatedPrivacyClient(self.client, readers)
        # Share simulated policy, not client/session/signing state.
        for method, reader in facade.readers.items():
            setattr(reader, method, getattr(self.client, method))
        self.facade = facade
        return adapter.OCIJournalStore(facade, 'ci_namespace', self.guard)

    def test_four_calls_overlap_once_with_exact_arguments_and_no_retry(self):
        barrier = threading.Barrier(4, timeout=2)
        seen = []
        for method, reader in self.facade.readers.items():
            original = getattr(reader, method)
            def read(*args, _method=method, _original=original, **kwargs):
                seen.append((_method, args, kwargs))
                barrier.wait()
                return _original(*args, **kwargs)
            setattr(reader, method, read)
        self.store.assert_private()
        self.assertEqual({m for m, _, _ in seen}, set(self.facade.METHODS))
        self.assertEqual(len(seen), 4)
        for method, args, kwargs in seen:
            self.assertEqual(args, ('ci_namespace', adapter.BUCKET))
            retry = kwargs.pop('retry_strategy')
            self.assertIs(retry, self.store.no_retry)
            self.assertEqual(kwargs, {'fields': ['autoTiering']} if method == 'get_bucket'
                             else {} if method == 'get_object_lifecycle_policy' else {'limit': 1})
        self.assertEqual(self.guard_calls, 0)
        self.assertFalse(self.client.calls)

    def test_failure_joins_all_readers_before_return_and_prevents_put(self):
        barrier = threading.Barrier(4, timeout=2)
        completed = set()
        failure = RuntimeError('simulated-read-failure')
        for method, reader in self.facade.readers.items():
            original = getattr(reader, method)
            def read(*args, _method=method, _original=original, **kwargs):
                barrier.wait()
                try:
                    if _method == 'get_bucket':
                        raise failure
                    return _original(*args, **kwargs)
                finally:
                    completed.add(_method)
            setattr(reader, method, read)
        with self.assertRaises(RuntimeError) as caught:
            self.store._put('never-written', b'no')
        self.assertIs(caught.exception, failure)
        self.assertEqual(completed, set(self.facade.METHODS))
        self.assertFalse(self.client.calls)
        self.assertEqual(self.guard_calls, 0)

    def test_lifecycle_404_only_and_malformed_none_refuses(self):
        for method in self.facade.METHODS:
            with self.subTest(method=method):
                reader = self.facade.readers[method]
                with patch.object(reader, method, side_effect=ServiceError(404)):
                    if method == 'get_object_lifecycle_policy':
                        self.store.assert_private()
                    else:
                        with self.assertRaises(ServiceError):
                            self.store.assert_private()
        reader = self.facade.readers['get_object_lifecycle_policy']
        with patch.object(reader, 'get_object_lifecycle_policy', return_value=NS(data=None)):
            with self.assertRaises(AttributeError):
                self.store.assert_private()

    def test_all_privacy_predicates_and_incomplete_lists_refuse(self):
        for method in ('list_preauthenticated_requests', 'list_replication_policies'):
            with patch.object(self.facade.readers[method], method,
                              return_value=NS(data=[], headers={'opc-next-page': 'more'})):
                with self.assertRaises(Refused):
                    self.store._put('never-written', b'no')
        self.assertFalse(self.client.calls)
        self.assertEqual(self.guard_calls, 0)

    def test_rejects_reused_clients_sessions_or_signers(self):
        for field in ('client', 'session', 'signer'):
            clients = tuple(NS(base_client=NS(session=object(), signer=object())) for _ in range(5))
            if field == 'client':
                clients = (clients[0], clients[0], *clients[2:])
            else:
                setattr(clients[1].base_client, field, getattr(clients[0].base_client, field))
            with self.assertRaises(Refused):
                adapter.IsolatedPrivacyClient(clients[0], clients[1:])

    def test_serial_consumer_retains_early_refusal(self):
        self.client.public = True
        store = adapter.OCIJournalStore(self.client, 'ci_namespace', self.guard)
        with patch.object(self.client, 'list_preauthenticated_requests') as later:
            with self.assertRaises(Refused):
                store.assert_private()
            later.assert_not_called()

    def test_submit_failure_joins_already_started_worker(self):
        released = threading.Event()
        finished = threading.Event()
        failure = RuntimeError('simulated-submit-failure')
        original = self.facade.readers['get_bucket'].get_bucket
        def read(*args, **kwargs):
            self.assertTrue(released.wait(2))
            try:
                return original(*args, **kwargs)
            finally:
                finished.set()
        class FailingPool(ThreadPoolExecutor):
            submitted = 0
            def submit(self, *args, **kwargs):
                self.submitted += 1
                if self.submitted == 2:
                    released.set()
                    raise failure
                return super().submit(*args, **kwargs)
        with patch.object(self.facade.readers['get_bucket'], 'get_bucket', side_effect=read), \
                patch.object(adapter, 'ThreadPoolExecutor', FailingPool):
            with self.assertRaises(RuntimeError) as caught:
                self.store._put('never-written', b'no')
        self.assertIs(caught.exception, failure)
        self.assertTrue(finished.is_set())
        self.assertFalse(self.client.calls)
        self.assertEqual(self.guard_calls, 0)

    def test_budget_reads_keep_general_client_and_privacy_uses_only_readers(self):
        with patch.object(self.client, 'get_bucket', wraps=self.client.get_bucket) as general:
            self.store.assert_private()
            general.assert_not_called()
            self.store._budget(1)
            general.assert_called_once()
        with self.assertRaises(Refused):
            self.facade.privacy_read('put_object', 'never-written', b'no')
        self.assertFalse(self.client.calls)
