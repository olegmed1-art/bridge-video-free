import unittest
from unittest.mock import patch
import diagnostic_guest as c

class ParserTests(unittest.TestCase):
 def test_uuid_and_fsck_names_are_consistent(self):
  self.assertEqual(c.FSCK,r'systemd-fsck@dev-disk-by\x2duuid-11111111\x2d2222\x2d3333\x2d4444\x2d555555555555.service')
 def test_fstab_only_relevant_rows_no_secret_options(self):
  raw=('UUID='+c.UUID+' /mnt/bridge-scratch ext4 defaults,nofail,x-systemd.device-timeout=90s,password=SECRET 0 2\n//secret/share /other cifs password=OTHER 0 0\n').encode()
  r=c.fstab_rows(raw);self.assertEqual(len(r['matching_rows']),1);self.assertNotIn('SECRET',str(r));self.assertNotIn('OTHER',str(r));self.assertEqual(r['matching_rows'][0]['pass'],2)
 def test_unrelated_mount_source_redacted(self):
  self.assertEqual(c.source('server:user:SECRET/share'),'NONLOCAL_OR_REDACTED_SOURCE')
 def test_coverage_requires_path_boundary(self):
  rows=[{'target':'/','fstype':'ext4'},{'target':'/mnt/scratch','fstype':'xfs'}]
  self.assertEqual(c.covering('/mnt/scratch2/file',rows)['target'],'/')
  self.assertEqual(c.covering('/mnt/scratch/file',rows)['target'],'/mnt/scratch')
 def test_mountinfo_omits_options(self):
  r=c.mounts('1 0 8:1 / / rw,password=SECRET - ext4 /dev/vda1 rw,secret=VALUE')
  self.assertNotIn('SECRET',str(r));self.assertNotIn('VALUE',str(r));self.assertEqual(r[0]['source'],'/dev/vda1')
 def test_unknown_properties_rejected(self):
  with patch.object(c,'command',return_value='Id=x.service\nNames=x.service\nEnvironment=SECRET'):
   with self.assertRaises(AssertionError):c.units({'x.service'})
 def test_duplicate_property_rejected(self):
  with patch.object(c,'command',return_value='Id=x.service\nId=x.service\nNames=x.service'):
   with self.assertRaises(AssertionError):c.units({'x.service'})
 def test_device_alias_resolves_requested_identity(self):
  with patch.object(c,'command',return_value='Id=dev-vdb.device\nNames=dev-vdb.device alias.device\nLoadState=loaded'):
   self.assertEqual(list(c.units({'alias.device'})),['dev-vdb.device'])
 def test_autofs_is_never_lstat_traversed(self):
  with patch.object(c.os,'lstat',side_effect=AssertionError('must not stat')):
   r=c.resolve_without_automount('/mnt/file',[{'target':'/','fstype':'autofs'}]);self.assertEqual(r['reason'],'AUTOFS_NOT_TRAVERSED')

if __name__=='__main__':unittest.main()
