"""Offline contracts: fake IBM clients, no guest SSH or cloud calls."""
import ast,contextlib,hashlib,io,json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
from ops import ibm_trial_executor as e, ibm_trial_guest_probe as g, ibm_ssh_probe_safe as safe

def quiet_host_observation(uid=1001):
 return dict(scope=safe.HOST_SCOPE,result="QUIET_IN_KNOWN_SCOPE",uid=uid,units=[dict(unit=name,load="not-found" if name in {"docker.service","docker.socket"} else "loaded",active="inactive",sub="dead") for name in safe.HOST_UNITS],docker="NOT_INSTALLED",containers=[],elapsed_seconds=.1)

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
 def run_once(self, *, max_seconds=None):
  self.budget=max_seconds
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
  names=[x["event"] for x in self.events];self.assertLess(names.index("TRIAL_STATE"),names.index("GUEST_DIAGNOSTIC_STARTED"));self.assertEqual("STOPPED_OBSERVED",names[-1])
 def test_auth_failure_remains_failure_after_confirmed_stop(self):
  c=Client(self.c);p=Probe(self.c,ok=False)
  self.assertEqual(3,self.trial(c,p));self.assertEqual(["start","stop"],c.actions);self.assertEqual(1,p.calls);self.assertEqual("STOPPED_OBSERVED",self.events[-1]["event"])
 def test_exception_preserves_stop_and_hides_arguments(self):
  c=Client(self.c);p=Probe(self.c,error=ValueError("PRIVATE_SENTINEL"))
  self.assertEqual(3,self.trial(c,p));self.assertEqual(["start","stop"],c.actions);self.assertNotIn("PRIVATE_SENTINEL",json.dumps(self.events))
 def test_cap_does_not_extend_power_timer(self):
  c=Client(self.c);p=Probe(self.c,seconds=38)
  self.assertEqual(0,self.trial(c,p));self.assertLess(self.c.t,85);self.assertEqual((600,120,338,420),(e.WINDOW_SECONDS,e.RUNNING_SECONDS,e.GUEST_DIAGNOSTIC_SECONDS,e.DIAGNOSTIC_STOP_SECONDS))
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
  rows=[dict(event="RUN_BOUND_GUEST_DIAGNOSTIC",**B),dict(event="TCP_PROBE_STARTED",user="ubuntu",target="161.156.86.34",power_mutations=False),dict(event="TCP_OPEN",attempts=1,elapsed_seconds=0),dict(event="SSH_PROBE_STARTED",user="ubuntu"),dict(event="SSH_PROBE_FINISHED",ssh_exit=0 if rc==0 else 255,error_classes=[] if rc==0 else ["AUTH_FAILURE"],stderr_sha256="c"*64,elapsed_seconds=.2,stderr_safe_exact_lines=[])]
  rows.append(dict(event="HOST_OBSERVER_RESULT",observation=quiet_host_observation(1000)))
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
  self.assertNotIn("PRIVATE_SENTINEL",out.getvalue());self.assertNotIn("Permission denied",out.getvalue());self.assertNotIn("stderr_safe_exact_lines",out.getvalue())
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
  with mock.patch.object(g.subprocess,"Popen") as child,self.assertRaises(g.ProbeError):g.bounded_process(["never"],payload=b"x"*32769)
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
    return types.SimpleNamespace(returncode=0,stdout=json.dumps(quiet_host_observation()),stderr="")
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
   self.assertEqual("probe",phase);self.assertLessEqual(timeout,333)
   if deadline_interrupt:raise e.ControlError("wall_deadline_expired")
   before=len(self.events);code=self.source(deadline=self.c.now()+timeout-5)
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
  self.assertIn("StrictHostKeyChecking=yes",self.ssh[0][1]);p.call.assert_called_once();wall.assert_called_once_with(338)
  self.assertEqual(22,self.c.now())
 def test_permanent_timeout_zero_ssh_one_stop(self):
  import errno
  self.ready_at=float("inf");self.connection_error=errno.ETIMEDOUT
  code,client,p,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.ssh)
  self.assertEqual([3]*75,[x[2] for x in self.connects]);self.assertEqual(315,self.c.now());p.call.assert_called_once()
  self.assertNotIn("PRIVATE_SENTINEL",json.dumps(self.events))
 def test_permanent_refusal_bounded_no_ssh(self):
  self.ready_at=float("inf")
  self.assertEqual(3,self.source());self.assertEqual(300,len(self.connects));self.assertEqual(300,self.c.now());self.assertEqual([],self.ssh)
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
  self.assertEqual(3,self.source(deadline=999));self.assertEqual(300,self.c.now());self.assertEqual([],self.ssh)
 def test_invalid_or_expired_deadline_no_commands(self):
  for deadline in [0,-1,float("nan"),float("inf"),True,"28"]:
   with self.subTest(deadline=deadline):self.assertEqual(3,self.source(deadline=deadline))
  self.assertEqual([],self.commands);self.assertEqual([],self.connects)
 def test_precheck_time_consumes_readiness_budget(self):
  self.precheck_delay=5;self.ready_at=float("inf")
  self.assertEqual(3,self.source(deadline=28));self.assertEqual([10,11,12],[x[0] for x in self.connects]);self.assertEqual(13,self.c.now())
 def test_pin_failure_zero_tcp_zero_ssh_then_stop(self):
  self.pin_ok=False
  code,client,_,_=self.controller()
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);self.assertEqual([],self.connects);self.assertEqual([],self.ssh)
 def test_forward_clock_jump_late_tcp_success_no_ssh_then_stop(self):
  def connect(*_):self.c.sleep(329);return contextlib.nullcontext()
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
  def connect(*_):self.c.sleep(301);return contextlib.nullcontext()
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
  def run(*_):self.c.sleep(329);return types.SimpleNamespace(returncode=0,stdout=json.dumps(quiet_host_observation()),stderr="")
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
 def captured_source_receipts(self):
  code=self.source();rows=[dict(event="RUN_BOUND_GUEST_DIAGNOSTIC",**B)]+self.events
  p=g.PreparedProbe(B,"fixture",None,None);p.ready=True
  p.call=mock.Mock(return_value=(code,("\n".join(map(json.dumps,rows))+"\n").encode(),b""))
  with contextlib.redirect_stdout(io.StringIO()) as out:result=p.run_once()
  return result,[json.loads(line) for line in out.getvalue().splitlines()]
 def test_actual_timeout_source_preserves_75_attempts_300_seconds(self):
  import errno
  self.ready_at=float("inf");self.connection_error=errno.ETIMEDOUT
  result,rows=self.captured_source_receipts();self.assertFalse(result)
  tcp=next(row for row in rows if row["event"]=="GUEST_TCP_READINESS_RESULT")
  self.assertEqual((75,300,"TCP_TIMEOUT",True),(tcp["attempts"],tcp["elapsed_seconds"],tcp["result_class"],tcp["retry_exhausted"]))
  self.assertEqual([3]*75,[x[2] for x in self.connects]);self.assertEqual([],self.ssh)
 def test_actual_delayed_ready_source_keeps_eight_attempts_one_ssh(self):
  self.ready_at=7;result,rows=self.captured_source_receipts();self.assertTrue(result)
  tcp=next(row for row in rows if row["event"]=="GUEST_TCP_READINESS_RESULT")
  self.assertEqual((8,7,"TCP_OPEN",False),(tcp["attempts"],tcp["elapsed_seconds"],tcp["result_class"],tcp["retry_exhausted"]))
  self.assertEqual(1,len(self.ssh));self.assertEqual(7,self.ssh[0][0]);self.assertEqual(15,self.ssh[0][2]["timeout"])
 def test_remote_deadline_is_anchored_before_watchdog_and_passed_to_probe(self):
  self.assertLess(g.REMOTE.index("remote_deadline=remote_started+28"),g.REMOTE.index("signal.setitimer(signal.ITIMER_REAL,28)"))
  self.assertIn('ns["probe"]("ubuntu",deadline=remote_deadline)',g.REMOTE)
  self.assertIn(g.GUEST_SOURCE_SHA,g.REMOTE)
  self.assertEqual((600,120,338,338),(e.WINDOW_SECONDS,e.RUNNING_SECONDS,e.GUEST_DIAGNOSTIC_SECONDS,g.DIAGNOSTIC_SECONDS))


 def test_tdx_boot_readiness_at_180_240_and_299_seconds(self):
  for ready in (180,240,299):
   with self.subTest(ready=ready):
    self.c.t=0;self.ready_at=ready;self.events.clear();self.connects.clear();self.ssh.clear()
    code,client,p,_=self.controller()
    self.assertEqual(0,code);self.assertEqual(["start","stop"],client.actions)
    self.assertEqual(ready,self.ssh[0][0]);self.assertEqual(1,len(self.ssh));self.assertLess(self.c.now(),420)
 def test_late_running_shares_original_absolute_budget_with_tcp(self):
  for running,ready,success in ((240,300,True),(340,350,True),(340,360,False)):
   with self.subTest(running=running,ready=ready):
    self.c.t=0;self.ready_at=ready;self.events.clear();self.connects.clear();self.ssh.clear()
    code,client,p,_=self.controller(delay=running)
    self.assertEqual(0 if success else 3,code);self.assertEqual(["start","stop"],client.actions)
    self.assertEqual(1 if success else 0,len(self.ssh));self.assertLess(self.c.now(),420)
 def test_ssh_success_beyond_sole_15_second_timeout_is_rejected(self):
  import types
  def run(*_):self.c.sleep(16);return types.SimpleNamespace(returncode=0,stdout=json.dumps(quiet_host_observation()),stderr="")
  self.ssh_hook=run
  self.assertEqual(3,self.source());self.assertEqual(1,len(self.ssh))
  self.assertTrue(any(row.get("reason")=="ssh_deadline_expired" for row in self.events))



class ReceiptEvidenceTests(unittest.TestCase):
 """Collector-only negative and adversarial coverage; no live process or network."""
 def rows(self,kind="timeout"):
  rows=[dict(event="RUN_BOUND_GUEST_DIAGNOSTIC",**B),dict(event="TCP_PROBE_STARTED",user="ubuntu",target="161.156.86.34",power_mutations=False)]
  if kind=="timeout":
   rows += [dict(event="TCP_TIMEOUT",errno=110,elapsed_seconds=10),dict(event="TCP_READINESS_EXHAUSTED",attempts=3,elapsed_seconds=10)]
  else:
   rows += [dict(event="TCP_OPEN",attempts=8,elapsed_seconds=7),dict(event="SSH_PROBE_STARTED",user="ubuntu"),dict(event="SSH_PROBE_FINISHED",ssh_exit=0 if kind=="success" else 255,error_classes=[] if kind=="success" else ["AUTH_FAILURE"],stderr_sha256="c"*64,elapsed_seconds=.2,stderr_safe_exact_lines=[])]
   rows += [dict(event="HOST_OBSERVER_RESULT",observation=quiet_host_observation(1000)),dict(event="SSH_AUTHENTICATED",user="ubuntu",uid=1000)] if kind=="success" else [dict(event="SSH_NOT_CONFIRMED",stdout_exported=False)]
  return rows
 def collect(self,rows=None,*,raw=None,rc=3,err=b""):
  p=g.PreparedProbe(B,"fixture",None,None);p.ready=True
  if raw is None:raw=("\n".join(map(json.dumps,self.rows() if rows is None else rows))+"\n").encode()
  p.call=mock.Mock(return_value=(rc,raw,err))
  with contextlib.redirect_stdout(io.StringIO()) as output:
   try:result=p.run_once()
   except BaseException as exc:result=exc
  return result,[json.loads(line) for line in output.getvalue().splitlines()],p,output.getvalue()
 def rejected(self,rows=None,**kwargs):
  result,events,p,output=self.collect(rows,**kwargs)
  self.assertIsInstance(result,g.ProbeError);self.assertNotIn("PRIVATE_SENTINEL",str(result)+output)
  self.assertTrue(p.attempted);p.call.assert_called_once()
  self.assertTrue(all(row["event"]=="GUEST_PROBE_CAPTURED" for row in events))
  with self.assertRaises(g.ProbeError):p.run_once()
  p.call.assert_called_once()
 def test_timeout_preserves_count_elapsed_and_stage_without_ssh_claim(self):
  result,events,_,_=self.collect();self.assertIs(result,False)
  tcp=next(r for r in events if r["event"]=="GUEST_TCP_READINESS_RESULT")
  self.assertEqual((3,10,"TCP_TIMEOUT",True,True,"RETRY_EXHAUSTED"),(tcp["attempts"],tcp["elapsed_seconds"],tcp["result_class"],tcp["readiness_exhausted"],tcp["retry_exhausted"],tcp["termination_class"]))
  self.assertFalse(any(r["event"]=="GUEST_SSH_RESULT" for r in events))
 def test_open_and_actual_ssh_failure_are_separate(self):
  result,events,_,_=self.collect(self.rows("failure"));self.assertIs(result,False)
  tcp=next(r for r in events if r["event"]=="GUEST_TCP_READINESS_RESULT")
  ssh=next(r for r in events if r["event"]=="GUEST_SSH_RESULT")
  self.assertEqual((8,7,"TCP_OPEN",False,False),(tcp["attempts"],tcp["elapsed_seconds"],tcp["result_class"],tcp["readiness_exhausted"],tcp["retry_exhausted"]))
  self.assertEqual(("SSH",255,["AUTH_FAILURE"]),(ssh["stage"],ssh["ssh_exit"],ssh["error_classes"]))
 def test_success_still_requires_existing_remote_auth_result(self):
  rows=self.rows("success")
  self.assertIs(self.collect(rows,rc=0)[0],True)
  self.assertIs(self.collect(rows,rc=3)[0],False)
  self.assertIs(self.collect(rows[:-1],rc=0)[0],False)
 def test_refusal_nonretryable_and_zero_attempt_receipts(self):
  for label,count,termination,retries in [("TCP_REFUSED",10,"RETRY_EXHAUSTED",True),("NETWORK_UNREACHABLE",1,"NON_RETRYABLE_FAILURE",False),("TCP_CONNECT_FAILURE",1,"NON_RETRYABLE_FAILURE",False),("TCP_TIMEOUT",0,"NO_TCP_ATTEMPT",False)]:
   rows=self.rows();rows[2].update(event=label,elapsed_seconds=0);rows[3].update(attempts=count,elapsed_seconds=0)
   with self.subTest(label=label,count=count):
    result,events,_,_=self.collect(rows);self.assertIs(result,False)
    tcp=next(r for r in events if r["event"]=="GUEST_TCP_READINESS_RESULT")
    self.assertEqual((count,termination,retries),(tcp["attempts"],tcp["termination_class"],tcp["retry_exhausted"]))
 def test_wall_timeout_keeps_only_ssh_class_duration_and_hash(self):
  rows=self.rows("failure");rows[4].update(event="SSH_WALL_TIMEOUT",error_classes=["SSH_BANNER_TIMEOUT"],elapsed_seconds=15);del rows[4]["ssh_exit"];rows=rows[:-1]
  result,events,_,_=self.collect(rows);self.assertIs(result,False)
  ssh=next(r for r in events if r["event"]=="GUEST_SSH_RESULT")
  self.assertEqual(("SSH_WALL_TIMEOUT",15,["SSH_BANNER_TIMEOUT"]),(ssh["result_class"],ssh["elapsed_seconds"],ssh["error_classes"]));self.assertNotIn("ssh_exit",ssh)
 def test_unknown_fields_timestamps_stderr_and_private_paths_never_exported(self):
  rows=self.rows("failure")
  for row in rows:row.update(at_utc="PRIVATE_SENTINEL",unknown={"token":"PRIVATE_SENTINEL"})
  rows[4]["stderr_safe_exact_lines"]=['Load key "/home/ubuntu/.ssh/ibm_bridge_ed25519": invalid format','ubuntu@161.156.86.34: Permission denied (publickey).',"PRIVATE_SENTINEL"]
  result,_,_,output=self.collect(rows,err=b"PRIVATE_SENTINEL")
  self.assertIs(result,False)
  for value in ["PRIVATE_SENTINEL","/home/ubuntu","ibm_bridge_ed25519","Permission denied","stderr_safe_exact_lines"]:self.assertNotIn(value,output)
 def test_malformed_json_utf8_nonobject_and_unknown_event_rejected(self):
  prefix=json.dumps(self.rows()[0]).encode()+b"\n"
  for part in [b"[]",b"null",b"true",b'"PRIVATE_SENTINEL"',b'{"event":"PRIVATE_SENTINEL"}',b'{"event":[]}',b'{"event":"TCP_OPEN",}',b'\xff']:
   with self.subTest(part=part):self.rejected(raw=prefix+part+b"\n")
 def test_input_size_line_count_and_empty_line_bounds(self):
  prefix=json.dumps(self.rows()[0]).encode()+b"\n"
  for raw in [b"",b"x"*32769,prefix+b"x"*4097+b"\n",prefix*17,prefix+b"\n"]:
   with self.subTest(length=len(raw)):self.rejected(raw=raw)
  self.rejected(err=b"x"*32768)
 def test_response_and_returncode_types_are_exact(self):
  for raw in ["PRIVATE_SENTINEL",bytearray(b"{}"),None]:self.rejected(raw=raw if raw is not None else b"null")
  for code in [True,False,"0",None,256,-256]:self.rejected(rc=code)
  self.rejected(err="PRIVATE_SENTINEL")
 def test_duplicate_keys_and_nonfinite_constants_rejected(self):
  prefix=json.dumps(self.rows()[0]).encode()+b"\n"
  for part in [b'{"event":"TCP_OPEN","event":"TCP_TIMEOUT"}',b'{"event":"TCP_OPEN","attempts":1,"elapsed_seconds":NaN}',b'{"event":"TCP_OPEN","attempts":1,"elapsed_seconds":Infinity}',b'{"event":"TCP_OPEN","attempts":1,"elapsed_seconds":1e999}']:
   self.rejected(raw=prefix+part)
 def test_nested_ignored_fields_stay_private_and_field_count_rejected(self):
  prefix=json.dumps(self.rows()[0]).encode()+b"\n"
  raw=prefix+b'{"event":"PRECHECK_BLOCKED","extra":'+b"["*500+b'"PRIVATE_SENTINEL"'+b"]"*500+b"}"
  result,_,_,output=self.collect(raw=raw);self.assertIs(result,False);self.assertNotIn("PRIVATE_SENTINEL",output)
  rows=self.rows();rows[0].update({"extra"+str(i):0 for i in range(21)});self.rejected(rows)
 def test_attempt_number_bounds_and_booleans(self):
  for kind,index in [("timeout",3),("failure",2)]:
   for value in [-1,302,True,False,1.0,"3",None,{},[]]+([0] if kind=="failure" else []):
    rows=self.rows(kind);rows[index]["attempts"]=value
    with self.subTest(kind=kind,value=value):self.rejected(rows)
 def test_elapsed_numbers_are_finite_nonnegative_and_bounded(self):
  for index in [2,3]:
   for value in [-.1,328.1,True,False,"10",None,{},[],float("nan"),float("inf")]:
    rows=self.rows();rows[index]["elapsed_seconds"]=value
    with self.subTest(index=index,value=value):self.rejected(rows)
  rows=self.rows();rows[2]["elapsed_seconds"]=11;self.rejected(rows)
 def test_binding_bool_and_wrong_scope_rejected(self):
  for key,value in [("attempt",True),("run_id",39999999999),("head","b"*40),("event","ORACLE_ROUTE_BLOCKED")]:
   rows=self.rows();rows[0][key]=value;self.rejected(rows)
 def test_duplicate_and_contradictory_tcp_records_rejected_atomically(self):
  rows=self.rows()
  for extra in [rows[0],rows[2],rows[3],dict(event="TCP_OPEN",attempts=1,elapsed_seconds=1),dict(event="SSH_PROBE_STARTED",user="ubuntu"),dict(event="SSH_AUTHENTICATED",user="ubuntu",uid=1000)]:self.rejected(rows+[extra])
  rows=self.rows();rows[2],rows[3]=rows[3],rows[2];self.rejected(rows)
  rows=self.rows();del rows[2];self.rejected(rows)
 def test_reordered_stages_and_impossible_zero_attempt_failure_rejected(self):
  for first,second in [(1,2),(2,3),(2,4),(3,4)]:
   rows=self.rows("failure");rows[first],rows[second]=rows[second],rows[first]
   with self.subTest(first=first,second=second):self.rejected(rows)
  rows=self.rows();rows[3]["attempts"]=0;rows[2]["event"]="NETWORK_UNREACHABLE";self.rejected(rows)
  rows=self.rows("failure");rows[2]["elapsed_seconds"]=300.001;self.rejected(rows)
 def test_later_malformed_ssh_does_not_export_earlier_tcp_receipt(self):
  rows=self.rows("failure");rows[4]["elapsed_seconds"]="PRIVATE_SENTINEL";self.rejected(rows)
 def test_ssh_field_bounds_and_untrusted_classes(self):
  for key,values in [("ssh_exit",[-1,256,True,False,1.0,"255",None]),("error_classes",[["AUTH_FAILURE","TCP_TIMEOUT"],[{}],[[]],[1],["PRIVATE_SENTINEL"],{},"AUTH_FAILURE",None]),("stderr_sha256",["x"*64,"c"*65,1,None,"PRIVATE_SENTINEL"])]:
   for value in values:
    rows=self.rows("failure");rows[4][key]=value
    with self.subTest(key=key,value=value):self.rejected(rows)
 def test_duplicate_ssh_terminal_not_evidence(self):
  rows=self.rows("failure");self.rejected(rows+[rows[4]])
  timeout=dict(rows[4],event="SSH_WALL_TIMEOUT");self.rejected(rows+[timeout])
 def test_partial_remote_abort_cannot_invent_tcp_counts_or_ssh_failure(self):
  for prefix in [self.rows()[:1],self.rows()[:2],self.rows()[:3]]:
   rows=prefix+[dict(event="ORACLE_ROUTE_BLOCKED",error_type="RemoteAbort",raw_error_exported=False)]
   result,events,_,output=self.collect(rows);self.assertIs(result,False)
   self.assertFalse(any(r["event"] in {"GUEST_TCP_READINESS_RESULT","GUEST_SSH_RESULT","GUEST_AUTHENTICATED"} for r in events))
   self.assertIn("ORACLE_ROUTE_BLOCKED",output)
 def test_transport_failure_or_cancellation_consumes_attempt_without_retry(self):
  for exc in [g.ProbeError("probe_wall_timeout"),KeyboardInterrupt(),SystemExit(3)]:
   p=g.PreparedProbe(B,"fixture",None,None);p.ready=True;p.call=mock.Mock(side_effect=exc)
   with contextlib.redirect_stdout(io.StringIO()) as out,self.assertRaises(type(exc)):p.run_once()
   self.assertEqual("",out.getvalue());self.assertTrue(p.attempted)
   with self.assertRaises(g.ProbeError):p.run_once()
   p.call.assert_called_once()
 def test_negative_process_exit_never_becomes_auth_success(self):
  result,events,_,_=self.collect(self.rows("success"),rc=-15)
  self.assertIs(result,False);self.assertFalse(any(r["event"]=="GUEST_AUTHENTICATED" for r in events))
 def test_malformed_receipt_cannot_skip_controller_stop(self):
  c=Clock();client=Client(c);p=g.PreparedProbe(B,"fixture",None,None);p.ready=True
  p.call=mock.Mock(return_value=(3,b'{"PRIVATE_SENTINEL":',b""))
  with mock.patch.object(e,"wall_deadline",return_value=contextlib.nullcontext()),contextlib.redirect_stdout(io.StringIO()) as out:
   code=e.trial(client,guest_probe=p,clock=c.now,sleep=c.sleep,out=lambda *_a,**_kw:None)
  self.assertEqual(3,code);self.assertEqual(["start","stop"],client.actions);p.call.assert_called_once();self.assertNotIn("PRIVATE_SENTINEL",out.getvalue())


class BootWindowTests(unittest.TestCase):
 """Start-anchored virtual-clock matrix; every provider/SSH operation is fake."""
 def run_case(self, *, running=0, io_seconds=0, stop_error=None, probe_error=None, signal_at=None, sink_error=None):
  c=Clock();events=[];actions=[];budgets=[];started=None;cancelled=False
  class Provider:
   def backup(self):pass
   def state(self):
    c.sleep(io_seconds)
    if not actions:return "stopped",True
    if actions[-1][0]=="stop" and stop_error is None:return "stopped",True
    return ("running",False) if c.now()-started >= running else ("starting",False)
   def action(self,action):
    nonlocal started,cancelled
    actions.append((action,c.now()))
    if action=="start":started=c.now()
    c.sleep(io_seconds)
    if signal_at==action:
     e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
    if action=="stop" and stop_error:raise e.ControlError(stop_error)
  class Guest:
   binding=B
   def run_once(self, *, max_seconds):
    budgets.append(max_seconds)
    if signal_at=="probe":e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
    c.sleep(max_seconds)
    if probe_error:raise probe_error
    return False
  def report(event,**fields):
   nonlocal cancelled
   events.append(dict(event=event,**fields))
   if sink_error==event:raise BrokenPipeError("PRIVATE_SENTINEL")
   if signal_at=="stop_twice" and event in {"TRIAL_STATE","ORDINARY_STOP_SUBMITTING"}:
    if event=="TRIAL_STATE" and cancelled:return
    cancelled=True;e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
  before={sig:e.signal.getsignal(sig) for sig in (e.signal.SIGTERM,e.signal.SIGINT)}
  with mock.patch.object(e,"wall_deadline",return_value=contextlib.nullcontext()):
   try:result=e.trial(Provider(),guest_probe=Guest(),clock=c.now,sleep=c.sleep,out=report)
   except BaseException as exc:result=exc
  self.assertEqual(before,{sig:e.signal.getsignal(sig) for sig in before})
  return result,actions,budgets,events,c.now()-started
 def test_delayed_running_and_slow_http_stop_submit_by_420(self):
  for running in (0,180,240,300,340,345,350,385,390,420,600):
   for io_seconds in (0,5,10):
    with self.subTest(running=running,io_seconds=io_seconds):
     result,actions,budgets,events,total=self.run_case(running=running,io_seconds=io_seconds)
     self.assertEqual(3,result);self.assertEqual(["start","stop"],[x[0] for x in actions])
     self.assertLessEqual(actions[1][1]-actions[0][1],420);self.assertLessEqual(total,600)
     self.assertLessEqual(len(budgets),1)
     for budget in budgets:self.assertLessEqual(budget,338);self.assertGreaterEqual(budget,38)
     self.assertFalse(any(x["event"]=="RUNNING_GUEST_WINDOW" for x in events))
 def test_late_running_shrinks_budget_instead_of_resetting_start_clock(self):
  result,actions,budgets,events,total=self.run_case(running=300)
  self.assertEqual([83],budgets);self.assertLessEqual(actions[1][1]-actions[0][1],420)
  row=next(x for x in events if x["event"]=="GUEST_DIAGNOSTIC_STARTED")
  self.assertEqual((300,420,180),(row["seconds_since_start"],row["stop_submit_by_seconds"],row["confirmation_reserve_seconds"]))
 def test_stop_refusal_and_unknown_result_never_retry_or_claim_stopped(self):
  for failure in ("http_403","transport_failed"):
   result,actions,_,events,total=self.run_case(running=340,io_seconds=10,stop_error=failure)
   self.assertEqual(4,result);self.assertEqual(["start","stop"],[x[0] for x in actions])
   self.assertEqual("STOP_NOT_CONFIRMED",events[-1]["event"]);self.assertLessEqual(total,600)
 def test_graceful_cancel_at_start_probe_and_again_during_stop(self):
  for phase in ("start","probe","stop_twice"):
   with self.subTest(phase=phase):
    result,actions,_,events,total=self.run_case(signal_at=phase)
    self.assertEqual(3,result);self.assertEqual(["start","stop"],[x[0] for x in actions])
    self.assertEqual("STOPPED_OBSERVED",events[-1]["event"])
 def test_unexpected_exception_and_interrupt_still_enter_stop(self):
  for exc in (RuntimeError("PRIVATE_SENTINEL"),KeyboardInterrupt(),SystemExit(7)):
   result,actions,_,events,total=self.run_case(probe_error=exc)
   self.assertEqual(["start","stop"],[x[0] for x in actions]);self.assertEqual("STOPPED_OBSERVED",events[-1]["event"])
   self.assertNotIn("PRIVATE_SENTINEL",json.dumps(events))
   self.assertEqual(3,result) if isinstance(exc,Exception) else self.assertIs(result,exc)
 def test_closed_logs_cannot_prevent_stop_submission(self):
  for event in ("START_SUBMITTING","TRIAL_CONTAINMENT","ORDINARY_STOP_SUBMITTING","STOP_STATE"):
   result,actions,_,events,total=self.run_case(sink_error=event)
   self.assertEqual(3,result);self.assertEqual(["start","stop"],[x[0] for x in actions])
 def test_cancellation_guard_restores_handlers_after_exception(self):
  previous={sig:e.signal.getsignal(sig) for sig in (e.signal.SIGINT,e.signal.SIGTERM)}
  with self.assertRaisesRegex(e.ControlError,"trial_cancelled"):
   with e.trial_cancellation():e.signal.getsignal(e.signal.SIGINT)(e.signal.SIGINT,None)
  self.assertEqual(previous,{sig:e.signal.getsignal(sig) for sig in previous})
 def test_repeated_cancel_during_unwind_is_latched(self):
  with e.trial_cancellation() as contain:
   with self.assertRaisesRegex(e.ControlError,"trial_cancelled"):
    e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
   e.signal.getsignal(e.signal.SIGINT)(e.signal.SIGINT,None)
   contain()
   e.signal.getsignal(e.signal.SIGTERM)(e.signal.SIGTERM,None)
 def test_budget_rejects_nonfinite_type_or_out_of_range_before_network(self):
  for value in (True,False,None,"38",37.99,338.01,float("nan"),float("inf")):
   p=g.PreparedProbe(B,"fixture",None,None);p.ready=True;p.call=mock.Mock()
   with self.subTest(value=value),self.assertRaises(g.ProbeError):p.run_once(max_seconds=value)
   p.call.assert_not_called()
 def test_transport_payload_and_timeout_shrink_together(self):
  p=g.PreparedProbe(B,"fixture",1,2)
  for timeout in (33,78,333):
   with mock.patch.object(g,"bounded_process",return_value=(3,b"",b"")) as process:
    p.call("probe",timeout)
   fields=json.loads(process.call_args.kwargs["payload"])
   self.assertEqual(timeout-5,fields["probe_seconds"]);self.assertEqual(timeout,process.call_args.kwargs["timeout"])
   self.assertIn("StrictHostKeyChecking=yes",process.call_args.args[0])
 def test_bare_auth_abort_auth_and_nonzero_ssh_fail_closed(self):
  bound=dict(event="RUN_BOUND_GUEST_DIAGNOSTIC",**B);auth=dict(event="SSH_AUTHENTICATED",user="ubuntu",uid=1000)
  variants=[[bound,auth],[bound,dict(event="ORACLE_ROUTE_BLOCKED"),auth]]
  rows=ReceiptEvidenceTests().rows("success");rows[4]["ssh_exit"]=255;variants.append(rows)
  for rows in variants:
   result,events,_,_=ReceiptEvidenceTests().collect(rows,rc=0)
   self.assertIs(result,False);self.assertFalse(any(x["event"]=="GUEST_AUTHENTICATED" for x in events))
 def test_shortened_budget_rejects_impossible_remote_durations(self):
  for event,value in (("TCP_OPEN",14),("SSH_PROBE_FINISHED",15.01)):
   rows=ReceiptEvidenceTests().rows("success")
   next(row for row in rows if row["event"]==event)["elapsed_seconds"]=value
   p=g.PreparedProbe(B,"fixture",None,None);p.ready=True
   p.call=mock.Mock(return_value=(0,("\n".join(map(json.dumps,rows))+"\n").encode(),b""))
   with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(g.ProbeError):p.run_once(max_seconds=38)
 def test_remote_budget_validation_and_anchor_execute_with_fake_signal(self):
  tree=ast.parse(g.REMOTE);body=next(node for node in tree.body if isinstance(node,ast.Try)).body
  begin=next(i for i,node in enumerate(body) if isinstance(node,ast.Assert) and 'set(p)' in ast.unparse(node))
  end=next(i for i,node in enumerate(body[begin:],begin) if isinstance(node,ast.Assert) and 'len(p[\'head\'])' in ast.unparse(node))
  program=compile(ast.Module(body=body[begin:end],type_ignores=[]),"offline_remote_budget","exec")
  import math,types
  fields=dict(B,phase="probe",source="fixture",source_sha="a"*64,probe_seconds=83)
  timer=mock.Mock();ns=dict(p=fields,math=math,time=types.SimpleNamespace(monotonic=lambda:5),signal=types.SimpleNamespace(setitimer=timer,ITIMER_REAL=0),remote_started=0)
  exec(program,ns);self.assertEqual(83,ns["remote_deadline"]);timer.assert_called_once_with(0,78)
  for duration in (27,329,True,float("nan"),float("inf")):
   ns["p"]={**fields,"probe_seconds":duration}
   with self.subTest(duration=duration),self.assertRaises(AssertionError):exec(program,ns)
  ns["p"]={**fields,"phase":"preflight"}
  with self.assertRaises(AssertionError):exec(program,ns)


class HostObserverTests(unittest.TestCase):
 """Only mocked systemctl/Docker. Real subprocess tests use harmless Python fixtures."""
 def execute(self, *, units=None, docker=False, daemon=True, present=(), container_status='exited', restart_count=0, fail_at=None, malformed=None):
  import types
  c=Clock();commands=[];output=[]
  selected=quiet_host_observation()['units'] if units is None else units
  if docker and daemon:
   selected=[dict(row,load='loaded',active='active',sub='running') if row['unit']=='docker.service' else row for row in selected]
  def command(argv):
   commands.append(argv);c.sleep(.1)
   if fail_at==len(commands):raise subprocess.TimeoutExpired(argv,2)
   if argv[0]=='/usr/bin/id':return 0,'1001\n'
   if argv[0]=='/usr/bin/systemctl':
    text='\n\n'.join('\n'.join((f"Id={r['unit']}",f"LoadState={r['load']}",f"ActiveState={r['active']}",f"SubState={r['sub']}")) for r in selected)+'\n'
    return (0,malformed if malformed is not None else text)
   if 'ls' in argv:return 0,'\n'.join(name+' '+container_status for name in present)+('\n' if present else '')
   return 0,'\n'.join('/'+name+' '+container_status+' '+str(restart_count) for name in present)+'\n'
  tree=ast.parse(safe.HOST_OBSERVER)
  body=[node for node in tree.body if not isinstance(node,(ast.Import,ast.ImportFrom)) and not(isinstance(node,ast.FunctionDef) and node.name=='command')]
  ns=dict(time=types.SimpleNamespace(monotonic=c.now),os=types.SimpleNamespace(getppid=lambda:99,path=types.SimpleNamespace(lexists=lambda p:docker if p=='/usr/bin/docker' else False,exists=lambda _:False)),signal=types.SimpleNamespace(SIGTERM=15,SIGALRM=14,ITIMER_REAL=0,getitimer=lambda _:(0,0),signal=lambda *_:None,setitimer=lambda *_:None),ctypes=types.SimpleNamespace(CDLL=lambda _:types.SimpleNamespace(prctl=lambda *_:0)),command=command,json=json,print=lambda value,**_:output.append(value))
  exec(compile(ast.Module(body=body,type_ignores=[]),'offline_guest_metadata','exec'),ns)
  self.assertEqual(1,len(output));self.assertLessEqual(len(commands),3)
  return json.loads(output[0]),commands
 def test_quiet_snapshot_missing_docker_is_explicit_and_complete(self):
  row,commands=self.execute();checked=safe.validate_host_observation(row)
  self.assertEqual('QUIET_IN_KNOWN_SCOPE',checked['result']);self.assertEqual('NOT_INSTALLED',checked['docker'])
  self.assertEqual(2,len(commands));self.assertEqual(['/usr/bin/id','-u'],commands[0])
  self.assertEqual(set(safe.HOST_UNITS),set(commands[1][4:]));self.assertEqual([],checked['containers'])
 def test_known_timer_and_service_pairs_are_in_scope(self):
  self.assertEqual(19,len(safe.HOST_UNITS))
  for stem in ('bridge-ben-healthcheck','dds3-healthcheck','dds3-cert-renew','universal-video-maintenance'):
   for ending in ('.service','.timer'):self.assertIn(stem+ending,safe.HOST_UNITS)
 def test_every_active_workload_or_timer_blocks_and_skips_docker(self):
  for name in safe.HOST_UNITS:
   if name in ('docker.service','docker.socket'):continue
   units=quiet_host_observation()['units'];next(r for r in units if r['unit']==name).update(active='active',sub='running')
   with self.subTest(unit=name):
    row,commands=self.execute(units=units,docker=True)
    self.assertEqual('ACTIVE_OR_TRANSITION',safe.validate_host_observation(row)['result']);self.assertEqual(2,len(commands))
 def test_missing_known_unit_failed_or_unrecognized_state_cannot_be_quiet(self):
  variants=[quiet_host_observation()['units'][:-2]]
  for field,value in (('active','failed'),('sub','unexpected'),('load','error')):
   rows=quiet_host_observation()['units'];rows[0][field]=value;variants.append(rows)
  for units in variants:
   row,_=self.execute(units=units);self.assertNotEqual('QUIET_IN_KNOWN_SCOPE',row['result'])
 def test_docker_absent_known_containers_not_confused_with_denied(self):
  row,commands=self.execute(docker=True,present=());self.assertEqual('OBSERVED',row['docker'])
  self.assertEqual([{'name':name,'status':'absent'} for name in safe.HOST_CONTAINERS],row['containers'])
  self.assertEqual('QUIET_IN_KNOWN_SCOPE',safe.validate_host_observation(row)['result']);self.assertEqual(3,len(commands))
  denied,_=self.execute(docker=True,fail_at=3);self.assertEqual('UNKNOWN',safe.validate_host_observation(denied)['result'])
 def test_inactive_daemon_is_not_started_or_contacted(self):
  row,commands=self.execute(docker=True,daemon=False)
  self.assertEqual('UNKNOWN',safe.validate_host_observation(row)['result']);self.assertEqual(2,len(commands))
 def test_socket_activation_and_ambiguous_absence_block_without_docker(self):
  for load,state,sub in (('loaded','active','listening'),('loaded','inactive','dead'),('error','inactive','dead')):
   units=quiet_host_observation()['units'];next(r for r in units if r['unit']=='docker.socket').update(load=load,active=state,sub=sub)
   row,commands=self.execute(units=units,docker=True)
   self.assertEqual('UNKNOWN',safe.validate_host_observation(row)['result']);self.assertEqual(2,len(commands))
  units=quiet_host_observation()['units'];next(r for r in units if r['unit']=='docker.service')['load']='loaded'
  row,commands=self.execute(units=units,docker=False)
  self.assertEqual('UNKNOWN',safe.validate_host_observation(row)['result']);self.assertEqual(2,len(commands))
 def test_declared_active_cannot_be_downgraded_to_quiet(self):
  row=quiet_host_observation();row['result']='ACTIVE_OR_TRANSITION'
  self.assertEqual('ACTIVE_OR_TRANSITION',safe.validate_host_observation(row)['result'])
 def test_docker_exact_status_only_and_existing_container_is_blocked(self):
  row,commands=self.execute(docker=True,present=safe.HOST_CONTAINERS)
  self.assertEqual('UNKNOWN',safe.validate_host_observation(row)['result'])
  self.assertEqual(3,len(commands));self.assertTrue(all(set(r)=={'name','status'} for r in row['containers']))
  self.assertIn('{{.Names}} {{.State}}',commands[-1])
  self.assertFalse(any('env' in arg.lower() or arg in ('inspect','exec','run','start','restart','stop') for cmd in commands for arg in cmd))
 def test_even_forged_quiet_existing_stopped_container_is_blocked(self):
  row,_=self.execute(docker=True,present=safe.HOST_CONTAINERS,container_status='exited');row['result']='QUIET_IN_KNOWN_SCOPE'
  self.assertEqual('UNKNOWN',safe.validate_host_observation(row)['result'])
 def test_all_active_container_states_block(self):
  for state in ('running','paused','restarting','removing'):
   row,_=self.execute(docker=True,present=safe.HOST_CONTAINERS,container_status=state)
   self.assertEqual('ACTIVE_OR_TRANSITION',safe.validate_host_observation(row)['result'])
 def test_any_command_timeout_or_malformed_output_is_blocked(self):
  for index in range(1,4):
   row,_=self.execute(docker=True,present=safe.HOST_CONTAINERS,fail_at=index)
   if index==1:
    with self.assertRaises(ValueError):safe.validate_host_observation(row)
   else:self.assertEqual('UNKNOWN',safe.validate_host_observation(row)['result'])
  row,_=self.execute(malformed='PRIVATE_SENTINEL');self.assertEqual('UNKNOWN',row['result']);self.assertNotIn('PRIVATE_SENTINEL',json.dumps(row))
 def test_forged_quiet_active_missing_timer_or_unknown_docker_never_passes(self):
  for change in ('active','missing_timer','docker','remote_exception'):
   row=quiet_host_observation()
   if change=='active':row['units'][0].update(active='active',sub='running')
   elif change=='missing_timer':row['units']=[x for x in row['units'] if x['unit']!='bridge-ben-healthcheck.timer']
   elif change=='docker':row['docker']='UNKNOWN'
   else:row['result']='UNKNOWN'
   self.assertNotEqual('QUIET_IN_KNOWN_SCOPE',safe.validate_host_observation(row)['result'])
 def test_schema_private_fields_bad_uid_duration_duplicates_and_counts(self):
  import copy
  cases=[];base=quiet_host_observation()
  for k,v in (('uid',True),('uid',-1),('elapsed_seconds',8.001),('elapsed_seconds',float('nan')),('units',{}),('scope','PRIVATE_SENTINEL')):
   row=copy.deepcopy(base);row[k]=v;cases.append(row)
  row=copy.deepcopy(base);row['units'].append(row['units'][0]);cases.append(row)
  row=copy.deepcopy(base);row['units'][0]['env']='PRIVATE_SENTINEL';cases.append(row)
  row=copy.deepcopy(base);row['containers']=[dict(name=safe.HOST_CONTAINERS[0],status='exited',restart_count=True)];cases.append(row)
  for row in cases:
   with self.subTest(row=row),self.assertRaises((ValueError,TypeError)):safe.validate_host_observation(row)
  for raw in ('{"uid":1,"uid":2}','x'*4097,'['*2000):
   with self.assertRaises((ValueError,RecursionError)):safe.parse_host_observation(raw)
 def test_active_unknown_and_missing_observer_receipts_cannot_authenticate(self):
  for kind in ('active','unknown','missing','uid_mismatch'):
   rows=ReceiptEvidenceTests().rows('success');obs=next(r for r in rows if r['event']=='HOST_OBSERVER_RESULT')
   if kind=='active':obs['observation']['units'][0].update(active='active',sub='running')
   elif kind=='unknown':obs['observation']['docker']='UNKNOWN'
   elif kind=='missing':rows.remove(obs)
   else:obs['observation']['uid']=999
   result,events,_,_=ReceiptEvidenceTests().collect(rows,rc=0)
   self.assertIs(result,False);self.assertFalse(any(r['event']=='GUEST_AUTHENTICATED' for r in events))
 def test_observer_payload_is_within_input_and_single_row_bounds(self):
  source=Path('ops/ibm_ssh_probe_safe.py').read_text()
  payload=json.dumps(dict(B,phase='probe',source=source,source_sha=g.GUEST_SOURCE_SHA,probe_seconds=328)).encode()
  self.assertLess(len(payload),32768)
  self.assertLess(len(json.dumps(dict(event='HOST_OBSERVER_RESULT',observation=quiet_host_observation())).encode()),4096)
  compile(safe.HOST_OBSERVER,'guest_host_observer','exec')
 def test_host_watchdog_parent_death_and_unconditional_group_cleanup(self):
  body=safe.HOST_OBSERVER
  self.assertLess(body.index('signal.signal(signal.SIGTERM,aborted)'),body.index('prctl(1,signal.SIGTERM)'))
  self.assertIn('parent>1',body);self.assertIn('signal.setitimer(signal.ITIMER_REAL,7.8)',body)
  self.assertIn('try:os.killpg(p.pid,signal.SIGKILL)',body)
  self.assertNotIn('if p.poll() is None:os.killpg',body)
  self.assertIn("'--host','unix:///var/run/docker.sock'",body)
  self.assertIn("'--config','/proc/0/bridge-ibm-observer'",body)
 def test_actual_harmless_child_output_is_bounded_and_reaped(self):
  import ctypes,selectors,signal
  nodes=[n for n in ast.parse(safe.HOST_OBSERVER).body if isinstance(n,(ast.ClassDef,ast.FunctionDef))]
  ns=dict(ctypes=ctypes,selectors=selectors,signal=signal,os=os,subprocess=subprocess,time=time,END=time.monotonic()+5)
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'offline_guest_command','exec'),ns)
  self.assertEqual((0,'fixture\n'),ns['command']([sys.executable,'-I','-B','-c',"print('fixture')"]))
  with self.assertRaises(ns['Blocked']):ns['command']([sys.executable,'-I','-B','-c',"print('x'*8192)"])
  ns['END']=time.monotonic()+.02
  with self.assertRaises(ns['Blocked']):ns['command']([sys.executable,'-I','-B','-c','import time;time.sleep(10)'])
 def test_controller_stops_once_after_active_observer(self):
  rows=ReceiptEvidenceTests().rows('success');next(r for r in rows if r['event']=='HOST_OBSERVER_RESULT')['observation']['units'][0].update(active='active',sub='running')
  p=g.PreparedProbe(B,'fixture',None,None);p.ready=True;p.call=mock.Mock(return_value=(0,('\n'.join(map(json.dumps,rows))+'\n').encode(),b''))
  clock=Clock();client=Client(clock)
  with mock.patch.object(e,'wall_deadline',return_value=contextlib.nullcontext()),contextlib.redirect_stdout(io.StringIO()):
   result=e.trial(client,guest_probe=p,clock=clock.now,sleep=clock.sleep,out=lambda *_a,**_kw:None)
  self.assertEqual(3,result);self.assertEqual(['start','stop'],client.actions);p.call.assert_called_once()


if __name__=="__main__":unittest.main()

