"""Finite Oracle namespace/lock qualification; no lease, service or Stop authority.

CLI: python3 -u ops/ibm_oracle_lease_qualification.py (no arguments).
EXPECTED_MAIN_SHA and the existing ORACLE_SSH_PRIVATE_KEY are the only inputs.
Remote writes are confined to one exclusively-created run/attempt/source child.
Interrupted-client evidence means owned-child SIGKILL, never SSH disconnect.
"""
import importlib.util
import json
import os
import re
import signal
import sys
import time

# -I intentionally excludes the script directory. Load only this fixed sibling.
_transport_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ibm_oracle_watchdog_inventory.py")
_transport_spec = importlib.util.spec_from_file_location("bridge_qualification_transport", _transport_path)
transport = importlib.util.module_from_spec(_transport_spec)
_transport_spec.loader.exec_module(transport)

SCHEMA = "ibm-oracle-lease-qualification/v1"
WORKFLOW_REF = (transport.REPOSITORY + "/.github/workflows/ibm-oracle-lease-qualification.yml@" + transport.REF)
REMOTE_SECONDS = 20
OUTER_SECONDS = 60
RECEIPT_BYTES = 12 * 1024
REMOTE_CAPTURE_BYTES = 112 * 1024
RUNNER_CAPTURE_BYTES = 16 * 1024
RUNNER_COMMANDS = 8
REMOTE_COMMANDS = 0
MAX_FORK_CLIENTS = 4
PARENT = "/home/ubuntu/.local/share/bridge-school"
ERRORS = transport.ERRORS | {"QUALIFICATION_BLOCKED"}


def need(ok, code):
    transport.need(ok, code)


def context(budget):
    e = os.environ
    exact = {"GITHUB_REPOSITORY": transport.REPOSITORY, "GITHUB_REPOSITORY_OWNER": transport.OWNER,
             "GITHUB_REPOSITORY_OWNER_ID": transport.OWNER_ID, "GITHUB_ACTOR": transport.OWNER,
             "GITHUB_ACTOR_ID": transport.OWNER_ID, "GITHUB_TRIGGERING_ACTOR": transport.OWNER,
             "GITHUB_REF": transport.REF, "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
             "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1"}
    need(all(e.get(k) == v for k, v in exact.items()), "CONTEXT")
    head, run = e.get("GITHUB_SHA", ""), e.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[0-9a-f]{40}", head) is not None
         and e.get("EXPECTED_MAIN_SHA") == head and e.get("GITHUB_WORKFLOW_SHA") == head
         and re.fullmatch(r"[1-9][0-9]{0,19}", run) is not None, "CONTEXT")
    rc, raw = transport.bounded_process(["/usr/bin/git", "rev-parse", "HEAD"], budget, seconds=3)
    need(rc == 0 and raw == (head + "\n").encode(), "CHECKOUT")
    rc, raw = transport.bounded_process(["/usr/bin/git", "diff", "--quiet", "HEAD", "--"], budget, seconds=3)
    need(rc == 0 and not raw, "CHECKOUT")
    rc, raw = transport.bounded_process(["/usr/bin/git", "-c", "credential.helper=", "-c", "http.extraHeader=",
                                        "ls-remote", "https://github.com/" + transport.REPOSITORY + ".git", transport.REF],
                                       budget, seconds=6)
    need(rc == 0 and raw == (head + "\t" + transport.REF + "\n").encode(), "CURRENT_MAIN")
    return {"head": head, "run_id": run, "attempt": 1}


# Literal reviewed program. Binding is appended as JSON containing validated
# decimal run/attempt and lowercase hex source; none enters shell interpolation.
REMOTE_SOURCE = r'''import ctypes,errno,fcntl,hashlib,json,os,pwd,re,select,signal,socket,stat,time
SCHEMA="ibm-oracle-lease-qualification/v1"
PARENT="/home/ubuntu/.local/share/bridge-school"
CAP=114688
SECONDS=20
MAX_CLIENTS=4
UID=1001
FLAGS=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
REASONS={"HOST_IDENTITY","BINDING","PARENT_METADATA","PARENT_CHANGED","ACL","MOUNT",
 "COLLISION","CHILD_METADATA","FILE_METADATA","IO","DEADLINE","OUTPUT_CAP","CLIENT_CAP",
 "CLIENT_FAILED","LOCK","ATOMIC_EXCHANGE","READBACK","INTERNAL"}
FILES={"lock","version000.json","version001.json","version002.json","current.json",
 "pending-before.json","pending-after.json","before-contender.json","after-contender.json",
 "qualification-receipt.json"}
class Blocked(Exception):pass
def need(ok,code):
 if not ok:raise Blocked(code)
def encoded(value):return json.dumps(value,sort_keys=True,separators=(",",":")).encode()+b"\n"
def digest(raw):return hashlib.sha256(raw).hexdigest()
def binding_ok(value):
 return (type(value) is dict and set(value)=={"head","run_id","attempt"} and
  type(value["head"]) is str and re.fullmatch(r"[0-9a-f]{40}",value["head"]) is not None and
  type(value["run_id"]) is str and re.fullmatch(r"[1-9][0-9]{0,19}",value["run_id"]) is not None and
  type(value["attempt"]) is int and value["attempt"]==1)
def child_name(binding):
 need(binding_ok(binding),"BINDING")
 return "qualification-"+binding["run_id"]+"-1-"+binding["head"]
def blank(binding):
 return {"schema":SCHEMA,"result":"BLOCKED","reason":"INTERNAL","binding":binding,
 "identity":"UNKNOWN","namespace":None,"scope_created":False,"phase":"ADMISSION",
 "retained_entries":[],"retained_entries_complete":False,"parent_metadata":[],"mount":None,
 "client_crash_model":"OWNED_CHILD_SIGKILL","ssh_disconnect_tested":False,"power_loss_tested":False,
 "reboot_tested":False,"persistence":"UNPROVEN","watchdog_ready":False,
 "stop_capability":"UNPROVEN","installed_credentials":"UNPROVEN","independent_deadline":"UNPROVEN",
 "remote_exec_commands":0,"fork_clients":0,"captured_bytes":0,"artifacts":[],"trials":[],
 "proof_retained":False,"capture_accounting":"PARENT_PLUS_RESERVED_CLIENT_READS"}
class Budget:
 def __init__(self,end):self.end=end;self.captured=0;self.clients=0
 def check(self):need(time.monotonic()<self.end,"DEADLINE")
 def read(self,fd,size):
  self.check();room=CAP-self.captured;need(room>0,"OUTPUT_CAP")
  raw=os.read(fd,min(size,room));self.captured+=len(raw)
  need(self.captured<CAP,"OUTPUT_CAP");return raw
 def account(self,size):
  need(type(size) is int and size>=0 and self.captured+size<CAP,"OUTPUT_CAP");self.captured+=size
def check_dir(fd,uid):
 info=os.fstat(fd)
 need(stat.S_ISDIR(info.st_mode) and info.st_uid==uid and
      stat.S_IMODE(info.st_mode) in (0o700,0o750,0o755),"PARENT_METADATA")
 # Refuse any POSIX ACL (including defaults) without reading its contents.
 attrs=os.listxattr(fd)
 need(len(attrs)<=8 and all(len(x)<=255 for x in attrs),"ACL")
 need(not any(x in ("system.posix_acl_access","system.posix_acl_default") for x in attrs),"ACL")
 return info
class Namespace:
 def __init__(self):self.fds=[];self.metadata=[];self.child=None;self.name=None;self.created=False
 def open(self):
  try:
   self.fds.append(os.open("/",FLAGS))
   for index,name in enumerate((None,"home","ubuntu",".local","share","bridge-school")):
    if name is not None:self.fds.append(os.open(name,FLAGS,dir_fd=self.fds[-1]))
    info=check_dir(self.fds[-1],0 if index<2 else UID)
    self.metadata.append({"path":("/","/home","/home/ubuntu","/home/ubuntu/.local",
     "/home/ubuntu/.local/share",PARENT)[index],"uid":info.st_uid,"mode":format(stat.S_IMODE(info.st_mode),"04o"),
     "device":info.st_dev,"inode":info.st_ino})
   return self
  except OSError:raise Blocked("PARENT_METADATA") from None
 def recheck(self):
  for index,fd in enumerate(self.fds):
   info=check_dir(fd,0 if index<2 else UID);saved=self.metadata[index]
   need(info.st_ino==saved["inode"] and info.st_dev==saved["device"] and
        format(stat.S_IMODE(info.st_mode),"04o")==saved["mode"],"PARENT_CHANGED")
   current=(os.stat("/",follow_symlinks=False) if index==0 else
    os.stat(("home","ubuntu",".local","share","bridge-school")[index-1],dir_fd=self.fds[index-1],follow_symlinks=False))
   need(current.st_ino==info.st_ino and current.st_dev==info.st_dev and stat.S_ISDIR(current.st_mode),"PARENT_CHANGED")
  if self.child is not None:
   info=check_dir(self.child,UID);current=os.stat(self.name,dir_fd=self.fds[-1],follow_symlinks=False)
   need(stat.S_IMODE(info.st_mode)==0o700 and stat.S_ISDIR(current.st_mode) and
        (info.st_dev,info.st_ino)==(current.st_dev,current.st_ino),"PARENT_CHANGED")
 def create(self,binding):
  self.recheck();self.name=child_name(binding)
  mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM,signal.SIGTERM})
  try:
   try:
    os.mkdir(self.name,0o700,dir_fd=self.fds[-1]);self.created=True
   except FileExistsError:raise Blocked("COLLISION") from None
  finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)
  self.child=os.open(self.name,FLAGS,dir_fd=self.fds[-1])
  info=check_dir(self.child,UID)
  need(stat.S_IMODE(info.st_mode)==0o700 and info.st_dev==os.fstat(self.fds[-1]).st_dev,"CHILD_METADATA")
  self.recheck();os.fsync(self.fds[-1]);return self.child
 def close(self):
  for fd in ([self.child] if self.child is not None else [])+list(reversed(self.fds)):os.close(fd)
def parse_mount(raw,device):
 try:
  text=raw.decode("ascii");need(len(text)<=32768 and text.endswith("\n"),"MOUNT")
  candidates=[]
  for row in text.splitlines():
   fields=row.split();separator=fields.index("-")
   need(separator>=6 and len(fields)==separator+4,"MOUNT")
   mount=fields[4]
   # We only accept unescaped canonical ancestors of our fixed parent.
   if mount not in ("/","/home","/home/ubuntu","/home/ubuntu/.local","/home/ubuntu/.local/share",PARENT):continue
   candidates.append((len(mount),fields,separator))
  need(bool(candidates),"MOUNT")
  selected=max(x[0] for x in candidates);matches=[x for x in candidates if x[0]==selected]
  need(len(matches)==1,"MOUNT");_,f,s=matches[0]
  major,minor=[int(x) for x in f[2].split(":")]
  need(major>0 and (major,minor)==(os.major(device),os.minor(device)) and
       f[3]=="/" and f[s+1] in ("ext4","xfs") and
       re.fullmatch(r"/dev/[A-Za-z0-9_.+/-]{1,100}",f[s+2]) is not None and
       "rw" in f[5].split(",") and "ro" not in f[5].split(",") and
       "rw" in f[s+3].split(",") and "ro" not in f[s+3].split(","),"MOUNT")
  return {"filesystem":f[s+1],"mountpoint":f[4],"device_major":major,"device_minor":minor,
   "classification":"LOCAL_BLOCK_BACKED_BY_MOUNT_METADATA","mount_root":"/","writable":True}
 except (ValueError,UnicodeError,IndexError):raise Blocked("MOUNT") from None
def mount_metadata(budget,device):
 fd=os.open("/proc/self/mountinfo",os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  raw=bytearray()
  while True:
   part=budget.read(fd,min(4096,32769-len(raw)))
   if not part:break
   raw.extend(part);need(len(raw)<=32768,"MOUNT")
  return parse_mount(bytes(raw),device)
 finally:os.close(fd)
def file_info(fd):
 info=os.fstat(fd)
 need(stat.S_ISREG(info.st_mode) and info.st_uid==UID and stat.S_IMODE(info.st_mode)==0o600
      and 0<=info.st_size<=4096 and 1<=info.st_nlink<=3,"FILE_METADATA")
 return info
def create_file(dfd,name,raw):
 need(name in FILES and type(raw) is bytes and len(raw)<=4096,"FILE_METADATA")
 fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=dfd)
 try:
  file_info(fd);offset=0
  while offset<len(raw):
   n=os.write(fd,raw[offset:]);need(n>0,"IO");offset+=n
  os.fsync(fd)
 finally:os.close(fd)
 os.fsync(dfd)
def read_file(dfd,name,budget):
 need(name in FILES,"FILE_METADATA")
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dfd)
 try:
  info=file_info(fd);raw=budget.read(fd,4097)
  need(len(raw)==info.st_size and not budget.read(fd,1),"READBACK")
  return raw
 finally:os.close(fd)
def link_file(dfd,source,dest):
 need(source in FILES and dest in FILES,"FILE_METADATA")
 os.link(source,dest,src_dir_fd=dfd,dst_dir_fd=dfd,follow_symlinks=False);os.fsync(dfd)
def entry_identity(dfd,name):
 need(name in FILES,"FILE_METADATA")
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dfd)
 try:
  info=file_info(fd);return (info.st_dev,info.st_ino,info.st_nlink)
 finally:os.close(fd)
def atomic_exchange(dfd,left,right):
 need((left,right)==("pending-after.json","current.json"),"ATOMIC_EXCHANGE")
 old=entry_identity(dfd,"version000.json");new=entry_identity(dfd,"version002.json")
 need(entry_identity(dfd,right)==old and entry_identity(dfd,left)==new and old!=new and
      old[2]==2 and new[2]==2,"ATOMIC_EXCHANGE")
 lib=ctypes.CDLL(None,use_errno=True)
 need(hasattr(lib,"renameat2"),"ATOMIC_EXCHANGE")
 lib.renameat2.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
 lib.renameat2.restype=ctypes.c_int
 need(lib.renameat2(dfd,left.encode(),dfd,right.encode(),2)==0,"ATOMIC_EXCHANGE")
 os.fsync(dfd)
def wire_write(fd,raw):
 need(len(raw)<=1024,"OUTPUT_CAP")
 offset=0
 while offset<len(raw):
  n=os.write(fd,raw[offset:]);need(n>0,"IO");offset+=n
class Clients:
 def __init__(self,budget,dfd):self.budget=budget;self.dfd=dfd;self.owned=set();self.pipes=set()
 def spawn(self,fn):
  self.budget.check();need(self.budget.clients<MAX_CLIENTS,"CLIENT_CAP")
  to_r,to_w=os.pipe2(os.O_CLOEXEC);from_r,from_w=os.pipe2(os.O_CLOEXEC)
  mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM,signal.SIGTERM})
  pid=None
  try:
   parent=os.getpid();pid=os.fork()
   if pid==0:
    try:
     os.close(to_w);os.close(from_r)
     for fd in self.pipes:os.close(fd)
     # No daemon survives collector death; race-check parent after prctl.
     lib=ctypes.CDLL(None,use_errno=True)
     need(lib.prctl(1,signal.SIGKILL,0,0,0)==0 and os.getppid()==parent,"CLIENT_FAILED")
     signal.signal(signal.SIGALRM,lambda *_:os._exit(70))
     signal.signal(signal.SIGTERM,lambda *_:os._exit(71))
     signal.pthread_sigmask(signal.SIG_SETMASK,mask)
     signal.setitimer(signal.ITIMER_REAL,min(5,max(.001,self.budget.end-time.monotonic())))
     fn(to_r,from_w);os._exit(0)
    except BaseException:os._exit(72)
   self.owned.add(pid);self.budget.clients+=1
  finally:
   if pid!=0:
    os.close(to_r);os.close(from_w)
    if pid is None:os.close(to_w);os.close(from_r)
   signal.pthread_sigmask(signal.SIG_SETMASK,mask)
  self.pipes.update((to_w,from_r));return pid,to_w,from_r
 def receive(self,fd):
  raw=bytearray()
  while not raw.endswith(b"\n"):
   self.budget.check();timeout=min(2,max(0,self.budget.end-time.monotonic()))
   need(bool(select.select([fd],[],[],timeout)[0]),"DEADLINE")
   part=self.budget.read(fd,1);need(bool(part),"CLIENT_FAILED");raw.extend(part)
   need(len(raw)<=1024,"OUTPUT_CAP")
  return bytes(raw)
 def reap(self,pid,killed=False):
  need(pid in self.owned,"CLIENT_FAILED")
  if killed:os.kill(pid,signal.SIGKILL)
  end=min(self.budget.end,time.monotonic()+2)
  while True:
   mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM,signal.SIGTERM})
   try:
    got,status=os.waitpid(pid,os.WNOHANG)
    if got:self.owned.remove(pid)
   finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)
   if got:
    need(os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGKILL if killed
     else os.WIFEXITED(status) and os.WEXITSTATUS(status)==0,"CLIENT_FAILED")
    return
   need(time.monotonic()<end,"DEADLINE");time.sleep(.005)
 def close(self):
  # Only unreaped direct children created by this object, never process groups.
  for pid in list(self.owned):
   try:os.kill(pid,signal.SIGKILL)
   except ProcessLookupError:pass
  end=time.monotonic()+.5
  while self.owned and time.monotonic()<end:
   for pid in list(self.owned):
    try:
     if os.waitpid(pid,os.WNOHANG)[0]:self.owned.remove(pid)
    except ChildProcessError:self.owned.remove(pid)
   if self.owned:time.sleep(.005)
  for fd in self.pipes:os.close(fd)
def acquire(dfd,expected):
 fd=os.open("lock",os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dfd)
 try:
  info=file_info(fd);need((info.st_dev,info.st_ino,info.st_nlink)==expected,"LOCK");return fd
 except BaseException:
  os.close(fd);raise
def exercise(dfd,binding,budget,clients,progress=lambda phase:None,anchor=lambda:None):
 baseline=encoded({"binding":binding,"version":0});before=encoded({"binding":binding,"version":1})
 after=encoded({"binding":binding,"version":2})
 create_file(dfd,"lock",b"");create_file(dfd,"version000.json",baseline)
 link_file(dfd,"version000.json","current.json")
 lock_identity=entry_identity(dfd,"lock");need(lock_identity[2]==1,"LOCK")
 trials=[]
 for phase,next_raw,version in (("before",before,"version001.json"),("after",after,"version002.json")):
  anchor();progress("CRASH_BEFORE_COMMIT" if phase=="before" else "CRASH_AFTER_COMMIT")
  expected=baseline if phase=="before" else after
  def holder(infd,outfd):
   anchor();lock=acquire(dfd,lock_identity);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   create_file(dfd,version,next_raw);link_file(dfd,version,"pending-"+phase+".json")
   if phase=="after":
    anchor();atomic_exchange(dfd,"pending-after.json","current.json")
   wire_write(outfd,b"READY\n")
   os.read(infd,1)  # Bounded by child timer and parent-owned lifecycle.
   os.close(lock)
  holder_pid,holder_in,holder_out=clients.spawn(holder)
  need(clients.receive(holder_out)==b"READY\n","CLIENT_FAILED")
  def contender(infd,outfd):
   anchor();lock=acquire(dfd,lock_identity)
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:pass
   else:raise Blocked("LOCK")
   wire_write(outfd,b"BLOCKED\n")
   need(os.read(infd,1)==b"R","CLIENT_FAILED")
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   local=Budget(budget.end)
   # Bound child read independently, and reserve its worst-case physical read
   # in the parent before fork. The control byte is included in that reserve.
   check=os.stat("current.json",dir_fd=dfd,follow_symlinks=False)
   need(check.st_size==len(expected),"READBACK")
   actual=read_file(dfd,"current.json",local)
   need(actual==expected,"READBACK")
   proof={"phase":phase,"contender_blocked":True,"recovered":True,"current_sha256":digest(actual),
          "client_crash_model":"OWNED_CHILD_SIGKILL","captured_bytes":local.captured}
   create_file(dfd,phase+"-contender.json",encoded(proof));wire_write(outfd,encoded(proof));os.close(lock)
  budget.account(4099)
  contender_pid,contender_in,contender_out=clients.spawn(contender)
  need(clients.receive(contender_out)==b"BLOCKED\n","LOCK")
  clients.reap(holder_pid,killed=True);wire_write(contender_in,b"R")
  proof=json.loads(clients.receive(contender_out));clients.reap(contender_pid)
  need(proof=={"phase":phase,"contender_blocked":True,"recovered":True,"current_sha256":digest(expected),
       "client_crash_model":"OWNED_CHILD_SIGKILL","captured_bytes":len(expected)},"LOCK")
  need(read_file(dfd,"current.json",budget)==expected,"READBACK")
  trials.append(proof)
 for name,raw in (("version000.json",baseline),("version001.json",before),("version002.json",after),
                  ("pending-before.json",before),("pending-after.json",baseline),("current.json",after)):
  need(read_file(dfd,name,budget)==raw,"READBACK")
 return trials
def artifact_metadata(dfd,budget):
 names=sorted(FILES-{"qualification-receipt.json"})
 result=[]
 for name in names:
  raw=read_file(dfd,name,budget)
  result.append({"name":name,"bytes":len(raw),"sha256":digest(raw)})
 return result
def retained_metadata(dfd):
 entries=[]
 for name in sorted(FILES):
  try:info=os.stat(name,dir_fd=dfd,follow_symlinks=False)
  except FileNotFoundError:continue
  kind="OWNED_REGULAR" if stat.S_ISREG(info.st_mode) and info.st_uid==UID and stat.S_IMODE(info.st_mode)==0o600 else "UNEXPECTED_METADATA"
  entries.append({"name":name,"state":kind,"bytes":info.st_size if 0<=info.st_size<=4096 else None})
 return entries
def collect(binding):
 result=blank(binding);namespace=Namespace();budget=Budget(time.monotonic()+SECONDS-1);clients=None
 old=signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(Blocked("DEADLINE")))
 signal.setitimer(signal.ITIMER_REAL,SECONDS-1)
 try:
  need(binding_ok(binding),"BINDING")
  need(os.getuid()==UID and os.geteuid()==UID and pwd.getpwuid(UID).pw_name=="ubuntu" and
       socket.gethostname()=="autopilot-lite-vnic","HOST_IDENTITY")
  result["identity"]="MATCH";result["phase"]="PARENT_METADATA"
  namespace.open();result["parent_metadata"]=namespace.metadata
  result["phase"]="MOUNT"
  result["mount"]=mount_metadata(budget,os.fstat(namespace.fds[-1]).st_dev)
  result["phase"]="CREATE_SCOPE"
  dfd=namespace.create(binding);result["namespace"]=PARENT+"/"+namespace.name
  result["scope_created"]=True;result["phase"]="BASELINE"
  clients=Clients(budget,dfd);result["trials"]=exercise(dfd,binding,budget,clients,lambda phase:result.update(phase=phase),namespace.recheck)
  result["phase"]="VERIFY"
  result["artifacts"]=artifact_metadata(dfd,budget);namespace.recheck()
  result.update(result="QUALIFIED",reason="NONE",persistence="MOUNT_CLASSIFIED_FSYNC_READBACK_ONLY")
  proof={"schema":SCHEMA,"binding":binding,"trials":result["trials"],"artifacts":result["artifacts"],
         "client_crash_model":"OWNED_CHILD_SIGKILL","ssh_disconnect_tested":False,"power_loss_tested":False}
  result["phase"]="RETAIN_PROOF";namespace.recheck()
  create_file(dfd,"qualification-receipt.json",encoded(proof))
  proof_raw=read_file(dfd,"qualification-receipt.json",budget);need(proof_raw==encoded(proof),"READBACK")
  result["artifacts"].append({"name":"qualification-receipt.json","bytes":len(proof_raw),"sha256":digest(proof_raw)})
  namespace.recheck()
  result["artifacts"].sort(key=lambda item:item["name"]);result["proof_retained"]=True;result["phase"]="COMPLETE"
 except BaseException as exc:
  code=str(exc) if isinstance(exc,Blocked) and str(exc) in REASONS else "IO" if isinstance(exc,OSError) else "INTERNAL"
  result.update(result="BLOCKED",reason=code,persistence="UNPROVEN",proof_retained=False)
 finally:
  signal.setitimer(signal.ITIMER_REAL,0)
  if clients is not None:clients.close()
  result["scope_created"]=namespace.created
  if namespace.created:result["namespace"]=PARENT+"/"+namespace.name
  if namespace.child is not None:
   try:
    result["retained_entries"]=retained_metadata(namespace.child);result["retained_entries_complete"]=True
   except OSError:pass
  namespace.close();signal.signal(signal.SIGALRM,old)
  result["fork_clients"]=budget.clients;result["captured_bytes"]=budget.captured
 return result
'''


def remote_namespace():
    namespace = {"__name__": "qualification_schema"}
    exec(compile(REMOTE_SOURCE, "<qualification-schema>", "exec"), namespace)
    return namespace


def validate_remote(raw, binding):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, "REMOTE_SCHEMA")
            out[key] = value
        return out
    need(type(raw) is bytes and len(raw) < RECEIPT_BYTES, "REMOTE_SCHEMA")
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False, "REMOTE_SCHEMA"))
        ns = remote_namespace()
        need(ns["binding_ok"](binding), "REMOTE_SCHEMA")
        blank = ns["blank"](binding)
        need(type(value) is dict and set(value) == set(blank), "REMOTE_SCHEMA")
        for key in ("schema", "binding", "client_crash_model", "ssh_disconnect_tested", "power_loss_tested",
                    "reboot_tested", "watchdog_ready", "stop_capability", "installed_credentials",
                    "independent_deadline", "remote_exec_commands", "capture_accounting"):
            need(type(value[key]) is type(blank[key]) and value[key] == blank[key], "REMOTE_SCHEMA")
        need(ns["binding_ok"](value["binding"]), "REMOTE_SCHEMA")
        need(value["result"] in ("BLOCKED", "QUALIFIED") and value["reason"] in ns["REASONS"] | {"NONE"}
             and value["identity"] in ("MATCH", "UNKNOWN"), "REMOTE_SCHEMA")
        need(type(value["fork_clients"]) is int and 0 <= value["fork_clients"] <= MAX_FORK_CLIENTS and
             type(value["captured_bytes"]) is int and 0 <= value["captured_bytes"] <= REMOTE_CAPTURE_BYTES and
             type(value["proof_retained"]) is bool, "REMOTE_SCHEMA")
        need(value["captured_bytes"] < REMOTE_CAPTURE_BYTES or
             value["result"] == "BLOCKED" and value["reason"] == "OUTPUT_CAP", "REMOTE_SCHEMA")
        path = PARENT + "/" + ns["child_name"](binding)
        need(type(value["scope_created"]) is bool and type(value["retained_entries_complete"]) is bool
             and value["namespace"] == (path if value["scope_created"] else None), "REMOTE_SCHEMA")
        need(value["phase"] in ("ADMISSION", "PARENT_METADATA", "MOUNT", "CREATE_SCOPE", "BASELINE",
             "CRASH_BEFORE_COMMIT", "CRASH_AFTER_COMMIT", "VERIFY", "RETAIN_PROOF", "COMPLETE"), "REMOTE_SCHEMA")
        retained = value["retained_entries"]
        need(type(retained) is list and len(retained) <= len(ns["FILES"]), "REMOTE_SCHEMA")
        retained_names = []
        for item in retained:
            need(type(item) is dict and set(item) == {"name", "state", "bytes"} and item["name"] in ns["FILES"]
                 and item["state"] in ("OWNED_REGULAR", "UNEXPECTED_METADATA")
                 and (item["bytes"] is None or type(item["bytes"]) is int and 0 <= item["bytes"] <= 4096), "REMOTE_SCHEMA")
            retained_names.append(item["name"])
        need(retained_names == sorted(set(retained_names)), "REMOTE_SCHEMA")
        need(value["scope_created"] or not retained and not value["retained_entries_complete"], "REMOTE_SCHEMA")
        metadata = value["parent_metadata"]
        need(type(metadata) is list and len(metadata) in (0, 6), "REMOTE_SCHEMA")
        for index, item in enumerate(metadata):
            need(type(item) is dict and set(item) == {"path", "uid", "mode", "device", "inode"}, "REMOTE_SCHEMA")
            need(item["path"] == ("/", "/home", "/home/ubuntu", "/home/ubuntu/.local", "/home/ubuntu/.local/share", PARENT)[index]
                 and type(item["uid"]) is int and item["uid"] == (0 if index < 2 else 1001)
                 and item["mode"] in ("0700", "0750", "0755")
                 and all(type(item[k]) is int and item[k] > 0 for k in ("device", "inode")), "REMOTE_SCHEMA")
        mount = value["mount"]
        if mount is not None:
            need(type(mount) is dict and set(mount) == {"filesystem", "mountpoint", "device_major", "device_minor",
                  "classification", "mount_root", "writable"} and mount["filesystem"] in ("ext4", "xfs")
                 and mount["mountpoint"] in [item["path"] for item in metadata]
                 and type(mount["device_major"]) is int and mount["device_major"] > 0
                 and type(mount["device_minor"]) is int and mount["device_minor"] >= 0
                 and mount["classification"] == "LOCAL_BLOCK_BACKED_BY_MOUNT_METADATA"
                 and mount["mount_root"] == "/" and mount["writable"] is True
                 and (mount["device_major"], mount["device_minor"]) ==
                     (os.major(metadata[-1]["device"]), os.minor(metadata[-1]["device"])), "REMOTE_SCHEMA")
        artifacts = value["artifacts"]
        need(type(artifacts) is list and len(artifacts) <= len(ns["FILES"]), "REMOTE_SCHEMA")
        names = []
        for artifact in artifacts:
            need(type(artifact) is dict and set(artifact) == {"name", "bytes", "sha256"}
                 and artifact["name"] in ns["FILES"] and type(artifact["bytes"]) is int
                 and 0 <= artifact["bytes"] <= 4096 and type(artifact["sha256"]) is str
                 and re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is not None, "REMOTE_SCHEMA")
            names.append(artifact["name"])
        need(names == sorted(set(names)), "REMOTE_SCHEMA")
        trials = value["trials"]
        need(type(trials) is list and len(trials) <= 2, "REMOTE_SCHEMA")
        for index, trial in enumerate(trials):
            expected = ns["encoded"]({"binding": binding, "version": 0 if index == 0 else 2})
            need(trial == {"phase": ("before", "after")[index], "contender_blocked": True, "recovered": True,
                  "current_sha256": ns["digest"](expected), "client_crash_model": "OWNED_CHILD_SIGKILL",
                  "captured_bytes": len(expected)} and all(type(trial[k]) is bool for k in ("contender_blocked", "recovered")), "REMOTE_SCHEMA")
        if value["result"] == "QUALIFIED":
            need(len(trials) == 2, "REMOTE_SCHEMA")
            versions = [ns["encoded"]({"binding": binding, "version": i}) for i in range(3)]
            bodies = {"lock": b"", "version000.json": versions[0], "version001.json": versions[1],
                      "version002.json": versions[2], "current.json": versions[2],
                      "pending-before.json": versions[1], "pending-after.json": versions[0],
                      "before-contender.json": ns["encoded"](trials[0]), "after-contender.json": ns["encoded"](trials[1])}
            initial_artifacts = [{"name": name, "bytes": len(raw), "sha256": ns["digest"](raw)}
                                 for name, raw in sorted(bodies.items())]
            proof = {"schema": SCHEMA, "binding": binding, "trials": trials, "artifacts": initial_artifacts,
                     "client_crash_model": "OWNED_CHILD_SIGKILL", "ssh_disconnect_tested": False, "power_loss_tested": False}
            bodies["qualification-receipt.json"] = ns["encoded"](proof)
            expected_artifacts = [{"name": name, "bytes": len(raw), "sha256": ns["digest"](raw)}
                                  for name, raw in sorted(bodies.items())]
            expected_retained = [{"name": name, "state": "OWNED_REGULAR", "bytes": len(raw)}
                                 for name, raw in sorted(bodies.items())]
            need(artifacts == expected_artifacts and retained == expected_retained, "REMOTE_SCHEMA")
            need(value["reason"] == "NONE" and value["identity"] == "MATCH" and value["namespace"] == path
                 and len(metadata) == 6 and mount is not None and set(names) == ns["FILES"]
                 and len(trials) == 2 and value["fork_clients"] == 4 and value["proof_retained"] is True
                 and value["persistence"] == "MOUNT_CLASSIFIED_FSYNC_READBACK_ONLY"
                 and value["phase"] == "COMPLETE" and value["retained_entries_complete"] is True
                 and set(retained_names) == ns["FILES"] and all(x["state"] == "OWNED_REGULAR" for x in retained), "REMOTE_SCHEMA")
        else:
            need(value["reason"] != "NONE" and value["persistence"] == "UNPROVEN"
                 and value["proof_retained"] is False, "REMOTE_SCHEMA")
        return value
    except (ValueError, UnicodeError, TypeError, KeyError, RecursionError):
        raise transport.InventoryError("REMOTE_SCHEMA") from None


def run_qualification(budget, recovery=None):
    recovery = {} if recovery is None else recovery
    binding = context(budget)
    recovery.update(binding=binding, candidate_namespace=PARENT + "/qualification-" + binding["run_id"] + "-1-" + binding["head"],
                    remote_attempted=False)
    raw = bytearray(os.environ.pop("ORACLE_SSH_PRIVATE_KEY", "").replace("\r", "").encode())
    keyfd = knownfd = None
    try:
        need(32 < len(raw) < 32768, "SECRET")
        keyfd = transport.memory(raw, "existing-oracle-owner-key")
        for index in range(len(raw)):
            raw[index] = 0
        rc, scanned = transport.bounded_process(["/usr/bin/ssh-keyscan", "-T", "3", "-t", "ed25519", transport.ORACLE], budget, seconds=4)
        need(rc == 0, "HOST_PIN")
        knownfd = transport.memory(transport.pinned_scan(scanned), "oracle-public-trust")
        need(context(budget) == binding, "CONTEXT")
        payload = REMOTE_SOURCE + "\nprint(encoded(collect(" + json.dumps(binding, sort_keys=True) + ")).decode(),end='',flush=True)\n"
        recovery["remote_attempted"] = True
        rc, response = transport.bounded_process(transport.ssh_command(keyfd, knownfd), budget,
                                               payload=payload.encode(), seconds=REMOTE_SECONDS + 7)
        need(rc == 0, "TRANSPORT")
        qualification = validate_remote(response, binding)
        return {"schema": SCHEMA, "result": qualification["result"], "binding": binding,
                "host_pin": "MATCH", "qualification": qualification, "raw_output_exported": False,
                "runner_commands": budget.started, "runner_captured_bytes": budget.captured,
                "candidate_namespace": recovery["candidate_namespace"], "remote_attempted": True, "automatic_cleanup": False}
    finally:
        for index in range(len(raw)):
            raw[index] = 0
        for fd in (knownfd, keyfd):
            if fd is not None:
                os.close(fd)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    def expired(*_):
        raise transport.InventoryError("DEADLINE")
    old_handler = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, OUTER_SECONDS - 1)
    recovery = {"binding": None, "candidate_namespace": None, "remote_attempted": False}
    try:
        need(not args, "ARGUMENTS")
        budget = transport.Budget(time.monotonic() + OUTER_SECONDS - 1, RUNNER_CAPTURE_BYTES, RUNNER_COMMANDS)
        receipt = run_qualification(budget, recovery)
    except BaseException as exc:
        code = str(exc) if isinstance(exc, transport.InventoryError) and str(exc) in ERRORS else "INTERNAL"
        receipt = {"schema": SCHEMA, "result": "BLOCKED", "reason": code,
                   "watchdog_ready": False, "stop_capability": "UNPROVEN", "raw_output_exported": False,
                   "remote_scope_state": "UNOBSERVED_MAY_EXIST" if recovery["remote_attempted"] else "NOT_ATTEMPTED",
                   "automatic_cleanup": False, **recovery}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) + 1 > RECEIPT_BYTES:
        encoded = json.dumps({"schema": SCHEMA, "result": "BLOCKED", "reason": "OUTPUT_CAP",
                              "watchdog_ready": False, "stop_capability": "UNPROVEN", "raw_output_exported": False,
                   "remote_scope_state": "UNOBSERVED_MAY_EXIST" if recovery["remote_attempted"] else "NOT_ATTEMPTED",
                   "automatic_cleanup": False, **recovery})
        receipt["result"] = "BLOCKED"
    print(encoded, flush=True)
    return 0 if receipt["result"] == "QUALIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
