"""DRAFT isolated CI bootstrap; default describe only. No production dispatch."""
import argparse,hashlib,json,math,os,re,select,stat,subprocess,sys,tempfile,time
from pathlib import Path
SOURCE="c36d581c22ca2ba5457e8db5019c5cf697542be4"
CASES=("payload","supervisor-kill","stopped-systemctl-supervisor-kill","stopped-systemd-run-supervisor-kill")
class Refused(RuntimeError):pass
def need(value):
 if not value:raise Refused("CI_SCOPE_OR_PIN_REFUSED")
def read_checked(path,pin):
 need(path.is_file() and not path.is_symlink())
 need(all(not parent.is_symlink() for parent in path.parents))
 before=path.stat();data=path.read_bytes();after=path.stat()
 need(stat.S_ISREG(before.st_mode) and hashlib.sha256(data).hexdigest()==pin and
      (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
      (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns))
 return data
def write_new(path,data):
 path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
 try:
  need(os.write(fd,data)==len(data));os.fsync(fd)
 finally:os.close(fd)
def stage(args):
 source=args.source_root
 need(source.is_absolute() and source.is_dir() and not source.is_symlink())
 raw=read_checked(source/"runtime-manifest.json",args.runtime_pin);manifest=json.loads(raw)
 need(manifest["source_sha"]==SOURCE and len(manifest["files"])==9)
 root=Path(tempfile.mkdtemp(prefix="bridge-stagea-ci-",dir="/run"))
 need(root.parent==Path("/run") and root.name.startswith("bridge-stagea-ci-") and root.stat().st_uid==0)
 for name,pin in manifest["files"].items():
  relative=Path(name);need(not relative.is_absolute() and ".." not in relative.parts)
  write_new(root/relative,read_checked(source/relative,pin))
 write_new(root/"runtime-manifest.json",raw)
 write_new(root/"qualification_suite.py",read_checked(source/"qualification_suite.py",args.suite_pin))
 (root/"evidence").mkdir(mode=0o700)
 return root
def safe_event(root,case):
 path=root/"evidence"/(case+".json")
 return json.loads(path.read_bytes()) if path.is_file() else None
def contain(control,root,outer,outer_main,launcher):
 failed=False
 if launcher:
  try:
   if launcher.poll() is None:launcher.kill()
   launcher.wait(timeout=5)
  except Exception:failed=True
  for stream in (launcher.stdin,launcher.stdout):
   try:
    if stream and not stream.closed:stream.close()
   except Exception:failed=True
 # Outer producer is stopped FIRST. Candidate units are independent PID1
 # children and are then contained by exact, pre-launch recorded names.
 try:
  control.ctl("stop",outer);control.ctl("reset-failed",outer)
 except Exception:failed=True
 for case in CASES:
  intent=root/"evidence"/(case+".intent.json")
  if not intent.exists():continue
  try:
   record=json.loads(intent.read_bytes());unit=record["control_unit"]
   need(outer_main and re.fullmatch(control.CONTROL,unit) and
        unit.startswith("bridge-stagea-control-"+SOURCE[:12]+"-"+str(outer_main)+"-1-"))
  except Exception:failed=True;continue
  try:
   control.ctl("stop",unit);control.ctl("reset-failed",unit)
   group=Path("/sys/fs/cgroup/system.slice/"+unit)
   need(not group.exists() or "populated 0" in (group/"cgroup.events").read_text())
  except Exception:failed=True
  try:
   event=safe_event(root,case)
   if event and event["worker_unit"]:
    worker=event["worker_unit"];pid=record.get("supervisor_pid")
    need(pid and re.fullmatch(r"bridge-native-ro-[0-9a-f]{12}-[0-9]{1,20}-[0-9]{1,6}-[0-9a-f]{16}\.service",worker)
         and worker.startswith("bridge-native-ro-"+SOURCE[:12]+"-"+str(pid)+"-1-"))
    control.ctl("stop",worker);control.ctl("reset-failed",worker)
    group=Path("/sys/fs/cgroup/system.slice/"+worker)
    need(not group.exists() or "populated 0" in (group/"cgroup.events").read_text())
  except Exception:failed=True
 try:
  group=Path("/sys/fs/cgroup/system.slice/"+outer)
  need(not group.exists() or "populated 0" in (group/"cgroup.events").read_text())
 except Exception:failed=True
 need(not failed)
PHASE_CODES={"preflight":"PREFLIGHT_REFUSED","staging":"STAGING_REFUSED",
 "outer_launch":"OUTER_LAUNCH_REFUSED","identity":"OUTER_IDENTITY_REFUSED",
 "child_capture":"CHILD_CAPTURE_REFUSED","receipt_validation":"RECEIPTS_REFUSED",
 "containment":"CONTAINMENT_UNPROVEN"}
OUTPUT_CODES={"CHILD_CAPTURE_TIMEOUT","CHILD_OUTPUT_LIMIT","CHILD_EXIT_NONZERO"}
MAX_CHILD_BYTES=65536
class OutputRefused(Refused):
 def __init__(self,code):
  need(code in OUTPUT_CODES);self.code=code
def emit_diagnostic(value):
 raw=json.dumps(value,sort_keys=True,separators=(",",":"));need(len(raw)<=8192)
 print(raw,flush=True)
def checkpoint(state,phase):
 need(phase in PHASE_CODES);state["phase"]=phase
 emit_diagnostic({"bootstrap_phase":phase})
def bounded_capture(launcher,raw):
 # Same305s ceiling as communicate; store at most cap+1, including on failure.
 deadline=time.monotonic()+305;launcher.stdin.close()
 while True:
  remaining=deadline-time.monotonic()
  if remaining<=0:raise OutputRefused("CHILD_CAPTURE_TIMEOUT")
  if not select.select([launcher.stdout],[],[],remaining)[0]:
   raise OutputRefused("CHILD_CAPTURE_TIMEOUT")
  chunk=os.read(launcher.stdout.fileno(),min(4096,MAX_CHILD_BYTES+1-len(raw)))
  if not chunk:break
  raw.extend(chunk)
  if len(raw)>MAX_CHILD_BYTES:raise OutputRefused("CHILD_OUTPUT_LIMIT")
 try:launcher.wait(timeout=max(0,deadline-time.monotonic()))
 except subprocess.TimeoutExpired:raise OutputRefused("CHILD_CAPTURE_TIMEOUT") from None
def unique_record(pairs):
 value={}
 for key,item in pairs:
  need(key not in value);value[key]=item
 return value
def typed_child_diagnostics(raw):
 # Retain only a bounded valid prefix; never echo arbitrary fields or text.
 records=[]
 overflow=len(raw)>MAX_CHILD_BYTES
 lines=bytes(raw[:MAX_CHILD_BYTES]).splitlines()
 for index,line in enumerate(lines):
  if index>=5:return records,"CHILD_OUTPUT_LIMIT" if overflow else "CHILD_RECORD_LIMIT"
  if len(line)>4096:return records,"CHILD_RECORD_LIMIT"
  try:
   value=json.loads(line,object_pairs_hook=unique_record)
   need(type(value) is dict)
   if index<4:
    need(value.get("case")==CASES[index])
    need(value.get("verdict") in ("PASS","FAIL_NOT_QUALIFIED"))
    flags={"production":False,"live_sql":False}
    if value["verdict"]=="PASS":
     flags.update({"actual_candidate_supervise_body":True,"natural_control_drain":True,
      "natural_worker_drain":True,"candidate_admin_clients_gone":True,
      "outer_launcher_is_harness_owned":True,"external_cleanup_counted_as_candidate_pass":False})
     seconds=value["seconds_since_control_activation"]
     need(type(seconds) in (int,float) and math.isfinite(seconds) and 0<=seconds<=62)
     need(type(value["candidate_admin_bound_seconds"]) is int and value["candidate_admin_bound_seconds"]==62)
     fields={"case","verdict","seconds_since_control_activation","candidate_admin_bound_seconds",*flags}
    else:fields={"case","verdict",*flags}
   else:
    flags={"frozen_supervisor_qualified":False,"production_ready":False,
           "external_containment_is_not_candidate_pass":True}
    need(value.get("qualification") in ("PASS_ACTUAL_CANDIDATE_SYNTHETIC","FAIL_NOT_QUALIFIED"))
    need(type(value["cases"]) is int and value["cases"]==4)
    fields={"qualification","cases",*flags}
   need(set(value)==fields)
   need(all(type(value[key]) is bool and value[key] is flag for key,flag in flags.items()))
  except Exception:return records,"CHILD_RECORD_INVALID"
  records.append(value)
 return records,"CHILD_OUTPUT_LIMIT" if overflow else ("COMPLETE" if len(records)==5 else "PARTIAL")
def run(args):
 state={"phase":"preflight"};primary_failure=None;cleanup_status="NO_OUTER_LAUNCH_ATTEMPT"
 launcher=None;outer_main=None;root=None;outer=None;control=None
 raw=bytearray();result=None;natural=False;launch_attempted=False;identity_verified=False;diagnostics_emitted=False
 try:
  checkpoint(state,"preflight")
  need(args.scope_approved and sys.platform=="linux" and os.getuid()==0 and os.getgid()==0)
  need(os.environ.get("GITHUB_ACTIONS")=="true")
  need(Path("/proc/1/comm").read_text().strip()=="systemd" and Path("/sys/fs/cgroup/cgroup.controllers").is_file())
  # Values are never printed or forwarded. Root child environment is a fixed allowlist.
  need(not any(name.endswith(("_DATABASE_URL","_SSH_PRIVATE_KEY","_API_KEY","_TOKEN")) for name in os.environ))
  need(all(re.fullmatch("[0-9a-f]{64}",pin or "") for pin in (args.runtime_pin,args.suite_pin,args.bootstrap_pin)))
  read_checked(Path(__file__).resolve(),args.bootstrap_pin)
  checkpoint(state,"staging")
  root=stage(args);sys.path.insert(0,str(root))
  import stagea_control as control
  checkpoint(state,"outer_launch")
  outer=control.new_unit(SOURCE,str(os.getpid())+"-1",outer=True)
  body="from pathlib import Path\nfrom qualification_suite import suite_main\nraise SystemExit(suite_main(Path("+repr(str(root))+")))\n"
  launch_attempted=True
  launcher=subprocess.Popen(control.command(outer,control.gated_code(root,body),outer=True,network=True),
      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
      env={"PATH":"/usr/bin:/bin"},close_fds=True,bufsize=0)
  need(select.select([launcher.stdout],[],[],5)[0] and os.read(launcher.stdout.fileno(),5)==b"GATE\n")
  checkpoint(state,"identity")
  control_state,group,inode=control.identity(outer,300,network=True);outer_main=int(control_state["MainPID"])
  identity_verified=True
  launcher.stdin.write(b"G");launcher.stdin.flush()
  checkpoint(state,"child_capture")
  bounded_capture(launcher,raw)
  result=launcher.returncode
  checkpoint(state,"receipt_validation")
  values,child_status=typed_child_diagnostics(raw)
  emit_diagnostic({"child_diagnostics":values,"child_diagnostics_status":child_status})
  diagnostics_emitted=True
  need(child_status=="COMPLETE")
  if result!=0:raise OutputRefused("CHILD_EXIT_NONZERO")
  from stagea_watchdog import profile
  natural=profile().empty(group,inode)
  need(natural)
  need(len(values)==5 and values[-1]["qualification"] in ("PASS_ACTUAL_CANDIDATE_SYNTHETIC","FAIL_NOT_QUALIFIED"))
  need(values[-1]["qualification"]=="PASS_ACTUAL_CANDIDATE_SYNTHETIC" and all(value["verdict"]=="PASS" for value in values[:4]))
 except Exception as error:
  primary_failure={"phase":state["phase"],"error_code":error.code if type(error) is OutputRefused else PHASE_CODES[state["phase"]]}
  if state["phase"]=="identity" and type(error) is getattr(control,"IdentityRefused",None):
   if error.code in control.IDENTITY_CODES:primary_failure["identity_condition"]=error.code
 finally:
  values,child_status=typed_child_diagnostics(raw)
  if not diagnostics_emitted:
   try:emit_diagnostic({"child_diagnostics":values,"child_diagnostics_status":child_status})
   except Exception:
    if primary_failure is None:primary_failure={"phase":state["phase"],"error_code":"DIAGNOSTIC_WRITE_REFUSED"}
  if launch_attempted:
   state["phase"]="containment"
   try:emit_diagnostic({"bootstrap_phase":"containment"})
   except Exception:
    if primary_failure is None:primary_failure={"phase":"containment","error_code":"DIAGNOSTIC_WRITE_REFUSED"}
   try:
    contain(control,root,outer,outer_main,launcher);cleanup_status="VERIFIED"
   except Exception:cleanup_status="CONTAINMENT_UNPROVEN"
 passed=primary_failure is None and cleanup_status=="VERIFIED" and result==0
 emit_diagnostic({"qualification":"PASS_ACTUAL_CANDIDATE_SYNTHETIC" if passed else "REFUSED_OR_CONTAINMENT_UNPROVEN",
  "primary_failure":primary_failure,"cleanup_status":cleanup_status,"child_diagnostics_status":child_status,
  "child_exit_status":"UNOBSERVED" if result is None else ("EXIT_ZERO" if result==0 else "EXIT_NONZERO"),
  "outer_launch_attempted":launch_attempted,"outer_identity_verified":identity_verified,
  "root_pid1_executed":True if identity_verified else None,
  "harness_natural_drain":natural,"harness_containment_verified":cleanup_status=="VERIFIED",
  "production_ready":False,"live_sql":False})
 return 0 if passed else 78
def main():
 parser=argparse.ArgumentParser();parser.add_argument("--describe",action="store_true")
 parser.add_argument("--run",action="store_true");parser.add_argument("--scope-approved",action="store_true")
 parser.add_argument("--source-root",type=Path);parser.add_argument("--runtime-pin")
 parser.add_argument("--suite-pin");parser.add_argument("--bootstrap-pin")
 args=parser.parse_args()
 if not args.run:
  print(json.dumps({"mode":"DESCRIBE_ONLY","root_pid1_executed":False,"resources_created":False,
   "scope":"draft sudo/root staging + transient PID1 + private-network fixtures; pending privilege review"}));return 0
 need(args.source_root is not None)
 return run(args)
if __name__=="__main__":
 try:raise SystemExit(main())
 except Exception:
  print(json.dumps({"qualification":"REFUSED_OR_CONTAINMENT_UNPROVEN","production":False}),flush=True)
  raise SystemExit(78)
