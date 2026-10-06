"""DRAFT root/PID1 suite. Describe-only by default. Never run before scope approval."""
import argparse,hashlib,json,os,re,select,signal,stat,subprocess,sys,time
from pathlib import Path
CASES=("payload","supervisor-kill","stopped-systemctl-supervisor-kill","stopped-systemd-run-supervisor-kill")
SOURCE="c36d581c22ca2ba5457e8db5019c5cf697542be4"
class Refused(RuntimeError):pass
def need(value):
 if not value:raise Refused("QUALIFICATION_REFUSED")
def emit(value):
 raw=json.dumps(value,sort_keys=True,separators=(",",":"))
 need(len(raw)<16384);print(raw,flush=True)
def identity(pid):
 try:
  text=Path("/proc/"+str(pid)+"/stat").read_text()
  tail=text.rsplit(")",1)[1].split()
  return {"pid":pid,"start_ticks":tail[19],"state":tail[0]}
 except FileNotFoundError:return None
def same(record):
 now=identity(record["pid"]);return now is not None and now["start_ticks"]==record["start_ticks"]
def own_signal(record,sig):
 fd=os.pidfd_open(record["pid"])
 try:
  need(same(record));signal.pidfd_send_signal(fd,sig)
 finally:os.close(fd)
def group_of(pid):return Path("/proc/"+str(pid)+"/cgroup").read_text().strip()
def commit(path,value):
 # This NEW private fixture metadata contains no credential or definition bytes.
 raw=json.dumps(value,sort_keys=True,separators=(",",":")).encode()
 need(len(raw)<=32768)
 temporary=path.with_name(path.name+".new")
 fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  need(os.write(fd,raw)==len(raw));os.fsync(fd)
 finally:os.close(fd)
 os.replace(temporary,path)
 d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(d);os.close(d)
def candidate_main(root,case,event):
 import stagea_watchdog as w,stagea_fixture_worker as fake
 need(case in CASES)
 pin=hashlib.sha256((root/"runtime-manifest.json").read_bytes()).hexdigest()
 run=str(os.getpid())+"-1"
 data={"case":case,"actual_supervise_called":False,"supervisor":identity(os.getpid()),
       "run":run,"worker_unit":None,"clients":[],"fault":None,"worker_ready":False}
 original_popen=subprocess.Popen;original_read=os.read;launch=None
 control_group=group_of(os.getpid())
 def stop(record,kind):
  need(record is not None and group_of(record["pid"])==control_group)
  own_signal(record,signal.SIGSTOP)
  now=identity(record["pid"])
  need(now is not None and same(record) and now["state"] in ("T","t"))
  data["fault"]={"kind":kind,"process":record,"control_cgroup":control_group,
                 "state":now["state"]}
  commit(event,data)
 def audited(*args,**kw):
  nonlocal launch
  command=args[0] if args else kw["args"]
  administrative=type(command) is list and command and command[0] in ("/usr/bin/systemctl","/usr/bin/systemd-run")
  if administrative and command[0]=="/usr/bin/systemd-run":
   units=[value[7:] for value in command if value.startswith("--unit=")]
   need(len(units)==1);data["worker_unit"]=units[0];commit(event,data)
  process=original_popen(*args,**kw)
  if administrative:
   record=identity(process.pid)
   if record:
    try:need(group_of(record["pid"])==control_group)
    except FileNotFoundError:
     need(process.poll() is not None);record=None
    if record:
     data["clients"].append(record);need(len(data["clients"])<=32);commit(event,data)
   if command[0]=="/usr/bin/systemd-run":launch=(process,record)
   if case=="stopped-systemctl-supervisor-kill" and command[0]=="/usr/bin/systemctl" and \
      data["worker_unit"] in command and any("ControlGroup" in value for value in command) and data["fault"] is None:
    stop(record,"systemctl")
  return process
 def observed_read(fd,count):
  result=original_read(fd,count)
  if launch and fd==launch[0].stdout.fileno() and result==b"READY\n":
   data["worker_ready"]=True;commit(event,data)
   if case=="stopped-systemd-run-supervisor-kill":stop(launch[1],"systemd-run")
  return result
 subprocess.Popen=audited;os.read=observed_read
 try:
  data["actual_supervise_called"]=True;commit(event,data)
  result=w.supervise(pin,fake.WIRE,SOURCE,run,fake.RUNTIME,
       qualification_mode="payload" if case=="payload" else "hang")
  data["returned_sha256"]=hashlib.sha256(result).hexdigest()
  data["returned_bytes"]=len(result);commit(event,data)
  return 0
 finally:subprocess.Popen=original_popen;os.read=original_read
def drain(lifetime,group,inode):
 return lifetime.empty(group,inode)
def reap(record):
 try:os.waitpid(record["pid"],os.WNOHANG)
 except ChildProcessError:pass
 return not same(record)
def cleanup(control,lifetime,unit,worker,launcher,event,supervisor):
 failed=False
 # Harness containment, after PASS/FAIL has already been fixed.
 if launcher is not None:
  try:
   if launcher.poll() is None:launcher.kill()
   launcher.wait(timeout=5)
  except Exception:failed=True
  for stream in (launcher.stdin,launcher.stdout):
   try:
    if stream and not stream.closed:stream.close()
   except Exception:failed=True
 # Stop the actual producer/control unit before reading its final worker intent.
 try:
  control.ctl("stop",unit);control.ctl("reset-failed",unit)
  group=Path("/sys/fs/cgroup/system.slice/"+unit)
  need(not group.exists() or "populated 0" in (group/"cgroup.events").read_text())
 except Exception:failed=True
 try:
  if event.exists():
   final=json.loads(event.read_bytes());latest=final["worker_unit"]
   if latest:
    need(supervisor is not None and re.fullmatch(lifetime.UNIT,latest) and
         latest.startswith("bridge-native-ro-"+SOURCE[:12]+"-"+str(supervisor["pid"])+"-1-"))
    worker=latest
 except Exception:failed=True
 if worker:
  try:
   need(supervisor is not None and re.fullmatch(lifetime.UNIT,worker) and
        worker.startswith("bridge-native-ro-"+SOURCE[:12]+"-"+str(supervisor["pid"])+"-1-"))
   control.ctl("stop",worker);control.ctl("reset-failed",worker)
   group=Path("/sys/fs/cgroup/system.slice/"+worker)
   need(not group.exists() or "populated 0" in (group/"cgroup.events").read_text())
  except Exception:failed=True
 if failed:raise Refused("CONTAINMENT_UNPROVEN")
def run_case(root,case):
 import stagea_watchdog as w,stagea_control as control,stagea_fixture_worker as fake
 lifetime=w.profile()
 run=str(os.getpid())+"-1"
 unit=control.new_unit(SOURCE,run);event=root/"evidence"/(case+".json")
 intent=root/"evidence"/(case+".intent.json")
 commit(intent,{"control_unit":unit,"supervisor_pid":None})
 body="from qualification_suite import candidate_main\nraise SystemExit(candidate_main("+"Path("+repr(str(root))+")"+","+repr(case)+","+"Path("+repr(str(event))+")"+"))\n"
 # Path literals in -c require pathlib import; no arbitrary worker/factory.
 body="from pathlib import Path\n"+body
 launcher=None;metadata=None;proof=None;worker=None;worker_group=worker_inode=None
 group=inode=None;main=None
 try:
  launcher=subprocess.Popen(control.command(unit,control.gated_code(root,body),network=True),
       stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
       env={"PATH":"/usr/bin:/bin"},close_fds=True,bufsize=0)
  need(select.select([launcher.stdout],[],[],5)[0] and os.read(launcher.stdout.fileno(),5)==b"GATE\n")
  state,group,inode=control.identity(unit,network=True)
  activation=int(state["ActiveEnterTimestampMonotonic"])/1e6
  main=identity(int(state["MainPID"]));need(main is not None)
  commit(intent,{"control_unit":unit,"supervisor_pid":main["pid"]})
  need(time.monotonic()<=activation+2)
  launcher.stdin.write(b"G");launcher.stdin.close()
  deadline=activation+8
  while time.monotonic()<deadline:
   if event.exists():
    metadata=json.loads(event.read_bytes());worker=metadata["worker_unit"]
    need(metadata["supervisor"]["pid"]==main["pid"] and metadata["supervisor"]["start_ticks"]==main["start_ticks"])
    if case=="payload" and metadata.get("returned_sha256"):break
    if case=="supervisor-kill" and metadata["worker_ready"] and worker:
     _,wg,wi=lifetime.identity(worker,50)
     if len((wg/"cgroup.procs").read_text().split())>=2:break
    if case.startswith("stopped-") and metadata["fault"]:break
   time.sleep(0.02)
  need(metadata and metadata["actual_supervise_called"] and worker)
  need(re.fullmatch(lifetime.UNIT,worker) and worker.startswith("bridge-native-ro-"+SOURCE[:12]+"-"+metadata["run"]+"-"))
  if case=="payload":
   expected=fake.payload()
   need(metadata.get("returned_sha256")==hashlib.sha256(expected).hexdigest() and metadata.get("returned_bytes")==len(expected))
   # May already be empty/inactive after normal completion: worker's initial
   # live identity was proved inside actual supervise. This case checks final
   # absence without claiming independent identity of an already vanished path.
   worker_group=Path("/sys/fs/cgroup/system.slice/"+worker)
   need(launcher.wait(timeout=max(.1,activation+62-time.monotonic()))==0)
   natural_control=drain(lifetime,group,inode)
   natural_worker=not worker_group.exists() or "populated 0" in (worker_group/"cgroup.events").read_text()
  else:
   _,worker_group,worker_inode=lifetime.identity(worker,50)
   need(metadata["worker_ready"])
   if case.startswith("stopped-"):
    fault=metadata["fault"];need(fault)
    need(same(fault["process"]) and group_of(fault["process"]["pid"])=="0::"+state["ControlGroup"])
    need(identity(fault["process"]["pid"])["state"] in ("T","t"))
   need(same(main));own_signal(main,signal.SIGKILL)
   natural_control=natural_worker=False
   while time.monotonic()<activation+62:
    natural_control=drain(lifetime,group,inode)
    natural_worker=drain(lifetime,worker_group,worker_inode)
    if natural_control and natural_worker:break
    time.sleep(.05)
  # All candidate administrative clients were in the independently bounded
  # control cgroup. Presence of that same cgroup cannot be inferred from
  # worker drain. Observe every recorded client and natural drain separately.
  # Refresh after main death/normal exit; control cgroup drain covers publication
  # gaps as well as all clients retained in the private metadata.
  if event.exists():metadata=json.loads(event.read_bytes())
  clients_gone=False
  while time.monotonic()<=activation+62:
   observed=[reap(record) for record in metadata["clients"]]
   clients_gone=all(observed)
   if clients_gone:break
   time.sleep(.02)
  need(natural_control and natural_worker and clients_gone and time.monotonic()<=activation+62)
  if launcher.poll() is None:launcher.wait(timeout=max(.001,activation+62-time.monotonic()))
  need(launcher.poll() is not None and time.monotonic()<=activation+62)
  proof={"case":case,"verdict":"PASS","actual_candidate_supervise_body":True,
   "natural_control_drain":True,"natural_worker_drain":True,
   "candidate_admin_clients_gone":True,"candidate_admin_bound_seconds":62,
   "seconds_since_control_activation":round(time.monotonic()-activation,3),
   "outer_launcher_is_harness_owned":True,"external_cleanup_counted_as_candidate_pass":False,
   "production":False,"live_sql":False}
 except Exception:
  proof={"case":case,"verdict":"FAIL_NOT_QUALIFIED","production":False,"live_sql":False}
 finally:
  cleanup(control,lifetime,unit,worker,launcher,event,main)
 return proof
def suite_main(root):
 import stagea_control as control,stagea_synthetic_core
 need(sys.platform=="linux" and os.getuid()==0 and os.getgid()==0)
 cgroup=Path("/proc/self/cgroup").read_text().strip()
 prefix="0::/system.slice/";need(cgroup.startswith(prefix))
 unit=cgroup[len(prefix):];need(re.fullmatch(control.OUTER,unit))
 state,_,_=control.identity(unit,300,network=True);need(int(state["MainPID"])==os.getpid())
 # Only this new fixture harness adopts orphaned test children.
 import ctypes
 need(ctypes.CDLL(None).prctl(36,1,0,0,0)==0)
 pin=hashlib.sha256((root/"runtime-manifest.json").read_bytes()).hexdigest()
 stagea_synthetic_core.source_guard(pin)
 results=[]
 for case in CASES:
  proof=run_case(root,case);results.append(proof);emit(proof)
 passed=all(result["verdict"]=="PASS" for result in results)
 emit({"qualification":"PASS_ACTUAL_CANDIDATE_SYNTHETIC" if passed else "FAIL_NOT_QUALIFIED",
       "cases":len(results),"frozen_supervisor_qualified":False,"production_ready":False,
       "external_containment_is_not_candidate_pass":True})
 return 0 if passed else 78
def main():
 parser=argparse.ArgumentParser();parser.add_argument("--describe",action="store_true")
 parser.add_argument("--run",action="store_true");parser.add_argument("--scope-approved",action="store_true")
 parser.add_argument("--root",type=Path);args=parser.parse_args()
 if not args.run:
  emit({"mode":"DESCRIBE_ONLY","cases":CASES,"root_pid1_executed":False,"resources_created":False});return 0
 need(args.scope_approved and args.root and os.environ.get("GITHUB_ACTIONS")=="true")
 return suite_main(args.root)
if __name__=="__main__":
 try:raise SystemExit(main())
 except Exception:
  emit({"qualification":"REFUSED_OR_CONTAINMENT_UNPROVEN","production":False});raise SystemExit(78)
