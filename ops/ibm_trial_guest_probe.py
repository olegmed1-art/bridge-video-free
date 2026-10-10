"""Source-only GitHub -> existing Oracle -> IBM ubuntu diagnostic. No power actions."""
import base64
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import socket
import stat
import subprocess
import time

try:
    from .ibm_ssh_probe_safe import validate_host_observation
except ImportError:
    from ibm_ssh_probe_safe import validate_host_observation

REPOSITORY = "olegmed1-art/bridge-video-free"
REF = "refs/heads/review/ibm-trial-control-20261001"
ORACLE = "92.5.47.149"
ORACLE_FP = "SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go"
GUEST_SOURCE_SHA = "7711357b7929dcf42d8e49739b16f2e4489191644a0bfb711259f57f182e3bee"
PREPARE_SECONDS = 20
DIAGNOSTIC_SECONDS = 338
MIN_DIAGNOSTIC_SECONDS = 38
REMOTE_SECONDS = 328
TCP_READINESS_SECONDS = 300
TCP_MAX_ATTEMPTS = 301
CONSUMED_RUNS = {"37776596059", "37788143504"}

class ProbeError(Exception):
    pass

def need(ok, code):
    if not ok:
        raise ProbeError(code)

def emit(event, **fields):
    print(json.dumps(dict(event=event, at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields), sort_keys=True), flush=True)

def bounded_process(argv, *, payload=b"", timeout=10, limit=32768):
    """Bound stdin, both outputs, wall time and cleanup; never print process errors."""
    need(len(payload) <= 32768, "probe_input_limit")
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True,
                             env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","LC_ALL":"C.UTF-8"})
    selector = selectors.DefaultSelector()
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
            need(left > 0, "probe_wall_timeout")
            events = selector.select(left)
            need(bool(events), "probe_wall_timeout")
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
                        need(sum(map(len, buffers.values())) <= limit, "probe_output_limit")
        rc = child.wait(timeout=max(.001, end-time.monotonic()))
        return rc, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    except subprocess.TimeoutExpired:
        raise ProbeError("probe_wall_timeout") from None
    finally:
        selector.close()
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=1)
        for stream in (child.stdin, child.stdout, child.stderr):
            if not stream.closed:
                stream.close()

def memory(raw, name):
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd, 0o600)
        done = 0
        while done < len(raw):
            count = os.write(fd, raw[done:])
            need(count > 0, "probe_memory_write")
            done += count
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:
        os.close(fd)
        raise

REMOTE = r"""import base64,ctypes,datetime,fcntl,hashlib,json,math,os,signal,socket,stat,subprocess,sys,time,types
knownfd=None
timer_owned=False
class RemoteAbort(Exception):
 pass
def out(event,**fields):
 print(json.dumps(dict(event=event,at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),**fields),sort_keys=True),flush=True)
def expired(signum,frame):
 raise RemoteAbort()
def select_known_record(text):
 records=[l.split() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
 assert records and all(not row[0].startswith("@") for row in records)
 ed=[row for row in records if len(row)>=3 and row[1]=="ssh-ed25519"]
 assert len(ed)==1
 return ed[0]
def bounded_child(argv,**kwargs):
 parent=os.getpid()
 boot="import ctypes,os,signal;parent="+repr(parent)+";assert ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0 and os.getppid()==parent;argv="+repr(argv)+";os.execv(argv[0],argv)"
 return subprocess.run([sys.executable,"-I","-B","-c",boot],**kwargs)
try:
 assert hasattr(signal,"setitimer") and signal.getitimer(signal.ITIMER_REAL)[0]==0
 signal.signal(signal.SIGALRM,expired)
 remote_started=time.monotonic()
 remote_deadline=remote_started+28
 signal.setitimer(signal.ITIMER_REAL,28)
 timer_owned=True
 parent=os.getppid()
 assert ctypes.CDLL(None).prctl(1,signal.SIGTERM)==0 and os.getppid()==parent
 signal.signal(signal.SIGTERM,expired)
 p=json.load(sys.stdin)
 assert set(p)=={"phase","run_id","attempt","head","source","source_sha","probe_seconds"}
 assert p["phase"] in ("preflight","probe") and p["attempt"]==1 and p["run_id"].isdigit()
 duration=p["probe_seconds"]
 assert type(duration) in (int,float) and math.isfinite(duration) and 28<=duration<=328
 assert p["phase"]!="preflight" or duration==28
 remote_deadline=remote_started+duration
 remaining=remote_deadline-time.monotonic()
 assert 0<remaining<=328
 signal.setitimer(signal.ITIMER_REAL,remaining)
 assert len(p["head"])==40 and all(c in "0123456789abcdef" for c in p["head"])
 assert socket.gethostname()=="autopilot-lite-vnic" and os.geteuid()==1001
 b=p["source"].encode()
 assert p["source_sha"]=="7711357b7929dcf42d8e49739b16f2e4489191644a0bfb711259f57f182e3bee" and hashlib.sha256(b).hexdigest()==p["source_sha"]
 ns={"__name__":"reviewed_probe"}
 exec(compile(b,"reviewed_ibm_ssh_probe.py","exec"),ns)
 assert ns["VERIFIED_USER"]=="ubuntu" and ns["HOST"]=="161.156.86.34"
 m=os.stat(ns["KEY"])
 assert stat.S_ISREG(m.st_mode) and m.st_uid==1001 and stat.S_IMODE(m.st_mode)==0o600 and os.access(ns["KEY"],os.R_OK)
 found=bounded_child(["/usr/bin/ssh-keygen","-F",ns["HOST"],"-f",ns["KNOWN"]],capture_output=True,text=True,timeout=3)
 assert found.returncode==0
 rec=select_known_record(found.stdout)
 blob=base64.b64decode(rec[2],validate=True)
 fp="SHA256:"+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
 assert fp==ns["EXPECTED"]
 snapshot=(ns["HOST"]+" ssh-ed25519 "+rec[2]+"\n").encode()
 knownfd=os.memfd_create("ibm-public-hostpin",os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
 os.fchmod(knownfd,0o600)
 assert os.write(knownfd,snapshot)==len(snapshot)
 fcntl.fcntl(knownfd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SEAL)
 ns["KNOWN"]="/proc/"+str(os.getpid())+"/fd/"+str(knownfd)
 ns["subprocess"]=types.SimpleNamespace(run=bounded_child,TimeoutExpired=subprocess.TimeoutExpired,SubprocessError=subprocess.SubprocessError)
 if p["phase"]=="preflight":
  out("ORACLE_ROUTE_READY",run_id=p["run_id"],head=p["head"],attempt=1,user="ubuntu",hostpin="MATCH",guest_network_requests=0,key_contents_read=False)
  sys.exit(0)
 out("RUN_BOUND_GUEST_DIAGNOSTIC",run_id=p["run_id"],head=p["head"],attempt=1)
 sys.exit(ns["probe"]("ubuntu",deadline=remote_deadline))
except Exception as exc:
 out("ORACLE_ROUTE_BLOCKED",error_type=type(exc).__name__,raw_error_exported=False)
 sys.exit(3)
finally:
 if timer_owned:
  signal.setitimer(signal.ITIMER_REAL,0)
 if knownfd is not None:
  os.close(knownfd)
"""

def context():
    e = os.environ
    need(e.get("GITHUB_REPOSITORY") == REPOSITORY and e.get("GITHUB_REF") == REF
         and e.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "probe_run_scope")
    owner = REPOSITORY.split("/")[0]
    need(e.get("GITHUB_ACTOR") == owner and e.get("GITHUB_TRIGGERING_ACTOR") == owner, "probe_owner")
    run_id, head = e.get("GITHUB_RUN_ID", ""), e.get("GITHUB_SHA", "")
    need(run_id.isdigit() and run_id not in CONSUMED_RUNS and e.get("GITHUB_RUN_ATTEMPT") == "1", "probe_run_attempt")
    need(re.fullmatch("[0-9a-f]{40}", head) is not None, "probe_checkout")
    rc, got, _ = bounded_process(["/usr/bin/git", "rev-parse", "HEAD"], timeout=3)
    need(rc == 0 and got.decode().strip() == head, "probe_checkout")
    event = json.loads(Path(e["GITHUB_EVENT_PATH"]).read_text())
    inp = event.get("inputs", {})
    yes = lambda x: x is True or x == "true"
    no = lambda x: x is False or x == "false"
    need(inp.get("mode") == "trial_start" and yes(inp.get("diagnose_ssh"))
         and no(inp.get("test_oracle")), "probe_dispatch_inputs")
    return dict(run_id=run_id, attempt=1, head=head)

# These limits are parser bounds, not additional probe time or retry budgets.
RESPONSE_BYTES = 32768
RESPONSE_ROWS = 16
RESPONSE_LINE_BYTES = 4096
TCP_FAILURES = {"TCP_REFUSED", "TCP_TIMEOUT", "NETWORK_UNREACHABLE", "TCP_CONNECT_FAILURE"}
SSH_CLASSES = {"HOSTKEY_FAILURE", "LOCAL_KEY_FAILURE", "AUTH_FAILURE", "TCP_REFUSED",
               "SSH_BANNER_TIMEOUT", "TCP_TIMEOUT", "NETWORK_UNREACHABLE",
               "CONNECTION_CLOSED", "SSH_NEGOTIATION_FAILURE", "UNCLASSIFIED"}
RESPONSE_EVENTS = TCP_FAILURES | {
    "RUN_BOUND_GUEST_DIAGNOSTIC", "TCP_PROBE_STARTED", "TCP_OPEN", "TCP_READINESS_EXHAUSTED",
    "SSH_PROBE_STARTED", "SSH_PROBE_FINISHED", "SSH_WALL_TIMEOUT", "SSH_AUTHENTICATED",
    "SSH_NOT_CONFIRMED", "PRECHECK_BLOCKED", "LOCAL_PRECHECK_FAILED", "ORACLE_ROUTE_BLOCKED",
    "HOST_OBSERVER_RESULT", "HOST_OBSERVER_BLOCKED",
}


def response_rows(raw):
    """Parse bounded, untrusted JSON lines; never include input in parser errors."""
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "probe_response_schema")
            result[key] = value
        return result

    def constant(_value):
        raise ProbeError("probe_response_schema")

    need(type(raw) is bytes and len(raw) <= RESPONSE_BYTES, "probe_response_limit")
    lines = raw.splitlines()
    need(0 < len(lines) <= RESPONSE_ROWS and all(0 < len(line) <= RESPONSE_LINE_BYTES for line in lines),
         "probe_response_limit")
    try:
        rows = [json.loads(line.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
                for line in lines]
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeError("probe_response_schema") from None
    need(all(type(row) is dict and len(row) <= 20 and type(row.get("event")) is str
             and row["event"] in RESPONSE_EVENTS for row in rows), "probe_response_schema")
    return rows


def diagnostic_receipts(rows, *, remote_seconds=REMOTE_SECONDS):
    """Validate all evidence before emitting any derived success or failure receipt.

    Only fixed labels, bounded numbers and hashes cross this boundary. In particular,
    remote timestamps, exception strings and even previously 'safe' stderr lines do not.
    Durations are the remote monotonic durations, not reconstructed wall-clock timing.
    """
    need(type(remote_seconds) in (int, float) and math.isfinite(remote_seconds)
         and 28 <= remote_seconds <= REMOTE_SECONDS, "probe_budget")

    def elapsed(row):
        value = row.get("elapsed_seconds")
        need(type(value) in (int, float) and 0 <= value <= remote_seconds, "probe_response_schema")
        return value

    def classes(row):
        value = row.get("error_classes")
        need(type(value) is list and len(value) <= 1
             and all(type(x) is str and x in SSH_CLASSES for x in value), "probe_response_schema")
        return value

    def digest(row):
        value = row.get("stderr_sha256")
        need(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None,
             "probe_response_schema")
        return value

    events = [r["event"] for r in rows]
    need(events.count("RUN_BOUND_GUEST_DIAGNOSTIC") == 1, "probe_response_schema")
    # One probe, one TCP terminal, and one SSH terminal: duplicates are not evidence.
    terminals = [r for r in rows if r["event"] in {"TCP_OPEN", "TCP_READINESS_EXHAUSTED"}]
    failures = [r for r in rows if r["event"] in TCP_FAILURES]
    ssh = [r for r in rows if r["event"] in {"SSH_PROBE_FINISHED", "SSH_WALL_TIMEOUT"}]
    need(len(terminals) <= 1 and len(failures) <= 1 and len(ssh) <= 1
         and events.count("TCP_PROBE_STARTED") <= 1 and events.count("SSH_PROBE_STARTED") <= 1,
         "probe_response_schema")
    # Preserve the remote sequence; never turn reordered input into plausible evidence.
    tcp_positions = [i for i, event in enumerate(events)
                     if event in TCP_FAILURES | {"TCP_OPEN", "TCP_READINESS_EXHAUSTED"}]
    ssh_positions = [i for i, event in enumerate(events)
                     if event in {"SSH_PROBE_STARTED", "SSH_PROBE_FINISHED", "SSH_WALL_TIMEOUT",
                                  "SSH_AUTHENTICATED", "SSH_NOT_CONFIRMED"}]
    if "TCP_PROBE_STARTED" in events:
        need(all(events.index("TCP_PROBE_STARTED") < i for i in tcp_positions + ssh_positions),
             "probe_response_schema")
    if terminals and ssh_positions:
        need(events.index(terminals[0]["event"]) < min(ssh_positions), "probe_response_schema")
    if "SSH_PROBE_STARTED" in events and ssh:
        need(events.index("SSH_PROBE_STARTED") < events.index(ssh[0]["event"]), "probe_response_schema")
    receipts = []
    host_receipts = []
    need(events.count("HOST_OBSERVER_RESULT") <= 1 and events.count("HOST_OBSERVER_BLOCKED") <= 1, "probe_response_schema")
    for row in rows:
        if row["event"]=="HOST_OBSERVER_RESULT":
            try:observation=validate_host_observation(row.get("observation"))
            except (ValueError,TypeError):raise ProbeError("probe_response_schema") from None
            need("SSH_PROBE_FINISHED" in events and events.index("SSH_PROBE_FINISHED")<events.index("HOST_OBSERVER_RESULT"), "probe_response_schema")
            host_receipts.append(("GUEST_HOST_OBSERVER_RESULT",dict(stage="HOST_OBSERVER",observation=observation)))
        elif row["event"]=="HOST_OBSERVER_BLOCKED":
            need(type(row.get("result_class")) is str and row["result_class"] in {"UNKNOWN","ACTIVE_OR_TRANSITION","INVALID_OR_INCOMPLETE"}, "probe_response_schema")
            host_receipts.append(("GUEST_HOST_OBSERVER_BLOCKED",dict(stage="HOST_OBSERVER",result_class=row["result_class"])))
    if terminals:
        row = terminals[0]
        exhausted = row["event"] == "TCP_READINESS_EXHAUSTED"
        attempts = row.get("attempts")
        need(type(attempts) is int and (0 if exhausted else 1) <= attempts <= TCP_MAX_ATTEMPTS,
             "probe_response_schema")
        duration = elapsed(row)
        if exhausted:
            need(len(failures) == 1 and events.index(failures[0]["event"]) < events.index(row["event"])
                 and not ssh and "SSH_PROBE_STARTED" not in events and "SSH_AUTHENTICATED" not in events,
                 "probe_response_schema")
            need(elapsed(failures[0]) <= duration, "probe_response_schema")
            result = failures[0]["event"]
            need(attempts > 0 or result == "TCP_TIMEOUT", "probe_response_schema")
            retry_exhausted = attempts > 0 and result in {"TCP_REFUSED", "TCP_TIMEOUT"}
            termination = ("NO_TCP_ATTEMPT" if attempts == 0 else
                           "RETRY_EXHAUSTED" if retry_exhausted else "NON_RETRYABLE_FAILURE")
        else:
            need(not failures and duration <= min(TCP_READINESS_SECONDS, remote_seconds - 15), "probe_response_schema")
            result, retry_exhausted, termination = "TCP_OPEN", False, "READY"
        receipts.append(("GUEST_TCP_READINESS_RESULT", dict(
            stage="TCP_READINESS", attempts=attempts, elapsed_seconds=duration,
            result_class=result, readiness_exhausted=exhausted,
            retry_exhausted=retry_exhausted, termination_class=termination)))
    for row in rows:
        event = row["event"]
        if event in TCP_FAILURES:
            elapsed(row)  # Even a partial failure record must have valid relative timing.
        if event in {"SSH_PROBE_FINISHED", "SSH_WALL_TIMEOUT"}:
            # A timed-out subprocess may need a bounded cleanup scheduling margin.
            need(elapsed(row) <= (16 if event == "SSH_WALL_TIMEOUT" else 15), "probe_response_schema")
            fields = dict(stage="SSH", result_class=event, error_classes=classes(row),
                          elapsed_seconds=elapsed(row), stderr_sha256=digest(row),
                          raw_output_exported=False)
            if event == "SSH_PROBE_FINISHED":
                need(type(row.get("ssh_exit")) is int and 0 <= row["ssh_exit"] <= 255,
                     "probe_response_schema")
                fields["ssh_exit"] = row["ssh_exit"]
            receipts.append(("GUEST_SSH_RESULT", fields))
        if event in TCP_FAILURES | {"PRECHECK_BLOCKED", "LOCAL_PRECHECK_FAILED", "SSH_WALL_TIMEOUT", "ORACLE_ROUTE_BLOCKED"}:
            stage = ("TCP_READINESS" if event in TCP_FAILURES else
                     "SSH" if event == "SSH_WALL_TIMEOUT" else "PRECHECK_OR_ROUTE")
            fields = dict(stage=stage, failure_class=event)
            if event in TCP_FAILURES:
                fields["elapsed_seconds"] = elapsed(row)
            receipts.append(("GUEST_DIAGNOSTIC_BLOCKED", fields))
    return receipts + host_receipts


class PreparedProbe:
    def __init__(self, binding, source, keyfd, knownfd):
        self.binding, self.source = binding, source
        self.keyfd, self.knownfd = keyfd, knownfd
        self.attempted, self.ready = False, False

    def close(self):
        for name in ("keyfd", "knownfd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def call(self, phase, timeout):
        need(self.keyfd is not None and self.knownfd is not None, "probe_closed")
        parent = str(os.getpid())
        argv = ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i", "/proc/"+parent+"/fd/"+str(self.keyfd),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "UserKnownHostsFile=/proc/"+parent+"/fd/"+str(self.knownfd),
                "-o", "UpdateHostKeys=no", "-o", "VerifyHostKeyDNS=no", "-o", "CheckHostIP=no",
                "-o", "AddKeysToAgent=no", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
                "-o", "ConnectionAttempts=1", "-o", "ConnectTimeout=3",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
                "ubuntu@"+ORACLE, "/usr/bin/python3 -I -B -c "+shlex.quote(REMOTE)]
        need(phase in {"preflight", "probe"}, "probe_phase")
        duration = 28 if phase == "preflight" else timeout - 5
        need(type(duration) in (int, float) and math.isfinite(duration)
             and 28 <= duration <= REMOTE_SECONDS, "probe_budget")
        payload = json.dumps(dict(self.binding, phase=phase, source=self.source,
                                  source_sha=GUEST_SOURCE_SHA, probe_seconds=duration)).encode()
        return bounded_process(argv, payload=payload, timeout=timeout)

    def preflight(self):
        rc, raw, err = self.call("preflight", 12)
        text = err.decode("utf-8", errors="replace")
        rules = (("HOSTKEY_FAILURE", r"Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED"),
                 ("AUTH_FAILURE", r"Permission denied \(publickey"),
                 ("LOCAL_KEY_FAILURE", r"Load key|bad permissions|UNPROTECTED PRIVATE KEY FILE"),
                 ("TCP_REFUSED", r"Connection refused"),
                 ("TCP_TIMEOUT", r"Connection timed out"),
                 ("NETWORK_UNREACHABLE", r"No route to host|Network is unreachable"))
        label = next((name for name, pattern in rules if re.search(pattern, text)), "UNCLASSIFIED") if text else "NONE"
        emit("ORACLE_PREFLIGHT_RESULT", **self.binding, ssh_exit=rc, stderr_class=label,
             stdout_bytes=len(raw), stdout_sha256=hashlib.sha256(raw).hexdigest(),
             stderr_bytes=len(err), stderr_sha256=hashlib.sha256(err).hexdigest(), raw_output_exported=False)
        need(rc == 0 and len(raw) <= 2048, "probe_preflight_refused")
        row = json.loads(raw)
        expected = dict(self.binding, event="ORACLE_ROUTE_READY", user="ubuntu", hostpin="MATCH", guest_network_requests=0, key_contents_read=False)
        need(set(row) == set(expected) | {"at_utc"} and all(type(row[k]) is type(v) and row[k] == v for k,v in expected.items()), "probe_preflight_schema")
        self.ready = True
        emit("GUEST_ROUTE_PREPARED", **self.binding, guest_network_requests=0)

    def run_once(self, *, max_seconds=DIAGNOSTIC_SECONDS):
        need(self.ready and not self.attempted, "probe_not_ready_or_replay")
        need(type(max_seconds) in (int, float) and math.isfinite(max_seconds)
             and MIN_DIAGNOSTIC_SECONDS <= max_seconds <= DIAGNOSTIC_SECONDS, "probe_budget")
        self.attempted = True  # before any network I/O; failures never retry
        rc, raw, err = self.call("probe", max_seconds - 5)
        need(type(raw) is bytes and type(err) is bytes
             and len(raw) + len(err) <= RESPONSE_BYTES, "probe_response_limit")
        need(type(rc) is int and -255 <= rc <= 255, "probe_response_schema")
        # The remote source is pinned, but arbitrary output is not exported.
        emit("GUEST_PROBE_CAPTURED", **self.binding, remote_exit=rc, stdout_bytes=len(raw),
             stdout_sha256=hashlib.sha256(raw).hexdigest(), stderr_bytes=len(err),
             stderr_sha256=hashlib.sha256(err).hexdigest(), raw_output_exported=False)
        rows = response_rows(raw)
        binding = rows[0]
        need(binding.get("event") == "RUN_BOUND_GUEST_DIAGNOSTIC"
             and all(type(binding.get(k)) is type(v) and binding.get(k) == v
                     for k,v in self.binding.items()), "probe_response_binding")
        receipts = diagnostic_receipts(rows, remote_seconds=max_seconds - 10)
        for event, fields in receipts:
            emit(event, **self.binding, **fields)
        auth = [r for r in rows if r.get("event") == "SSH_AUTHENTICATED"]
        events = [r["event"] for r in rows]
        chain = ["TCP_PROBE_STARTED", "TCP_OPEN", "SSH_PROBE_STARTED", "SSH_PROBE_FINISHED", "HOST_OBSERVER_RESULT", "SSH_AUTHENTICATED"]
        complete = (all(events.count(event) == 1 for event in chain)
                    and [events.index(event) for event in chain] == sorted(events.index(event) for event in chain)
                    and not set(events) & (TCP_FAILURES | {"TCP_READINESS_EXHAUSTED", "SSH_WALL_TIMEOUT",
                         "SSH_NOT_CONFIRMED", "PRECHECK_BLOCKED", "LOCAL_PRECHECK_FAILED", "ORACLE_ROUTE_BLOCKED", "HOST_OBSERVER_BLOCKED"})
                    and next((r.get("ssh_exit") for r in rows if r["event"] == "SSH_PROBE_FINISHED"), None) == 0)
        observed = [validate_host_observation(r["observation"]) for r in rows if r["event"]=="HOST_OBSERVER_RESULT"]
        quiet = len(observed)==1 and observed[0]["result"]=="QUIET_IN_KNOWN_SCOPE"
        if rc == 0 and complete and quiet and len(auth) == 1 and auth[0].get("user") == "ubuntu" and type(auth[0].get("uid")) is int and 0 <= auth[0]["uid"] <= 2147483647 and auth[0]["uid"]==observed[0]["uid"]:
            emit("GUEST_AUTHENTICATED", **self.binding, user="ubuntu", uid=auth[0]["uid"])
            return True
        return False

def prepare():
    binding = context()
    source = Path(__file__).with_name("ibm_ssh_probe_safe.py").read_text()
    need(hashlib.sha256(source.encode()).hexdigest() == GUEST_SOURCE_SHA, "probe_guest_source_pin")
    # Existing secret only; removed from the environment before any child starts.
    raw = bytearray(os.environ.pop("ORACLE_SSH_PRIVATE_KEY", "").replace("\r", "").encode())
    need(32 < len(raw) < 32768, "probe_existing_owner_key_missing")
    keyfd = knownfd = None
    try:
        keyfd = memory(raw, "existing-oracle-owner-key")
        for i in range(len(raw)):
            raw[i] = 0
        rc, scanned, _ = bounded_process(["/usr/bin/ssh-keyscan", "-T", "3", "-t", "ed25519", ORACLE], timeout=4, limit=8192)
        rows = [r.split() for r in scanned.decode().splitlines() if r and not r.startswith("#")]
        need(rc == 0 and len(rows) == 1 and rows[0][:2] == [ORACLE, "ssh-ed25519"], "probe_oracle_public_trust")
        fp = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(rows[0][2], validate=True)).digest()).decode().rstrip("=")
        need(fp == ORACLE_FP, "probe_oracle_hostpin")
        knownfd = memory(scanned, "oracle-public-trust")
        probe = PreparedProbe(binding, source, keyfd, knownfd)
        probe.preflight()
        return probe
    except BaseException:
        for fd in (knownfd, keyfd):
            if fd is not None:
                os.close(fd)
        raise

