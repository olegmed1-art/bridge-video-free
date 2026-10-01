"""Offline only: synthetic URI, mocked Docker; no credentials or network."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from ops import neon_backup_source as backup

URI = ('postgresql://neondb_owner:synthetic-only@' + backup.HOST
       + '/neondb?sslmode=require&channel_binding=require')


def evidence():
    return dict(session=['neondb', 'neondb_owner', 'neondb_owner', 'on'], version=18,
                identity=[[k, v, c, 'configuration file', v, False]
                          for k, (v, c) in backup.IDENTITY.items()],
                denied_schemas=0, denied_tables=0, denied_sequences=0, denied_largeobjects=0, denied_rls=0)


class BackupSourceTests(unittest.TestCase):
    def test_direct_and_pooler_use_fixed_direct_tls_readonly_parameters(self):
        with patch.dict(os.environ, {}, clear=True):
            for uri in (URI, URI.replace('.c-5.', '-pooler.c-5.')):
                pg = backup.parameters(uri)
                self.assertEqual(pg['PGHOST'], backup.HOST)
                self.assertEqual(pg['PGSSLMODE'], 'verify-full')
                self.assertEqual(pg['PGCHANNELBINDING'], 'require')
                self.assertEqual(pg['PGSSLROOTCERT'], '/backup-ca.crt')
                self.assertIn('default_transaction_read_only=on', pg['PGOPTIONS'])
                self.assertIn('row_security=off', pg['PGOPTIONS'])

    def test_untrusted_uri_and_ambient_libpq_options_refuse_before_client(self):
        bad = ['', URI.replace(backup.HOST, 'wrong.neon.tech'),
               URI.replace('/neondb?', '/postgres?'), URI.replace('neondb_owner', 'reader'),
               URI.replace('sslmode=require', 'sslmode=disable'),
               URI.replace('&channel_binding=require', ''), URI + '#fragment',
               URI.replace('/neondb?', ':9999/neondb?'), URI + '&sslmode=require',
               URI.replace('synthetic-only', '%0Aprivate'), URI + '\ninside']
        bad += [URI + '&' + option for option in
                ('host=wrong', 'hostaddr=127.0.0.1', 'service=other', 'options=-crole=other',
                 'dbname=wrong', 'user=other', 'sslrootcert=other')]
        with patch.dict(os.environ, {}, clear=True), patch.object(backup, 'client') as client:
            for uri in bad:
                with self.subTest(uri=uri), self.assertRaises(ValueError):
                    backup.parameters(uri)
            client.assert_not_called()
        for key in ('PGHOST', 'PGSERVICE', 'PGOPTIONS', 'PGPASSFILE'):
            with patch.dict(os.environ, {key: 'untrusted'}, clear=True), self.assertRaises(ValueError):
                backup.parameters(URI)

    def test_exact_server_identity_and_all_privileges_required(self):
        rows = []
        for key in ('denied_schemas', 'denied_tables', 'denied_sequences', 'denied_largeobjects', 'denied_rls'):
            for value in (1, None, False):
                row = evidence(); row[key] = value; rows.append(row)
        for i in range(3):
            for column, value in ((1, 'wrong'), (2, 'user'), (3, 'session'), (4, 'wrong'), (5, True)):
                row = evidence(); row['identity'][i][column] = value; rows.append(row)
        for i in range(4):
            row = evidence(); row['session'][i] = 'wrong'; rows.append(row)
        for value in (None, [], evidence()['identity'][:2]):
            row = evidence(); row['identity'] = value; rows.append(row)
        row = evidence(); row['version'] = 17; rows.append(row)
        with patch.object(backup, 'client', return_value=json.dumps(evidence())):
            backup.preflight({})
        for row in rows:
            with self.subTest(row=row), patch.object(backup, 'client', return_value=json.dumps(row)), \
                    self.assertRaises((ValueError, TypeError)):
                backup.preflight({})

    def test_preflight_failure_stops_statistics_and_dump(self):
        for mode in ('stats', 'dump'):
            with patch.dict(os.environ, {'DATABASE_URL': URI}, clear=True), \
                 patch.object(backup, 'client', return_value=json.dumps({})) as client, \
                 self.assertRaises(ValueError):
                backup.main(mode)
            self.assertEqual(client.call_count, 1)
            self.assertEqual(client.call_args.args[1][0], 'psql')

    def test_dump_uses_preflight_parameters_without_raw_uri_or_privilege_changes(self):
        with patch.dict(os.environ, {'DATABASE_URL': URI}, clear=True), \
             patch.object(backup, 'client', side_effect=[json.dumps(evidence()), '']) as client:
            backup.main('dump')
        self.assertEqual(client.call_count, 2)
        self.assertEqual(client.call_args_list[0].args[0], client.call_args_list[1].args[0])
        self.assertEqual(client.call_args.args[1][0], 'pg_dump')
        self.assertTrue(client.call_args.kwargs['dump'])
        self.assertNotIn(URI, str(client.call_args.args[1]))
        self.assertNotIn('--enable-row-security', client.call_args.args[1])
        self.assertIn('--lock-wait-timeout=5s', client.call_args.args[1])

    def test_subprocess_does_not_receive_uri_passphrase_or_emit_private_errors(self):
        with patch.dict(os.environ, {}, clear=True):
            pg = backup.parameters(URI)
        with patch.dict(os.environ, {'DATABASE_URL': URI, 'BACKUP_PASSPHRASE': 'private'}, clear=True), \
             patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'private')) as run:
            with self.assertRaises(ValueError):
                backup.client(pg, ['psql'], sql=backup.PREFLIGHT)
        self.assertNotIn('synthetic-only', str(run.call_args.args))
        self.assertNotIn('DATABASE_URL', run.call_args.kwargs['env'])
        self.assertNotIn('BACKUP_PASSPHRASE', run.call_args.kwargs['env'])
        self.assertTrue(run.call_args.kwargs['capture_output'])
        self.assertIn('type=bind,source=/etc/ssl/certs/ca-certificates.crt,target=/backup-ca.crt,readonly',
                      run.call_args.args[0])
        for exc in (RuntimeError(URI), subprocess.TimeoutExpired(['private'], 1)):
            err = io.StringIO()
            with patch.object(backup.sys, 'argv', ['backup', 'dump']), \
                 patch.object(backup, 'main', side_effect=exc), contextlib.redirect_stderr(err), \
                 self.assertRaises(SystemExit) as stopped:
                backup.entrypoint()
            self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(err.getvalue(), 'BACKUP_SOURCE_REFUSED:TOOL_FAILED\n')

    def test_timeout_removes_only_the_named_container(self):
        with patch.dict(os.environ, {}, clear=True):
            pg = backup.parameters(URI)
        with patch.object(subprocess, 'run', side_effect=[subprocess.TimeoutExpired('docker', 660),
                           subprocess.CompletedProcess([], 0)]) as run, self.assertRaises(ValueError):
            backup.client(pg, ['pg_dump'], dump=True)
        first = run.call_args_list[0].args[0]
        name = first[first.index('--name') + 1]
        self.assertTrue(name.startswith('neon-backup-'))
        self.assertEqual(run.call_args_list[1].args[0], ['docker', 'rm', '-f', name])
        self.assertEqual(run.call_args_list[1].kwargs['timeout'], 15)

    def test_preflight_is_readonly_and_checks_full_catalog_not_registry_only(self):
        for marker in ('BEGIN READ ONLY', 'ROLLBACK', 'row_security_active',
                       'has_schema_privilege', 'has_table_privilege',
                       'has_sequence_privilege', 'has_largeobject_privilege'):
            self.assertIn(marker, backup.PREFLIGHT)
        for command in ('GRANT ', 'ALTER ', 'INSERT ', 'UPDATE ', 'DELETE '):
            self.assertNotIn(command, backup.PREFLIGHT)


if __name__ == '__main__':
    unittest.main()
