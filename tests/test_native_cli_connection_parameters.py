"""Exercise real psycopg parameter filtering with synthetic libpq metadata."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from psycopg import ConnectionInfo, pq
from database import native_cli_permission_engine as engine


class ConnectionParametersTests(unittest.TestCase):
    def fixture(self):
        binding = engine.NeonBinding('test-project', 'br-test', 'ep-test',
                                     'ep-test.c-5.eu-central-1.aws.neon.tech')
        params = {b'host': binding.host.encode(), b'hostaddr': b'192.0.2.10',
                  b'sslmode': b'verify-full', b'gssencmode': b'disable', b'options': b''}
        class LibpqMetadata:
            _encoding = 'utf-8'
            host = binding.host.encode()
            hostaddr = b'192.0.2.10'
            port = b'5432'
            @property
            def info(self):
                return [SimpleNamespace(keyword=k, val=v) for k, v in params.items()]
        pgconn = LibpqMetadata()
        return binding, params, SimpleNamespace(pgconn=pgconn, info=ConnectionInfo(pgconn))

    def test_actual_driver_omits_defaults_but_secure_effective_values_are_accepted(self):
        binding, params, conn = self.fixture()
        defaults = {r.keyword: r.compiled for r in pq.Conninfo.get_defaults()}
        filtered = conn.info.get_parameters()  # Real installed psycopg implementation.
        if defaults[b'gssencmode'] == b'disable':
            self.assertNotIn('gssencmode', filtered)
        with patch.object(conn.info, 'get_parameters', side_effect=AssertionError('lossy API')), \
                patch.object(engine, 'neon_server_identity') as server:
            engine.neon_identity(conn, binding)
            server.assert_called_once_with(conn, binding)

    def test_unsafe_or_missing_effective_parameters_still_fail_closed(self):
        for key, value in ((b'gssencmode', b'prefer'), (b'gssencmode', None),
                           (b'sslmode', b'require'), (b'options', b'endpoint=ep-other'),
                           (b'hostaddr', b'127.0.0.1'), (b'host', b'other')):
            binding, params, conn = self.fixture()
            params[key] = value
            with self.subTest(key=key, value=value), \
                    patch.object(engine, 'neon_server_identity') as server:
                with self.assertRaises(engine.Refused):
                    engine.neon_identity(conn, binding)
                server.assert_not_called()

    def test_password_and_unknown_values_are_never_decoded(self):
        binding, params, conn = self.fixture()
        class Private:
            def decode(self, *_):
                raise AssertionError('private value must not be read')
        params[b'password'] = Private()
        params[b'unknown'] = Private()
        with patch.object(engine, 'neon_server_identity'):
            engine.neon_identity(conn, binding)


if __name__ == '__main__':
    unittest.main()
