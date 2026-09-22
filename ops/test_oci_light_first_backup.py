"""Offline safety cases: the real OCI API is never contacted."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import oci_light_first_backup as target


def backup(state='AVAILABLE', **kw):
    values = dict(id='backup', boot_volume_id='boot', display_name=target.NAME,
                  lifecycle_state=state, freeform_tags={'managed_by': target.TOKEN})
    values.update(kw)
    return NS(**values)


class Safety(unittest.TestCase):
    def invoke(self, existing=(), count=1, other='FAULTY', states=('AVAILABLE',), clock=None):
        client = Mock()
        client.create_boot_volume_backup.return_value = NS(data=backup('CREATING'))
        client.get_boot_volume_backup.side_effect = [NS(data=backup(s)) for s in states]
        fake_oci = NS(pagination=NS(list_call_get_all_results=Mock(return_value=NS(data=list(existing)))),
                      core=NS(models=NS(CreateBootVolumeBackupDetails=lambda **kw: NS(**kw))))
        inventory = {'allocated_gb': 47, 'backup_count': count, 'backups': [{'state': other}]}
        with patch.dict(sys.modules, {'oci': fake_oci}), patch.object(target, 'audit', return_value=(client, 'boot', inventory)), patch.object(target.time, 'sleep'), patch('builtins.print'):
            if clock is None:
                target.main()
            else:
                with patch.object(target.time, 'monotonic', side_effect=clock):
                    target.main()
        return client

    def test_create_once(self):
        c = self.invoke(states=('CREATING', 'AVAILABLE'))
        c.create_boot_volume_backup.assert_called_once()
        args, kwargs = c.create_boot_volume_backup.call_args
        self.assertEqual(vars(args[0]), {'boot_volume_id': 'boot', 'display_name': target.NAME,
                         'type': 'FULL', 'freeform_tags': {'managed_by': target.TOKEN}})
        self.assertEqual(kwargs, {'opc_retry_token': target.TOKEN})

    def test_resume(self):
        for state in ('AVAILABLE', 'CREATING'):
            with self.subTest(state=state):
                c = self.invoke(existing=[backup(state)], states=('CREATING', 'AVAILABLE'))
                c.create_boot_volume_backup.assert_not_called()

    def test_reject_scope_or_quota(self):
        cases = [dict(existing=[backup(boot_volume_id='other')]),
                 dict(existing=[backup(freeform_tags={})]),
                 dict(existing=[backup(), backup()]), dict(count=5), dict(other='CREATING')]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(AssertionError):
                self.invoke(**case)

    def test_failure_or_deadline(self):
        for state in ('FAULTY', 'TERMINATING'):
            with self.subTest(state=state), self.assertRaises(AssertionError):
                self.invoke(states=(state,))
        with self.assertRaises(TimeoutError):
            self.invoke(clock=[0, 901])


if __name__ == '__main__':
    unittest.main()
