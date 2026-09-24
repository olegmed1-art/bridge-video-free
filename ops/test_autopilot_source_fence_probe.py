import unittest

from ops import autopilot_source_fence_probe as probe


class SourceFenceProbe(unittest.TestCase):
    def test_dsn_is_pinned_to_owner_source(self):
        valid=('postgresql://neondb_owner:synthetic-password@'+probe.SOURCE_HOST+
               '/neondb?sslmode=verify-full&channel_binding=require')
        self.assertEqual(probe.validate_dsn(valid),valid)
        for invalid in (valid.replace(probe.SOURCE_HOST,'attacker.invalid'),
                        valid.replace('neondb_owner','autopilot_callback_login'),
                        valid.replace('/neondb','/other'),
                        valid.replace('sslmode=verify-full','sslmode=disable'),
                        valid.replace('sslmode=verify-full','sslmode=require'),
                        valid.replace('channel_binding=require','channel_binding=disable'),
                        valid+'&host=attacker.invalid', valid+'&sslmode=disable',
                        valid+'#fragment', valid+'\n',
                        valid.replace('synthetic-password','synthetic%0Apassword')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                probe.validate_dsn(invalid)

    def test_only_complete_effective_fence_passes(self):
        clear={principal:(2,1,0,0,0,0) for principal in probe.PRINCIPALS}
        result=probe.assess(clear)
        self.assertTrue(result['application_principals_fenced'])
        self.assertFalse(result['full_cutover_ready'])
        for principal in probe.PRINCIPALS:
            for index in range(2,6):
                changed=clear.copy()
                value=list(changed[principal]); value[index]=1
                changed[principal]=tuple(value)
                with self.subTest(principal=principal,index=index):
                    self.assertFalse(probe.assess(changed)['application_principals_fenced'])
        for changed in ({probe.PRINCIPALS[0]:clear[probe.PRINCIPALS[0]]},
                        {**clear,probe.PRINCIPALS[0]:(1,1,0,0,0,0)},
                        {**clear,probe.PRINCIPALS[0]:(2,0,0,0,0,0)}):
            with self.assertRaises(ValueError):
                probe.assess(changed)


if __name__=='__main__':
    unittest.main()
