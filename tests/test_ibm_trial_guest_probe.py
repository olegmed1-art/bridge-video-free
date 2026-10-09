"""Offline contracts: fake IBM clients, no guest SSH or cloud calls."""
import ast,contextlib,hashlib,io,json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
from ops import ibm_trial_executor as e, ibm_trial_guest_probe as g
B=dict(run_id="39999999999",attempt=1,head="a"*40)
ENV=dict(GITHUB_REPOSITORY=g.REPOSITORY,GITHUB_REF=g.REF,GITHUB_EVENT_NAME="workflow_dispatch",GITHUB_ACTOR="olegmed1-art",GITHUB_TRIGGERING_ACTOR="olegmed1-art",GITHUB_RUN_ID=B["run_id"],GITHUB_RUN_ATTEMPT="1",GITHUB_SHA=B["head"],GITHUB_EVENT_PATH="/fixture")
INPUT=dict(mode="trial_start",diagnose_ssh=True,test_oracle=False)
class Clock:
 def __init__(self):self.t=0
 def now(self):return self.t
 def sleep(self,t):self.t+=t
class Client:
 def __init__(self,c,delay=0,unknown=False):self.c=c;self.delay=delay;self.unknown=unknown;self.actions=[]
 def backup(self):pass
 def state(self):
  if not self.actions or self.actions[-1]=="stop":return "stopped",True
  self.c.sleep(self.delay);self.delay=0;return "running",True
 def action(self,a):
  self.actions.append(a)
  if a=="start" and self.unknown:raise e.ControlError("transport_failed")
class Probe:
 binding=B
 def __init__(self,c,ok=True,error=None,seconds=0):self.c=c;self.ok=ok;self.error=error;self.seconds=seconds;self.calls=0;self.closed=False
 def run_once(self):
  self.calls+=1;self.c.sleep(self.seconds)
  if self.error:raise self.error
  return self.ok
 def close(self):self.closed=True
class ControllerTests(unittest.TestCase):
 def setUp(self):
  self.c=Clock();self.events=[]
  p=mock.patch.object(socket.socket,"connect",side_effect=AssertionError("LIVE_NETWORK_FORBIDDEN"));p.start();self.addCleanup(p.stop)
 def trial(self,c,p):
  with mock.patch.object(e,"LIVE_START_ENABLED",True),mock.patch.object(e,"wall_deadline",return_value=contextlib.nullcontext()):
   return e.trial(c,guest_probe=p,clock=self.c.now,sleep=self.c.sleep,out=lambda event,**kw:self.events.append(dict(event=event,**kw)))
 def test_success_after_running_stops_once(self):
  c=Client(self.c);p=Probe(self.c,seconds=12)
  self.assertEqual(0,self.trial(c,p));self.assertEqual(["start","stop"],c.actions);self.assertEqual(1,p.calls);self.assertLess(self.c.t,85)
  names=[x["event"] for x in self.events];self.assertLess(names.index("RUNNING_GUEST_WINDOW"),names.index("GUEST_DIAGNOSTIC_STARTED"));self.assertEqual("STOPPED_OBSERVED",names[-1])
 def test_auth_failure_remains_failure_after_confirmed_stop(self):
  c=Client(self.c);p=Probe(self.c,ok=False)
  self.assertEqual(3,self.trial(c,p));self.assertEqual(["start","stop"],c.actions);self.assertEqual(1,p.calls);self.assertEqual("STOPPED_OBSERVED",self.events[-1]["event"])
 def test_exception_preserves_stop_and_hides_arguments(self):
  c=Client(self.c);p=Probe(self.c,error=ValueError("PRIVATE_SENTINEL"))
  self.assertEqual(3,self.trial(c,p));self.assertEqual(["start","stop"],c.actions);self.assertNotIn("PRIVATE_SENTINEL",json.dumps(self.events))
 def test_cap_does_not_extend_power_timer(self):
  c=Client(self.c);p=Probe(self.c,seconds=38)
  self.assertEqual(0,self.trial(c,p));self.assertLess(self.c.t,85);self.assertEqual((600,120,38),(e.WINDOW_SECONDS,e.RUNNING_SECONDS,e.GUEST_DIAGNOSTIC_SECONDS))
 def test_late_running_skips_probe_then_stops(self):
  c=Client(self.c,delay=590);p=Probe(self.c)
  self.assertEqual(3,self.trial(c,p));self.assertEqual(0,p.calls);self.assertEqual(["start","stop"],c.actions)
 def test_unknown_start_no_probe_or_retry(self):
  c=Client(self.c,unknown=True);p=Probe(self.c)
  self.assertEqual(3,self.trial(c,p));self.assertEqual(0,p.calls);self.assertEqual(["start","stop"],c.actions)
 def test_wrong_mode_rejected_before_actions(self):
  c=Client(self.c)
  with self.assertRaises(e.ControlError):e.trial(c,mode="manual_console_trial",guest_probe=Probe(self.c))
  self.assertEqual([],c.actions)
 def test_preflight_failure_before_iam(self):
  with mock.patch.object(g,"prepare",side_effect=g.ProbeError("PRIVATE_SENTINEL")),mock.patch.object(e,"authenticate") as auth,contextlib.redirect_stdout(io.StringIO()) as out:
   self.assertEqual(4,e.main(["trial","--ack","OWNER_APPROVED_10MIN_10USD","--guest-diagnostic"]))
  auth.assert_not_called();self.assertNotIn("PRIVATE_SENTINEL",out.getvalue())
 def test_iam_failure_closes_prepared_route(self):
  p=Probe(self.c)
  with mock.patch.object(g,"prepare",return_value=p),mock.patch.object(e,"authenticate",side_effect=e.ControlError("http_403")),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(3,e.main(["trial","--ack","OWNER_APPROVED_10MIN_10USD","--guest-diagnostic"]))
  self.assertTrue(p.closed);self.assertEqual(0,p.calls)
 def test_stop_flag_cannot_load_oracle_secret(self):
  with mock.patch.object(g,"prepare") as prep,mock.patch.object(e,"authenticate") as auth,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(3,e.main(["stop","--ack","ORDINARY_STOP_EXACT_IBM","--guest-diagnostic"]))
  prep.assert_not_called();auth.assert_not_called()
 def test_prepare_before_iam_and_trial_then_close(self):
  order=[];p=Probe(self.c)
  with mock.patch.object(g,"prepare",side_effect=lambda:order.append("prepare") or p),mock.patch.object(e,"authenticate",side_effect=lambda:order.append("iam") or "fake"),mock.patch.object(e,"trial",side_effect=lambda *a,**kw:order.append("trial") or 0),contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(0,e.main(["trial","--ack","OWNER_APPROVED_10MIN_10USD","--guest-diagnostic"]))
  self.assertEqual(["prepare","iam","trial"],order);self.assertTrue(p.closed)
class BindingTests(unittest.TestCase):
 def ctx(self,env=None,inputs=None,head=None):
  with mock.patch.dict(os.environ,env or ENV,clear=True),mock.patch.object(Path,"read_text",return_value=json.dumps(dict(inputs=inputs or INPUT))),mock.patch.object(g,"bounded_process",return_value=(0,(head or B["head"]).encode()+b"\n",b"")):return g.context()
 def test_exact_binding(self):self.assertEqual(B,self.ctx())
 def test_consumed_rerun_wrong_ref_owner_or_event_refused(self):
  for k,v in [("GITHUB_RUN_ID","37788143504"),("GITHUB_RUN_ATTEMPT","2"),("GITHUB_REF","refs/heads/main"),("GITHUB_ACTOR","other"),("GITHUB_TRIGGERING_ACTOR","other"),("GITHUB_EVENT_NAME","pull_request"),("GITHUB_REPOSITORY","other/repo")]:
   with self.subTest(k=k),self.assertRaises(g.ProbeError):self.ctx({**ENV,k:v})
 def test_checkout_drift_refused(self):
  with self.assertRaises(g.ProbeError):self.ctx(head="b"*40)
 def test_dispatch_mode_and_flags_refused(self):
  for change in [dict(mode="status"),dict(mode="manual_console_trial"),dict(diagnose_ssh=False),dict(test_oracle=True)]:
   with self.subTest(change=change),self.assertRaises(g.ProbeError):self.ctx(inputs={**INPUT,**change})
class OutputTests(unittest.TestCase):
 def probe(self,rc=0,extra=None,err=b""):
  p=g.PreparedProbe(B,"fixture",None,None);p.ready=True
  rows=[dict(event="RUN_BOUND_GUEST_DIAGNOSTIC",**B),dict(event="SSH_PROBE_FINISHED",ssh_exit=0 if rc==0 else 255,error_classes=[] if rc==0 else ["AUTH_FAILURE"],stderr_sha256="c"*64,elapsed_seconds=.2,stderr_safe_exact_lines=[])]
  rows.extend(extra if extra is not None else [dict(event="SSH_AUTHENTICATED",user="ubuntu",uid=1000)])
  p.call=mock.Mock(return_value=(rc,("\n".join(map(json.dumps,rows))+"\n").encode(),err));return p
 def test_auth_success_receipt(self):
  p=self.probe()
  with contextlib.redirect_stdout(io.StringIO()) as out:self.assertTrue(p.run_once())
  self.assertIn("GUEST_AUTHENTICATED",out.getvalue())
 def test_replay_issues_no_second_network_call(self):
  p=self.probe()
  with contextlib.redirect_stdout(io.StringIO()):
   p.run_once()
   with self.assertRaises(g.ProbeError):p.run_once()
  p.call.assert_called_once()
 def test_timeout_consumes_attempt(self):
  p=self.probe();p.call.side_effect=g.ProbeError("probe_wall_timeout")
  with self.assertRaises(g.ProbeError):p.run_once()
  with self.assertRaises(g.ProbeError):p.run_once()
  p.call.assert_called_once()
 def test_safe_stderr_exact_unknown_hash_only(self):
  p=self.probe(rc=3,extra=[],err=b"PRIVATE_SENTINEL");rc,raw,err=p.call.return_value
  rows=[json.loads(l) for l in raw.splitlines()];rows[1]["stderr_safe_exact_lines"]=["PRIVATE_SENTINEL","ubuntu@161.156.86.34: Permission denied (publickey)."]
  p.call.return_value=rc,("\n".join(map(json.dumps,rows))+"\n").encode(),err
  with contextlib.redirect_stdout(io.StringIO()) as out:self.assertFalse(p.run_once())
  self.assertNotIn("PRIVATE_SENTINEL",out.getvalue());self.assertIn("Permission denied",out.getvalue())
 def test_foreign_response_binding_refused(self):
  p=self.probe();rc,raw,err=p.call.return_value;rows=[json.loads(l) for l in raw.splitlines()];rows[0]["head"]="b"*40
  p.call.return_value=rc,("\n".join(map(json.dumps,rows))+"\n").encode(),err
  with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(g.ProbeError):p.run_once()
 def test_root_or_duplicate_auth_not_confirmed(self):
  for extra in [[dict(event="SSH_AUTHENTICATED",user="root",uid=0)],[dict(event="SSH_AUTHENTICATED",user="ubuntu",uid=1000)]*2]:
   with contextlib.redirect_stdout(io.StringIO()):self.assertFalse(self.probe(extra=extra).run_once())
class BoundsTests(unittest.TestCase):
 def test_capture_both_outputs_and_stdin(self):
  rc,out,err=g.bounded_process([sys.executable,"-I","-B","-c","import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stderr.write('fixture')"],payload=b"fixture",timeout=2)
  self.assertEqual((0,b"fixture",b"fixture"),(rc,out,err))
 def test_child_has_no_parent_secret_environment(self):
  with mock.patch.dict(os.environ,dict(PRIVATE_SENTINEL="PRIVATE_SENTINEL")):
   rc,out,err=g.bounded_process([sys.executable,"-I","-B","-c","import os;print(os.environ.get('PRIVATE_SENTINEL','absent'))"],timeout=2)
  self.assertEqual(b"absent\n",out)
 def test_output_limit(self):
  with self.assertRaisesRegex(g.ProbeError,"probe_output_limit"):g.bounded_process([sys.executable,"-I","-B","-c","print('x'*10000)"],timeout=2,limit=64)
 def test_wall_timeout_reaps_local_child(self):
  with self.assertRaisesRegex(g.ProbeError,"probe_wall_timeout"):g.bounded_process([sys.executable,"-I","-B","-c","import time;time.sleep(10)"],timeout=.05)
 def test_input_limit_before_process(self):
  with mock.patch.object(g.subprocess,"Popen") as child,self.assertRaises(g.ProbeError):g.bounded_process(["never"],payload=b"x"*16385)
  child.assert_not_called()
 def test_sealed_public_memory_readonly(self):
  fd=g.memory(b"public","public")
  try:
   self.assertEqual(0o600,os.fstat(fd).st_mode & 0o777)
   with self.assertRaises(OSError):os.write(fd,b"x")
  finally:os.close(fd)
 def test_remote_child_timeout_cleanup_primitive(self):
  tree=ast.parse(g.REMOTE);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="bounded_child"]
  ns=dict(os=os,sys=sys,subprocess=subprocess);exec(compile(ast.Module(body=nodes,type_ignores=[]),"offline_remote_child","exec"),ns)
  with self.assertRaises(subprocess.TimeoutExpired):
   ns["bounded_child"]([sys.executable,"-I","-B","-c","import time;time.sleep(10)"],capture_output=True,timeout=.05)
class SourceTests(unittest.TestCase):
 def test_stop_lane_has_no_guest_secret_or_dependency(self):
  s=Path(".github/workflows/ibm-vpc-power-probe.yml").read_text();start=s.split("  trial-start:")[1].split("  manual-console-trial:")[0];stop=s.split("  trial-stop:")[1].split("  oracle-probe:")[0]
  self.assertIn("--guest-diagnostic",start);self.assertIn("ref: "+"$"+"{{ github.sha }}",start)
  self.assertNotIn("ORACLE_SSH_PRIVATE_KEY",stop);self.assertNotIn("needs: trial-start",stop);self.assertIn("ibm-vpc-independent-stop",s);self.assertNotIn("RUN_SECOND_ONCE",s)
 def test_guest_source_pin_and_user(self):
  b=Path("ops/ibm_ssh_probe_safe.py").read_bytes();self.assertEqual(g.GUEST_SOURCE_SHA,hashlib.sha256(b).hexdigest());self.assertIn(b'VERIFIED_USER = "ubuntu"',b)
 def test_remote_watchdog_pin_snapshot_and_parent_death(self):
  compile(g.REMOTE,"remote","exec")
  self.assertIn("signal.setitimer(signal.ITIMER_REAL,28)",g.REMOTE);self.assertIn("F_SEAL_WRITE",g.REMOTE);self.assertIn('all(not row[0].startswith("@")',g.REMOTE);self.assertIn('ns["KNOWN"]="/proc/"',g.REMOTE);self.assertIn("prctl(1,signal.SIGKILL)",g.REMOTE)

class PreflightAndPinTests(unittest.TestCase):
 def test_preflight_exact_binding_and_no_guest_network(self):
  p=g.PreparedProbe(B,"fixture",None,None)
  row=dict(B,event="ORACLE_ROUTE_READY",at_utc="2026-10-08T00:00:00Z",user="ubuntu",hostpin="MATCH",guest_network_requests=0,key_contents_read=False)
  p.call=mock.Mock(return_value=(0,json.dumps(row).encode(),b""))
  with contextlib.redirect_stdout(io.StringIO()):p.preflight()
  self.assertTrue(p.ready);self.assertFalse(p.attempted)
 def test_oracle_auth_error_keeps_safe_class(self):
  p=g.PreparedProbe(B,"fixture",None,None);p.call=mock.Mock(return_value=(255,b"",b"ubuntu@92.5.47.149: Permission denied (publickey). PRIVATE_SENTINEL"))
  with contextlib.redirect_stdout(io.StringIO()) as out,self.assertRaises(g.ProbeError):p.preflight()
  self.assertFalse(p.ready);self.assertIn("AUTH_FAILURE",out.getvalue());self.assertNotIn("PRIVATE_SENTINEL",out.getvalue())
 def selector(self):
  tree=ast.parse(g.REMOTE);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="select_known_record"]
  ns={};exec(compile(ast.Module(body=nodes,type_ignores=[]),"offline_pin_parser","exec"),ns);return ns["select_known_record"]
 def test_marked_other_algorithm_refused_before_ed_selection(self):
  select=self.selector()
  for marker in ["@revoked","@cert-authority","@unknown"]:
   with self.subTest(marker=marker),self.assertRaises(AssertionError):
    select(marker+" host ssh-rsa public\nhost ssh-ed25519 public\n")
 def test_unmarked_matching_ed_selected_and_duplicate_refused(self):
  select=self.selector()
  self.assertEqual(["host","ssh-ed25519","public"],select("# comment\nhost ssh-rsa public\nhost ssh-ed25519 public\n"))
  with self.assertRaises(AssertionError):select("host ssh-ed25519 public\nhost ssh-ed25519 public\n")


class ReadinessTests(unittest.TestCase):
 """Virtual clocks and fake TCP/SSH only, including controller containment."""
 def setUp(self):
  from ops import ibm_ssh_probe_safe as safe
  import types,stat,errno
  self.s=safe;self.c=Clock();self.events=[];self.connects=[];self.ssh=[];self.commands=[]
  self.ready_at=0;self.connection_error=errno.ECONNREFUSED;self.pin_ok=True
  self.connect_hook=None;self.ssh_hook=None;self.precheck_delay=0
  def run(argv,**kw):
   self.commands.append((argv,kw))
   if argv[0]=="/usr/bin/ssh":
    self.ssh.append((self.c.now(),argv,kw))
    if self.ssh_hook:return self.ssh_hook(argv,kw)
    return types.SimpleNamespace(returncode=0,stdout="1001\n",stderr="")
   self.c.sleep(self.precheck_delay)
   value=(safe.HOST+" ssh-ed25519 public\n") if "-F" in argv else "256 "+(safe.EXPECTED if self.pin_ok else "wrong")+" fixture"
   return types.SimpleNamespace(returncode=0,stdout=value,stderr="")
  def connect(address,timeout):
   self.connects.append((self.c.now(),address,timeout))
   if self.connect_hook:return self.connect_hook(address,timeout)
   if self.c.now()<self.ready_at:
    if self.connection_error==errno.ETIMEDOUT:self.c.sleep(timeout)
    raise OSError(self.connection_error,"PRIVATE_SENTINEL")
   return contextlib.nullcontext()
  patches=[mock.patch.object(safe,"time",types.SimpleNamespace(monotonic=self.c.now,sleep=self.c.sleep)),
   mock.patch.object(safe,"os",types.SimpleNamespace(stat=lambda _:types.SimpleNamespace(st_mode=stat.S_IFREG|0o600),access=lambda *_:True,R_OK=os.R_OK)),
   mock.patch.object(safe,"socket",types.SimpleNamespace(create_connection=connect)),
   mock.patch.object(safe,"subprocess",types.SimpleNamespace(run=run,TimeoutExpired=subprocess.TimeoutExpired,SubprocessError=subprocess.SubprocessError)),
   mock.patch.object(safe,"emit",side_effect=lambda event,**kw:self.events.append(dict(event=event,**kw))),
   mock.patch.object(socket.socket,"connect",side_effect=AssertionError("LIVE_NETWORK_FORBIDDEN"))]
  for p in patches:p.start();self.addCleanup(p.stop)
 def source(self,**kw):return self.s.probe("ubuntu",**kw)
 def controller(self,*,delay=0,unknown=False,deadline_interrupt=False):
  client=Client(self.c,delay=delay,unknown=unknown)
  prepared=g.PreparedProbe(B,"fixture",None,None);prepared.ready=True
  def remote(phase,timeout):
   self.assertEqual(("probe",33),(phase,timeout))
   if deadline_interrupt:raise e.ControlError("wall_deadline_expired")
   before=len(self.events);code=self.source(deadline=self.c.now()+28)
   rows=[dict(event="RUN_BOUND_GUEST_DIAGNOSTIC",**B)]+self.events[before:]
   return code,("\n".join(map(json.dumps,rows))+"\n").encode(),b""
  prepared.call=mock.Mock(side_effect=remote)
  with mock.patch.object(e,"wall_deadline",return_value=contextlib.nullcontext()) as wall,contextlib.redirect_stdout(io.StringIO()):
   code=e.trial(client,guest_probe=prepared,clock=self.c.now,sleep=self.c.sleep,out=lambda *_a,**_kw:None)
  return code,client,prepared,wall
 def test_ready_at_seven_seconds_one_ssh_one_stop(self):
  self.ready_at=7
  code,client,p,wall=self.controller()
  self.assertEqual(0,code);self.assertEqual(["start","stop"],client.actions)
  self.assertEqual(8,len(self.connects));self.assertEqual(1,len(self.ssh));self.assertEqual(7,self.ssh[0][0])
  self.assertEqual(15,self.ssh[0][2]["timeout"]);self.assertIn("ConnectionAttempts=1",self.ssh[0][1])
  self.assertIn("StrictHostKeyChecking=yes",self.ssh[0][1]);p.call.assert_called_once();wall.assert_called_once_with(38)
  self.assertEqual(22,self.c.now())
 def test_permanent_timeout_zero_ssh_one_stop(self):
  import errno
  self.ready_at=float("inf");self.connection_error=errno.ETIMEDOUT
  code,client,p,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.ssh)
  self.assertEqual([3,3,2],[x[2] for x in self.connects]);self.assertEqual(25,self.c.now());p.call.assert_called_once()
  self.assertNotIn("PRIVATE_SENTINEL",json.dumps(self.events))
 def test_permanent_refusal_bounded_no_ssh(self):
  self.ready_at=float("inf")
  self.assertEqual(3,self.source());self.assertEqual(10,len(self.connects));self.assertEqual(10,self.c.now());self.assertEqual([],self.ssh)
 def test_nontransient_network_error_not_retried(self):
  import errno
  self.ready_at=float("inf");self.connection_error=errno.ENETUNREACH
  self.assertEqual(3,self.source());self.assertEqual(1,len(self.connects));self.assertEqual([],self.ssh)
 def test_frozen_clock_still_has_finite_attempt_limit(self):
  self.ready_at=float("inf");self.s.time.sleep=lambda _:None
  self.assertEqual(3,self.source());self.assertEqual(self.s.TCP_MAX_ATTEMPTS,len(self.connects));self.assertEqual([],self.ssh)
 def test_smaller_remaining_deadline_caps_tcp_and_reserves_ssh(self):
  import errno
  self.ready_at=float("inf");self.connection_error=errno.ETIMEDOUT
  self.assertEqual(3,self.source(deadline=17));self.assertEqual([2],[x[2] for x in self.connects]);self.assertEqual(2,self.c.now());self.assertEqual([],self.ssh)
 def test_insufficient_remaining_budget_never_starts_tcp_or_ssh(self):
  self.assertEqual(3,self.source(deadline=15));self.assertEqual([],self.connects);self.assertEqual([],self.ssh)
 def test_longer_deadline_cannot_extend_local_cap(self):
  self.ready_at=float("inf")
  self.assertEqual(3,self.source(deadline=999));self.assertEqual(10,self.c.now());self.assertEqual([],self.ssh)
 def test_invalid_or_expired_deadline_no_commands(self):
  for deadline in [0,-1,float("nan"),float("inf"),True,"28"]:
   with self.subTest(deadline=deadline):self.assertEqual(3,self.source(deadline=deadline))
  self.assertEqual([],self.commands);self.assertEqual([],self.connects)
 def test_precheck_time_consumes_readiness_budget(self):
  self.precheck_delay=5;self.ready_at=float("inf")
  self.assertEqual(3,self.source());self.assertEqual([10,11,12],[x[0] for x in self.connects]);self.assertEqual(13,self.c.now())
 def test_pin_failure_zero_tcp_zero_ssh_then_stop(self):
  self.pin_ok=False
  code,client,_,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.connects);self.assertEqual([],self.ssh)
 def test_forward_clock_jump_late_tcp_success_no_ssh_then_stop(self):
  def connect(*_):self.c.sleep(29);return contextlib.nullcontext()
  self.connect_hook=connect
  code,client,_,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.ssh)
  self.assertTrue(any(x.get("reason")=="probe_deadline_expired" for x in self.events))
 def test_backward_clock_jump_no_ssh_then_stop(self):
  def connect(*_):self.c.t=-1;return contextlib.nullcontext()
  self.connect_hook=connect
  code,client,_,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.ssh)
  self.assertTrue(any(x.get("reason")=="monotonic_clock_regressed" for x in self.events))
 def test_tcp_success_after_readiness_cutoff_no_ssh(self):
  def connect(*_):self.c.sleep(11);return contextlib.nullcontext()
  self.connect_hook=connect
  self.assertEqual(3,self.source());self.assertEqual([],self.ssh)
 def test_deadline_interrupt_zero_ssh_one_stop(self):
  code,client,p,_=self.controller(deadline_interrupt=True)
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.connects);self.assertEqual([],self.ssh);p.call.assert_called_once()
 def test_ssh_timeout_is_one_attempt_then_stop(self):
  def run(argv,kw):self.c.sleep(kw["timeout"]);raise subprocess.TimeoutExpired(argv,kw["timeout"],stderr=b"PRIVATE_SENTINEL")
  self.ssh_hook=run
  code,client,p,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual(1,len(self.ssh));p.call.assert_called_once()
  self.assertNotIn("PRIVATE_SENTINEL",json.dumps(self.events))
 def test_late_ssh_success_not_accepted_or_retried(self):
  import types
  def run(*_):self.c.sleep(29);return types.SimpleNamespace(returncode=0,stdout="1001\n",stderr="")
  self.ssh_hook=run
  self.assertEqual(3,self.source());self.assertEqual(1,len(self.ssh));self.assertFalse(any(x["event"]=="SSH_AUTHENTICATED" for x in self.events))
 def test_start_failure_no_tcp_or_ssh_and_one_stop(self):
  code,client,p,_=self.controller(unknown=True)
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);p.call.assert_not_called();self.assertEqual([],self.connects);self.assertEqual([],self.ssh)
 def test_late_running_cannot_borrow_stop_reserve(self):
  code,client,p,_=self.controller(delay=530)
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);p.call.assert_not_called();self.assertEqual([],self.connects);self.assertEqual([],self.ssh)
 def test_before_start_preflight_failure_no_power_actions(self):
  client=Client(self.c)
  with mock.patch.object(g,"prepare",side_effect=g.ProbeError("preflight_failed")),mock.patch.object(e,"Client",return_value=client),mock.patch.object(e,"authenticate") as auth,contextlib.redirect_stdout(io.StringIO()):
   self.assertEqual(4,e.main(["trial","--ack","OWNER_APPROVED_10MIN_10USD","--guest-diagnostic"]))
  auth.assert_not_called();self.assertEqual([],client.actions);self.assertEqual([],self.connects);self.assertEqual([],self.ssh)
 def test_remote_watchdog_and_parent_death_abort_not_retried(self):
  tree=ast.parse(g.REMOTE)
  nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in {"RemoteAbort","expired"}]
  ns={};exec(compile(ast.Module(body=nodes,type_ignores=[]),"offline_abort","exec"),ns)
  self.assertFalse(issubclass(ns["RemoteAbort"],OSError))
  for signum in [14,15]:
   self.connects.clear();self.ssh.clear()
   self.s.time.sleep=lambda _:None
   def connect(*_):ns["expired"](signum,None)
   self.connect_hook=connect
   with self.subTest(signum=signum),self.assertRaises(ns["RemoteAbort"]):self.source()
   self.assertEqual(1,len(self.connects));self.assertEqual([],self.ssh)
 def test_remote_deadline_is_anchored_before_watchdog_and_passed_to_probe(self):
  self.assertLess(g.REMOTE.index("remote_deadline=time.monotonic()+28"),g.REMOTE.index("signal.setitimer(signal.ITIMER_REAL,28)"))
  self.assertIn('ns["probe"]("ubuntu",deadline=remote_deadline)',g.REMOTE)
  self.assertIn(g.GUEST_SOURCE_SHA,g.REMOTE)
  self.assertEqual((600,120,38,38),(e.WINDOW_SECONDS,e.RUNNING_SECONDS,e.GUEST_DIAGNOSTIC_SECONDS,g.DIAGNOSTIC_SECONDS))


if __name__=="__main__":unittest.main()

