import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'ops/oracle_light_0355_admin.py'
spec = importlib.util.spec_from_file_location('light_admin', PATH)
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class AdminBoundary(TestCase):
    def test_hold_is_pinned_to_existing_root_dropin(self):
        import hashlib
        self.assertEqual(hashlib.sha256(admin.HOLD).hexdigest(), admin.DROP_SHA)
        self.assertEqual(admin.ACTIVE.count(b'ADMISSION_MODE=ACTIVE'), 1)
        self.assertNotIn(b'ADMISSION_MODE=HOLD', admin.ACTIVE)

    def test_stop_rejects_wrong_invocation_without_mutation(self):
        state = {'ActiveState':'active','SubState':'running','MainPID':'29894',
                 'InvocationID':'a'*32}
        with patch.object(admin.os, 'geteuid', return_value=0), \
             patch.object(admin.os, 'uname') as uname, \
             patch.object(admin.sys, 'argv', ['script','stop','b'*32]), \
             patch.object(admin, 'pinned_route'), patch.object(admin, 'read_pinned',
                   side_effect=[b'unit',admin.HOLD]), \
             patch.object(admin, 'show', return_value=state), \
             patch.object(admin, 'common'), patch.object(admin, 'run') as run, \
             patch.object(admin.Path, 'open') as opened, \
             patch.object(admin.fcntl, 'flock'):
            uname.return_value.nodename = 'autopilot-lite-vnic'
            opened.return_value.__enter__.return_value = object()
            with self.assertRaisesRegex(RuntimeError, 'INVOCATION_DRIFT'):
                admin.main()
        run.assert_not_called()

    def test_activate_rejects_non_stopped_worker(self):
        state = {'ActiveState':'active','MainPID':'29894'}
        with patch.object(admin.os, 'geteuid', return_value=0), \
             patch.object(admin.os, 'uname') as uname, \
             patch.object(admin.sys, 'argv', ['script','activate','0'*32]), \
             patch.object(admin, 'pinned_route'), patch.object(admin, 'read_pinned',
                   side_effect=[b'unit',admin.HOLD]), \
             patch.object(admin, 'show', return_value=state), \
             patch.object(admin, 'common'), patch.object(admin, 'install_active') as install, \
             patch.object(admin.Path, 'open') as opened, \
             patch.object(admin.fcntl, 'flock'):
            uname.return_value.nodename = 'autopilot-lite-vnic'
            opened.return_value.__enter__.return_value = object()
            with self.assertRaisesRegex(RuntimeError, 'STOP_BOUNDARY_NOT_ATTESTED'):
                admin.main()
        install.assert_not_called()
