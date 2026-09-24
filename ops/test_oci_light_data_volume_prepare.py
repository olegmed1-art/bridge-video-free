import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
sys.path.insert(0, str(Path(__file__).parent))
import oci_light_data_volume_prepare as target


class Safety(unittest.TestCase):
    def run_case(self, allocated=47, existing=False, good_backup=True, vpu=10):
        self.client = Mock()
        source = NS(availability_domain='ad')
        volume = NS(id='new-volume', display_name=target.NAME, lifecycle_state='AVAILABLE',
                    freeform_tags={'managed_by': target.TOKEN}, size_in_gbs=50, availability_domain='ad', vpus_per_gb=vpu)
        saved = NS(boot_volume_id='boot', display_name=target.BACKUP_NAME,
                   freeform_tags={'managed_by': target.BACKUP_TOKEN},
                   lifecycle_state='AVAILABLE' if good_backup else 'FAULTY')
        self.client.get_boot_volume.return_value = NS(data=source)
        self.client.create_volume.return_value = NS(data=volume)
        self.client.get_volume.return_value = NS(data=volume)
        fake = NS(pagination=NS(list_call_get_all_results=Mock(side_effect=[NS(data=[saved]), NS(data=[volume] if existing else [])])),
                  core=NS(models=NS(CreateVolumeDetails=lambda **kw: NS(**kw))))
        with patch.dict(sys.modules, {'oci': fake}), patch.object(target, 'audit', return_value=(self.client,'boot',{'allocated_gb':allocated})), patch('builtins.print'):
            target.main()

    def test_create_one_fixed_volume(self):
        self.run_case()
        self.client.create_volume.assert_called_once()
        args, kw = self.client.create_volume.call_args
        self.assertEqual(args[0].size_in_gbs, 50)
        self.assertEqual(args[0].vpus_per_gb, 10)
        self.assertEqual(kw['opc_retry_token'], target.TOKEN)

    def test_resume_no_create(self):
        self.run_case(allocated=97, existing=True)
        self.client.create_volume.assert_not_called()

    def test_keep_restore_reserve(self):
        with self.assertRaises(AssertionError):
            self.run_case(allocated=51)
        self.client.create_volume.assert_not_called()

    def test_no_good_backup_no_creation(self):
        with self.assertRaises(AssertionError):
            self.run_case(good_backup=False)
        self.client.create_volume.assert_not_called()

    def test_wrong_vpu_resume_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_case(allocated=97, existing=True, vpu=20)
        self.client.create_volume.assert_not_called()


if __name__ == '__main__':
    unittest.main()
