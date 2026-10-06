"""Existing owner SSH secret in sealed memfd; private reviewed packet -> short entry."""
import argparse,base64,fcntl,hashlib,io,json,os,re,stat,sys,tarfile,tempfile,types,shlex
from pathlib import Path,PurePosixPath
from diagnostic_process import run,need
SAFE_SETUP_PHASES=frozenset(('OWNER_CONTEXT', 'EVENT_INPUT', 'OWNER_SECRET', 'ORACLE_TRUST', 'PACKET_DOWNLOAD', 'ARCHIVE_UNPACK', 'MANIFEST_VERIFY', 'CLOSURE_VERIFY', 'IDENTITY_IMPORT', 'CHECKOUT_VERIFY', 'FACTORY_IMPORT', 'FACTORY_VERIFY', 'ENTRY_IMPORT', 'PREARM'))
SAFE_SETUP_REFUSAL_CODES=frozenset(('ADMIN_ROUTE_APPROVAL_BINDING', 'AUTHENTICATED_WINDOW_INPUTS_REQUIRED', 'COLLECT_ADMIT_CLOSURE', 'COLLECT_ADMIT_MODE', 'DDS3_SCALARS', 'DIAGNOSTIC_BASELINE', 'DIAGNOSTIC_BINDING', 'DIAGNOSTIC_FRESHNESS', 'DIAGNOSTIC_REMAINING', 'DIAGNOSTIC_SCHEMA', 'DISPATCH_FACTORY_CLOSURE', 'DISPATCH_FACTORY_CYCLE', 'EXISTING_OWNER_SECRET', 'EXISTING_OWNER_SSH_REFERENCES', 'EXPLICIT_OWNER_WINDOW_AND_STOP_APPROVAL_REQUIRED', 'FROZEN_RUNTIME_PROVENANCE', 'FROZEN_RUNTIME_SOURCE', 'GUEST_BOOT', 'INDEPENDENT_STOP_RUN_REQUIRED', 'INPUT_PINS', 'MAIN_PIN', 'MANIFEST_PIN', 'MEMFD_MODE', 'ORACLE_FINGERPRINT', 'ORACLE_PUBLIC_TRUST', 'OWNER_BUDGET', 'OWNER_CHECKOUT_CODE_PIN', 'OWNER_CHILD_EXIT', 'OWNER_CHILD_RESULT', 'OWNER_CLOSURE_SCOPE', 'OWNER_CODE_CHANGED', 'OWNER_CONTENT_IDENTITY', 'OWNER_CONTEXT', 'OWNER_CURRENT_MAIN', 'OWNER_DEADLINE_RESERVE', 'OWNER_DISPATCH_DUPLICATE', 'OWNER_DISPATCH_EVENT_LIMIT', 'OWNER_DISPATCH_EVENT_REQUIRED', 'OWNER_DISPATCH_INPUT_SCOPE', 'OWNER_DISPATCH_MODE', 'OWNER_DISPATCH_PACKET_PINS', 'OWNER_DISPATCH_REVIEW_PINS', 'OWNER_MANIFEST_PIN', 'OWNER_MANIFEST_SCOPE', 'OWNER_PARENT', 'OWNER_POWER_LANE', 'OWNER_PRIMARY_JOBS', 'OWNER_PRIMARY_RUN', 'OWNER_REVIEWED_CODE_PINS', 'OWNER_RUN', 'OWNER_SETUP_CONTEXT', 'OWNER_SHA', 'OWNER_STOP_ID', 'OWNER_WINDOW_POLICY_PIN', 'PRIMARY_UNVERIFIED', 'PRIVATE_ARCHIVE_PIN', 'PRIVATE_ARCHIVE_SCOPE', 'PRIVATE_CLOSURE', 'PRIVATE_FAILURE_CODE', 'PRIVATE_FAILURE_FIELDS', 'PRIVATE_FAILURE_STAGE', 'PRIVATE_FAILURE_TIMING', 'PRIVATE_RECEIPT_DIRECTORY', 'PRIVATE_RECEIPT_FILE', 'PRIVATE_RECEIPT_LIMIT', 'PRIVATE_RECEIPT_PARENT', 'PRIVATE_RECEIPT_READBACK', 'PRIVATE_RECEIPT_WRITE', 'PRIVATE_SOURCE', 'PROTECTED_SCOPE', 'PROTECTED_STATES', 'PUBLIC_BOOL', 'PUBLIC_BUFFER', 'PUBLIC_DEFAULT', 'PUBLIC_DUPLICATE', 'PUBLIC_FAILURE', 'PUBLIC_FAILURE_KIND', 'PUBLIC_FRAME', 'PUBLIC_HASH', 'PUBLIC_MUTATIONS', 'PUBLIC_ORDER', 'PUBLIC_PHASE_ORDER', 'PUBLIC_REFUSAL', 'PUBLIC_RESULT', 'PUBLIC_RESULT_BOOL', 'PUBLIC_RESULT_FIELDS', 'PUBLIC_RESULT_KIND', 'PUBLIC_RESULT_MUTATIONS', 'PUBLIC_STATUS', 'PUBLIC_WRITE_LIMIT', 'PUBLIC_WRITE_TIMEOUT', 'PUBLIC_WRITE_ZERO', 'QUALIFIED_MAIN_CHECKOUT', 'QUEUE_FRESHNESS', 'QUEUE_STALE_BEFORE_ARM', 'REVIEWED_PRIOR_BINDING_REQUIRED', 'ROOT_ROUTE_APPROVAL_REQUIRED', 'RUNNING_NOT_OBSERVED', 'SOURCE_ONLY_NOT_ARMED', 'SOURCE_PATH', 'SOURCE_READBACK', 'STATUS_PHASE', 'STOP_CONTRACT_PRELUDE', 'STOP_CONTRACT_PRELUDE_ORIGIN', 'STOP_CONTRACT_PRELUDE_STALE', 'TRUSTED_DISPATCH_FACTORY_REQUIRED', 'TRUSTED_DISPATCH_INPUTS_REQUIRED', 'UNKNOWN', 'UV_SCALARS', 'UV_SCHEMA', 'UV_TYPED_PRE', 'UV_TYPED_ROW'))
_setup_phase="OWNER_CONTEXT"
def setup_phase(value):
 global _setup_phase
 need(value in SAFE_SETUP_PHASES,"UNKNOWN")
 _setup_phase=value
def setup_refusal(error):
 # Never stringify an exception or its arbitrary arguments.
 args=error.args if type(error) in (RuntimeError,ValueError) else ()
 code=args[0] if type(error) in (RuntimeError,ValueError) and type(args) is tuple and len(args)==1 and type(args[0]) is str and args[0] in SAFE_SETUP_REFUSAL_CODES else "UNKNOWN"
 phase=_setup_phase if _setup_phase in SAFE_SETUP_PHASES else "OWNER_CONTEXT"
 return dict(kind="OWNER_SETUP_REFUSED",completion_proven=False,power_state="UNKNOWN",failure_stage=phase,failure_code=code)

FP="SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go"
def memory(raw,name):
 fd=os.memfd_create(name,os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
 try:
  os.fchmod(fd,0o600);need(stat.S_IMODE(os.fstat(fd).st_mode)==0o600,"MEMFD_MODE")
  offset=0
  while offset<len(raw):offset+=os.write(fd,raw[offset:])
  fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SEAL);return fd
 except BaseException:os.close(fd);raise
def known_hosts():
 import time
 raw=run(["/usr/bin/ssh-keyscan","-T","3","-t","ed25519","92.5.47.149"],time.monotonic()+4)
 rows=[r.split() for r in raw.decode().splitlines() if r and not r.startswith("#")]
 need(len(rows)==1 and rows[0][:2]==["92.5.47.149","ssh-ed25519"],"ORACLE_PUBLIC_TRUST")
 need("SHA256:"+base64.b64encode(hashlib.sha256(base64.b64decode(rows[0][2],validate=True)).digest()).decode().rstrip("=")==FP,"ORACLE_FINGERPRINT")
 return raw
def private_packet(digest,keyfd,knownfd):
 import time
 root="/home/ubuntu/.local/share/bridge-school/ibm-diagnostic-v9/"+digest
 code="import os,stat,hashlib,base64;assert os.getuid()==1001;root="+repr(root)+";fds=[];fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)\nfor part in root.split('/')[1:]:\n fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);fds.append(fd);s=os.fstat(fd);assert s.st_uid in (0,1001) and not(s.st_mode&0o022)\ns=os.fstat(fd);assert s.st_uid==1001 and stat.S_IMODE(s.st_mode)==0o700;rootfd=fd;f=os.open('DIAGNOSTIC-PACKAGE.tar',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=rootfd);s=os.fstat(f);assert stat.S_ISREG(s.st_mode) and s.st_uid==1001 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=8388608;raw=os.read(f,s.st_size+1);after=os.fstat(f);assert len(raw)==s.st_size and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) and hashlib.sha256(raw).hexdigest()=="+repr(digest)+";print(base64.b64encode(raw).decode());os.close(f)\nfor fd in reversed(fds):os.close(fd)"
 cmd=["/usr/bin/ssh","-F","/dev/null","-T","-i","/proc/"+str(os.getpid())+"/fd/"+str(keyfd),"-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=yes","-o","HostKeyAlgorithms=ssh-ed25519","-o","GlobalKnownHostsFile=/dev/null","-o","UserKnownHostsFile=/proc/"+str(os.getpid())+"/fd/"+str(knownfd),"-o","UpdateHostKeys=no","-o","VerifyHostKeyDNS=no","-o","ForwardAgent=no","-o","ClearAllForwardings=yes","-o","ConnectTimeout=3","-o","ConnectionAttempts=1","ubuntu@92.5.47.149","/usr/bin/python3 -I -B -c "+shlex.quote(code)]
 raw=base64.b64decode(run(cmd,time.monotonic()+10,limit=16*1024*1024).strip(),validate=True)
 need(len(raw)<=8388608 and hashlib.sha256(raw).hexdigest()==digest,"PRIVATE_ARCHIVE_PIN")
 return raw
def unpack(root,raw):
 total=0;names=set()
 with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as tar:
  for m in tar.getmembers():
   p=PurePosixPath(m.name);total+=m.size
   need(len(names)<64 and m.isfile() and m.name==str(p) and not p.is_absolute() and ".." not in p.parts and m.name not in names and 0<m.size<=131072 and total<=8388608,"PRIVATE_ARCHIVE_SCOPE");names.add(m.name)
   target=root/m.name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   try:
    data=tar.extractfile(m).read();offset=0
    while offset<len(data):offset+=os.write(fd,data[offset:])
    os.fsync(fd)
   finally:os.close(fd)
def main(argv=None):
 import time
 started=time.monotonic()
 p=argparse.ArgumentParser()
 for name in ("archive-sha","manifest-sha","expected-main"):p.add_argument("--"+name,required=True)
 p.add_argument("--run-reviewed",action="store_true");a=p.parse_args(argv)
 need(re.fullmatch("[0-9a-f]{64}",a.archive_sha) and re.fullmatch("[0-9a-f]{64}",a.manifest_sha) and re.fullmatch("[0-9a-f]{40}",a.expected_main),"INPUT_PINS")
 expected={"GITHUB_REPOSITORY":"olegmed1-art/bridge-video-free","GITHUB_ACTOR":"olegmed1-art","GITHUB_TRIGGERING_ACTOR":"olegmed1-art","GITHUB_EVENT_NAME":"workflow_dispatch","GITHUB_REF":"refs/heads/main","GITHUB_SHA":a.expected_main,"GITHUB_RUN_ATTEMPT":"1","GITHUB_JOB":"ibm-diagnostic-trial"}
 need(all(os.environ.get(k)==v for k,v in expected.items()) and re.fullmatch("[1-9][0-9]{5,14}",os.environ.get("GITHUB_RUN_ID","")),"OWNER_SETUP_CONTEXT")
 return _supervise(lambda end:_run(a,end),started)
def _run(a,end):
 setup_phase("EVENT_INPUT")
 event_path=os.environ.get("GITHUB_EVENT_PATH","");need(bool(event_path),"OWNER_DISPATCH_EVENT_REQUIRED")
 event_fd=os.open(event_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  event_raw=os.read(event_fd,131073);need(0<len(event_raw)<=131072,"OWNER_DISPATCH_EVENT_LIMIT")
 finally:os.close(event_fd)
 def unique(items):
  out={}
  for k,v in items:need(k not in out,"OWNER_DISPATCH_DUPLICATE");out[k]=v
  return out
 event=json.loads(event_raw,object_pairs_hook=unique);dispatch=event.get("inputs")
 yes=lambda v:v is True or type(v) is str and v=="true"
 need(type(dispatch) is dict,"OWNER_DISPATCH_MODE")
 mode=dispatch.get("run_reviewed")
 need((type(mode) is bool or type(mode) is str and mode in ("true","false")) and yes(mode)==a.run_reviewed,"OWNER_DISPATCH_MODE")
 if a.run_reviewed:
  need(yes(dispatch.get("approve_closed_window")) and yes(dispatch.get("approve_stop_route_hold")),"EXPLICIT_OWNER_WINDOW_AND_STOP_APPROVAL_REQUIRED")
 need(dispatch.get("reviewed_archive_sha")==a.archive_sha and dispatch.get("reviewed_manifest_sha")==a.manifest_sha and dispatch.get("expected_main_sha")==a.expected_main,"OWNER_DISPATCH_PACKET_PINS")
 setup_phase("OWNER_SECRET")
 key=bytearray(os.environ.pop("ORACLE_SSH_PRIVATE_KEY","").encode());need(32<len(key)<32768,"EXISTING_OWNER_SECRET")
 keyfd=knownfd=None
 try:
  keyfd=memory(key,"existing-oracle-owner-key")
  for i in range(len(key)):key[i]=0
  setup_phase("ORACLE_TRUST")
  knownfd=memory(known_hosts(),"oracle-public-trust")
  setup_phase("PACKET_DOWNLOAD")
  raw=private_packet(a.archive_sha,keyfd,knownfd)
  root=Path(tempfile.mkdtemp(prefix="ibm-diagnostic-",dir=os.environ.get("RUNNER_TEMP")))
  setup_phase("ARCHIVE_UNPACK")
  unpack(root,raw)
  setup_phase("MANIFEST_VERIFY")
  manifest_raw=(root/"SUCCESSOR-MANIFEST.json").read_bytes();need(hashlib.sha256(manifest_raw).hexdigest()==a.manifest_sha,"MANIFEST_PIN")
  manifest=json.loads(manifest_raw);need(manifest["main_sha"]=='64ed969050882d375e2eff87cb130ee7752aee8b',"FROZEN_RUNTIME_SOURCE")
  setup_phase("CLOSURE_VERIFY")
  verified_cache={}
  for name,pin in manifest["files"].items():
   data=(root/name).read_bytes();need(hashlib.sha256(data).hexdigest()==pin,"PRIVATE_CLOSURE");verified_cache[name]=data
  setup_phase("IDENTITY_IMPORT")
  identity=types.ModuleType("owner_identity");sys.modules["owner_identity"]=identity;exec(compile(verified_cache["owner_identity.py"],"<verified-owner-identity>","exec"),identity.__dict__)
  identity.pins(manifest["owner_code_pins"])
  setup_phase("CHECKOUT_VERIFY")
  checkout=Path(os.environ["GITHUB_WORKSPACE"])
  for path,pin in manifest["owner_code_pins"].items():
   need(hashlib.sha256((checkout/path).read_bytes()).hexdigest()==pin,"OWNER_CHECKOUT_CODE_PIN")
  context={k:os.environ.get("GITHUB_"+k.upper()) for k in ("repository","actor","triggering_actor","event_name","ref","sha")}
  context.update(run_id=int(os.environ.get("GITHUB_RUN_ID","0")),run_attempt=int(os.environ.get("GITHUB_RUN_ATTEMPT","0")))
  setup_phase("FACTORY_IMPORT")
  owner_inputs=types.ModuleType("owner_inputs");owner_inputs.__file__="<verified-owner-inputs>";sys.modules["owner_inputs"]=owner_inputs
  exec(compile(verified_cache["owner_inputs.py"],owner_inputs.__file__,"exec"),owner_inputs.__dict__);owner_inputs.__source_sha256__=manifest["files"]["owner_inputs.py"]
  setup_phase("FACTORY_VERIFY")
  fresh_inputs=owner_inputs.DispatchInputsFactory.from_runner(context,dispatch,manifest) if a.run_reviewed else None
  setup_phase("ENTRY_IMPORT")
  raw=verified_cache["diagnostic_entry.py"]
  module=types.ModuleType("reviewed_diagnostic_entry");module.__file__=str(root/"diagnostic_entry.py");sys.modules[module.__name__]=module
  exec(compile(raw,module.__file__,"exec"),module.__dict__)
  # Same process holds memory fds; target c.Channel opens parent's /proc fd paths.
  os.environ.update(ORACLE_KEY_PATH="/proc/"+str(os.getpid())+"/fd/"+str(keyfd),ORACLE_KNOWN_HOSTS="/proc/"+str(os.getpid())+"/fd/"+str(knownfd))
  args=["--manifest-sha",a.manifest_sha]+(["--run-reviewed"] if a.run_reviewed else [])
  setup_phase("PREARM")
  return module.entry(args,owner_deadline=end,collect_admit_inputs=fresh_inputs)
 finally:
  for i in range(len(key)):key[i]=0
  for fd in (knownfd,keyfd):
   if fd is not None:os.close(fd)

OWNER_BUDGET_SECONDS=420
STATUS_PHASES=("ARMED","START_REQUESTED","START_ACKNOWLEDGED","RUNNING_OBSERVED")
def _public_frame(raw):
 def unique(pairs):
  out={}
  for k,v in pairs:need(k not in out,"PUBLIC_DUPLICATE");out[k]=v
  return out
 v=json.loads(raw,object_pairs_hook=unique);need(type(v) is dict,"PUBLIC_FRAME")
 if v.get("kind")=="DIAGNOSTIC_STATUS":
  need(set(v)=={"kind","phase"} and v["phase"] in STATUS_PHASES,"PUBLIC_STATUS");return v
 if v.get("kind")=="DIAGNOSTIC_TRIAL_COMPLETE":
  expected={"kind","diagnostic_completed","power_completion_proven","channel_cleanup_verified","failure_kind","completion_proven","repair_completed","production_guest_mutations","private_result_sha256","private_receipt_retained"}
  need(set(v)==expected,"PUBLIC_RESULT")
  for k in ("diagnostic_completed","power_completion_proven","channel_cleanup_verified","completion_proven","repair_completed","private_receipt_retained"):need(type(v[k]) is bool,"PUBLIC_BOOL")
  need(v["repair_completed"] is False and type(v["production_guest_mutations"]) is int and v["production_guest_mutations"]==0,"PUBLIC_MUTATIONS")
  need(v["failure_kind"] is None or (type(v["failure_kind"]) is str and re.fullmatch("[A-Za-z][A-Za-z0-9_]{0,63}",v["failure_kind"])),"PUBLIC_FAILURE")
  need(type(v["private_result_sha256"]) is str and re.fullmatch("[0-9a-f]{64}",v["private_result_sha256"]),"PUBLIC_HASH");return v
 if v.get("kind")=="VERIFIED_NOT_ARMED":
  need(set(v)=={"kind","main_sha","runtime_package_sha"} and re.fullmatch("[0-9a-f]{40}",v["main_sha"]) and re.fullmatch("[0-9a-f]{64}",v["runtime_package_sha"]),"PUBLIC_DEFAULT");return v
 if v.get("kind")=="OWNER_SETUP_REFUSED":
  need(set(v)=={"kind","completion_proven","power_state","failure_stage","failure_code"} and v["completion_proven"] is False and v["power_state"]=="UNKNOWN" and v["failure_stage"] in SAFE_SETUP_PHASES and v["failure_code"] in SAFE_SETUP_REFUSAL_CODES,"PUBLIC_REFUSAL");return v
 need(False,"PUBLIC_REFUSAL");return v
def _supervise(target,started,seconds=OWNER_BUDGET_SECONDS):
 import signal,selectors,time,ctypes
 need(type(seconds) in (int,float) and 0<seconds<=OWNER_BUDGET_SECONDS,"OWNER_BUDGET")
 end=started+seconds;reader,writer=os.pipe();parent=os.getpid();pid=None;reaped=False;terminal=False;terminal_frame=None;phase_index=0;seq=0;reason=None;status=None;buf=bytearray()
 original_blocking=os.get_blocking(1)
 def emit(v,limit=None):
  raw=(json.dumps(v,sort_keys=True)+"\n").encode();need(len(raw)<=2048,"PUBLIC_WRITE_LIMIT")
  until=min(end,time.monotonic()+1) if limit is None else limit
  s=selectors.DefaultSelector();offset=0
  try:
   s.register(1,selectors.EVENT_WRITE)
   while offset<len(raw):
    left=until-time.monotonic();need(left>0 and s.select(left),"PUBLIC_WRITE_TIMEOUT")
    try:n=os.write(1,raw[offset:])
    except BlockingIOError:continue
    need(n>0,"PUBLIC_WRITE_ZERO");offset+=n
  finally:s.close()
 try:
  os.set_blocking(1,False)
  emit({"kind":"DIAGNOSTIC_STATUS","phase":"OWNER_STARTED","seq":0,"elapsed_ms":max(0,int((time.monotonic()-started)*1000))})
  pid=os.fork()
  if pid==0:
   try:
    os.close(reader);os.setsid()
    need(ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0 and os.getppid()==parent,"OWNER_PARENT")
    os.dup2(writer,1);os.close(writer);null=os.open("/dev/null",os.O_WRONLY);os.dup2(null,2);os.close(null)
    try:code=target(end)
    except BaseException as error:
     print(json.dumps(setup_refusal(error)),flush=True);code=78
    sys.stdout.flush();os._exit(code if type(code) is int and 0<=code<=255 else 78)
   except BaseException:os._exit(78)
  os.close(writer);writer=None;os.set_blocking(reader,False);s=selectors.DefaultSelector();s.register(reader,selectors.EVENT_READ);eof=False
  try:
   while time.monotonic()<end:
    if not eof:
     for _,_ in s.select(min(.05,max(0,end-time.monotonic()))):
      part=os.read(reader,4096)
      if not part:eof=True;s.unregister(reader);break
      buf.extend(part);need(len(buf)<=8192,"PUBLIC_BUFFER")
      while b"\n" in buf:
       raw,_,tail=buf.partition(b"\n");buf=bytearray(tail);need(not terminal and len(raw)<=2048,"PUBLIC_ORDER")
       v=_public_frame(raw)
       if v["kind"]=="DIAGNOSTIC_STATUS":
        need(phase_index<len(STATUS_PHASES) and v["phase"]==STATUS_PHASES[phase_index],"PUBLIC_PHASE_ORDER");phase_index+=1;seq+=1
        v.update(seq=seq,elapsed_ms=max(0,int((time.monotonic()-started)*1000)))
        emit(v)
       else:terminal=True;terminal_frame=v
    if not reaped:
     child,value=os.waitpid(pid,os.WNOHANG)
     if child:status=os.waitstatus_to_exitcode(value);pid=None;reaped=True
    if reaped:
     if not eof:
      # Drain only bytes already delivered by the sole child; inherited writers are a refusal.
      if s.select(0):continue
     if eof:
      need(not buf and terminal,"OWNER_CHILD_RESULT")
      expected=0 if terminal_frame["kind"]=="VERIFIED_NOT_ARMED" or (terminal_frame["kind"]=="DIAGNOSTIC_TRIAL_COMPLETE" and terminal_frame["completion_proven"] and terminal_frame["private_receipt_retained"]) else 78
      need(status==expected,"OWNER_CHILD_EXIT")
      emit(terminal_frame);return status
     reason="CHILD_OUTPUT_OPEN";break
    if eof:time.sleep(min(.01,max(0,end-time.monotonic())))
   if reason is None:reason="DEADLINE"
  finally:s.close()
 except BaseException:reason="OUTPUT_OR_SUPERVISOR_REFUSED"
 finally:
  os.close(reader)
  if writer is not None:os.close(writer)
  if pid is not None:
   try:os.killpg(pid,signal.SIGKILL)
   except ProcessLookupError:
    try:os.kill(pid,signal.SIGKILL)
    except ProcessLookupError:pass
   cleanup_end=time.monotonic()+1
   while time.monotonic()<cleanup_end:
    child,_=os.waitpid(pid,os.WNOHANG)
    if child:reaped=True;pid=None;break
    time.sleep(.01)
  # A blocked log sink cannot suspend child termination; reporting itself is bounded.
  if reason is not None:
   try:emit({"kind":"OWNER_DEADLINE_UNKNOWN","reason":reason,"power_state":"UNKNOWN","completion_proven":False,"child_reaped":reaped},min(end+2,time.monotonic()+1))
   except BaseException:pass
  os.set_blocking(1,original_blocking)
 return 78

if __name__=="__main__":
 try:raise SystemExit(main())
 except Exception as error:print(json.dumps(setup_refusal(error)),flush=True);raise SystemExit(78)
