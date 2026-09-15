import unittest
from unittest.mock import Mock
from witness import DSN_VALIDATOR


class DsnBoundary(unittest.TestCase):
    def setUp(self):
        namespace = {}
        exec(DSN_VALIDATOR, namespace)
        self.validate = namespace['validated_dsn']
        self.uri = ('postgresql://bridge_school_worker_principal:synthetic-password@'
                    'ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech/neondb'
                    '?sslmode=verify-full&channel_binding=require')

    def test_exact_uri_accepted(self):
        self.assertEqual(self.validate(self.uri), self.uri)

    def test_invalid_uri_never_reaches_connect(self):
        for uri in [self.uri.replace('ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech', 'evil.invalid'),
                    self.uri.replace('verify-full', 'disable'),
                    self.uri.replace('bridge_school_worker_principal', 'neondb_owner'),
                    self.uri.replace('/neondb', '/other'),
                    self.uri+'&hostaddr=127.0.0.1', self.uri+'&sslmode=require',
                    self.uri.replace('&channel_binding=require', ''), self.uri+'#fragment']:
            with self.subTest(uri=uri):
                connect = Mock()
                with self.assertRaises(ValueError):
                    connect(self.validate(uri))
                connect.assert_not_called()
