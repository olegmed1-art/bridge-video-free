"""Real pinned SDK construction with an ephemeral synthetic key, no OCI calls."""
import unittest
from unittest.mock import patch

from ops import oci_readonly_inventory as probe
from ops import oci_backup_bucket_metadata as exact_bucket

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


if __name__ == '__main__':
    unittest.main()
