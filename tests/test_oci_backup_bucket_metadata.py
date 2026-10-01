"""Offline boundary and disclosure checks for the exact-bucket GET probe."""
import ast
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, mock_open, patch

from ops import oci_backup_bucket_metadata as probe


class ApiError(Exception):
    def __init__(self, status):
        super().__init__('SECRET_RAW_EXCEPTION')
        self.status = status


class BucketMetadataTests(unittest.TestCase):
    def env(self):
        return dict(OCI_CLI_TENANCY=probe.TENANCY, OCI_CLI_REGION=probe.REGION,
                    OCI_CLI_USER='ocid1.user.oc1..example', OCI_CLI_FINGERPRINT=':'.join(['ab'] * 16),
                    OCI_CLI_KEY_CONTENT='-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----')

    def client(self):
        client = Mock()
        client.get_namespace.return_value = NS(data='namespace_example')
        client.get_bucket.return_value = NS(data=NS(
            name=probe.BUCKET, namespace='namespace_example', compartment_id=probe.TENANCY,
            public_access_type='NoPublicAccess', storage_tier='Standard', kms_key_id=None,
            versioning='Enabled', auto_tiering='Disabled', object_events_enabled=False,
            approximate_size=8388608, approximate_count=2))
        client.get_object_lifecycle_policy.return_value = NS(data=NS(items=[{'private_rule': 'SECRET'}]))
        return client

    def test_exact_three_gets_and_safe_metadata(self):
        client = self.client()
        result = probe.read_metadata(client)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual([call[0] for call in client.mock_calls],
                         ['get_namespace', 'get_bucket', 'get_object_lifecycle_policy'])
        client.get_namespace.assert_called_once_with(compartment_id=probe.TENANCY)
        client.get_bucket.assert_called_once_with(namespace_name='namespace_example', bucket_name=probe.BUCKET,
            fields=['approximateSize', 'approximateCount', 'autoTiering'])
        client.get_object_lifecycle_policy.assert_called_once_with(namespace_name='namespace_example', bucket_name=probe.BUCKET)
        self.assertEqual(result['approximate_size_bytes'], 8388608)
        self.assertEqual(result['approximate_object_count'], 2)
        self.assertEqual(result['encryption'], 'ORACLE_MANAGED')
        self.assertEqual(result['lifecycle_rule_count'], 1)
        self.assertEqual(result['billing_free_remaining'], 'UNKNOWN')
        self.assertFalse(result['object_contents_read'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_response_identity_mismatch_stops_before_lifecycle(self):
        for attr in ('name', 'namespace', 'compartment_id'):
            with self.subTest(attr=attr):
                client = self.client()
                setattr(client.get_bucket.return_value.data, attr, 'wrong')
                result = probe.read_metadata(client)
                self.assertEqual(result['status'], 'TARGET_MISMATCH')
                self.assertFalse(result['bucket_identity_match'])
                client.get_object_lifecycle_policy.assert_not_called()

    def test_invalid_namespace_stops_before_bucket(self):
        client = self.client()
        client.get_namespace.return_value.data = 'unsafe\n::warning::SECRET'
        result = probe.read_metadata(client)
        self.assertEqual(result['status'], 'INVALID_METADATA')
        client.get_bucket.assert_not_called()
        self.assertNotIn('SECRET', json.dumps(result))

    def test_auth_denial_stops_at_every_stage_without_retry(self):
        for method in ('get_namespace', 'get_bucket', 'get_object_lifecycle_policy'):
            for code in (401, 403):
                with self.subTest(method=method, code=code):
                    client = self.client()
                    getattr(client, method).side_effect = ApiError(code)
                    result = probe.read_metadata(client)
                    self.assertEqual(result['status'], 'ACCESS_DENIED')
                    self.assertEqual(client.mock_calls[-1][0], method)
                    self.assertEqual(getattr(client, method).call_count, 1)
                    self.assertNotIn('SECRET', json.dumps(result))

    def test_lifecycle_404_unknown_not_absence(self):
        client = self.client()
        client.get_object_lifecycle_policy.side_effect = ApiError(404)
        result = probe.read_metadata(client)
        self.assertEqual(result['status'], 'NOT_FOUND_OR_NOT_VISIBLE')
        self.assertEqual(result['lifecycle'], 'UNKNOWN')
        self.assertEqual(result['lifecycle_rule_count'], 'UNKNOWN')
        self.assertEqual(result['privacy'], 'NoPublicAccess')

    def test_missing_privacy_and_encryption_unknown(self):
        client = self.client()
        del client.get_bucket.return_value.data.public_access_type
        del client.get_bucket.return_value.data.kms_key_id
        result = probe.read_metadata(client)
        self.assertEqual(result['privacy'], 'UNKNOWN')
        self.assertEqual(result['encryption'], 'UNKNOWN')

    def test_customer_key_identifier_not_disclosed(self):
        client = self.client()
        client.get_bucket.return_value.data.kms_key_id = 'ocid1.key.SECRET'
        result = probe.read_metadata(client)
        self.assertEqual(result['encryption'], 'CUSTOMER_MANAGED')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_noninteger_counts_unknown_and_empty_rules_distinct(self):
        client = self.client()
        client.get_bucket.return_value.data.approximate_size = True
        client.get_bucket.return_value.data.approximate_count = -1
        client.get_object_lifecycle_policy.return_value.data.items = []
        result = probe.read_metadata(client)
        self.assertEqual(result['approximate_size_bytes'], 'UNKNOWN')
        self.assertEqual(result['approximate_object_count'], 'UNKNOWN')
        self.assertEqual(result['lifecycle'], 'EMPTY_RULES')

    def test_fixed_credential_target_and_no_readonly_fallback(self):
        env = self.env()
        for key in ('OCI_CLI_TENANCY', 'OCI_CLI_REGION'):
            with self.subTest(key=key), self.assertRaises(probe.StopProbe) as raised:
                probe.credential_config(dict(env, **{key: 'wrong'}))
            self.assertEqual(raised.exception.code, 'TARGET_MISMATCH')
        readonly = {key.replace('OCI_CLI_', 'OCI_READONLY_CLI_'): value for key, value in env.items()}
        with self.assertRaises(probe.StopProbe):
            probe.credential_config(readonly)
        escaped = dict(env, OCI_CLI_KEY_CONTENT=env['OCI_CLI_KEY_CONTENT'].replace('\n', '\\r\\n'))
        self.assertEqual(probe.credential_config(escaped)[1], env['OCI_CLI_KEY_CONTENT'])

    def test_sdk_in_memory_key_timeout_and_no_retry(self):
        config, key = probe.credential_config(self.env())
        sdk = NS(signer=NS(Signer=Mock()), object_storage=NS(ObjectStorageClient=Mock()),
                 retry=NS(NoneRetryStrategy=Mock()))
        with patch.dict(sys.modules, {'oci': sdk}):
            probe.make_client(config, key)
        sdk.signer.Signer.assert_called_once_with(config['tenancy'], config['user'], config['fingerprint'],
                                                 None, private_key_content=key)
        args, kwargs = sdk.object_storage.ObjectStorageClient.call_args
        self.assertEqual(args[0]['key_content'], key)
        self.assertEqual(kwargs['timeout'], (3, 8))
        self.assertIs(kwargs['retry_strategy'], sdk.retry.NoneRetryStrategy.return_value)

    def test_client_setup_fixed_error_codes(self):
        config, key = probe.credential_config(self.env())
        for failure, expected in [('import', 'SDK_IMPORT_FAILED'), ('signer', 'SIGNER_SETUP_FAILED'),
                                  ('client', 'STORAGE_CLIENT_SETUP_FAILED')]:
            with self.subTest(failure=failure):
                sdk = NS(signer=NS(Signer=Mock()), object_storage=NS(ObjectStorageClient=Mock()),
                         retry=NS(NoneRetryStrategy=Mock()))
                if failure == 'signer':
                    sdk.signer.Signer.side_effect = ValueError('SECRET')
                if failure == 'client':
                    sdk.object_storage.ObjectStorageClient.side_effect = ValueError('SECRET')
                with patch.dict(sys.modules, {'oci': None if failure == 'import' else sdk}), \
                     self.assertRaises(probe.StopProbe) as raised:
                    probe.make_client(config, key)
                self.assertEqual(raised.exception.code, expected)
                self.assertNotIn('SECRET', str(raised.exception))

    def test_deadline_stops_without_next_call(self):
        client = self.client()
        result = probe.read_metadata(client, clock=Mock(side_effect=[0, 0, 121]))
        self.assertEqual(result['status'], 'TIME_BUDGET_EXCEEDED')
        client.get_bucket.assert_not_called()

    def test_main_exception_and_summary_redaction(self):
        stdout = io.StringIO()
        output = mock_open()
        with patch.object(probe.signal, 'SIGALRM', 14, create=True), patch.object(probe.signal, 'signal'), \
             patch.object(probe.signal, 'alarm', create=True), \
             patch.object(probe, 'credential_config', side_effect=ValueError('SECRET_RAW_EXCEPTION')), \
             patch.dict(probe.os.environ, {'GITHUB_STEP_SUMMARY': 'runner-output'}, clear=True), \
             patch('builtins.open', output), patch('sys.stdout', stdout):
            self.assertEqual(probe.main(), 2)
        payload = stdout.getvalue().strip()
        self.assertEqual(json.loads(payload)['status'], 'UNEXPECTED_LOCAL_FAILURE')
        self.assertNotIn('SECRET', payload)
        output().write.assert_called_once_with('```json\n' + payload + '\n```\n')

    def test_ast_exact_api_allowlist(self):
        tree = ast.parse(Path(probe.__file__).read_text(encoding='utf-8'))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                      and isinstance(node.value, ast.Name) and node.value.id == 'client'}
        self.assertEqual(attributes, {'get_namespace', 'get_bucket', 'get_object_lifecycle_policy'})
        env_keys = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                    and isinstance(node.value, str) and node.value.startswith('OCI_')}
        self.assertFalse(any(key.startswith('OCI_READONLY_') for key in env_keys))


if __name__ == '__main__':
    unittest.main()
