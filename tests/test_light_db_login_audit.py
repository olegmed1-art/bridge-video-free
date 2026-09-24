"""Credential probe contracts: no secret on argv/stdout and fail closed."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import oracle_light_db_runtime_audit as audit


class LightLoginAuditTests(unittest.TestCase):
    def setUp(self):
        self.role = patch.object(audit.pwd, 'getpwnam',
                                 return_value=SimpleNamespace(pw_gid=1001, pw_uid=1001))
        self.role.start()
        self.addCleanup(self.role.stop)

    def trust_temporary_file_owner(self):
        real_fstat = os.fstat
        self.enterContext(patch.object(audit.os, 'fstat',
            side_effect=lambda fd: os.stat_result(
                (*real_fstat(fd)[:4], 0, *real_fstat(fd)[5:]))))

    @patch.object(audit.subprocess, 'run')
    def test_connect_uses_service_uid_and_read_only_session_without_argv_secret(self, run):
        secret = 'postgresql://autopilot_light_worker_login:secret-example@host/neondb'
        run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({
            'user': 'autopilot_light_worker_login',
            'database': 'neondb', 'read_only': 'on'}), stderr='')
        audit.verify_production_login(secret)
        args, kwargs = run.call_args
        self.assertNotIn(secret, repr(args))
        self.assertEqual(kwargs['env']['AUDIT_DATABASE_URL'], secret)
        self.assertTrue(kwargs['capture_output'])
        self.assertEqual(kwargs['cwd'], '/')
        self.assertIn("default_transaction_read_only=on", audit.READ_ONLY_LOGIN)

    @patch.object(audit.subprocess, 'run')
    def test_rejected_connection_does_not_expose_provider_error(self, run):
        secret = 'postgresql://role:secret-example@host/neondb'
        run.return_value = SimpleNamespace(
            returncode=2, stdout='{"error_code":"AUTHENTICATION_FAILED"}',
            stderr='password=secret-example rejected')
        with self.assertRaises(audit.AuditFailure) as result:
            audit.verify_production_login(secret)
        self.assertEqual(str(result.exception), 'AUTHENTICATION_FAILED')
        self.assertNotIn('secret-example', str(result.exception))

    def test_live_secret_source_reports_only_match_or_drift(self):
        self.trust_temporary_file_owner()
        dsn = 'postgresql://role@host/neondb?sslmode=require'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'light.env'
            path.write_text('AUTOPILOT_DATABASE_URL="' + dsn + '"\n')
            os.chmod(path, 0o600)
            self.assertTrue(audit.live_credential_matches_disk(dsn, path))
            self.assertFalse(audit.live_credential_matches_disk(dsn + 'x', path))
            self.assertFalse(audit.live_credential_matches_disk('different', path))

    def test_duplicate_or_insecure_source_fails_closed(self):
        self.trust_temporary_file_owner()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'light.env'
            path.write_text('AUTOPILOT_DATABASE_URL=private\nAUTOPILOT_DATABASE_URL=private\n')
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(audit.AuditFailure, 'ENV_FILE_INVALID'):
                audit.live_credential_matches_disk('private', path)
            path.write_text('AUTOPILOT_DATABASE_URL=private\n')
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(audit.AuditFailure, 'ENV_FILE_UNTRUSTED'):
                audit.live_credential_matches_disk('private', path)

    def test_unrecognized_systemd_source_is_counted_without_path_leak(self):
        output = ('EnvironmentFiles=/etc/school-autopilot-production-light.env (ignore_errors=no)\n'
                  'EnvironmentFiles=/arbitrary/dont-display-this (ignore_errors=no)\n')
        layout = audit.environment_source_layout(output)
        self.assertEqual(layout, {'entries': 2, 'expected_primary': True,
                                  'expected_pin': False,
                                  'unknown_entries': 1})
        self.assertNotIn('/arbitrary/', json.dumps(layout))

    def test_known_broker_pin_file_has_no_database_override(self):
        self.trust_temporary_file_owner()
        source = (f'EnvironmentFiles={audit.LIGHT_ENV_FILE} (ignore_errors=no)\n'
                  f'EnvironmentFiles={audit.PIN_ENV_FILE} (ignore_errors=no)\n')
        self.assertEqual(audit.environment_source_layout(source),
                         {'entries': 2, 'expected_primary': True,
                          'expected_pin': True, 'unknown_entries': 0})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'broker-hold.env'
            path.write_text(''.join(key + '=example\n' for key in sorted(audit.PIN_KEYS)))
            os.chmod(path, 0o644)
            audit.verify_broker_pin_file(path)
            path.write_text(path.read_text() + 'AUTOPILOT_DATABASE_URL=override\n')
            with self.assertRaisesRegex(audit.AuditFailure, 'PIN_FILE_INVALID'):
                audit.verify_broker_pin_file(path)


if __name__ == '__main__':
    unittest.main()
