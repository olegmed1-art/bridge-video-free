"""Run-bound four-unit maintenance transport; separate from the read-only probe.

No Start/Stop here. The controller owns one window and ordinary Stop in finally.
Importing this module performs no I/O. No additional credential or SSH trust is made.
"""
import base64
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import subprocess
import time

try:
    from . import ibm_trial_guest_probe as read_only
    from . import ibm_unit_isolation as host_contract
except ImportError:
    import ibm_trial_guest_probe as read_only
    import ibm_unit_isolation as host_contract

HOST_SOURCE_SHA = "b388d41dd1b873fd30920c610201105388a929aa3bfb4be6fcc52485ee711783"
SAFE_SOURCE_SHA = "7711357b7929dcf42d8e49739b16f2e4489191644a0bfb711259f57f182e3bee"
PREPARE_SECONDS = 20
MAINTENANCE_SECONDS = 378
MIN_MAINTENANCE_SECONDS = 115
REMOTE_SECONDS = 368
GUEST_SECONDS = 90
SSH_SECONDS = 105
INPUT_BYTES = 131072
OUTPUT_BYTES = 32768
TARGETS = ("assistant-lab-observer.service", "assistant-lab-control.service",
           "assistant-lab-control-bridge.service", "bridge-ben-healthcheck.timer")
IDENTITY = dict(instance_id="02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2",
                instance_name="bridge-school-compute-ibm",
                boot_volume_id="r010-430d0d70-3cdf-4f0c-8709-53e0ee0c4d7e",
                image_id="r010-25f84546-413c-4476-83ee-ae9580f554e5",
                image_name="bridge-ibm-before-start-20261001", profile="bx3dc-8x40")
TRANSPORT = dict(ibm_host="161.156.86.34", ibm_hostkey_sha256="SHA256:o+7KPm2p4VSZSvFFYzIIzA8iiXfze2hhW9tUM58e00w",
                 oracle_host=read_only.ORACLE, oracle_hostkey_sha256=read_only.ORACLE_FP,
                 strict_host_key_checking="yes", known_hosts_source="sealed-pinned-record")
CONSUMED_RUNS = read_only.CONSUMED_RUNS | {"38065148168", "38064916368", "37939530764"}

class IsolationError(Exception):
    pass

def need(ok, code):
    if not ok:
        raise IsolationError(code)

def emit(event, **fields):
    read_only.emit(event, **fields)

def exact(row, expected):
    return type(row) is dict and set(row)==set(expected) and all(type(row[k]) is type(v) and row[k]==v for k,v in expected.items())

def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, "duplicate_json_field")
            out[key] = value
        return out
    def constant(_):
        raise IsolationError("invalid_json_constant")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError):
        raise IsolationError("invalid_json") from None

def bounded_process(argv, *, payload=b"", timeout=10, limit=OUTPUT_BYTES):
    """Both directions, elapsed time and owned descendant group remain bounded."""
    need(type(payload) is bytes and len(payload) <= INPUT_BYTES, "input_limit")
    need(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= MAINTENANCE_SECONDS,
         "transport_budget")
    selector = selectors.DefaultSelector()  # Allocate before starting any child.
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True,
                                 env={"PATH":"/usr/bin:/bin", "LANG":"C.UTF-8", "LC_ALL":"C.UTF-8"})
    except BaseException:
        selector.close()
        raise
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    end = time.monotonic() + timeout
    offset = 0
    try:
        for stream, label in ((child.stdout, "stdout"), (child.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        if payload:
            os.set_blocking(child.stdin.fileno(), False)
            selector.register(child.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            child.stdin.close()
        while selector.get_map():
            left = end - time.monotonic()
            need(left > 0, "transport_timeout")
            events = selector.select(left)
            need(bool(events), "transport_timeout")
            for key, _ in events:
                if key.data == "stdin":
                    try:
                        offset += os.write(key.fd, payload[offset:offset+4096])
                    except BrokenPipeError:
                        offset = len(payload)
                    if offset == len(payload):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    part = os.read(key.fd, 4096)
                    if not part:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    else:
                        buffers[key.data].extend(part)
                        need(sum(map(len, buffers.values())) <= limit, "output_limit")
        # Observe exit without reaping: the zombie leader reserves the process
        # group ID until cleanup, avoiding a recycled-PID kill race.
        while True:
            need(time.monotonic() < end, "transport_timeout")
            info = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if info is not None:
                rc = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
                break
            time.sleep(min(.01, max(0, end-time.monotonic())))
        need(time.monotonic() <= end, "transport_timeout")
        return rc, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except subprocess.TimeoutExpired:
        raise IsolationError("transport_timeout") from None
    finally:
        selector.close()
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=1)
        for stream in (child.stdin, child.stdout, child.stderr):
            if not stream.closed:
                stream.close()

# Oracle receives fixed, locally hash-verified sources in memory. No Oracle files
# are changed. IBM receives only the reviewed host module through one pinned SSH.
REMOTE_TEMPLATE = r'''import base64,ctypes,fcntl,hashlib,json,math,os,re,selectors,shlex,signal,socket,stat,subprocess,sys,time
knownfd=None
class Abort(Exception):pass
def need(x):
 if not x:raise Abort()
def out(event,**fields):
 print(json.dumps(dict(event=event,**fields),separators=(',',':')),flush=True)
def expired(sig,frame):raise Abort()
def run(argv,payload=b'',seconds=3,limit=32768):
 parent=os.getpid()
 def setup():
  if ctypes.CDLL(None).prctl(1,signal.SIGKILL)!=0 or os.getppid()!=parent:os._exit(125)
 sel=selectors.DefaultSelector()
 try:p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,preexec_fn=setup,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
 except BaseException:sel.close();raise
 buf={'out':bytearray(),'err':bytearray()};pos=0;end=min(deadline,time.monotonic()+seconds)
 try:
  for f,label in ((p.stdout,'out'),(p.stderr,'err')):os.set_blocking(f.fileno(),False);sel.register(f,selectors.EVENT_READ,label)
  if payload:os.set_blocking(p.stdin.fileno(),False);sel.register(p.stdin,selectors.EVENT_WRITE,'in')
  else:p.stdin.close()
  while sel.get_map():
   left=end-time.monotonic();need(left>0);events=sel.select(left);need(events)
   for k,_ in events:
    if k.data=='in':
     try:pos+=os.write(k.fd,payload[pos:pos+4096])
     except BrokenPipeError:pos=len(payload)
     if pos==len(payload):sel.unregister(k.fileobj);k.fileobj.close()
    else:
     b=os.read(k.fd,4096)
     if not b:sel.unregister(k.fileobj);k.fileobj.close()
     else:buf[k.data].extend(b);need(sum(map(len,buf.values()))<=limit)
  while True:
   need(time.monotonic()<end);info=os.waitid(os.P_PID,p.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)
   if info is not None:
    rc=info.si_status if info.si_code==os.CLD_EXITED else -info.si_status;break
   time.sleep(min(.01,max(0,end-time.monotonic())))
  need(time.monotonic()<=end)
  return rc,bytes(buf['out']),bytes(buf['err'])
 finally:
  sel.close()
  try:os.killpg(p.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  p.wait(timeout=1)
  for f in (p.stdin,p.stdout,p.stderr):
   if not f.closed:f.close()
def pairs(items):
 d={}
 for k,v in items:need(k not in d);d[k]=v
 return d
def parse(raw):return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Abort()))
try:
 signal.signal(signal.SIGTERM,expired);parent=os.getppid();need(parent>1 and ctypes.CDLL(None).prctl(1,signal.SIGTERM)==0 and os.getppid()==parent)
 signal.signal(signal.SIGALRM,expired);need(signal.getitimer(signal.ITIMER_REAL)[0]==0)
 began=time.monotonic();deadline=began+28;signal.setitimer(signal.ITIMER_REAL,28)
 raw=sys.stdin.buffer.read(131073);need(len(raw)<=131072);p=parse(raw)
 need(set(p)=={'phase','binding','safe_source','host_source','host_sha','seconds','request'})
 binding=p['binding'];need(set(binding)=={'run_id','attempt','head'} and type(binding['run_id']) is str and re.fullmatch('[1-9][0-9]{0,19}',binding['run_id']) is not None and type(binding['attempt']) is int and binding['attempt']==1 and len(binding['head'])==40 and all(c in '0123456789abcdef' for c in binding['head']))
 need(p['phase'] in ('preflight','isolate') and type(p['seconds']) in (int,float) and math.isfinite(p['seconds']))
 need((p['phase']=='preflight' and p['seconds']==28 and p['request'] is None) or (p['phase']=='isolate' and 105<=p['seconds']<=368))
 deadline=began+p['seconds'];need(deadline>time.monotonic());signal.setitimer(signal.ITIMER_REAL,deadline-time.monotonic())
 need(socket.gethostname()=='autopilot-lite-vnic' and os.geteuid()==1001)
 safe=p['safe_source'].encode();host=p['host_source'].encode()
 need(hashlib.sha256(safe).hexdigest()=='@SAFE_SHA@' and p['host_sha']=='@HOST_SHA@' and hashlib.sha256(host).hexdigest()==p['host_sha'])
 ns={'__name__':'reviewed_readonly_helpers'};exec(compile(safe,'reviewed_readonly_helpers','exec'),ns)
 need(ns['HOST']=='161.156.86.34' and ns['VERIFIED_USER']=='ubuntu')
 m=os.stat(ns['KEY']);need(stat.S_ISREG(m.st_mode) and m.st_uid==1001 and stat.S_IMODE(m.st_mode)==0o600 and os.access(ns['KEY'],os.R_OK))
 rc,raw,err=run(['/usr/bin/ssh-keygen','-F',ns['HOST'],'-f',ns['KNOWN']],seconds=3,limit=8192);need(rc==0)
 records=[r.split() for r in raw.decode().splitlines() if r.strip() and not r.lstrip().startswith('#')];need(records and all(not r[0].startswith('@') for r in records))
 ed=[r for r in records if len(r)>=3 and r[1]=='ssh-ed25519'];need(len(ed)==1)
 fp='SHA256:'+base64.b64encode(hashlib.sha256(base64.b64decode(ed[0][2],validate=True)).digest()).decode().rstrip('=');need(fp==ns['EXPECTED'])
 known=(ns['HOST']+' ssh-ed25519 '+ed[0][2]+'\n').encode();knownfd=os.memfd_create('ibm-maintenance-public-pin',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.fchmod(knownfd,0o600);need(os.write(knownfd,known)==len(known));fcntl.fcntl(knownfd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SEAL)
 if p['phase']=='preflight':out('ISOLATION_ROUTE_READY',**binding,hostpin='MATCH',guest_network_requests=0);sys.exit(0)
 req=p['request'];need(type(req) is dict and req['operation']=='isolate' and all(req[k]==v and type(req[k]) is type(v) for k,v in binding.items()))
 out('RUN_BOUND_UNIT_ISOLATION',**binding)
 # These in-memory maintenance-specific budgets do not modify the read-only source.
 ns['PROBE_SECONDS']=368;ns['SSH_SECONDS']=105;ns['TCP_READINESS_SECONDS']=300;ns['emit']=out
 budget=ns['ProbeBudget'](deadline)
 need(budget.remaining()>=105)
 ready=ns['tcp_ready'](budget)
 if not ready:sys.exit(3)
 need(budget.remaining()>=105)
 argv=['/usr/bin/ssh','-F','/dev/null','-T','-i',ns['KEY'],'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','GlobalKnownHostsFile=/dev/null','-o','VerifyHostKeyDNS=no','-o','CheckHostIP=no','-o','AddKeysToAgent=no','-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','ConnectionAttempts=1','-o','UserKnownHostsFile=/proc/'+str(os.getpid())+'/fd/'+str(knownfd),'-o','HostKeyAlgorithms=ssh-ed25519','-o','ConnectTimeout=8','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1','ubuntu@'+ns['HOST'],'/usr/bin/sudo -n /usr/bin/python3 -I -B -c '+shlex.quote(host.decode())]
 start=time.monotonic();out('ISOLATION_SSH_STARTED',**binding)
 rc,stdout,stderr=run(argv,json.dumps(req,separators=(',',':')).encode(),seconds=105,limit=16384)
 out('ISOLATION_SSH_FINISHED',**binding,ssh_exit=rc,elapsed_seconds=round(time.monotonic()-start,3),stdout_bytes=len(stdout),stderr_bytes=len(stderr),stdout_sha256=hashlib.sha256(stdout).hexdigest(),stderr_sha256=hashlib.sha256(stderr).hexdigest(),raw_output_exported=False)
 if rc not in (0,3):sys.exit(3)
 receipt=parse(stdout);need(type(receipt) is dict)
 need(stdout==(json.dumps(receipt,sort_keys=True,separators=(',',':'))+'\n').encode() and not stderr)
 out('UNIT_ISOLATION_RECEIPT',**binding,receipt=receipt)
 sys.exit(0 if receipt.get('result')=='ISOLATED_TARGET_SCOPE' else 3)
except Exception:
 out('ISOLATION_ROUTE_BLOCKED',reason='bounded_transport_or_preflight_failed',raw_output_exported=False);sys.exit(3)
finally:
 signal.setitimer(signal.ITIMER_REAL,0)
 if knownfd is not None:os.close(knownfd)
'''

def remote_source():
    return REMOTE_TEMPLATE.replace("@HOST_SHA@", HOST_SOURCE_SHA).replace("@SAFE_SHA@", SAFE_SOURCE_SHA)

def context():
    e = os.environ
    need(e.get("GITHUB_REPOSITORY") == read_only.REPOSITORY and e.get("GITHUB_REF") == read_only.REF
         and e.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "run_scope")
    owner = read_only.REPOSITORY.split("/")[0]
    need(e.get("GITHUB_ACTOR") == owner and e.get("GITHUB_TRIGGERING_ACTOR") == owner, "run_owner")
    run_id, head = e.get("GITHUB_RUN_ID", ""), e.get("GITHUB_SHA", "")
    need(re.fullmatch("[1-9][0-9]{0,19}", run_id) is not None and run_id not in CONSUMED_RUNS and e.get("GITHUB_RUN_ATTEMPT") == "1", "run_attempt")
    need(re.fullmatch("[0-9a-f]{40}", head) is not None, "checkout_binding")
    rc, got, _ = bounded_process(["/usr/bin/git", "rev-parse", "HEAD"], timeout=3)
    need(rc == 0 and got.decode().strip() == head, "checkout_binding")
    event = strict_json(Path(e["GITHUB_EVENT_PATH"]).read_bytes())
    inputs = event.get("inputs", {})
    no = lambda value: value is False or value == "false"
    need(inputs.get("mode") == "isolate_units" and no(inputs.get("diagnose_ssh")) and no(inputs.get("test_oracle")), "dispatch_inputs")
    return dict(run_id=run_id, attempt=1, head=head)

def validate_receipt(row, binding):
    keys = {"schema","operation","run_id","head","attempt","result","reason","snapshot_id","snapshot_sha256","changed_units","target_states","host_identity","snapshot_restore_sha256","limitations","changes"}
    need(type(row) is dict and set(row) == keys, "receipt_schema")
    need(row["schema"] == "bridge.ibm.unit-isolation.receipt.v1" and row["operation"] == "isolate", "receipt_schema")
    need(all(type(row[k]) is type(v) and row[k] == v for k,v in binding.items()), "receipt_binding")
    need(type(row["result"]) is str and row["result"] in {"ISOLATED_TARGET_SCOPE","BLOCKED","PARTIAL_BLOCKED"}, "receipt_result")
    need(type(row["reason"]) is str and row["reason"] in host_contract.REASONS, "receipt_reason")
    need(row["host_identity"] == "TRANSPORT_ASSERTION_ONLY", "receipt_identity")
    need(type(row["limitations"]) is list and row["limitations"] == host_contract.LIMITATIONS, "receipt_limitations")
    need(row["snapshot_restore_sha256"] is None or (type(row["snapshot_restore_sha256"]) is str and re.fullmatch("[0-9a-f]{64}",row["snapshot_restore_sha256"])), "receipt_restore")
    need(type(row["changes"]) is list and len(row["changes"])<=4, "receipt_changes")
    change_seen=set()
    for change in row["changes"]:
        need(type(change) is dict and set(change)=={"unit","gate","stop"},"receipt_changes")
        need(type(change["unit"]) is str and change["unit"] in TARGETS and change["unit"] not in change_seen,"receipt_changes")
        change_seen.add(change["unit"])
        need(type(change["gate"]) is str and change["gate"] in {"INTENDED","CREATED","VERIFIED","REMOVED"} and type(change["stop"]) is str and change["stop"] in {"NOT_REQUESTED","REQUESTED","OBSERVED_INACTIVE"},"receipt_changes")
    need(row["snapshot_id"] is None or (type(row["snapshot_id"]) is str and re.fullmatch("[a-zA-Z0-9_-]{1,100}",row["snapshot_id"])), "receipt_snapshot")
    need(row["snapshot_id"] in (None,binding["run_id"]+"-"+binding["head"][:12]), "receipt_snapshot_binding")
    need(row["snapshot_sha256"] is None or (type(row["snapshot_sha256"]) is str and re.fullmatch("[0-9a-f]{64}",row["snapshot_sha256"])), "receipt_snapshot")
    need(type(row["changed_units"]) is list and len(row["changed_units"]) <= 4 and all(type(n) is str and n in TARGETS for n in row["changed_units"]) and len(set(row["changed_units"])) == len(row["changed_units"]), "receipt_changes")
    need(change_seen==set(row["changed_units"]),"receipt_changes")
    need(type(row["target_states"]) is list and len(row["target_states"]) <= 4, "receipt_states")
    seen = set()
    for state in row["target_states"]:
        need(type(state) is dict and set(state) == {"unit","active","sub","job_pending","cgroup_empty"}, "receipt_state")
        need(type(state["unit"]) is str and state["unit"] in TARGETS and state["unit"] not in seen, "receipt_state")
        seen.add(state["unit"])
        need(type(state["active"]) is str and state["active"] in host_contract.SAFE_STATES|{"unknown"} and type(state["sub"]) is str and state["sub"] in host_contract.SAFE_SUB|{"unknown"}, "receipt_state")
        need((state["job_pending"] is None or type(state["job_pending"]) is bool) and (state["cgroup_empty"] is None or type(state["cgroup_empty"]) is bool), "receipt_state")
    if row["result"] == "ISOLATED_TARGET_SCOPE":
        need(row["reason"] == "OK" and row["snapshot_restore_sha256"] is not None and row["snapshot_restore_sha256"]==row["snapshot_sha256"] and change_seen==set(TARGETS) and all(c["gate"]=="VERIFIED" and c["stop"]=="OBSERVED_INACTIVE" for c in row["changes"]), "receipt_incomplete")
        need(row["snapshot_id"] is not None and row["snapshot_sha256"] is not None and set(row["changed_units"]) == set(TARGETS) and seen == set(TARGETS), "receipt_incomplete")
        need(all(s["active"] == "inactive" and s["sub"] == "dead" and s["job_pending"] is False and s["cgroup_empty"] is True for s in row["target_states"]), "receipt_not_isolated")
    return row

class PreparedIsolation:
    kind = "four_unit_isolation"
    def __init__(self, binding, safe_source, host_source, keyfd, knownfd):
        self.binding, self.safe_source, self.host_source = binding, safe_source, host_source
        self.keyfd, self.knownfd = keyfd, knownfd
        self.ready, self.attempted, self.identity_bound = False, False, False
    def close(self):
        for name in ("keyfd","knownfd"):
            fd = getattr(self,name)
            if fd is not None:
                os.close(fd);setattr(self,name,None)
    def bind_identity(self, identity):
        need(type(identity) is dict and identity == IDENTITY, "fresh_api_identity_required")
        self.identity_bound = True
    def call(self, phase, timeout):
        need(self.keyfd is not None and self.knownfd is not None, "transport_closed")
        parent = str(os.getpid())
        argv = ["/usr/bin/ssh","-F","/dev/null","-T","-i","/proc/"+parent+"/fd/"+str(self.keyfd),
                "-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=yes",
                "-o","HostKeyAlgorithms=ssh-ed25519","-o","GlobalKnownHostsFile=/dev/null",
                "-o","UserKnownHostsFile=/proc/"+parent+"/fd/"+str(self.knownfd),
                "-o","UpdateHostKeys=no","-o","VerifyHostKeyDNS=no","-o","CheckHostIP=no",
                "-o","AddKeysToAgent=no","-o","ForwardAgent=no","-o","ClearAllForwardings=yes",
                "-o","ConnectionAttempts=1","-o","ConnectTimeout=3","-o","ServerAliveInterval=5","-o","ServerAliveCountMax=1",
                "ubuntu@"+read_only.ORACLE,"/usr/bin/python3 -I -B -c "+shlex.quote(remote_source())]
        request = None
        seconds = 28 if phase == "preflight" else timeout - 5
        need(phase in {"preflight","isolate"} and ((phase == "preflight" and timeout == 12) or (105 <= seconds <= REMOTE_SECONDS)), "remote_budget")
        if phase == "isolate":
            need(self.identity_bound, "fresh_api_identity_required")
            request = dict(schema="bridge.ibm.unit-isolation.request.v1",operation="isolate",**self.binding,identity=IDENTITY,transport=TRANSPORT)
        payload = json.dumps(dict(phase=phase,binding=self.binding,safe_source=self.safe_source,host_source=self.host_source,host_sha=HOST_SOURCE_SHA,seconds=seconds,request=request)).encode()
        return bounded_process(argv,payload=payload,timeout=timeout)
    def preflight(self):
        rc,raw,err = self.call("preflight",12)
        need(rc == 0 and len(raw) <= 2048 and not err,"oracle_preflight_failed")
        expected = dict(event="ISOLATION_ROUTE_READY",**self.binding,hostpin="MATCH",guest_network_requests=0)
        need(exact(strict_json(raw),expected),"oracle_preflight_schema")
        self.ready = True
        emit("ISOLATION_ROUTE_PREPARED",**self.binding,guest_network_requests=0)
    def run_once(self, *, max_seconds=MAINTENANCE_SECONDS):
        need(self.ready and not self.attempted and self.identity_bound,"not_ready_or_replay")
        need(type(max_seconds) in (int,float) and math.isfinite(max_seconds) and MIN_MAINTENANCE_SECONDS <= max_seconds <= MAINTENANCE_SECONDS,"maintenance_budget")
        self.attempted = True
        rc,raw,err = self.call("isolate",max_seconds-5)
        need(type(rc) is int and type(raw) is bytes and type(err) is bytes and len(raw)+len(err)<=OUTPUT_BYTES,"transport_response")
        emit("UNIT_ISOLATION_CAPTURED",**self.binding,remote_exit=rc,stdout_bytes=len(raw),stdout_sha256=hashlib.sha256(raw).hexdigest(),stderr_bytes=len(err),stderr_sha256=hashlib.sha256(err).hexdigest(),raw_output_exported=False)
        lines=raw.splitlines();need(0<len(lines)<=12 and all(0<len(line)<=16384 for line in lines),"response_limit")
        rows=[strict_json(line) for line in lines]
        need(all(type(row) is dict and type(row.get("event")) is str for row in rows),"response_schema")
        events=[row['event'] for row in rows]
        allowed={"RUN_BOUND_UNIT_ISOLATION","TCP_OPEN","TCP_REFUSED","TCP_TIMEOUT","TCP_CONNECT_FAILURE","NETWORK_UNREACHABLE","TCP_READINESS_EXHAUSTED","ISOLATION_SSH_STARTED","ISOLATION_SSH_FINISHED","UNIT_ISOLATION_RECEIPT","ISOLATION_ROUTE_BLOCKED"}
        need(set(events)<=allowed,"response_events")
        bound=rows[0]
        need(exact(bound,dict(event="RUN_BOUND_UNIT_ISOLATION",**self.binding)),"response_binding")
        chain=["RUN_BOUND_UNIT_ISOLATION","TCP_OPEN","ISOLATION_SSH_STARTED","ISOLATION_SSH_FINISHED","UNIT_ISOLATION_RECEIPT"]
        if events != chain:
            return False
        tcp=rows[1]
        need(set(tcp)=={"event","elapsed_seconds","attempts"} and type(tcp['attempts']) is int and 1<=tcp['attempts']<=301 and type(tcp['elapsed_seconds']) in (int,float) and math.isfinite(tcp['elapsed_seconds']) and 0<=tcp['elapsed_seconds']<=300,"tcp_receipt")
        need(exact(rows[2],dict(event="ISOLATION_SSH_STARTED",**self.binding)),"ssh_binding")
        finished=rows[3]
        need(set(finished)==set(self.binding)|{"event","ssh_exit","elapsed_seconds","stdout_bytes","stderr_bytes","stdout_sha256","stderr_sha256","raw_output_exported"} and all(type(finished[k]) is type(v) and finished[k]==v for k,v in self.binding.items()),"ssh_receipt")
        need(type(finished['ssh_exit']) is int and finished['ssh_exit'] in (0,3) and finished['raw_output_exported'] is False and type(finished['elapsed_seconds']) in (int,float) and math.isfinite(finished['elapsed_seconds']) and 0<=finished['elapsed_seconds']<=SSH_SECONDS,"ssh_receipt")
        for key in ('stdout_bytes','stderr_bytes'):need(type(finished[key]) is int and 0<=finished[key]<=16384,"ssh_receipt")
        for key in ('stdout_sha256','stderr_sha256'):need(type(finished[key]) is str and re.fullmatch('[0-9a-f]{64}',finished[key]),"ssh_receipt")
        holder=rows[4];need(set(holder)==set(self.binding)|{'event','receipt'} and all(type(holder[k]) is type(v) and holder[k]==v for k,v in self.binding.items()),"receipt_binding")
        receipt=validate_receipt(holder['receipt'],self.binding)
        canonical=(json.dumps(receipt,sort_keys=True,separators=(",",":"))+"\n").encode()
        need(finished["stdout_bytes"]==len(canonical) and finished["stdout_sha256"]==hashlib.sha256(canonical).hexdigest() and finished["stderr_bytes"]==0 and finished["stderr_sha256"]==hashlib.sha256(b"").hexdigest(),"receipt_capture_binding")
        emit("UNIT_ISOLATION_VERIFIED_RECEIPT",**self.binding,receipt=receipt)
        return rc==0 and finished['ssh_exit']==0 and receipt['result']=='ISOLATED_TARGET_SCOPE'

def prepare():
    binding=context()
    source=Path(__file__).with_name("ibm_ssh_probe_safe.py").read_text()
    host=Path(__file__).with_name("ibm_unit_isolation.py").read_text()
    need(hashlib.sha256(source.encode()).hexdigest()==SAFE_SOURCE_SHA and hashlib.sha256(host.encode()).hexdigest()==HOST_SOURCE_SHA,"source_pin")
    raw=bytearray(os.environ.pop("ORACLE_SSH_PRIVATE_KEY","").replace("\r","").encode())
    need(32<len(raw)<32768,"existing_owner_key_missing")
    keyfd=knownfd=None
    try:
        keyfd=read_only.memory(raw,"existing-oracle-owner-key")
        for i in range(len(raw)):raw[i]=0
        rc,scanned,_=bounded_process(["/usr/bin/ssh-keyscan","-T","3","-t","ed25519",read_only.ORACLE],timeout=4,limit=8192)
        records=[row.split() for row in scanned.decode().splitlines() if row and not row.startswith('#')]
        need(rc==0 and len(records)==1 and records[0][:2]==[read_only.ORACLE,"ssh-ed25519"],"oracle_public_trust")
        fp="SHA256:"+base64.b64encode(hashlib.sha256(base64.b64decode(records[0][2],validate=True)).digest()).decode().rstrip('=')
        need(fp==read_only.ORACLE_FP,"oracle_hostpin")
        knownfd=read_only.memory(scanned,"oracle-public-trust")
        probe=PreparedIsolation(binding,source,host,keyfd,knownfd)
        probe.preflight()
        return probe
    except BaseException:
        for fd in (knownfd,keyfd):
            if fd is not None:os.close(fd)
        raise
