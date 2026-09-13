"""Run as root in an isolated temporary directory; never connect to a database."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('target', Path(__file__).resolve().parents[1] / 'ops/issue_881_queue_production_target.py')
target = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'psycopg': types.SimpleNamespace()}):
    spec.loader.exec_module(target)
real_verify = target.verify


class TargetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.dsn = self.directory / 'video-queue-dsn'
        self.backup = self.directory / 'backup'
        self.raw = f'postgresql://{target.USER}:encoded%40password@{target.SOURCE_HOST}/neondb?sslmode=require&channel_binding=require'.encode()
        self.dsn.write_bytes(self.raw)
        self.dsn.chmod(0o640)
        (self.directory/'lock').touch(mode=0o600)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [('DSN_FILE', self.dsn), ('BACKUP', self.backup),
                            ('OWNER_ATTESTATION_DEADLINE', int(target.time.time())+299),
                            ('LOCK_FILE', self.directory/'lock'), ('FENCE', self.directory/'fence')]:
            self.stack.enter_context(patch.object(target, name, value))
        self.stack.enter_context(patch.object(target.socket, 'gethostname', return_value='bridge-school-dds3-frankfurt'))
        self.stack.enter_context(patch.object(target.grp, 'getgrnam', return_value=types.SimpleNamespace(gr_gid=0)))
        self.verify = self.stack.enter_context(patch.object(target, 'verify'))

    def test_check_leaves_credential_and_backup_untouched(self):
        target.run('check')
        self.assertEqual(self.dsn.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.verify.call_count, 2)

    def test_apply_atomically_replaces_and_preserves_backup(self):
        with self.dsn.open('rb') as previous_inode:
            target.run('apply')
            self.assertEqual(previous_inode.read(), self.raw)
        self.assertEqual(self.dsn.read_bytes(), target.candidate(self.raw))
        self.assertEqual(self.backup.read_bytes(), self.raw)
        self.assertEqual(stat.S_IMODE(self.dsn.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        target.run('apply')
        self.assertEqual(self.backup.read_bytes(), self.raw)

    def test_auth_failure_prevents_write(self):
        self.verify.side_effect = [None, RuntimeError('authentication')]
        with self.assertRaises(RuntimeError): target.run('apply')
        self.assertEqual(self.dsn.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())

    def test_expired_owner_evidence_blocks_replacement(self):
        self.verify.side_effect = lambda *args: setattr(target, 'OWNER_ATTESTATION_DEADLINE', 0)
        with self.assertRaises(RuntimeError): target.run('apply')
        self.assertEqual(self.dsn.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())

    def test_expiry_during_backup_sync_removes_only_new_backup(self):
        with patch.object(target, 'sync_directory', side_effect=lambda *args: setattr(target, 'OWNER_ATTESTATION_DEADLINE', 0)):
            with self.assertRaises(RuntimeError): target.run('apply')
        self.assertEqual(self.dsn.read_bytes(), self.raw)
        self.assertFalse(self.backup.exists())

    def test_completed_repair_requires_intact_protected_rollback(self):
        target.run('apply')
        for data, mode in [(b'', 0o600), (self.raw, 0o640),
                           (self.raw.replace(b'encoded%40password', b'wrong'), 0o600)]:
            self.backup.write_bytes(data)
            self.backup.chmod(mode)
            with self.assertRaises(RuntimeError): target.run('check')
        self.backup.unlink()
        with self.assertRaises(FileNotFoundError): target.run('check')
        self.assertEqual(self.dsn.read_bytes(), target.candidate(self.raw))

    def test_post_install_failure_rolls_back(self):
        self.verify.side_effect = [None, None, RuntimeError('lost_connection')]
        with self.assertRaises(RuntimeError): target.run('apply')
        self.assertEqual(self.dsn.read_bytes(), self.raw)
        self.assertEqual(self.backup.read_bytes(), self.raw)

    def test_existing_backup_blocks_apply(self):
        self.backup.write_bytes(b'prior-backup')
        with self.assertRaises(RuntimeError): target.run('check')
        with self.assertRaises(RuntimeError): target.run('apply')
        self.assertEqual(self.dsn.read_bytes(), self.raw)
        self.assertEqual(self.backup.read_bytes(), b'prior-backup')

    def test_symlink_and_fence_fail_closed(self):
        target.FENCE.touch()
        with self.assertRaises(RuntimeError): target.run('apply')
        target.FENCE.unlink()
        actual = self.directory/'other'
        self.dsn.rename(actual)
        self.dsn.symlink_to(actual)
        with self.assertRaises(RuntimeError): target.run('apply')
        self.assertEqual(actual.read_bytes(), self.raw)

    def test_check_does_not_create_missing_lock(self):
        target.LOCK_FILE.unlink()
        with self.assertRaises(FileNotFoundError): target.run('check')
        self.assertFalse(target.LOCK_FILE.exists())
        self.assertEqual(self.dsn.read_bytes(), self.raw)

    def test_already_production_still_requires_safe_tls(self):
        self.dsn.write_bytes(target.candidate(self.raw).replace(b'channel_binding=require', b'channel_binding=disable'))
        with self.assertRaises(RuntimeError): target.run('check')
        self.verify.assert_not_called()

    def test_candidate_rejects_destination_override_and_preserves_password(self):
        self.assertIn(b'encoded%40password@'+target.TARGET_HOST.encode(), target.candidate(self.raw))
        for suffix in [
            b'&host=evil.example', b'&host=', b'&options=endpoint%3Devil',
            b'&options=', b'&sslmode=disable', b'&sslmode=',
            b'&channel_binding=', b'&service=other', b'&service=',
        ]:
            with self.assertRaises(RuntimeError): target.candidate(self.raw+suffix)
        with self.assertRaises(RuntimeError): target.candidate(self.raw.replace(target.SOURCE_HOST.encode(), b'evil.example'))
        for value in [b'disable', b'prefer', b'']:
            with self.assertRaises(RuntimeError): target.candidate(self.raw.replace(b'channel_binding=require', b'channel_binding='+value))
        with self.assertRaises(RuntimeError): target.candidate(self.raw.replace(b'&channel_binding=require', b''))

    def test_failure_logging_omits_exception_text(self):
        out = io.StringIO()
        with patch.object(target, 'run', side_effect=RuntimeError('secret-password')), patch.object(sys, 'argv', ['target','check','1']), contextlib.redirect_stdout(out):
            self.assertEqual(target.main(), 1)
        self.assertNotIn('secret-password', out.getvalue())

    def test_live_query_contract_rejects_wrong_branch_busy_and_privileged_roles(self):
        for branch, busy, privileged, extra_role, safe_acl, queue_acl, current_version, should_pass in [
            (target.PRODUCTION, False, False, False, True, True, True, True),
            (target.PREVIEW, False, False, False, True, True, True, False),
            (target.PRODUCTION, True, False, False, True, True, True, False),
            (target.PRODUCTION, False, True, False, True, True, True, False),
            (target.PRODUCTION, False, False, True, True, True, True, False),
            (target.PRODUCTION, False, False, False, False, True, True, False),
            (target.PRODUCTION, False, False, False, True, False, True, False),
            (target.PRODUCTION, False, False, False, True, True, False, False),
        ]:
            with self.subTest(branch=branch, busy=busy, privileged=privileged):
                calls = []
                class Connection:
                    read_only = False
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def cursor(self): return self
                    def execute(self, sql):
                        if not self.read_only: raise AssertionError('not read-only')
                        calls.append(sql)
                    def fetchone(self):
                        if calls[-1] == target.QUEUE_DIGEST_SQL: return target.EXPECTED_QUEUE_DIGEST
                        if calls[-1] == target.QUEUE_SEQUENCE_SQL: return (True,)
                        if calls[-1] == target.QUEUE_CAPABILITIES_SQL: return target.EXPECTED_QUEUE_CAPABILITIES
                        if calls[-1] == target.QUEUE_OBJECT_SECURITY_SQL: return target.EXPECTED_QUEUE_OBJECT_SECURITY
                        if calls[-1] == target.QUEUE_SECURITY_SQL: return target.EXPECTED_QUEUE_SECURITY
                        if 'SELECT *' in calls[-1]: return (1 if busy else 0, 0)
                        if 'pg_get_functiondef' in calls[-1]:
                            return target.EXPECTED_QUEUE_VERSION if current_version else ('old-definition','old-constraints',2)
                        if 'has_any_column_privilege' in calls[-1]:
                            return (queue_acl, target.ALLOWED_QUEUE_FUNCTIONS, ['batch_status','job_status'])
                        return (target.PROJECT, branch, 'neondb', target.USER, True, True, privileged,
                                ['bridge_school_app', 'bridge_school_reader', 'bridge_school_worker', 'other'] if extra_role else ['bridge_school_app', 'bridge_school_reader', 'bridge_school_worker'],
                                False, ['bridge_school_worker'], safe_acl)
                with patch.object(target.psycopg, 'connect', return_value=Connection(), create=True):
                    if should_pass: real_verify(target.candidate(self.raw), target.PRODUCTION)
                    else:
                        with self.assertRaises(RuntimeError): real_verify(target.candidate(self.raw), target.PRODUCTION)


if __name__ == '__main__':
    unittest.main()
