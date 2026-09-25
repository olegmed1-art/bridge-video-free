import json
import unittest
from pathlib import Path
from unittest.mock import patch
from ops import oracle_light_active_hold_attest as attest


class ActiveHoldContract(unittest.TestCase):
    def test_env_rejects_duplicate_and_only_returns_values_in_memory(self):
        raw=b'AUTOPILOT_DATABASE_URL=postgresql://a:private-secret@host/db\n'
        self.assertEqual(attest.env(raw)['AUTOPILOT_DATABASE_URL'],
                         'postgresql://a:private-secret@host/db')
        with self.assertRaises(attest.Blocked):
            attest.env(raw+raw)

    def test_login_child_uses_read_only_session_and_checks_empty_queue(self):
        self.assertIn('default_transaction_read_only=on',attest.CHILD)
        self.assertIn("SELECT count(*) FROM autopilot.task_status",attest.CHILD)
        self.assertIn("current_setting('transaction_read_only')",attest.CHILD)

    def test_bad_login_does_not_output_dsn(self):
        with patch.object(attest.pwd,'getpwnam') as user, \
             patch.object(attest.subprocess,'run') as run:
            user.return_value.pw_gid=1000
            user.return_value.pw_uid=1000
            run.return_value.returncode=2
            run.return_value.stdout=json.dumps({'ok':False})
            with self.assertRaisesRegex(attest.Blocked,'LOGIN_OR_QUEUE_FAILED'):
                attest.login('postgresql://user:private-secret@host/db')

    def test_workflow_manual_probe_requires_owner_and_exact_main(self):
        text=Path('.github/workflows/oracle-light-active-hold-attest.yml').read_text()
        self.assertIn('needs: contract',text)
        self.assertIn("github.ref == 'refs/heads/main'",text)
        self.assertIn("github.event_name == 'push'",text)
        self.assertIn("github.event_name == 'workflow_dispatch'",text)
        self.assertIn('github.actor == github.repository_owner',text)
        self.assertIn('inputs.expected_main_sha == github.sha',text)
        self.assertNotIn('NEON_API_KEY',text)


if __name__=='__main__':
    unittest.main()
