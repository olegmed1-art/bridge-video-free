"""Portable preparation and unapproved-scope guards; no root/PID1 execution."""
import contextlib,io,json,subprocess,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
import stagea_control as control,stagea_synthetic_core as owner
import qualification_suite as suite,ci_bootstrap as boot
ROOT=Path(__file__).resolve().parent
class Preparation(unittest.TestCase):
 def test_describe_only_defaults_create_nothing(self):
  for name in ("qualification_suite.py","ci_bootstrap.py"):
   result=subprocess.run([sys.executable,"-I","-B","-S",str(ROOT/name),"--describe"],
       stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
   self.assertEqual(result.returncode,0);value=json.loads(result.stdout)
   self.assertFalse(value["resources_created"]);self.assertFalse(value["root_pid1_executed"])
 def test_unapproved_bootstrap_never_stages_or_launches(self):
  with patch.object(sys,"argv",["bootstrap","--run"]),patch.object(boot,"stage",side_effect=AssertionError("staging")):
   with self.assertRaises(boot.Refused):boot.main()
 def test_unapproved_suite_never_starts_root_controller(self):
  with patch.object(sys,"argv",["suite","--run"]),patch.object(suite,"suite_main",side_effect=AssertionError("root")):
   with self.assertRaises(suite.Refused):suite.main()
 def test_runtime_or_launcher_mismatch_refuses(self):
  with self.assertRaises(boot.Refused):boot.read_checked(ROOT/"runtime-manifest.json","0"*64)
 def test_unbounded_or_hooked_control_profile_refuses_before_proc(self):
  unit="bridge-stagea-control-"+owner.SOURCE[:12]+"-123-1-0123456789abcdef.service"
  good={**control.PROPERTIES,"ReadOnlyPaths":str(ROOT)}
  for key,value in (("ExitType","main"),("RuntimeMaxUSec","infinity"),("RuntimeRandomizedExtraUSec","1s"),
                    ("ExecStop","unsafe-hook"),("ExecStopPost","unsafe-hook"),("CapabilityBoundingSet","cap_sys_admin"),
                    ("NoNewPrivileges","no"),("ReadOnlyPaths","/foreign")):
   with self.subTest(key=key),patch.object(control,"show",return_value={**good,key:value}):
    with self.assertRaises(owner.Refused):control.identity(unit)
 def test_foreign_worker_never_receives_emergency_stop(self):
  calls=[];unit="bridge-stagea-control-"+owner.SOURCE[:12]+"-123-1-0123456789abcdef.service"
  fake=types.SimpleNamespace(ctl=lambda *args:calls.append(args))
  lifetime=types.SimpleNamespace(UNIT=r"bridge-native-ro-[0-9a-f]{12}-[0-9]{1,20}-[0-9]{1,6}-[0-9a-f]{16}\.service")
  missing=ROOT/"nonexistent-fixture-evidence-file"
  with self.assertRaises(suite.Refused):
   suite.cleanup(fake,lifetime,unit,"unrelated-existing.service",None,missing,{"pid":123})
  self.assertTrue(calls);self.assertTrue(all(call[1]==unit for call in calls))
if __name__=="__main__":unittest.main()
