"""New synthetic-only portable tests; mocks never establish Linux qualification."""
import contextlib,errno,hashlib,io,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import stagea_synthetic_core as core,stagea_watchdog as w,stagea_control as control
import stagea_fixture_worker as fixture,stagea_capture as capture
ROOT=Path(__file__).resolve().parent
PIN=lambda:hashlib.sha256((ROOT/"runtime-manifest.json").read_bytes()).hexdigest()
class Pipe:
 def __init__(self,fd):self.fd=fd;self.closed=False
 def fileno(self):return self.fd
 def close(self):self.closed=True
class Process:
 def __init__(self):self.stdin=Pipe(10);self.stdout=Pipe(11);self.killed=False
 def wait(self,timeout):return 0
 def poll(self):return 0
 def kill(self):self.killed=True
class Lifetime:
 def __init__(self):self.events=[];self.empty_ok=True;self.identity_fail=False;self.code=""
 def new_unit(self,*args):self.events.append("new");return "synthetic-owned-unit"
 def loader(self,*args):return ""
 def command(self,unit,code,seconds):self.code=code;return ["/usr/bin/python3","-c",code]
 def identity(self,*args):
  self.events.append("identity")
  if self.identity_fail:raise RuntimeError("synthetic identity failure")
  return {},Path("/synthetic"),3
 def cleanup(self,*args):self.events.append("cleanup")
 def empty(self,*args):self.events.append("empty");return self.empty_ok
class Candidate(unittest.TestCase):
 def stack(self,lifetime,exchange):
  stack=contextlib.ExitStack()
  stack.enter_context(patch.object(sys,"platform","linux"))
  for name in ("getuid","getgid"):stack.enter_context(patch.object(os,name,return_value=0,create=True))
  for name,kwargs in (("source_guard",{"return_value":{}}),("_control_guard",{"side_effect":lambda **kw:w.time.monotonic()}),
    ("_rebase_deadline",{}),("arm_deadline",{}),("disarm_deadline",{}),("profile",{"return_value":lifetime}),
    ("exchange",{"side_effect":exchange})):stack.enter_context(patch.object(w,name,**kwargs))
  stack.enter_context(patch.object(w.subprocess,"Popen",return_value=Process()))
  import select
  stack.enter_context(patch.object(select,"select",return_value=([1],[],[])))
  stack.enter_context(patch.object(os,"read",return_value=b"READY\n"))
  return stack
 def invoke(self,**kwargs):
  return w.supervise("0"*64,kwargs.pop("wire",fixture.WIRE),core.SOURCE,"123-1",
   kwargs.pop("runtime",fixture.RUNTIME),qualification_mode=kwargs.pop("mode","payload"),**kwargs)
 def test_default_or_unknown_mode_refused_before_alarm(self):
  for mode in (None,"owner","arbitrary",1,[]):
   with self.subTest(mode=mode),patch.object(sys,"platform","linux"),patch.object(os,"getuid",return_value=0,create=True),patch.object(os,"getgid",return_value=0,create=True),patch.object(w,"arm_deadline") as alarm,patch.object(w.subprocess,"Popen") as process:
    with self.assertRaises(core.Refused):self.invoke(mode=mode)
    alarm.assert_not_called();process.assert_not_called()
 def test_actual_supervise_body_validates_identity_before_exchange(self):
  lifetime=Lifetime()
  def exchange(*args):self.assertIn("identity",lifetime.events);return fixture.payload()
  with self.stack(lifetime,exchange):
   result=self.invoke()
  self.assertEqual(result,fixture.payload());self.assertEqual(lifetime.events[-2:],["cleanup","empty"])
  self.assertIn("stagea_fixture_worker",lifetime.code)
 def test_unmanaged_control_refuses_before_worker(self):
  l=Lifetime()
  with self.stack(l,lambda *a:self.fail("exchange")),patch.object(w,"_control_guard",side_effect=core.Refused()):
   with self.assertRaises(core.Refused):self.invoke()
  self.assertNotIn("new",l.events)
 def test_other_wire_refused_before_worker(self):
  l=Lifetime()
  with self.stack(l,lambda *a:self.fail("exchange")):
   with self.assertRaises(core.Refused):self.invoke(wire=b"not-the-fixed-synthetic-wire")
  self.assertNotIn("new",l.events)
 def test_other_runtime_refused_before_worker(self):
  l=Lifetime()
  with self.stack(l,lambda *a:self.fail("exchange")):
   with self.assertRaises(core.Refused):self.invoke(runtime="0"*64)
  self.assertNotIn("new",l.events)
 def test_budget_uses_control_activation(self):
  l=Lifetime();deadlines=[]
  def exchange(proc,wire,deadline):deadlines.append(deadline);return fixture.payload()
  with self.stack(l,exchange),patch.object(w.time,"monotonic",return_value=101),patch.object(w,"_control_guard",return_value=100):
   self.invoke()
  self.assertEqual(deadlines,[156])
 def test_late_activation_refuses_worker(self):
  l=Lifetime()
  with self.stack(l,lambda *a:self.fail("exchange")),patch.object(w.time,"monotonic",return_value=104),patch.object(w,"_control_guard",return_value=100):
   with self.assertRaises(core.Refused):self.invoke()
  self.assertNotIn("new",l.events)
 def test_identity_failure_never_delivers_wire(self):
  l=Lifetime();l.identity_fail=True
  with self.stack(l,lambda *a:self.fail("exchange")):
   with self.assertRaises(core.Refused):self.invoke()
  self.assertIn("cleanup",l.events)
 def test_drain_failure_discards_payload(self):
  l=Lifetime();l.empty_ok=False
  with self.stack(l,lambda *a:fixture.payload()):
   with self.assertRaises(core.Refused):self.invoke()
 def test_exchange_failure_cleans_owned_unit(self):
  l=Lifetime()
  with self.stack(l,lambda *a:(_ for _ in ()).throw(TimeoutError("synthetic"))):
   with self.assertRaises(core.Refused):self.invoke()
  self.assertIn("cleanup",l.events)
 def test_wrong_ready_refuses_before_exchange(self):
  l=Lifetime()
  with self.stack(l,lambda *a:self.fail("exchange")),patch.object(os,"read",return_value=b"WRONG\n"):
   with self.assertRaises(core.Refused):self.invoke()
 def test_bad_payload_discards_result(self):
  l=Lifetime()
  with self.stack(l,lambda *a:b"{}"):
   with self.assertRaises(core.Refused):self.invoke()
 def test_control_profile_bounds_stopped_clients(self):
  unit="bridge-stagea-control-"+core.SOURCE[:12]+"-123-1-0123456789abcdef.service"
  command=control.command(unit,"synthetic",network=True)
  for value in ("ExitType=cgroup","KillMode=control-group","RuntimeMaxSec=60s","RuntimeRandomizedExtraSec=0",
                "TimeoutStopSec=2s","NoNewPrivileges=yes","PrivateNetwork=yes","RestrictAddressFamilies=AF_UNIX",
                "CapabilityBoundingSet=","AmbientCapabilities="):
   self.assertIn("--property="+value,command)
  self.assertEqual(control.PROPERTIES["ExecStop"],"");self.assertEqual(control.PROPERTIES["ExecStopPost"],"")
 def test_socket_denial_without_traffic(self):
  with patch.object(fixture.socket,"socket",side_effect=OSError(errno.EAFNOSUPPORT,"synthetic")):
   fixture.assert_network_sandbox()
 def test_allowed_socket_cannot_prove_sandbox(self):
  with patch.object(fixture.socket,"socket",return_value=types.SimpleNamespace(close=lambda:None)):
   with self.assertRaises(core.Refused):fixture.assert_network_sandbox()
 def test_fixture_payload_uses_only_fixed_wire(self):
  output=types.SimpleNamespace(buffer=io.BytesIO());inp=types.SimpleNamespace(buffer=io.BytesIO(fixture.WIRE))
  with patch.object(sys,"stdin",inp),patch.object(sys,"stdout",output),patch.object(fixture,"assert_network_sandbox"):
   self.assertEqual(fixture.run(PIN(),"payload"),0)
  self.assertEqual(output.buffer.getvalue(),fixture.payload())
 def test_fixture_other_wire_refuses(self):
  inp=types.SimpleNamespace(buffer=io.BytesIO(b"wrong"))
  with patch.object(sys,"stdin",inp),patch.object(fixture,"assert_network_sandbox"):
   with self.assertRaises(core.Refused):fixture.run(PIN(),"payload")
class CoreAndCapture(unittest.TestCase):
 def test_exact_safe_runtime_manifest(self):
  manifest=core.source_guard(PIN())
  self.assertEqual(set(manifest["files"]),core.RUNTIME_FILES)
 def test_wrong_manifest_hash_refuses(self):
  with self.assertRaises(core.Refused):core.source_guard("0"*64)
 def test_duplicate_json_keys_refused(self):
  with self.assertRaises(core.Refused):json.loads('{"x":1,"x":2}',object_pairs_hook=core.unique)
 def test_mutated_source_refused(self):
  with tempfile.TemporaryDirectory(prefix="synthetic-source-") as directory:
   root=Path(directory);manifest=json.loads((ROOT/"runtime-manifest.json").read_bytes())
   for name in core.RUNTIME_FILES:
    target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
   (root/"runtime-manifest.json").write_bytes((ROOT/"runtime-manifest.json").read_bytes())
   (root/"stagea_capture.py").write_bytes(b"changed")
   with self.assertRaises(core.Refused):core.source_guard(PIN(),root)
 def test_extra_runtime_file_refused(self):
  with tempfile.TemporaryDirectory(prefix="synthetic-source-") as directory:
   root=Path(directory);manifest=json.loads((ROOT/"runtime-manifest.json").read_bytes())
   manifest["files"]["extra.py"]="0"*64
   raw=json.dumps(manifest).encode();(root/"runtime-manifest.json").write_bytes(raw)
   with self.assertRaises(core.Refused):core.source_guard(hashlib.sha256(raw).hexdigest(),root)
 def test_capture_validates_exact_synthetic_schema(self):
  self.assertEqual(capture.validate(fixture.payload(),fixture.RUNTIME),fixture.payload())
 def test_capture_extra_field_refused(self):
  value=json.loads(fixture.payload());value["unexpected"]="synthetic"
  with self.assertRaises(core.Refused):capture.validate(json.dumps(value).encode(),fixture.RUNTIME)
 def test_capture_wrong_runtime_refused(self):
  with self.assertRaises(core.Refused):capture.validate(fixture.payload(),"0"*64)
 def test_capture_size_cap_refused(self):
  with self.assertRaises(core.Refused):capture.validate(b"x"*(core.MAX_OUTPUT+1),fixture.RUNTIME)
 def test_capture_wrong_relation_refused(self):
  value=json.loads(fixture.payload());value["relations"][0]["name"]="foreign"
  with self.assertRaises(core.Refused):capture.validate(json.dumps(value).encode(),fixture.RUNTIME)
 def test_receipt_cannot_include_definitions(self):
  with self.assertRaises(core.Refused):capture.public_receipt({"definitions":"synthetic"})
if __name__=="__main__":unittest.main()
