"""Fixed Oracle adapter. Private package/approval stay on host; no keys are copied here."""
import base64,hashlib,json,os,re,selectors,stat,sys,time,types
from pathlib import Path
from ops.ibm_machine_queue_protocol import PipeClient,HardCap,need,parse,canonical,remaining_budget
ROOT=Path("/home/ubuntu/.local/share/bridge-school/ibm-machine-queue")
FILES={"relay.py","bundle.json"}
def read_private(name,limit=150000):
 need(Path(name).name==name,"PRIVATE_PATH")
 root=os.lstat(ROOT);need(stat.S_ISDIR(root.st_mode) and root.st_uid==os.getuid() and stat.S_IMODE(root.st_mode)==0o700,"PRIVATE_ROOT")
 fd=os.open(ROOT/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  st=os.fstat(fd);need(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)==0o600 and st.st_nlink==1 and 0<st.st_size<=limit,"PRIVATE_FILE")
  raw=os.read(fd,limit+1);need(len(raw)==st.st_size,"PRIVATE_READBACK");return raw
 finally:os.close(fd)
class Stdio:
 def __init__(self):self.buf=bytearray()
 def recv(self,end):
  while b"\n" not in self.buf:
   s=selectors.DefaultSelector();s.register(0,selectors.EVENT_READ)
   try:need(end>time.monotonic() and bool(s.select(end-time.monotonic())),"PARENT_TIMEOUT")
   finally:s.close()
   part=os.read(0,4096);need(bool(part),"PARENT_EOF");self.buf.extend(part);need(len(self.buf)<=65536,"FRAME_LIMIT")
  raw,_,rest=self.buf.partition(b"\n");self.buf=bytearray(rest);v=parse(raw);need(type(v) is dict,"FRAME_SCHEMA");return v
 def send(self,v):
  raw=canonical(v);need(len(raw)<=65536,"FRAME_LIMIT")
  # Parent reads concurrently; independent hard cap bounds a blocked stdout.
  sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
def validate_guest_review(bundle,policy,authorization):
 names=("repair_gate.py","fs_transaction.py","host_adapter.py","executor.py","inspector.py")
 need(set(bundle["sources"])==set(names) and set(bundle["source_pins"])==set(names),"GUEST_CLOSURE")
 saved={name[:-3]:sys.modules.get(name[:-3]) for name in names};modules={}
 try:
  for name in names:
   raw=base64.b64decode(bundle["sources"][name],validate=True)
   need(hashlib.sha256(raw).hexdigest()==bundle["source_pins"][name],"GUEST_SOURCE_PIN")
   module=types.ModuleType(name[:-3]);module.__file__="<verified-private-guest>/"+name
   sys.modules[module.__name__]=module;exec(compile(raw,module.__file__,"exec"),module.__dict__);modules[name]=module
  # Pure full allowlist/policy/authorization validation before any IBM connection.
  gp,ga=modules["executor.py"].validate_review(policy,authorization,bundle["source_pins"])
  need((ga["host"],ga["boot"],ga["run"],ga["operation"],ga["candidate_sha256"])==(gp.host,gp.boot,gp.run,modules["executor.py"].gate.OPERATION,gp.candidate_sha256),"APPROVAL_OPERATION_SCOPE")
  scope=modules["host_adapter.py"]
  return {"project":scope.QUEUE_PROJECT,"branch":scope.QUEUE_BRANCH,"database":scope.QUEUE_DATABASE}
 finally:
  for name,value in saved.items():
   if value is None:sys.modules.pop(name,None)
   else:sys.modules[name]=value
def entry(main,expected_package_sha):
 deadline_total=time.monotonic()+135;cap=HardCap(135);child=None;io=Stdio()
 try:
  need(re.fullmatch(r"[0-9a-f]{40}",main or "") and re.fullmatch(r"[0-9a-f]{64}",expected_package_sha or ""),"PIN_SCHEMA")
  raw=read_private("package-manifest.json");need(hashlib.sha256(raw).hexdigest()==expected_package_sha,"PACKAGE_PIN");m=parse(raw)
  need(set(m)=={"format","main_sha","power_sha","files"} and m["format"]=="PRIVATE_IBM_MACHINE_QUEUE_PACKAGE_V1" and m["main_sha"]==main and set(m["files"])==FILES,"PACKAGE_SCHEMA")
  cache={name:read_private(name) for name in FILES}
  need(all(hashlib.sha256(raw).hexdigest()==m["files"][name] for name,raw in cache.items()),"PACKAGE_READBACK")
  relay=types.ModuleType("reviewed_relay");exec(compile(cache["relay.py"],"<reviewed-relay>","exec"),relay.__dict__)
  need(relay.NAMESPACE==ROOT,"RELAY_FIXED_NAMESPACE")
  bundle=relay.validate_bundle(cache["bundle.json"],m["files"]["bundle.json"])
  io.send({"kind":"CHANNEL_PREPARED_NO_IBM_CONNECTION"})
  end=time.monotonic()+60
  while True:
   try:
    approval=read_private("approval.json");pin=read_private("approval.sha256",65).decode().strip()
    need(re.fullmatch(r"[0-9a-f]{64}",pin) and hashlib.sha256(approval).hexdigest()==pin,"APPROVAL_PIN")
    packet=parse(approval);break
   except FileNotFoundError:
    need(time.monotonic()<end,"APPROVAL_NOT_READY");time.sleep(.05)
  need(set(packet)=={"kind","policy","authorization"} and packet["kind"]=="BIND_REVIEWED","APPROVAL_SCHEMA")
  p,a=packet["policy"],packet["authorization"]
  need(p["main_sha"]==m["main_sha"] and p["power_sha"]==m["power_sha"] and p["source_pins"]==bundle["source_pins"] and p["artifact_sha256"]==bundle["artifact_sha256"] and a["reviewed_policy_sha256"]==hashlib.sha256(canonical(p)).hexdigest(),"APPROVAL_SCOPE")
  need(p["parent_safe_stop_armed"] is True and p["parent_stop_deadline"]==p["t0_wall"]+270 and 0<=time.time()-p["t0_wall"]<120 and p["reserve_seconds"]>=p["reviewed_operation_cap"]+5,"PARENT_STOP_WINDOW")
  reviewed={k:p[k] for k in ("host","boot","run","main_sha","power_sha")}
  # Queue scope is hard-pinned in the reviewed guest's adapter, not caller input.
  scope=validate_guest_review(bundle,p,a)
  reviewed.update(scope)
  remaining_budget(deadline_total)
  io.send({"kind":"CHANNEL_BINDING","reviewed":reviewed,"source_pins":bundle["source_pins"]})
  need(io.recv(time.monotonic()+8)=={"kind":"CHANNEL_ACCEPT"},"CHANNEL_NOT_ACCEPTED")
  remaining_budget(deadline_total)
  args=["relay.py","--bundle-sha",m["files"]["bundle.json"],"--execute-reviewed","--run",p["run"],"--policy-sha",hashlib.sha256(canonical(p)).hexdigest(),"--authorization-sha",hashlib.sha256(canonical(a)).hexdigest()]
  code="import base64,sys,types;v=types.ModuleType('reviewed_relay');exec(compile(base64.b64decode("+repr(base64.b64encode(cache["relay.py"]).decode())+"),'<verified-relay>','exec'),v.__dict__);sys.argv="+repr(args)+";sys.exit(v.main())"
  child=PipeClient([sys.executable,"-I","-B","-u","-c",code],{"PATH":"/usr/bin:/bin"},65)
  deadline=time.monotonic()+65
  ready=child.recv(min(deadline,time.monotonic()+10))
  need(ready.get("kind")=="EXECUTOR_READY" and ready.get("host")==p["host"] and ready.get("boot")==p["boot"] and ready.get("source_pins")==bundle["source_pins"],"GUEST_READBACK")
  child.send(packet,min(deadline,time.monotonic()+1));io.send(ready)
  for phase in ("PRE_STOP","POST_STOP"):
   request=child.recv(deadline);need(request.get("kind")=="QUEUE_REQUEST" and request.get("phase")==phase,"GUEST_PHASE")
   io.send(request);proof=io.recv(min(deadline,time.monotonic()+7))
   need(proof.get("kind")=="QUEUE_PROOF" and proof.get("nonce")==request["nonce"],"PARENT_PROOF")
   child.send(proof,min(deadline,time.monotonic()+1))
  result=child.recv(deadline);need(result.get("kind")=="REPAIR_RESULT","GUEST_RESULT");io.send(result)
  need(result.get("state")=="APPLIED_QUIESCENT_NOT_STARTED","REPAIR_REFUSED");return 0
 except BaseException:
  io.send({"kind":"ADAPTER_ERROR"});return 2
 finally:
  if child:child.close()
  cap.close()
