"""DRAFT isolated CI bootstrap; default describe only. No production dispatch."""
import argparse,hashlib,json,os,re,select,stat,subprocess,sys,tempfile,time
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
def run(args):
 need(args.scope_approved and sys.platform=="linux" and os.getuid()==0 and os.getgid()==0)
 need(os.environ.get("GITHUB_ACTIONS")=="true")
 need(Path("/proc/1/comm").read_text().strip()=="systemd" and Path("/sys/fs/cgroup/cgroup.controllers").is_file())
 # Values are never printed or forwarded. Root child environment is a fixed allowlist.
 need(not any(name.endswith(("_DATABASE_URL","_SSH_PRIVATE_KEY","_API_KEY","_TOKEN")) for name in os.environ))
 need(all(re.fullmatch("[0-9a-f]{64}",pin or "") for pin in (args.runtime_pin,args.suite_pin,args.bootstrap_pin)))
 read_checked(Path(__file__).resolve(),args.bootstrap_pin)
 root=stage(args);sys.path.insert(0,str(root))
 import stagea_control as control
 outer=control.new_unit(SOURCE,str(os.getpid())+"-1",outer=True)
 body="from pathlib import Path\nfrom qualification_suite import suite_main\nraise SystemExit(suite_main(Path("+repr(str(root))+")))\n"
 launcher=None;outer_main=None;raw=b"";result=78;natural=False
 try:
  launcher=subprocess.Popen(control.command(outer,control.gated_code(root,body),outer=True,network=True),
      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
      env={"PATH":"/usr/bin:/bin"},close_fds=True,bufsize=0)
  need(select.select([launcher.stdout],[],[],5)[0] and os.read(launcher.stdout.fileno(),5)==b"GATE\n")
  state,group,inode=control.identity(outer,300,network=True);outer_main=int(state["MainPID"])
  launcher.stdin.write(b"G");launcher.stdin.flush()
  # Leave communicate to close stdin; it must not be passed a manually closed FD.
  raw,_=launcher.communicate(timeout=305)
  need(len(raw)<=65536)
  result=launcher.returncode
  from stagea_watchdog import profile
  natural=profile().empty(group,inode)
  need(natural)
  values=[json.loads(line) for line in raw.splitlines()]
  need(len(values)==5 and values[-1]["qualification"] in ("PASS_ACTUAL_CANDIDATE_SYNTHETIC","FAIL_NOT_QUALIFIED"))
 finally:
  contain(control,root,outer,outer_main,launcher)
 # Only sanitized fixed metadata from our pinned source can enter the log.
 for value in values:print(json.dumps(value,sort_keys=True),flush=True)
 print(json.dumps({"harness_natural_drain":natural,"harness_containment_verified":True,
      "root_pid1_executed":True,"production_ready":False,"live_sql":False}),flush=True)
 return 0 if result==0 else 78
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
