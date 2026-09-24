from types import SimpleNamespace as NS
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parent))
import oci_light_candidate_backup_store as target


class BucketSafety(unittest.TestCase):
    def fixture(self):
        return dict(compartment_id=target.TENANCY,freeform_tags={'managed_by':target.TAG},
                    public_access_type='NoPublicAccess',storage_tier='Standard',versioning='Disabled',
                    auto_tiering='Disabled',is_read_only=False,kms_key_id=None)

    def test_private_standard_bucket(self):
        target.validate_bucket(NS(**self.fixture()))

    def test_reject_unsafe_or_costly_bucket_drift(self):
        for key,value in [('compartment_id','other'),('freeform_tags',{}),
                          ('public_access_type','ObjectRead'),('storage_tier','Archive'),
                          ('versioning','Enabled'),('auto_tiering','InfrequentAccess'),
                          ('is_read_only',True),('kms_key_id','custom-key')]:
            with self.subTest(key=key):
                item=self.fixture()
                item[key]=value
                with self.assertRaises(AssertionError):
                    target.validate_bucket(NS(**item))


if __name__=='__main__':
    unittest.main()
