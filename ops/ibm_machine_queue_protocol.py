"""Fixed queue proof protocol. No default production context or power authority."""
import os,sys,json,time,hashlib,selectors,subprocess,signal,threading,datetime,concurrent.futures
from pathlib import Path
SQL="SELECT clock_timestamp() AS observed_at, current_database() AS database_name, (SELECT count(*) FROM assistant_lab.job WHERE status NOT IN ('CANCELLED','COMPLETED','FAILED')) AS lab_nonterminal, (SELECT count(*) FROM assistant_lab.control_command WHERE status NOT IN ('CANCELLED','COMPLETED','FAILED')) AS control_nonterminal, (SELECT count(*) FROM assistant_lab.job WHERE status IS NULL) + (SELECT count(*) FROM assistant_lab.control_command WHERE status IS NULL) AS null_status_count, (SELECT NOT relrowsecurity FROM pg_class WHERE oid='assistant_lab.job'::regclass) AS job_rls_off, (SELECT NOT relrowsecurity FROM pg_class WHERE oid='assistant_lab.control_command'::regclass) AS control_rls_off;"
SQL_SHA="c6f2135e43741413ffac82e5fb4b33a8670a6701680cf2345f8794838eb04d8a"
class Refused(Exception):pass
def need(ok,code):
 if not ok:raise Refused(code)
def canonical(x):return (json.dumps(x,sort_keys=True,separators=(",",":"))+"\n").encode()
def parse(raw):
 def unique(pairs):
  d={}
  for k,v in pairs:need(k not in d,"DUPLICATE_KEY");d[k]=v
  return d
 try:return json.loads(raw,object_pairs_hook=unique)
 except Refused:raise
 except BaseException:raise Refused("INVALID_JSON") from None
class HardCap:
 def __init__(self,seconds):
  reader,writer=os.pipe();parent=os.getpid();end=time.monotonic()+seconds;pid=os.fork()
  if pid==0:
   os.close(writer);sel=selectors.DefaultSelector();sel.register(reader,selectors.EVENT_READ)
   while True:
    left=end-time.monotonic()
    if left<=0:
     if os.getppid()==parent:os.kill(parent,signal.SIGKILL)
     os._exit(124)
    if sel.select(min(left,.05)) and not os.read(reader,1):os._exit(0)
  os.close(reader);self.fd=writer;self.pid=pid
 def close(self):os.close(self.fd);os.waitpid(self.pid,0)
class PipeClient:
 def __init__(self,argv,env,cap=5):
  parent=os.getpid()
  def guard():
   import ctypes
   if ctypes.CDLL(None).prctl(1,signal.SIGKILL)!=0 or os.getppid()!=parent:os._exit(125)
  self.p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,start_new_session=True,preexec_fn=guard)
  os.set_blocking(self.p.stdin.fileno(),False);os.set_blocking(self.p.stdout.fileno(),False)
  self.cap=cap;self.buf=bytearray();self.lock=threading.Lock()
 def wait(self,fd,event,end):
  sel=selectors.DefaultSelector();sel.register(fd,event)
  try:
   left=end-time.monotonic();need(left>0 and bool(sel.select(left)),"PIPE_TIMEOUT")
  finally:sel.close()
 def send(self,value,end):
  raw=canonical(value);need(len(raw)<=65536,"FRAME_LIMIT");pos=0
  while pos<len(raw):
   self.wait(self.p.stdin.fileno(),selectors.EVENT_WRITE,end)
   try:n=os.write(self.p.stdin.fileno(),raw[pos:])
   except (BrokenPipeError,OSError):raise Refused("PIPE_CLOSED") from None
   need(n>0,"PIPE_CLOSED");pos+=n
 def recv(self,end):
  while b"\n" not in self.buf:
   self.wait(self.p.stdout.fileno(),selectors.EVENT_READ,end)
   try:raw=os.read(self.p.stdout.fileno(),4096)
   except OSError:raise Refused("PIPE_CLOSED") from None
   need(bool(raw),"PIPE_EOF");self.buf.extend(raw);need(len(self.buf)<=65536,"FRAME_LIMIT")
  raw,_,rest=self.buf.partition(b"\n");self.buf=bytearray(rest)
  result=parse(raw);need(isinstance(result,dict),"FRAME_SCHEMA")
  need(result.get("kind")!="ADAPTER_ERROR","ADAPTER_FAILED")
  return result
 def call(self,value,end):
  with self.lock:self.send(value,end);return self.recv(end)
 def close(self):
  if self.p.poll() is None:
   try:os.killpg(self.p.pid,signal.SIGKILL)
   except ProcessLookupError:pass
  self.p.wait(timeout=1);self.p.stdin.close();self.p.stdout.close()
def stamp(value):
 try:t=datetime.datetime.fromisoformat(value.replace("Z","+00:00"));need(t.tzinfo is not None,"TIMESTAMP_ZONE");return t.timestamp()
 except Refused:raise
 except BaseException:raise Refused("TIMESTAMP_FORMAT") from None
class Supervisor:
 """Pure orchestration with supplied bound contexts. No production default."""
 def __init__(self,queue,source,reviewed,cap=5,used_nonces=()):
  self.queue=queue;self.source=source;self.reviewed=reviewed;self.cap=cap;self.used=set(used_nonces)
 def prove(self,request):
  r=self.reviewed;required={"kind","phase","nonce","host","boot","run","requested_mono","project","branch","database","sql_sha256"}
  need(set(request)==required and request["kind"]=="QUEUE_REQUEST" and request["phase"] in ("PRE_STOP","POST_STOP"),"REQUEST_SCHEMA")
  need((request["host"],request["boot"],request["run"])==(r["host"],r["boot"],r["run"]),"REQUEST_BINDING")
  need((request["project"],request["branch"],request["database"],request["sql_sha256"])==(r["project"],r["branch"],r["database"],SQL_SHA),"QUERY_SCOPE")
  need(isinstance(request["nonce"],str) and len(request["nonce"])==32 and all(c in "0123456789abcdef" for c in request["nonce"]),"NONCE_SCHEMA")
  need(request["nonce"] not in self.used,"NONCE_REPLAY");self.used.add(request["nonce"])
  need(type(request["requested_mono"]) in (int,float) and __import__("math").isfinite(request["requested_mono"]) and request["requested_mono"]>=0,"MONOTONIC_SCHEMA")
  end=time.monotonic()+self.cap
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
   a=pool.submit(self.queue.call,dict(request,sql=SQL),end);b=pool.submit(self.source.call,request,end)
   row=a.result(timeout=self.cap+1);primary=b.result(timeout=self.cap+1)
  need(time.monotonic()<end,"ROUNDTRIP_TIMEOUT")
  need(set(row)=={"kind","nonce","observed_at","database_name","lab_nonterminal","control_nonterminal","null_status_count","job_rls_off","control_rls_off"} and row["kind"]=="FIXED_QUERY_RESULT" and row["nonce"]==request["nonce"] and row["database_name"]==r["database"],"QUEUE_RESULT_SCHEMA")
  for k in ("lab_nonterminal","control_nonterminal","null_status_count"):need(type(row[k]) is int and row[k]==0,"QUEUE_NOT_ZERO")
  need(row["job_rls_off"] is True and row["control_rls_off"] is True,"RLS_VISIBILITY")
  validate_primary(primary,r,request["nonce"])
  need(0<=time.time()-stamp(row["observed_at"])<=10,"STALE_QUEUE_PROOF")
  proof={k:request[k] for k in ("nonce","host","boot","run","project","branch","database","sql_sha256")}
  proof.update(kind="QUEUE_PROOF",query_complete=True,read_only=True,observed_at=row["observed_at"],primary={k:primary[k] for k in ("main_sha","power_sha","run","run_status","mode","trial_start","manual_console_trial","verified_at")})
  proof.update({k:row[k] for k in ("lab_nonterminal","control_nonterminal","null_status_count","job_rls_off","control_rls_off")})
  return proof

 def session(self,relay,bind):
  r=self.reviewed;end=time.monotonic()+10
  ready=relay.recv(end)
  need(set(ready)=={"kind","host","boot","source_pins","wall","mono","deadline_seconds","no_mutation_yet"} and ready["kind"]=="EXECUTOR_READY" and ready["host"]==r["host"] and ready["boot"]==r["boot"] and ready["source_pins"]==bind["source_pins"] and ready["no_mutation_yet"] is True and ready["deadline_seconds"]==55,"EXECUTOR_READBACK")
  phases=[]
  while True:
   request=relay.recv(time.monotonic()+55)
   if request.get("kind")=="REPAIR_RESULT":
    need(phases==["PRE_STOP","POST_STOP"] and request.get("state")=="APPLIED_QUIESCENT_NOT_STARTED" and request.get("automatic_starts")==0 and request.get("stop_now_required") is True,"REPAIR_RESULT_REFUSED")
    return {"kind":"MACHINE_QUEUE_COMPLETE","proofs":2,"automatic_starts":0,"parent_stop_required":True}
   need(request.get("kind")=="QUEUE_REQUEST" and len(phases)<2 and request.get("phase")==("PRE_STOP" if not phases else "POST_STOP"),"PHASE_SEQUENCE")
   proof=self.prove(request);phases.append(request["phase"])
   relay.send(proof,time.monotonic()+min(1,self.cap))

def validate_primary(primary,reviewed,nonce,now=None):
 need(type(primary) is dict and set(primary)=={"kind","nonce","main_sha","power_sha","run","run_status","mode","trial_start","manual_console_trial","verified_at"} and primary["kind"]=="PRIMARY_RESULT" and primary["nonce"]==nonce,"PRIMARY_SCHEMA_OR_NONCE")
 need((primary["main_sha"],primary["power_sha"],primary["run"])==(reviewed["main_sha"],reviewed["power_sha"],reviewed["run"]),"PRIMARY_PIN")
 need((primary["run_status"],primary["mode"],primary["trial_start"],primary["manual_console_trial"])==("in_progress","manual_console_trial","skipped","in_progress"),"POWER_PHASE")
 need(0<=(time.time() if now is None else now)-stamp(primary["verified_at"])<=10,"PRIMARY_STALE_OR_FUTURE")
def remaining_budget(deadline,now=None):
 need(deadline-(time.monotonic() if now is None else now)>=70,"INSUFFICIENT_RELAY_CLEANUP_BUDGET")
def accept_channel(relay,primary,reviewed,nonce,deadline):
 validate_primary(primary,reviewed,nonce);remaining_budget(deadline)
 relay.send({"kind":"CHANNEL_ACCEPT"},time.monotonic()+1)
