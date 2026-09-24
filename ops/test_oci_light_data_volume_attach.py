import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
sys.path.insert(0, str(Path(__file__).parent))
import oci_light_data_volume_attach as target


class Safety(unittest.TestCase):
    def run_case(self, existing=False, foreign=False, collision=False, encrypted=True, available=True):
        self.compute = Mock()
        block = Mock()
        block.get_boot_volume.return_value = NS(data=NS(availability_domain='ad'))
        volume = NS(id='volume', display_name=target.NAME, lifecycle_state='AVAILABLE',
                    freeform_tags={'managed_by': target.TOKEN}, size_in_gbs=50,
                    vpus_per_gb=10, availability_domain='ad')
        attachment = NS(id='attachment', instance_id='foreign' if foreign else target.LIGHT,
                        volume_id='volume', device=target.DEVICE, lifecycle_state='ATTACHED',
                        is_read_only=False, is_shareable=False, attachment_type='paravirtualized',
                        is_pv_encryption_in_transit_enabled=encrypted)
        attachments = [attachment] if existing else []
        if collision:
            attachments.append(NS(instance_id=target.LIGHT, volume_id='other',
                                  device=target.DEVICE, lifecycle_state='ATTACHED'))
        responses = [NS(data=[volume]), NS(data=attachments),
                     NS(data=[NS(name=target.DEVICE, is_available=available)])]
        fake = NS(pagination=NS(list_call_get_all_results=Mock(side_effect=responses)),
                  core=NS(models=NS(AttachParavirtualizedVolumeDetails=lambda **kw: NS(**kw))))
        self.compute.attach_volume.return_value = NS(data=attachment)
        self.compute.get_volume_attachment.return_value = NS(data=attachment)
        env = dict(OCI_USER='user', OCI_TENANCY=target.TENANCY, OCI_FINGERPRINT='fp',
                   OCI_REGION='eu-frankfurt-1', OCI_KEY='test-only')
        with patch.dict(sys.modules, {'oci': fake}), patch.dict(os.environ, env), \
             patch.object(target, 'audit', return_value=(block,'boot',{'allocated_gb':97})), \
             patch.object(target, 'clients', return_value=(self.compute,Mock())), patch('builtins.print'):
            target.main()

    def test_attach_exact_volume(self):
        self.run_case()
        args, kwargs = self.compute.attach_volume.call_args
        self.assertEqual(args[0].instance_id, target.LIGHT)
        self.assertEqual(args[0].volume_id, 'volume')
        self.assertTrue(args[0].is_pv_encryption_in_transit_enabled)
        self.assertEqual(kwargs['opc_retry_token'], target.TOKEN+'-attach')

    def test_resume_without_duplicate(self):
        self.run_case(existing=True)
        self.compute.attach_volume.assert_not_called()

    def test_foreign_attachment_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_case(existing=True, foreign=True)
        self.compute.attach_volume.assert_not_called()

    def test_device_collision_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_case(collision=True)
        self.compute.attach_volume.assert_not_called()

    def test_unavailable_device_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_case(available=False)
        self.compute.attach_volume.assert_not_called()

    def test_unencrypted_resume_rejected(self):
        with self.assertRaises(AssertionError):
            self.run_case(existing=True, encrypted=False)
        self.compute.attach_volume.assert_not_called()


if __name__ == '__main__':
    unittest.main()
