import json
import unittest
from unittest.mock import patch
from pathlib import Path
from ops import oracle_light_credential_recovery as remote
from ops import oracle_light_neon_recovery_runner as runner

class RecoveryContract(unittest.TestCase):
    def test_replaces_only_one_credential_and_preserves_unrelated_values(self):
        old = (b'# pinned comment\nAUTOPILOT_DB_BACKEND=neon\n'
               b'AUTOPILOT_DATABASE_URL=postgresql://autopilot_light_worker_login:old@'
               b'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require\n'
               b'AUTOPILOT_WORKER_ID=oracle-autopilot-light-1\n')
        previous, candidate, written = remote.replace_dsn(old,'new+secret/!')
        self.assertIn('old@', previous)
        self.assertNotIn('old@', candidate)
        self.assertEqual(remote.env_values(written)['AUTOPILOT_DATABASE_URL'], candidate)
        self.assertEqual(written.splitlines()[0],old.splitlines()[0])
        self.assertEqual(written.splitlines()[-1],old.splitlines()[-1])
        self.assertNotIn(b'new+secret/!',written) # percent-encoded secret

    def test_refuses_wrong_role_host_and_duplicate_assignment(self):
        raw=('AUTOPILOT_DATABASE_URL=postgresql://autopilot_light_worker_login:old@'
             'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb\n').encode()
        for bad in (raw.replace(b'autopilot_light_worker_login',b'neondb_owner'),
                    raw.replace(b'ep-noisy-pine-',b'ep-wrong-pine-'),raw+raw):
            with self.subTest(bad=bad[:20]),self.assertRaises(remote.Blocked):
                remote.replace_dsn(bad,'new')

    def test_rejects_password_with_newline_and_unchanged_password(self):
        raw=('AUTOPILOT_DATABASE_URL=postgresql://autopilot_light_worker_login:old@'
             'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb\n').encode()
        for bad in ('bad\ncredential','old'):
            with self.assertRaises(remote.Blocked):
                remote.replace_dsn(raw,bad)

    def test_runner_neon_412_never_ssh_mutation(self):
        from urllib.error import HTTPError
        with patch.dict('os.environ',{'NEON_API_KEY':'test'}),patch.object(runner,'urlopen',side_effect=HTTPError('url',412,'',None,None)):
            with self.assertRaisesRegex(runner.Blocked,'PASSWORD_STORAGE_DISABLED'):
                runner.retrieve()

    def test_workflow_manual_and_protected(self):
        content=Path('.github/workflows/oracle-light-neon-credential-recovery.yml').read_text()
        self.assertIn('workflow_dispatch:',content)
        self.assertNotIn('  push:',content)
        self.assertIn('environment: database-production',content)
        self.assertIn("github.ref == 'refs/heads/main'",content)
        self.assertIn('group: oracle-light-backup-mutation',content)

if __name__=='__main__':
    unittest.main()
