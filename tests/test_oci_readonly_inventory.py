import io
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, mock_open, patch

from ops import oci_readonly_inventory as probe


def response(data, headers=None):
    return NS(data=data, headers=headers or {})


class ApiError(Exception):
    def __init__(self, status):
        super().__init__('PRIVATE KEY RAW ERROR MUST NEVER APPEAR')
        self.status = status


class InventoryTests(unittest.TestCase):
    def clients(self):
        iam = Mock()
        storage = Mock()
        iam.get_tenancy.return_value = response(NS(id=probe.TENANCY, home_region_key='FRA'))
        storage.get_namespace.return_value = response('example_namespace')
        storage.list_buckets.return_value = response([NS(name='private-backups')])
        storage.get_bucket.return_value = response(NS(name='private-backups', compartment_id=probe.TENANCY,
            public_access_type='NoPublicAccess', storage_tier='Standard', approximate_size=8388608,
            approximate_count=1))
        return iam, storage

    def test_success_exact_metadata_and_scope(self):
        iam, storage = self.clients()
        result = probe.inventory(iam, storage)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertTrue(result['tenancy_match'])
        self.assertEqual(result['inventory_scope'], 'TENANCY_ROOT_ONLY')
        self.assertEqual(result['other_compartments'], 'NOT_CHECKED')
        self.assertEqual(result['billing_free_remaining'], 'UNKNOWN')
        self.assertEqual(result['buckets'][0]['approximate_size_bytes'], 8388608)
        iam.get_tenancy.assert_called_once_with(tenancy_id=probe.TENANCY)
        storage.get_bucket.assert_called_once_with(namespace_name='example_namespace', bucket_name='private-backups',
                                                   fields=['approximateSize', 'approximateCount'])
        self.assertEqual([c[0] for c in storage.mock_calls], ['get_namespace', 'list_buckets', 'get_bucket'])

    def test_denial_stops_at_every_stage_and_redacts(self):
        for method in ('get_tenancy', 'get_namespace', 'list_buckets', 'get_bucket'):
            for status in (401, 403):
                with self.subTest(method=method, status=status):
                    iam, storage = self.clients()
                    getattr(iam if method == 'get_tenancy' else storage, method).side_effect = ApiError(status)
                    result = probe.inventory(iam, storage)
                    self.assertEqual(result['status'], 'ACCESS_DENIED')
                    self.assertNotIn('PRIVATE', json.dumps(result))
                    names = [c[0] for c in iam.mock_calls + storage.mock_calls]
                    self.assertEqual(names[-1], method)
                    self.assertEqual(names.count(method), 1)

    def test_missing_privacy_is_unknown_not_private(self):
        iam, storage = self.clients()
        del storage.get_bucket.return_value.data.public_access_type
        del storage.get_bucket.return_value.data.approximate_size
        result = probe.inventory(iam, storage)
        self.assertEqual(result['buckets'][0]['privacy'], 'UNKNOWN')
        self.assertEqual(result['buckets'][0]['approximate_size_bytes'], 'UNKNOWN')

    def test_404_not_interpreted_as_absent_or_denial(self):
        iam, storage = self.clients()
        storage.list_buckets.side_effect = ApiError(404)
        result = probe.inventory(iam, storage)
        self.assertEqual(result['status'], 'NOT_FOUND_OR_NOT_VISIBLE')
        self.assertFalse(result['bucket_inventory_complete'])
        storage.get_bucket.assert_not_called()

    def test_page_limit_incomplete(self):
        iam, storage = self.clients()
        storage.list_buckets.side_effect = [response([], {'opc-next-page': str(i)}) for i in range(3)]
        result = probe.inventory(iam, storage)
        self.assertEqual(result['status'], 'INCOMPLETE_LIMIT')
        self.assertEqual(storage.list_buckets.call_count, 3)
        self.assertFalse(result['bucket_inventory_complete'])

    def test_bucket_limit_incomplete(self):
        iam, storage = self.clients()
        storage.list_buckets.return_value = response([NS(name='b' + str(i)) for i in range(21)])
        storage.get_bucket.side_effect = lambda **kw: response(NS(name=kw['bucket_name'], compartment_id=probe.TENANCY))
        result = probe.inventory(iam, storage)
        self.assertEqual(result['status'], 'INCOMPLETE_LIMIT')
        self.assertEqual(storage.get_bucket.call_count, 20)

    def test_repeated_pagination_token_stops(self):
        iam, storage = self.clients()
        storage.list_buckets.return_value = response([], {'opc-next-page': 'opaque'})
        result = probe.inventory(iam, storage)
        self.assertEqual(result['status'], 'INVALID_PAGINATION')
        self.assertEqual(storage.list_buckets.call_count, 2)

    def test_metadata_identity_mismatch_stops(self):
        iam, storage = self.clients()
        storage.get_bucket.return_value.data.compartment_id = 'other'
        self.assertEqual(probe.inventory(iam, storage)['status'], 'TARGET_MISMATCH')

    def test_tenancy_identity_mismatch_stops(self):
        iam, storage = self.clients()
        iam.get_tenancy.return_value.data.id = 'other'
        result = probe.inventory(iam, storage)
        self.assertEqual(result['status'], 'TARGET_MISMATCH')
        self.assertFalse(result['tenancy_match'])
        storage.get_namespace.assert_not_called()

    def test_deadline_prevents_next_call(self):
        iam, storage = self.clients()
        result = probe.inventory(iam, storage, clock=Mock(side_effect=[0, 0, 181]))
        self.assertEqual(result['status'], 'TIME_BUDGET_EXCEEDED')
        storage.get_namespace.assert_not_called()

    def test_name_control_characters_not_emitted(self):
        iam, storage = self.clients()
        storage.list_buckets.return_value.data[0].name = 'unsafe\n::warning::secret'
        storage.get_bucket.return_value.data.name = 'unsafe\n::warning::secret'
        result = probe.inventory(iam, storage)
        self.assertEqual(result['buckets'][0]['name'], 'UNKNOWN')
        self.assertNotIn('warning', json.dumps(result))

    def test_credentials_no_broad_fallback_and_scalar_injection(self):
        with self.assertRaises(probe.StopProbe):
            probe.credential_config({'OCI_CLI_TENANCY': probe.TENANCY})
        for value in ('safe\nother=secret', 'safe\rsecret', 'a=b', ''):
            with self.subTest(value=value), self.assertRaises(probe.StopProbe):
                probe.scalar(value, 'user')

    def test_fixed_region_and_tenancy(self):
        env = dict(OCI_READONLY_CLI_TENANCY=probe.TENANCY, OCI_READONLY_CLI_USER='ocid1.user.oc1..example',
                   OCI_READONLY_CLI_FINGERPRINT=':'.join(['ab'] * 16), OCI_READONLY_CLI_REGION=probe.REGION,
                   OCI_READONLY_CLI_KEY_CONTENT='-----BEGIN PRIVATE KEY-----\nexample\n-----END PRIVATE KEY-----')
        config, _ = probe.credential_config(env)
        self.assertEqual(config['tenancy'], probe.TENANCY)
        escaped = dict(env, OCI_READONLY_CLI_KEY_CONTENT=env['OCI_READONLY_CLI_KEY_CONTENT'].replace('\n', '\\r\\n'))
        self.assertEqual(probe.credential_config(escaped)[1], env['OCI_READONLY_CLI_KEY_CONTENT'])
        for key in ('OCI_READONLY_CLI_TENANCY', 'OCI_READONLY_CLI_REGION'):
            altered = dict(env, **{key: 'wrong'})
            with self.assertRaises(probe.StopProbe) as exc:
                probe.credential_config(altered)
            self.assertEqual(exc.exception.code, 'TARGET_MISMATCH')

    def test_main_safe_stdout_and_summary_no_exception_leak(self):
        stdout = io.StringIO()
        output = mock_open()
        with patch.object(probe.signal, 'SIGALRM', 14, create=True), \
             patch.object(probe.signal, 'signal'), \
             patch.object(probe.signal, 'alarm', create=True), \
             patch.object(probe, 'credential_config', side_effect=RuntimeError('PRIVATE_KEY_SECRET')), \
             patch.dict(probe.os.environ, {'GITHUB_STEP_SUMMARY': 'runner-summary'}, clear=True), \
             patch('builtins.open', output), patch('sys.stdout', stdout):
            self.assertEqual(probe.main(), 2)
        payload = stdout.getvalue().strip()
        self.assertEqual(json.loads(payload)['status'], 'CLIENT_SETUP_FAILED')
        self.assertNotIn('PRIVATE_KEY_SECRET', payload)
        output().write.assert_called_once_with('```json\n' + payload + '\n```\n')


if __name__ == '__main__':
    unittest.main()
