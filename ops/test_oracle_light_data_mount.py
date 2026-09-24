import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
sys.path.insert(0, str(Path(__file__).parent))
import oracle_light_data_mount as target


class DiskSafety(unittest.TestCase):
    def check_disk(self, **changes):
        disk = dict(path='/dev/sdb',size=target.SIZE,type='disk',fstype=None,
                    mountpoints=[None],uuid=None,serial='new-data-disk')
        disk.update(changes)
        device=Mock()
        device.resolve.return_value=Path('/dev/sdb')
        device.stat.return_value=NS(st_mode=stat.S_IFBLK)
        with patch.object(target,'DEVICE',device), patch.object(target,'run',
                return_value=json.dumps({'blockdevices':[disk]})):
            return target.disk_inventory()

    def test_empty_data_disk_accepted(self):
        self.assertEqual(self.check_disk()['serial'],'new-data-disk')

    def test_root_partitioned_disk_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_disk(children=[dict(path='/dev/sdb1',mountpoints=['/'])])

    def test_wrong_size_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_disk(size=47*1024**3)

    def test_existing_other_mount_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_disk(mountpoints=['/existing-data'])

    def test_missing_identity_rejected(self):
        with self.assertRaises(AssertionError):
            self.check_disk(serial=None)

    def test_unit_binds_uuid(self):
        text=target.unit_text('f00')
        self.assertIn('What=/dev/disk/by-uuid/f00\n',text)
        self.assertIn('Where=/srv/autopilot-data\n',text)


if __name__=='__main__':
    unittest.main()
