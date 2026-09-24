"""Credential probe contracts: no secret on argv/stdout and fail closed."""
import json
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


if __name__ == '__main__':
    unittest.main()
