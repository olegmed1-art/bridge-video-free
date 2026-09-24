import json
import unittest
from unittest.mock import patch
from pathlib import Path
from ops import oracle_light_credential_recovery as remote
from ops import oracle_light_neon_recovery_runner as runner

class RecoveryContract(unittest.TestCase):
    def test_replaces_only_one_credential_and_preserves_unrelated_values(self):
        old = (b'# pinned comment\nAUTOPILOT_DB_BACKEND=neon\n'
               b'AUTOPILOT_DATABASE_URL=postgresql://autopilot_light_worker_login:test_old_password@'
               b'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb?sslmode=require\n'
               b'AUTOPILOT_WORKER_ID=oracle-autopilot-light-1\n')
        previous, candidate, written = remote.replace_dsn(old,'new+secret/!')
        self.assertIn('test_old_password@', previous)
        self.assertNotIn('test_old_password@', candidate)
        self.assertEqual(remote.env_values(written)['AUTOPILOT_DATABASE_URL'], candidate)
        self.assertEqual(written.splitlines()[0],old.splitlines()[0])
        self.assertEqual(written.splitlines()[-1],old.splitlines()[-1])
        self.assertNotIn(b'new+secret/!',written) # percent-encoded secret

    def test_refuses_wrong_role_host_and_duplicate_assignment(self):
        raw=('AUTOPILOT_DATABASE_URL=postgresql://autopilot_light_worker_login:test_old_password@'
             'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb\n').encode()
        for bad in (raw.replace(b'autopilot_light_worker_login',b'neondb_owner'),
                    raw.replace(b'ep-noisy-pine-',b'ep-wrong-pine-'),raw+raw):
            with self.subTest(bad=bad[:20]),self.assertRaises(remote.Blocked):
                remote.replace_dsn(bad,'new')

    def test_rejects_password_with_newline_and_unchanged_password(self):
        raw=('AUTOPILOT_DATABASE_URL=postgresql://autopilot_light_worker_login:test_old_password@'
             'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb\n').encode()
        for bad in ('bad\ncredential','test_old_password'):
            with self.assertRaises(remote.Blocked):
                remote.replace_dsn(raw,bad)

    def test_failures_contain_hold_and_rollback(self):
        # Inject failures at every mutation boundary; no path may report a
        # successful recovery or leave the installed admission ACTIVE.
        for boundary in ('first_stop','hold_write','daemon_reload','post_start'):
            with self.subTest(boundary=boundary):
                state={'active':True,'drop':b'ACTIVE','env':b'old','failure_used':False}
                def run(command,*args):
                    if command=='stop':
                        if boundary=='first_stop' and not state['failure_used']:
                            state['failure_used']=True
                            raise RuntimeError('injected')
                        state['active']=False
                    elif command=='daemon-reload' and boundary=='daemon_reload' and not state['failure_used']:
                        state['failure_used']=True
                        raise RuntimeError('injected')
                    elif command=='start':
                        state['active']=True
                def atomic(path,content,mode,suffix):
                    if suffix=='hold' and boundary=='hold_write' and not state['failure_used']:
                        state['failure_used']=True
                        raise RuntimeError('injected')
                    state['drop' if path==remote.DROP else 'env']=content
                def service():
                    return {'ActiveState':'active' if state['active'] else 'inactive',
                            'MainPID':'123' if state['active'] else '0'}
                def validate(st,mode,active):
                    self.assertEqual(st['ActiveState']=='active',active)
                    self.assertEqual(state['drop'],b'HOLD')
                    if active and boundary=='post_start':
                        raise RuntimeError('post-start verification failed')
                def read_file(path,*args):
                    return state['drop' if path==remote.DROP else 'env']
                with patch.object(remote,'run',side_effect=run),patch.object(remote,'atomic',side_effect=atomic), \
                     patch.object(remote,'service',side_effect=service), \
                     patch.object(remote,'validate_state',side_effect=validate), \
                     patch.object(remote,'read_file',side_effect=read_file), \
                     patch.object(remote,'check_login',return_value=None), \
                     patch.object(remote,'env_values',return_value={'AUTOPILOT_DATABASE_URL':'candidate'}):
                    with self.assertRaises(Exception):
                        remote.transition(b'ACTIVE',b'HOLD',b'old',b'new','candidate')
                self.assertFalse(state['active'])
                self.assertEqual(state['drop'],b'HOLD')
                self.assertEqual(state['env'],b'old')

    def test_unverified_containment_is_distinct(self):
        def failed_stop(*args):
            raise RuntimeError('systemctl unavailable')
        with patch.object(remote,'run',side_effect=failed_stop):
            with self.assertRaisesRegex(remote.Blocked,'CONTAINMENT_UNVERIFIED'):
                remote.transition(b'ACTIVE',b'HOLD',b'old',b'new','candidate')

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
