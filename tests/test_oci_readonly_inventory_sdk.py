"""Real pinned SDK construction with an ephemeral synthetic key, no OCI calls."""
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace as NS

from ops import oci_readonly_inventory as probe
from ops import oci_backup_bucket_metadata as exact_bucket
from ops import oci_backup_write_probe as write_probe

try:
    import oci
except ImportError:
    oci = None


@unittest.skipIf(oci is None, 'Pinned OCI SDK not installed in this offline environment')
class RealSdkConstruction(unittest.TestCase):
    def test_old_config_reproduces_and_fixed_clients_construct_without_network(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.assertEqual(oci.__version__, '2.187.1')
        # Never saved, printed, registered or used to sign an HTTP request.
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode('ascii')
        config = dict(tenancy=probe.TENANCY, region=probe.REGION,
                      user='ocid1.user.oc1..synthetic', fingerprint=':'.join(['ab'] * 16))
        with patch('socket.socket.connect', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch('socket.create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('NETWORK_FORBIDDEN')):
            signer = oci.signer.Signer(config['tenancy'], config['user'], config['fingerprint'],
                                       None, private_key_content=pem)
            for client in (oci.identity.IdentityClient, oci.object_storage.ObjectStorageClient):
                with self.subTest(client=client.__name__), self.assertRaises(oci.exceptions.InvalidConfig) as caught:
                    client(config, signer=signer)
                self.assertEqual(caught.exception.errors, {'key_file': 'missing'})
            iam, storage = probe.make_clients(config, pem)
            self.assertIsInstance(iam, oci.identity.IdentityClient)
            self.assertIsInstance(storage, oci.object_storage.ObjectStorageClient)
            self.assertNotIn('key_content', config)  # Caller dictionary is not mutated.
            self.assertIn('eu-frankfurt-1', storage.base_client.endpoint)
            exact_client = exact_bucket.make_client(config, pem)
            self.assertIsInstance(exact_client, oci.object_storage.ObjectStorageClient)
            self.assertIn('eu-frankfurt-1', exact_client.base_client.endpoint)
            with patch.object(exact_client.base_client, 'call_api') as transport:
                exact_client.get_namespace(compartment_id=probe.TENANCY)
                exact_client.get_bucket(namespace_name='synthetic', bucket_name=exact_bucket.BUCKET,
                                        fields=['approximateSize', 'approximateCount', 'autoTiering'])
                exact_client.get_object_lifecycle_policy(namespace_name='synthetic',
                                                         bucket_name=exact_bucket.BUCKET)
            self.assertEqual(len(transport.call_args_list), 3)
            self.assertTrue(all(call.kwargs['method'] == 'GET' for call in transport.call_args_list))
            self.assertEqual([call.kwargs['resource_path'] for call in transport.call_args_list],
                             ['/n', '/n/{namespaceName}/b/{bucketName}',
                              '/n/{namespaceName}/b/{bucketName}/l'])
            sha = 'a' * 40
            env = dict(GITHUB_SHA=sha, GITHUB_WORKFLOW_SHA=sha,
                       GITHUB_REPOSITORY=write_probe.REPOSITORY, GITHUB_REF=write_probe.BRANCH,
                       GITHUB_WORKFLOW_REF=write_probe.WORKFLOW,
                       GITHUB_ACTOR='olegmed1-art', GITHUB_TRIGGERING_ACTOR='olegmed1-art',
                       GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                       WRITE_GATE='oci-backup-write-probe-v1:' + sha + ':policies-reviewed:one-write-approved')
            stream = NS(raw=NS(read=Mock(return_value=write_probe.PAYLOAD)), close=Mock())
            responses = [NS(data='synthetic'), NS(data=NS(name=exact_bucket.BUCKET,
                namespace='synthetic', compartment_id=probe.TENANCY, public_access_type='NoPublicAccess',
                storage_tier='Standard', versioning='Disabled', auto_tiering='Disabled', kms_key_id=None)),
                NS(status=200), NS(headers={'content-length': str(len(write_probe.PAYLOAD))}, data=stream)]
            with patch.object(exact_client.base_client, 'call_api', side_effect=responses) as transport:
                result = write_probe.run_probe(exact_client, env)
            self.assertEqual(result['status'], 'VERIFIED')
            self.assertEqual([call.kwargs['method'] for call in transport.call_args_list],
                             ['GET', 'GET', 'PUT', 'GET'])
            put = transport.call_args_list[2].kwargs
            headers = {k.lower(): v for k, v in put['header_params'].items()}
            self.assertEqual(headers['if-none-match'], '*')
            self.assertEqual(put['body'], write_probe.PAYLOAD)
            self.assertLessEqual(len(put['body']), 1024)
            self.assertEqual(put['path_params']['objectName'], result['object_key'])
            self.assertEqual(transport.call_args_list[3].kwargs['path_params']['objectName'], result['object_key'])
            stream.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
