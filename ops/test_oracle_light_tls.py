"""Offline TLS handshakes for the standalone Light PostgreSQL probes."""
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings

import oracle_light_pg_certificate_check as certificate
import oracle_light_pg_tunnel_probe as tunnel
import oracle_light_postgres_stage as stage


CLIENTS = (certificate, tunnel, stage)


def handshake(client_context, server_context, hostname='localhost'):
    """Exchange TLS records in memory, without sockets or production access."""
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    complete = set()
    for _ in range(20):
        for name, endpoint, outgoing, incoming in (
            ('client', client, client_out, server_in),
            ('server', server, server_out, client_in),
        ):
            if name not in complete:
                try:
                    endpoint.do_handshake()
                    complete.add(name)
                except ssl.SSLWantReadError:
                    pass
            data = outgoing.read()
            if data:
                incoming.write(data)
        if len(complete) == 2:
            return client.version()
    raise AssertionError('TLS handshake did not terminate')


class PostgreSQLProbeTLS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        for name in ('server', 'unrelated'):
            subprocess.run([
                'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(cls.directory / f'{name}.key'),
                '-out', str(cls.directory / f'{name}.crt'), '-days', '1',
                '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
            ], check=True, capture_output=True, timeout=15)

    def server(self, version):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            context.minimum_version = version
            context.maximum_version = version
        context.set_ciphers('ALL:@SECLEVEL=0')
        context.load_cert_chain(self.directory / 'server.crt', self.directory / 'server.key')
        return context

    def test_trusted_tls12_and_tls13_accept_dns_and_ip(self):
        for module in CLIENTS:
            for version, expected in ((ssl.TLSVersion.TLSv1_2, 'TLSv1.2'),
                                      (ssl.TLSVersion.TLSv1_3, 'TLSv1.3')):
                for hostname in ('localhost', '127.0.0.1'):
                    with self.subTest(client=module.__name__, tls=expected, host=hostname):
                        context = module.verified_tls_context(self.directory / 'server.crt')
                        self.assertEqual(handshake(context, self.server(version), hostname), expected)

    def test_wrong_hostname_rejected(self):
        for module in CLIENTS:
            with self.subTest(client=module.__name__):
                context = module.verified_tls_context(self.directory / 'server.crt')
                with self.assertRaises(ssl.SSLCertVerificationError):
                    handshake(context, self.server(ssl.TLSVersion.TLSv1_2), 'foreign.invalid')

    def test_untrusted_certificate_rejected(self):
        for module in CLIENTS:
            with self.subTest(client=module.__name__):
                context = module.verified_tls_context(self.directory / 'unrelated.crt')
                with self.assertRaises(ssl.SSLCertVerificationError):
                    handshake(context, self.server(ssl.TLSVersion.TLSv1_2))

    def test_tls11_rejected_even_when_interpreter_defaults_allow_it(self):
        original = ssl.create_default_context

        def legacy_default(**kwargs):
            context = original(**kwargs)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                context.minimum_version = ssl.TLSVersion.TLSv1
            context.set_ciphers('ALL:@SECLEVEL=0')
            return context

        # Prove that this test's legacy server really can negotiate TLS 1.1.
        baseline = legacy_default(cafile=str(self.directory / 'server.crt'))
        self.assertEqual(handshake(baseline, self.server(ssl.TLSVersion.TLSv1_1)), 'TLSv1.1')
        for module in CLIENTS:
            with self.subTest(client=module.__name__):
                with patch.object(module.ssl, 'create_default_context', side_effect=legacy_default):
                    context = module.verified_tls_context(self.directory / 'server.crt')
                with self.assertRaises(ssl.SSLError):
                    handshake(context, self.server(ssl.TLSVersion.TLSv1_1))


if __name__ == '__main__':
    unittest.main()
