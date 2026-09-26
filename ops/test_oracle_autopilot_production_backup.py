"""Failure-mode tests for the production backup contract; no cloud access."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name('oracle_autopilot_production_backup.py')
spec = importlib.util.spec_from_file_location('oracle_autopilot_production_backup', MODULE)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupFailureTests(unittest.TestCase):
    def route_dir(self, directory, route):
        root = Path(directory)
        (root / 'route.lock').write_bytes(b'')
        stat = (root / 'route.lock').stat()
        (root / 'lock-identity.json').write_text(json.dumps({'device': stat.st_dev, 'inode': stat.st_ino}))
        (root / 'route.json').write_text(json.dumps(route))
        for path in root.iterdir():
            path.chmod(0o600)
        return root

    def test_stale_neon_route_cannot_issue_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.route_dir(directory, {'version':1,'backend':'neon',
                                              'database':'autopilot','epoch':0})
            with patch.object(backup, 'ROUTE_ROOT', root), \
                 patch.object(backup.os, 'geteuid', return_value=0), \
                 patch.object(backup.socket, 'gethostname', return_value='autopilot-lite-vnic'):
                with self.assertRaisesRegex(ValueError, 'PRODUCTION_ROUTE_NOT_ACTIVE'):
                    with backup.pinned_route():
                        self.fail('stale route admitted')

    def test_route_transition_during_backup_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            route = {'version':1,'backend':'postgresql','database':'autopilot','epoch':2}
            root = self.route_dir(directory, route)
            with patch.object(backup, 'ROUTE_ROOT', root), \
                 patch.object(backup.os, 'geteuid', return_value=0), \
                 patch.object(backup.socket, 'gethostname', return_value='autopilot-lite-vnic'):
                with self.assertRaisesRegex(ValueError, 'ROUTE_CHANGED_DURING_BACKUP'):
                    with backup.pinned_route():
                        (root / 'route.json').write_text(json.dumps({**route,'epoch':3}))

    def test_password_never_enters_dump_argv_or_failure_output(self):
        secret = 'SECRET_SENTINEL_7cfc9d'
        try:
            env = backup.libpq_environment(
                f'host=127.0.0.1 port=55432 dbname=autopilot user=backup password={secret}')
        except ModuleNotFoundError:
            self.skipTest('psycopg is unavailable in this runner')
        self.assertEqual(env['PGPASSWORD'], secret)
        self.assertNotIn(secret, ' '.join(['pg_dump', '--format=custom', '--snapshot', '00001']))
        self.assertNotIn(secret, str(backup.execute.__code__.co_consts))

    def test_libpq_hostaddr_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_LIBPQ_PARAMETERS'):
            backup.libpq_environment('host=127.0.0.1 hostaddr=198.51.100.2 '
                                     'port=55432 dbname=autopilot user=backup')
        with patch.dict(os.environ, {'PGHOSTADDR': '198.51.100.2'}):
            with self.assertRaisesRegex(ValueError, 'LIBPQ_HOST_OVERRIDE_FORBIDDEN'):
                backup.libpq_environment('host=127.0.0.1 port=55432 dbname=autopilot user=backup')

    def test_snapshot_explicit_repeatable_read_not_implicit_transaction(self):
        import sys
        class Cursor:
            calls = []
            rows = iter([
                ('snap-1', 'autopilot', 180000, '17', __import__('datetime').datetime.now(__import__('datetime').timezone.utc), '127.0.0.1', 55432),
                (0,),
                [('autopilot_callback_login',), ('autopilot_light_worker_login',),
                 ('bridge_school_worker_principal',), ('neondb_owner',)],
                (False, False, False, False, False),
                (0,),
                ({'effective_acl': [1, 'x']},),
                ([['autopilot', 'f', '', 'neondb_owner', True]],),
                ([['autopilot', 'queue_id_seq', 'neondb_owner', 'bigint', 1, 1, 1, 9223372036854775807, 1, False]],),
                [],
            ])
            def execute(self, sql, *args):
                self.calls.append(sql)
            def fetchone(self):
                return next(self.rows)
            def fetchall(self):
                return next(self.rows)
            def nextset(self):
                return False
        class Connection:
            autocommit = False
            def cursor(self):
                self.cursor_instance = Cursor()
                return self.cursor_instance
            def close(self):
                pass
        connection = Connection()
        class Psycopg:
            @staticmethod
            def connect(*args, **kwargs):
                return connection
        with patch.dict(sys.modules, {'psycopg': Psycopg}):
            result = backup.source_snapshot('sensitive')
        self.assertTrue(connection.autocommit)
        self.assertEqual(Cursor.calls[0], 'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        self.assertEqual(result[1], 'snap-1')

    def test_unexpected_application_relation_blocks_full_database_dump(self):
        import sys
        class Cursor:
            rows = iter([
                ('snap', 'autopilot', 180000, '17', __import__('datetime').datetime.now(__import__('datetime').timezone.utc), '127.0.0.1', 55432),
                (1,),
            ])
            def execute(self, sql, *args):
                pass
            def fetchone(self):
                return next(self.rows)
        class Connection:
            autocommit = False
            closed = False
            def cursor(self):
                return Cursor()
            def close(self):
                self.closed = True
        connection = Connection()
        class Psycopg:
            @staticmethod
            def connect(*args, **kwargs):
                return connection
        with patch.dict(sys.modules, {'psycopg': Psycopg}):
            with self.assertRaisesRegex(ValueError, 'UNEXPECTED_DATABASE_RELATIONS'):
                backup.source_snapshot('sensitive')
        self.assertTrue(connection.closed)

    def test_partial_upload_never_downloads_or_confirms(self):
        class Client:
            calls = []
            def get_namespace(self, **kwargs):
                return type('R', (), {'data': 'ns'})()
            def put_object(self, *args, **kwargs):
                self.calls.append(('put', kwargs['if_none_match']))
                raise OSError('connection lost during upload')
            def get_object(self, *args):
                self.calls.append(('get', None))
        client = Client()
        class OCI:
            class config:
                @staticmethod
                def from_file(*args):
                    return {'tenancy': 'test'}
            class retry:
                @staticmethod
                def NoneRetryStrategy():
                    return None
            class object_storage:
                @staticmethod
                def ObjectStorageClient(*args, **kwargs):
                    return client
        import sys
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'oci': OCI}), \
             patch.object(backup, 'private_bucket'), patch.dict(os.environ, {
                 'OCI_CONFIG_FILE': '/nonexistent', 'OCI_PROFILE': 'test',
                 'OCI_TENANCY': 'test', 'AUTOPILOT_BACKUP_BUCKET': 'private'}):
            source, target = Path(directory) / 'a', Path(directory) / 'b'
            source.write_bytes(b'good')
            with self.assertRaises(OSError):
                backup.upload_download(source, target, 'object')
            self.assertEqual(client.calls, [('put', '*')])
            self.assertFalse(target.exists())

    def test_download_hash_rejects_corrupt_off_host_object(self):
        class Object:
            headers = {'content-length': '4', 'opc-meta-sha256': hashlib.sha256(b'good').hexdigest()}
            class data:
                class raw:
                    @staticmethod
                    def stream(*args, **kwargs):
                        return iter([b'evil'])
        class Client:
            def get_namespace(self, **kwargs):
                return type('R', (), {'data': 'ns'})()
            def put_object(self, *args, **kwargs):
                pass
            def get_object(self, *args):
                return Object()
        class OCI:
            class config:
                @staticmethod
                def from_file(*args):
                    return {'tenancy': 'test'}
            class retry:
                @staticmethod
                def NoneRetryStrategy():
                    return None
            class object_storage:
                @staticmethod
                def ObjectStorageClient(*args, **kwargs):
                    return Client()
        import sys
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'oci': OCI}), \
             patch.object(backup, 'private_bucket'), patch.dict(os.environ, {
                 'OCI_CONFIG_FILE': '/nonexistent', 'OCI_PROFILE': 'test',
                 'OCI_TENANCY': 'test', 'AUTOPILOT_BACKUP_BUCKET': 'private'}):
            source, target = Path(directory) / 'a', Path(directory) / 'b'
            source.write_bytes(b'good')
            with self.assertRaisesRegex(ValueError, 'DOWNLOAD_MISMATCH'):
                backup.upload_download(source, target, 'object')

    def test_receipt_requires_trusted_main_before_touching_dsn(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'GITHUB_REPOSITORY': 'fork/project', 'GITHUB_REF': 'refs/heads/main',
        }, clear=True), patch.object(backup.sys, 'argv', ['script', str(Path(directory) / 'receipt.json')]):
            with self.assertRaisesRegex(ValueError, 'TRUSTED_MAIN_REQUIRED'):
                backup.main()

    def test_image_requires_digest(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'AUTOPILOT_RESTORE_IMAGE': 'postgres:18',
        }):
            path = Path(directory) / 'archive.dump'
            path.write_bytes(b'PGDMP')
            with self.assertRaisesRegex(ValueError, 'RESTORE_IMAGE_MUST_BE_PINNED'):
                backup.drill(path, {}, [], [], [], [])

    def test_restore_detects_acl_or_row_manifest_drift(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'AUTOPILOT_RESTORE_IMAGE': 'postgres@sha256:' + 'a' * 64,
        }), patch.object(backup.os, 'geteuid', return_value=0), \
             patch.object(backup.os, 'chown'), \
             patch.object(backup, 'execute', return_value=b'{"effective_acl":[1,"different"]}\n[]\n[]\n'):
            path = Path(directory) / 'archive.dump'
            path.write_bytes(b'PGDMP')
            with self.assertRaisesRegex(ValueError, 'RESTORE_MANIFEST_MISMATCH'):
                backup.drill(path, {'effective_acl': [1, 'source']}, [], [], {'autopilot_callback_login'}, [])

    def test_restore_detects_security_definer_owner_drift(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'AUTOPILOT_RESTORE_IMAGE': 'postgres@sha256:' + 'a' * 64,
        }), patch.object(backup.os, 'geteuid', return_value=0), \
             patch.object(backup.os, 'chown'), \
             patch.object(backup, 'execute', return_value=b'{}\n[["autopilot","f","","postgres",true]]\n[]\n'):
            path = Path(directory) / 'archive.dump'
            path.write_bytes(b'PGDMP')
            with self.assertRaisesRegex(ValueError, 'RESTORE_OWNERSHIP_MISMATCH'):
                backup.drill(path, {}, [['autopilot', 'f', '', 'neondb_owner', True]],
                             [], {'neondb_owner'}, [])

    def test_restore_detects_sequence_structure_drift(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'AUTOPILOT_RESTORE_IMAGE': 'postgres@sha256:' + 'a' * 64,
        }), patch.object(backup.os, 'geteuid', return_value=0), \
             patch.object(backup.os, 'chown'), \
             patch.object(backup, 'execute', return_value=b'{}\n[]\n[]\n'):
            path = Path(directory) / 'archive.dump'
            path.write_bytes(b'PGDMP')
            with self.assertRaisesRegex(ValueError, 'RESTORE_SEQUENCE_STRUCTURE_MISMATCH'):
                backup.drill(path, {}, [], [['autopilot', 'id_seq']], {'neondb_owner'}, [])

    def test_restore_never_uses_production_network_or_data_volume(self):
        captured = []
        def fake_run(argv, **kwargs):
            captured.extend(argv)
            return b'{"effective_acl":[1,"source"]}\n[]\n[]\n'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'AUTOPILOT_RESTORE_IMAGE': 'postgres@sha256:' + 'a' * 64,
        }), patch.object(backup.os, 'geteuid', return_value=0), \
             patch.object(backup.os, 'chown'), patch.object(backup, 'execute', side_effect=fake_run):
            path = Path(directory) / 'archive.dump'
            path.write_bytes(b'PGDMP')
            backup.drill(path, {'effective_acl': [1, 'source']}, [], [], {'autopilot_callback_login'}, [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o710)
            self.assertIn('none', captured)
            self.assertIn('--read-only', captured)
            mounts = [captured[i + 1] for i, value in enumerate(captured[:-1]) if value == '-v']
            self.assertTrue(mounts)
            self.assertTrue(all(item.endswith(':ro') for item in mounts))
            self.assertNotIn('/srv/autopilot-data', ' '.join(captured))


if __name__ == '__main__':
    unittest.main()
