import ast
import hashlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from ops import oci_backup_write_probe as probe


class ApiError(Exception):
    def __init__(self, status):
        super().__init__('PRIVATE_ERROR_SECRET')
        self.status = status


class WriteProbeTests(unittest.TestCase):
    def env(self):
        sha = 'a' * 40
        return dict(GITHUB_SHA=sha, GITHUB_REPOSITORY=probe.REPOSITORY, GITHUB_REF=probe.BRANCH,
                    GITHUB_ACTOR='olegmed1-art', GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_WORKFLOW_SHA=sha,
                    GITHUB_WORKFLOW_REF=probe.WORKFLOW, GITHUB_RUN_ID='123456', GITHUB_RUN_ATTEMPT='1',
                    WRITE_GATE='oci-backup-write-probe-v1:' + sha + ':policies-reviewed:one-write-approved')

    def client(self):
        client = Mock()
        client.get_namespace.return_value = NS(data='namespace_example')
        client.get_bucket.return_value = NS(data=NS(name=probe.BUCKET, namespace='namespace_example',
            compartment_id=probe.TENANCY, public_access_type='NoPublicAccess', storage_tier='Standard',
            versioning='Disabled', auto_tiering='Disabled', kms_key_id=None))
        client.put_object.return_value = NS(status=200)
        data = Mock()
        data.raw.read.return_value = probe.PAYLOAD
        client.get_object.return_value = NS(headers={'content-length': str(len(probe.PAYLOAD))}, data=data)
        return client

    def test_one_conditional_create_exact_bounded_readback(self):
        client = self.client()
        result = probe.run_probe(client, self.env())
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertTrue(result['created_confirmed'])
        self.assertTrue(result['readback_verified'])
        self.assertLessEqual(result['payload_bytes'], 1024)
        self.assertEqual(result['payload_sha256'], hashlib.sha256(probe.PAYLOAD).hexdigest())
        self.assertRegex(result['object_key'], r'^neon-backups/probe-v1/123456-1-[a-f0-9]{32}$')
        self.assertEqual([call[0] for call in client.mock_calls if not call[0].startswith('get_object().')],
                         ['get_namespace', 'get_bucket', 'put_object', 'get_object'])
        kwargs = client.put_object.call_args.kwargs
        self.assertEqual(kwargs['if_none_match'], '*')
        self.assertEqual(kwargs['put_object_body'], probe.PAYLOAD)
        self.assertEqual(kwargs['object_name'], result['object_key'])
        client.get_object.assert_called_once_with(namespace_name='namespace_example', bucket_name=probe.BUCKET,
                                                 object_name=result['object_key'])
        client.get_object.return_value.data.raw.read.assert_called_once_with(1025, decode_content=False)
        client.get_object.return_value.data.close.assert_called_once()

    def test_every_gate_field_required_before_calls(self):
        for field in self.env():
            with self.subTest(field=field):
                env = self.env()
                env[field] = 'wrong'
                client = self.client()
                result = probe.run_probe(client, env)
                self.assertEqual(result['status'], 'OWNER_GATE_REJECTED')
                self.assertFalse(result['put_attempted'])
                self.assertEqual(client.mock_calls, [])

    def test_rerun_rejected(self):
        env = dict(self.env(), GITHUB_RUN_ATTEMPT='2')
        client = self.client()
        self.assertEqual(probe.run_probe(client, env)['status'], 'RERUN_REJECTED')
        self.assertEqual(client.mock_calls, [])

    def test_gate_before_credentials_in_main(self):
        stdout = io.StringIO()
        with patch.dict(probe.os.environ, {}, clear=True), patch.object(probe, 'credential_config') as creds, \
             patch.object(probe, 'make_client') as factory, patch('sys.stdout', stdout):
            self.assertEqual(probe.main(), 2)
        creds.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'OWNER_GATE_REJECTED')

    def test_all_bucket_conditions_known_before_put(self):
        for field in ('name', 'namespace', 'compartment_id', 'public_access_type', 'storage_tier',
                      'versioning', 'auto_tiering', 'kms_key_id'):
            for missing in (True, False):
                with self.subTest(field=field, missing=missing):
                    client = self.client()
                    bucket = client.get_bucket.return_value.data
                    if missing:
                        delattr(bucket, field)
                    else:
                        setattr(bucket, field, 'wrong')
                    result = probe.run_probe(client, self.env())
                    self.assertEqual(result['status'], 'BUCKET_PREFLIGHT_REJECTED')
                    client.put_object.assert_not_called()

    def test_read_denial_stops_without_write(self):
        for method in ('get_namespace', 'get_bucket'):
            for code in (401, 403, 404):
                client = self.client()
                getattr(client, method).side_effect = ApiError(code)
                result = probe.run_probe(client, self.env())
                self.assertEqual(result['status'], 'NOT_FOUND_OR_NOT_VISIBLE' if code == 404 else 'ACCESS_DENIED')
                client.put_object.assert_not_called()
                self.assertNotIn('PRIVATE_ERROR_SECRET', json.dumps(result))

    def test_lost_put_response_never_retried_or_read(self):
        for error, expected in [(RuntimeError('SECRET'), 'WRITE_OUTCOME_UNKNOWN'),
                                (probe.StopProbe('TIME_BUDGET_EXCEEDED'), 'WRITE_OUTCOME_UNKNOWN'),
                                (ApiError(500), 'WRITE_OUTCOME_UNKNOWN'), (ApiError(412), 'COLLISION_STOP'),
                                (ApiError(403), 'ACCESS_DENIED')]:
            client = self.client()
            client.put_object.side_effect = error
            result = probe.run_probe(client, self.env())
            self.assertEqual(result['status'], expected)
            self.assertTrue(result['put_attempted'])
            self.assertFalse(result['created_confirmed'])
            client.put_object.assert_called_once()
            client.get_object.assert_not_called()
            self.assertNotIn('SECRET', json.dumps(result))

    def test_unexpected_put_status_unknown(self):
        client = self.client()
        client.put_object.return_value.status = 202
        self.assertEqual(probe.run_probe(client, self.env())['status'], 'WRITE_OUTCOME_UNKNOWN')
        client.get_object.assert_not_called()

    def test_signal_after_put_return_before_confirmation_unknown(self):
        class InterruptedResponse:
            @property
            def status(self):
                raise probe.StopProbe('TIME_BUDGET_EXCEEDED')
        client = self.client()
        client.put_object.return_value = InterruptedResponse()
        result = probe.run_probe(client, self.env())
        self.assertEqual(result['status'], 'WRITE_OUTCOME_UNKNOWN')
        self.assertTrue(result['put_attempted'])
        self.assertFalse(result['created_confirmed'])
        client.get_object.assert_not_called()

    def test_readback_failure_preserves_object_and_no_retry(self):
        for code in (403, 404, 500):
            client = self.client()
            client.get_object.side_effect = ApiError(code)
            result = probe.run_probe(client, self.env())
            self.assertTrue(result['created_confirmed'])
            self.assertFalse(result['readback_verified'])
            self.assertTrue(result['object_preserved'])
            client.put_object.assert_called_once()
            client.get_object.assert_called_once()

    def test_stream_bound_encoding_length_hash_and_close(self):
        for change in ('oversize', 'hash', 'length', 'encoding', 'stream_error'):
            with self.subTest(change=change):
                client = self.client()
                response = client.get_object.return_value
                if change == 'oversize':
                    response.data.raw.read.return_value = b'x' * 1025
                elif change == 'hash':
                    response.data.raw.read.return_value = b'x' * len(probe.PAYLOAD)
                elif change == 'length':
                    response.headers['content-length'] = '99999999'
                elif change == 'encoding':
                    response.headers['content-encoding'] = 'gzip'
                else:
                    response.data.raw.read.side_effect = RuntimeError('SECRET')
                result = probe.run_probe(client, self.env())
                self.assertFalse(result['readback_verified'])
                self.assertTrue(result['created_confirmed'])
                response.data.close.assert_called_once()
                self.assertNotIn('SECRET', json.dumps(result))

    def test_deadline_before_preflight_no_calls(self):
        client = self.client()
        result = probe.run_probe(client, self.env(), clock=Mock(side_effect=[0, 121]))
        self.assertEqual(result['status'], 'TIME_BUDGET_EXCEEDED')
        self.assertEqual(client.mock_calls, [])

    def test_ast_allowlist_no_lifecycle_list_delete(self):
        tree = ast.parse(Path(probe.__file__).read_text(encoding='utf-8'))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                      and isinstance(node.value, ast.Name) and node.value.id == 'client'}
        self.assertEqual(attributes, {'get_namespace', 'get_bucket', 'put_object', 'get_object'})
        put_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == 'put_object']
        self.assertEqual(len(put_calls), 1)
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_probe')
        self.assertFalse(any(isinstance(n, (ast.For, ast.While)) for n in ast.walk(run)))


if __name__ == '__main__':
    unittest.main()
