"""OCI adapter request/response fault tests with explicit simulated SDK/client."""
import sys
import io
import json
import os
from contextlib import redirect_stdout
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from ops import native_maintenance_checkpoint as cp
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_checkpoint_probe as probe
from ops.native_maintenance_workflow_pause import Refused, encoded
import test_native_maintenance_checkpoint as protocol_tests


class ServiceError(Exception):
    def __init__(self, status):
        self.status = status


class FakeClient:
    def __init__(self):
        self.objects, self.calls, self.serial = {}, [], 0
        self.public = False
        self.links = self.replication = self.lifecycle = False
        self.extra_bytes = 0
        self.lose = None
        self.detail = dict(compartment_id=adapter.TENANCY, freeform_tags={'managed_by': adapter.TAG},
                           public_access_type='NoPublicAccess', storage_tier='Standard', versioning='Disabled',
                           auto_tiering='Disabled', is_read_only=False, kms_key_id=None)

    def get_bucket(self, *args, **kw):
        return NS(data=NS(**{**self.detail, 'public_access_type': 'ObjectRead' if self.public else 'NoPublicAccess'}))

    def list_preauthenticated_requests(self, *args, **kw):
        return NS(data=[1] if self.links else [], headers={})

    def list_replication_policies(self, *args, **kw):
        return NS(data=[1] if self.replication else [], headers={})

    def get_object_lifecycle_policy(self, *args, **kw):
        return NS(data=NS(items=[1] if self.lifecycle else []))

    def list_buckets(self, *args, **kw):
        return NS(data=[NS(name=adapter.BUCKET)], headers={})

    def list_objects(self, *args, **kw):
        rows = [NS(name=k, size=len(v[0])) for k, v in self.objects.items()]
        if self.extra_bytes:
            rows.append(NS(name='other-object', size=self.extra_bytes))
        return NS(data=NS(objects=rows, next_start_with=None))

    def get_object(self, namespace, bucket, path, **kw):
        self.calls.append(('GET', path, kw))
        if path not in self.objects:
            raise ServiceError(404)
        data, revision = self.objects[path]
        return NS(headers={'content-length': str(len(data)), 'etag': revision},
                  data=NS(raw=NS(stream=lambda *a, **k: iter([data])), close=lambda: None))

    def put_object(self, namespace, bucket, path, data, **kw):
        self.calls.append(('PUT', path, kw))
        assert (namespace, bucket) == ('ci_namespace', adapter.BUCKET)
        assert kw['content_length'] == len(data)
        current = self.objects.get(path)
        if kw.get('if_none_match') == '*':
            if current is not None:
                raise ServiceError(412)
        elif kw.get('if_match') != (current[1] if current else None):
            raise ServiceError(412)
        else:
            assert 'if_match' in kw
        self.serial += 1
        self.objects[path] = (data, str(self.serial))
        if self.lose and path.endswith(self.lose):
            raise ConnectionError('CI_LOST_PUT_REPLY')
        return NS(headers={})


class OCIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = protocol_tests.CheckpointTests('test_capture_retains_exclusive_locks_and_matches_offline')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = FakeClient()
        self.guard_calls = 0
        self.fake_sdk = NS(retry=NS(NoneRetryStrategy=type('NoRetry', (), {})), exceptions=NS(ServiceError=ServiceError))
        self.sdk_patch = patch.dict(sys.modules, {'oci': self.fake_sdk})
        self.sdk_patch.start()
        self.addCleanup(self.sdk_patch.stop)
        self.store = self.new_store()
        self.fixture.store = self.store
        self.fixture.cp = cp.JournalCheckpoint(self.store)

    def guard(self):
        self.guard_calls += 1

    def new_store(self):
        return adapter.OCIJournalStore(self.client, 'ci_namespace', self.guard)

    def test_initial_registration_and_conditional_successor_with_readback(self):
        first = self.fixture.sync()
        self.fixture.operation.append(dict(kind='SESSION_INTENT', outcome='UNKNOWN'))
        accepted = self.fixture.sync()
        self.assertNotEqual(first, accepted)
        self.assertIn('SESSION_INTENT', cp.accepted_latest(self.store, self.fixture.scope, accepted).decode())
        puts = [x for x in self.client.calls if x[0] == 'PUT']
        self.assertEqual(len(puts), 5)  # two archives, one registration, two head writes
        self.assertTrue(all(isinstance(x[2]['retry_strategy'], self.fake_sdk.retry.NoneRetryStrategy) for x in puts))
        self.assertTrue(all(x[2].get('if_none_match') == '*' for x in puts[:-1]))
        self.assertIn('if_match', puts[-1][2])
        self.assertEqual(self.guard_calls, 5)

    def test_lost_registration_reply_cannot_rebootstrap(self):
        self.client.lose = 'registered.json'
        with self.assertRaises(ConnectionError):
            self.fixture.sync()
        self.client.lose = None
        with self.assertRaisesRegex(Refused, 'REGISTRY_HEAD_INCOMPLETE'):
            self.new_store().read_head(self.fixture.scope)
        self.assertEqual(len([x for x in self.client.calls if x[0] == 'PUT']), 2)

    def test_missing_registered_head_blocks_recovery(self):
        self.fixture.sync()
        path = self.store._path(self.fixture.scope, 'head.json')
        self.client.objects.pop(path)  # Simulated external deletion; adapter has no delete method.
        with self.assertRaisesRegex(Refused, 'REGISTRY_HEAD_INCOMPLETE'):
            self.new_store().read_head(self.fixture.scope)

    def test_lost_head_reply_remains_recoverable_but_not_retried(self):
        self.client.lose = 'head.json'
        with self.assertRaises(ConnectionError):
            self.fixture.sync()
        self.client.lose = None
        with self.assertRaisesRegex(Refused, 'ALREADY_FAILED'):
            self.fixture.sync()
        recovery = self.new_store()
        head = recovery.read_head(self.fixture.scope)
        data = cp.accepted_latest(recovery, self.fixture.scope, cp.sha(head[0]))
        self.assertEqual(cp.sha(data), cp.parse_head(head[0], self.fixture.scope)['archive_digest'])

    def test_public_links_replication_lifecycle_and_budget_refuse_before_writes(self):
        for flag in ('public', 'links', 'replication', 'lifecycle', 'extra_bytes'):
            with self.subTest(flag=flag):
                setattr(self.client, flag, adapter.LIMIT if flag == 'extra_bytes' else True)
                self.fixture.cp = cp.JournalCheckpoint(self.new_store())
                with self.assertRaises(Refused):
                    self.fixture.sync()
                setattr(self.client, flag, False)
                self.assertFalse(any(x[0] == 'PUT' for x in self.client.calls))

    def test_cas_conflict_and_non_successor_leave_head_unchanged(self):
        self.fixture.sync()
        original = self.store.read_head(self.fixture.scope)
        current = cp.parse_head(original[0], self.fixture.scope)
        new = encoded({**current, 'sequence': 2, 'previous': cp.sha(original[0])})
        with self.assertRaisesRegex(Refused, 'CAS_CONFLICT'):
            self.new_store().compare_head(self.fixture.scope, 'wrong', new)
        with self.assertRaisesRegex(Refused, 'NOT_SUCCESSOR'):
            self.new_store().compare_head(self.fixture.scope, original[1], original[0])
        self.assertEqual(self.new_store().read_head(self.fixture.scope), original)

    def test_guard_loss_stops_first_put(self):
        def refuse():
            raise Refused('CI_GUARD_LOST')
        self.store.guard = refuse
        with self.assertRaisesRegex(Refused, 'GUARD_LOST'):
            self.fixture.sync()
        self.assertFalse(any(x[0] == 'PUT' for x in self.client.calls))

    def test_malformed_download_and_other_service_error_are_not_absence(self):
        with patch.object(self.client, 'get_object', side_effect=ServiceError(403)):
            with self.assertRaises(ServiceError):
                self.store.read_head(self.fixture.scope)

        response = NS(headers={'content-length': str(4097), 'etag': '1'},
                      data=NS(raw=NS(stream=lambda *a, **k: iter([b'x'])), close=lambda: None))
        with patch.object(self.client, 'get_object', return_value=response):
            with self.assertRaisesRegex(Refused, 'DOWNLOAD_SIZE'):
                self.store.read_head(self.fixture.scope)

    def test_fixed_probe_restores_and_checks_provider_cas_without_production_payload(self):
        self.fake_sdk.object_storage = NS(ObjectStorageClient=lambda *a, **k: self.client)
        self.client.get_namespace = lambda **kw: NS(data='ci_namespace')
        audit = NS(main=lambda: (None, None, {'compartments': 1, 'allocated_gb': 97}))
        env = dict(EXPECTED_MAIN='a' * 40, GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                   GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1', OCI_USER='synthetic-user',
                   OCI_TENANCY=adapter.TENANCY, OCI_FINGERPRINT='synthetic-fingerprint',
                   OCI_KEY='synthetic-key-not-a-credential', OCI_REGION='eu-frankfurt-1')
        output = io.StringIO()
        with patch.dict(os.environ, env), patch.dict(sys.modules, {'oci_storage_audit': audit}), \
                patch.object(probe, 'source_check') as check, redirect_stdout(output):
            probe.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['audit'], 'SYNTHETIC_CHECKPOINT_OCI_RESTORE_PASS')
        self.assertFalse(result['production_journal_backup'])
        self.assertFalse(result['production_mutations'])
        self.assertEqual(result['deleted_objects'], 0)
        self.assertGreaterEqual(check.call_count, 7)
        self.assertEqual(len(self.client.objects), 4)
        payloads = [v[0].decode() for k, v in self.client.objects.items() if '/archive-' in k]
        self.assertTrue(all('SYNTHETIC_CHECKPOINT_ONLY' in p for p in payloads))


if __name__ == '__main__':
    unittest.main()
