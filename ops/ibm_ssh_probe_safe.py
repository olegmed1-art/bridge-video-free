#!/usr/bin/env python3
"""Prepared diagnostic only. No Start/Stop, service changes, credential export or SSH retries.
Network use requires a separately coordinated authorized running window.
"""
import argparse
import datetime
import errno
import hashlib
import json
import math
import os
import re
import shlex
import socket
import stat
import subprocess
import time

HOST = "161.156.86.34"
KEY = "/home/ubuntu/.ssh/ibm_bridge_ed25519"
KNOWN = "/home/ubuntu/.ssh/known_hosts"
EXPECTED = "SHA256:o+7KPm2p4VSZSvFFYzIIzA8iiXfze2hhW9tUM58e00w"
VERIFIED_USER = "ubuntu"
PROBE_SECONDS = 328  # caller may shorten; remote watchdog and controller are stricter
TCP_READINESS_SECONDS = 300
TCP_CONNECT_SECONDS = 3
TCP_POLL_SECONDS = 1
TCP_MAX_ATTEMPTS = 301
SSH_SECONDS = 15


HOST_SCOPE = "KNOWN_UNITS_TIMERS_AND_CONTAINERS_V1"
HOST_UNITS = ('assistant-lab.service', 'assistant-lab-observer.service', 'assistant-lab-control.service', 'assistant-lab-control-bridge.service', 'universal-video.service', 'universal-video-container.service', 'universal-video-maintenance.service', 'dds3-mass@10000.service', 'dds3-mass@30000.service', 'bridge-ben.service', 'universal-video-maintenance.timer', 'bridge-ben-healthcheck.service', 'bridge-ben-healthcheck.timer', 'dds3-healthcheck.service', 'dds3-healthcheck.timer', 'dds3-cert-renew.service', 'dds3-cert-renew.timer', 'docker.service', 'docker.socket')
HOST_CONTAINERS = ('universal-video-container', 'bridge-school-dds3-runtime')
HOST_OBSERVER = r"""import ctypes,json,os,selectors,signal,subprocess,time
UNITS=('assistant-lab.service','assistant-lab-observer.service','assistant-lab-control.service','assistant-lab-control-bridge.service','universal-video.service','universal-video-container.service','universal-video-maintenance.service','dds3-mass@10000.service','dds3-mass@30000.service','bridge-ben.service','universal-video-maintenance.timer','bridge-ben-healthcheck.service','bridge-ben-healthcheck.timer','dds3-healthcheck.service','dds3-healthcheck.timer','dds3-cert-renew.service','dds3-cert-renew.timer','docker.service','docker.socket')
NAMES=('universal-video-container','bridge-school-dds3-runtime')
START=time.monotonic();END=START+7.5
class Blocked(Exception):pass
def aborted(signum,frame):
 signal.setitimer(signal.ITIMER_REAL,0)
 raise Blocked()
def command(args):
 end=min(END,time.monotonic()+2)
 if end<=time.monotonic():raise Blocked()
 parent=os.getpid()
 def child_setup():
  if ctypes.CDLL(None).prctl(1,signal.SIGKILL)!=0 or os.getppid()!=parent:os._exit(125)
 p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,preexec_fn=child_setup,env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'})
 sel=selectors.DefaultSelector();raw=bytearray()
 try:
  os.set_blocking(p.stdout.fileno(),False);sel.register(p.stdout,selectors.EVENT_READ)
  while sel.get_map():
   left=end-time.monotonic()
   if left<=0 or not sel.select(left):raise Blocked()
   part=os.read(p.stdout.fileno(),1024)
   if not part:sel.unregister(p.stdout)
   raw.extend(part)
   if len(raw)>4096:raise Blocked()
  rc=p.wait(timeout=max(.001,end-time.monotonic()))
  if time.monotonic()>end:raise Blocked()
  return rc,raw.decode('ascii')
 finally:
  sel.close()
  try:os.killpg(p.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  p.wait(timeout=.2);p.stdout.close()
result={'scope':'KNOWN_UNITS_TIMERS_AND_CONTAINERS_V1','result':'UNKNOWN','uid':None,'units':[],'docker':'UNKNOWN','containers':[]}
try:
 parent=os.getppid();assert parent>1
 signal.signal(signal.SIGTERM,aborted)
 assert ctypes.CDLL(None).prctl(1,signal.SIGTERM)==0 and os.getppid()==parent
 assert signal.getitimer(signal.ITIMER_REAL)[0]==0
 signal.signal(signal.SIGALRM,aborted)
 signal.setitimer(signal.ITIMER_REAL,7.8)
 rc,text=command(['/usr/bin/id','-u'])
 assert rc==0 and text.strip().isdigit() and len(text.strip())<=10
 result['uid']=int(text.strip());assert 0<=result['uid']<=2147483647
 rc,text=command(['/usr/bin/systemctl','show','--no-pager','--property=Id,LoadState,ActiveState,SubState',*UNITS])
 assert rc in (0,1)
 seen={}
 for block in text.strip().split('\n\n'):
  row={}
  for line in block.splitlines():
   key,value=line.split('=',1);assert key not in row;row[key]=value
  assert set(row)=={'Id','LoadState','ActiveState','SubState'} and row['Id'] in UNITS and row['Id'] not in seen
  seen[row['Id']]=row
 assert set(seen)==set(UNITS)
 active=False;unknown=False
 for name in UNITS:
  row=seen[name];load=row['LoadState'];state=row['ActiveState'];sub=row['SubState']
  assert load in ('loaded','not-found','masked','error','bad-setting','merged')
  assert state in ('active','reloading','inactive','failed','activating','deactivating','maintenance','refreshing')
  if sub not in ('dead','running','exited','failed','waiting','start','start-pre','start-post','stop','stop-sigterm','stop-sigkill','auto-restart','condition','listening'):sub='other'
  result['units'].append({'unit':name,'load':load,'active':state,'sub':sub})
  if name in ('docker.service','docker.socket'):continue
  active |= state in ('active','reloading','activating','deactivating','refreshing')
  unknown |= not(state=='inactive' and sub=='dead' and load in ('loaded','not-found','masked')) and not active
 if active:result['result']='ACTIVE_OR_TRANSITION'
 elif unknown:result['result']='UNKNOWN'
 else:result['result']='QUIET_IN_KNOWN_SCOPE'
 if active:raise Blocked()
 if os.path.lexists('/usr/bin/docker'):
  assert not os.path.lexists('/proc/0/bridge-ibm-observer')
  assert seen['docker.service']['LoadState']=='loaded' and seen['docker.service']['ActiveState']=='active' and seen['docker.service']['SubState']=='running'
  assert seen['docker.socket']['LoadState'] in ('not-found','masked') and seen['docker.socket']['ActiveState']=='inactive' and seen['docker.socket']['SubState']=='dead'
  rc,text=command(['/usr/bin/docker','--config','/proc/0/bridge-ibm-observer','--host','unix:///var/run/docker.sock','container','ls','--all','--format','{{.Names}} {{.State}}','--filter','name=^/universal-video-container$','--filter','name=^/bridge-school-dds3-runtime$'])
  assert rc==0
  details={}
  for line in text.splitlines():
   name,state=line.split()
   assert name in NAMES and name not in details and state in ('created','running','paused','restarting','removing','exited','dead')
   details[name]={'name':name,'status':state}
  result['containers']=[details.get(name,{'name':name,'status':'absent'}) for name in NAMES];result['docker']='OBSERVED'
  # Existing containers cannot be cleared without inspect; never fetch that data.
  if details:result['result']='UNKNOWN'
  if any(row['status'] in ('running','paused','restarting','removing') for row in result['containers']):result['result']='ACTIVE_OR_TRANSITION'
 else:
  assert not os.path.exists('/var/run/docker.sock')
  assert all(seen[name]['LoadState']=='not-found' and seen[name]['ActiveState']=='inactive' and seen[name]['SubState']=='dead' for name in ('docker.service','docker.socket'))
  result['docker']='NOT_INSTALLED'
 assert time.monotonic()<END
except Exception:
 result['result']='UNKNOWN'
finally:
 signal.setitimer(signal.ITIMER_REAL,0)
result['elapsed_seconds']=round(max(0,time.monotonic()-START),3)
print(json.dumps(result,separators=(',',':')),flush=True)
"""

def validate_host_observation(data):
    """Allowlist a bounded snapshot; never infer host-wide inactivity."""
    def need(ok):
        if not ok:raise ValueError("host_observer_schema")
    need(type(data) is dict and set(data)=={"scope","result","uid","units","docker","containers","elapsed_seconds"})
    need(data["scope"]==HOST_SCOPE and data["result"] in {"UNKNOWN","ACTIVE_OR_TRANSITION","QUIET_IN_KNOWN_SCOPE"})
    need(type(data["uid"]) is int and 0<=data["uid"]<=2147483647)
    need(type(data["elapsed_seconds"]) in (int,float) and math.isfinite(data["elapsed_seconds"]) and 0<=data["elapsed_seconds"]<=8)
    need(type(data["units"]) is list and len(data["units"])<=len(HOST_UNITS))
    seen={};active=False;unknown=False
    for row in data["units"]:
        need(type(row) is dict and set(row)=={"unit","load","active","sub"})
        need(type(row["unit"]) is str and row["unit"] in HOST_UNITS and row["unit"] not in seen)
        need(type(row["load"]) is str and row["load"] in {"loaded","not-found","masked","error","bad-setting","merged"})
        need(type(row["active"]) is str and row["active"] in {"active","reloading","inactive","failed","activating","deactivating","maintenance","refreshing"})
        need(type(row["sub"]) is str and row["sub"] in {"dead","running","exited","failed","waiting","start","start-pre","start-post","stop","stop-sigterm","stop-sigkill","auto-restart","condition","listening","other"})
        seen[row["unit"]]=row
        if row["unit"] not in {"docker.service","docker.socket"}:
            active |= row["active"] in {"active","reloading","activating","deactivating","refreshing"}
            unknown |= not(row["active"]=="inactive" and row["sub"]=="dead" and row["load"] in {"loaded","not-found","masked"})
    unknown |= set(seen)!=set(HOST_UNITS)
    need(type(data["docker"]) is str and data["docker"] in {"UNKNOWN","NOT_INSTALLED","OBSERVED"})
    need(type(data["containers"]) is list and len(data["containers"])<=len(HOST_CONTAINERS))
    containers={}
    for row in data["containers"]:
        need(type(row) is dict and type(row.get("name")) is str and row["name"] in HOST_CONTAINERS and row["name"] not in containers)
        need(type(row.get("status")) is str and row["status"] in {"absent","created","running","paused","restarting","removing","exited","dead"})
        need(set(row)=={"name","status"})
        unknown |= row["status"]!="absent"
        active |= row["status"] in {"running","paused","restarting","removing"};containers[row["name"]]=row
    daemon=seen.get("docker.service",{})
    activation=seen.get("docker.socket",{})
    socket_safe=activation.get("load") in {"not-found","masked"} and activation.get("active")=="inactive" and activation.get("sub")=="dead"
    if data["docker"]=="OBSERVED":
        unknown |= not socket_safe or set(containers)!=set(HOST_CONTAINERS) or not(daemon.get("load")=="loaded" and daemon.get("active")=="active" and daemon.get("sub")=="running")
    elif data["docker"]=="NOT_INSTALLED":
        unknown |= bool(containers) or not all(row.get("load")=="not-found" and row.get("active")=="inactive" and row.get("sub")=="dead" for row in (daemon,activation))
    else:unknown=True
    label="ACTIVE_OR_TRANSITION" if active else "UNKNOWN" if unknown else "QUIET_IN_KNOWN_SCOPE"
    # A remote exception/UNKNOWN may not be upgraded by superficially complete data.
    if data["result"]=="ACTIVE_OR_TRANSITION":label="ACTIVE_OR_TRANSITION"
    elif data["result"]=="UNKNOWN" and label=="QUIET_IN_KNOWN_SCOPE":label="UNKNOWN"
    return dict(data,result=label)


def parse_host_observation(text):
    if type(text) is not str or len(text.encode("utf-8"))>4096:raise ValueError("host_observer_limit")
    def unique(items):
        row={}
        for key,value in items:
            if key in row:raise ValueError("host_observer_schema")
            row[key]=value
        return row
    return validate_host_observation(json.loads(text,object_pairs_hook=unique))


class ProbeDeadlineError(Exception):
    pass


class ProbeBudget:
    """One monotonic deadline shared by prechecks, TCP readiness and sole SSH."""
    def __init__(self, deadline):
        self.last = time.monotonic()
        if not math.isfinite(self.last):
            raise ProbeDeadlineError("invalid_monotonic_clock")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ProbeDeadlineError("invalid_probe_deadline")
        self.deadline = min(self.last + PROBE_SECONDS, deadline) if deadline is not None else self.last + PROBE_SECONDS

    def now(self):
        now = time.monotonic()
        if not math.isfinite(now) or now < self.last:
            raise ProbeDeadlineError("monotonic_clock_regressed")
        self.last = now
        if now >= self.deadline:
            raise ProbeDeadlineError("probe_deadline_expired")
        return now

    def remaining(self):
        return self.deadline - self.now()


def tcp_ready(budget):
    """Poll TCP only; reserve the sole SSH attempt and never reset the deadline."""
    started = budget.now()
    end = min(started + TCP_READINESS_SECONDS, budget.deadline - SSH_SECONDS)
    attempts = 0
    failure, error_number = "TCP_TIMEOUT", None
    while attempts < TCP_MAX_ATTEMPTS:
        left = end - budget.now()
        if left <= 0:
            break
        attempts += 1
        try:
            with socket.create_connection((HOST, 22), timeout=min(TCP_CONNECT_SECONDS, left)):
                elapsed = budget.now() - started
            if budget.now() <= end:
                emit("TCP_OPEN", elapsed_seconds=round(elapsed, 3), attempts=attempts)
                return True
            break  # a late success cannot borrow the reserved SSH time
        except OSError as exc:
            error_number = exc.errno
            failure = {errno.ECONNREFUSED: "TCP_REFUSED", errno.ETIMEDOUT: "TCP_TIMEOUT",
                       errno.EHOSTUNREACH: "NETWORK_UNREACHABLE", errno.ENETUNREACH: "NETWORK_UNREACHABLE"}.get(exc.errno)
            failure = failure or ("TCP_TIMEOUT" if isinstance(exc, TimeoutError) else "TCP_CONNECT_FAILURE")
            # Only early-boot refusal/timeout is eligible for another TCP check.
            if failure not in {"TCP_REFUSED", "TCP_TIMEOUT"}:
                break
        left = end - budget.now()
        if left <= 0 or attempts >= TCP_MAX_ATTEMPTS:
            break
        time.sleep(min(TCP_POLL_SECONDS, left))
    emit(failure, errno=error_number, elapsed_seconds=round(budget.now() - started, 3))
    emit("TCP_READINESS_EXHAUSTED", attempts=attempts,
         elapsed_seconds=round(budget.now() - started, 3))
    return False


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit(event, **fields):
    print(json.dumps(dict(event=event, at_utc=utc(), **fields), sort_keys=True), flush=True)


def stderr_info(text, user):
    """Return exact known safe client error lines; preserve a hash of unknown text."""
    rules = (
        ("HOSTKEY_FAILURE", r"Host key verification failed\.|REMOTE HOST IDENTIFICATION HAS CHANGED|No ED25519 host key is known"),
        ("LOCAL_KEY_FAILURE", r"UNPROTECTED PRIVATE KEY FILE|bad permissions|(?:Load key|Identity file).*ibm_bridge_ed25519.*(?:Permission denied|not accessible|invalid format|libcrypto|passphrase)"),
        ("AUTH_FAILURE", r"Permission denied \(publickey[^)]*\)\."),
        ("TCP_REFUSED", r"Connection refused"),
        ("SSH_BANNER_TIMEOUT", r"Connection timed out during banner exchange"),
        ("TCP_TIMEOUT", r"Connection timed out|Connection timeout"),
        ("NETWORK_UNREACHABLE", r"No route to host|Network is unreachable"),
        ("CONNECTION_CLOSED", r"Connection closed|Connection reset|closed by remote host"),
        ("SSH_NEGOTIATION_FAILURE", r"Unable to negotiate|no matching host key type|no matching key exchange method"),
    )
    classes = [next((name for name, pattern in rules if re.search(pattern, text, re.I)), "UNCLASSIFIED")] if text else []
    exact = []
    safe_line = (
        r"ssh: connect to host 161\.156\.86\.34 port 22: (?:Connection refused|Connection timed out|No route to host|Network is unreachable)",
        re.escape(user + "@" + HOST) + r": Permission denied \(publickey(?:,keyboard-interactive)?\)\.",
        r"Host key verification failed\.",
        r"Connection timed out during banner exchange",
        r"Load key \"" + re.escape(KEY) + r"\": (?:Permission denied|invalid format|error in libcrypto|incorrect passphrase supplied to decrypt private key)",
    )
    for line in text.splitlines():
        if any(re.fullmatch(pattern, line) for pattern in safe_line):
            exact.append(line)
    return dict(error_classes=classes, stderr_safe_exact_lines=exact,
                stderr_bytes=len(text.encode("utf-8")), stderr_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                raw_stderr_exported=False, wrong_user="NOT_ESTABLISHED")


def probe(user, *, deadline=None):
    try:
        return _probe(user, ProbeBudget(deadline))
    except ProbeDeadlineError as exc:
        emit("PRECHECK_BLOCKED", reason=str(exc))
        return 3


def _probe(user, budget):
    budget.remaining()
    if user != VERIFIED_USER:
        emit("PRECHECK_BLOCKED", reason="previously_verified_ubuntu_required")
        return 3
    meta = os.stat(KEY)
    if not stat.S_ISREG(meta.st_mode) or not os.access(KEY, os.R_OK):
        emit("PRECHECK_BLOCKED", reason="existing_key_not_readable", key_contents_read=False)
        return 3
    found = subprocess.run(["/usr/bin/ssh-keygen", "-F", HOST, "-f", KNOWN], capture_output=True, text=True, timeout=min(5, budget.remaining()))
    records = "\n".join(line for line in found.stdout.splitlines()
                        if line and not line.startswith("#") and "ssh-ed25519" in line)
    pin = subprocess.run(["/usr/bin/ssh-keygen", "-lf", "-", "-E", "sha256"],
                         input=records + "\n", capture_output=True, text=True, timeout=min(5, budget.remaining()))
    budget.remaining()
    fingerprints = {line.split()[1] for line in pin.stdout.splitlines() if len(line.split()) > 1}
    if not records or fingerprints != {EXPECTED}:
        emit("PRECHECK_BLOCKED", reason="exact_known_host_pin_missing_or_conflicting")
        return 3
    emit("TCP_PROBE_STARTED", user=user, target=HOST, power_mutations=False)
    if not tcp_ready(budget):
        return 3
    args = ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i", KEY, "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UpdateHostKeys=no", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "VerifyHostKeyDNS=no", "-o", "CheckHostIP=no",
            "-o", "AddKeysToAgent=no", "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes", "-o", "ConnectionAttempts=1",
            "-o", "UserKnownHostsFile=" + KNOWN,
            "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
            user + "@" + HOST, "/usr/bin/python3 -I -B -c " + shlex.quote(HOST_OBSERVER)]
    timeout = min(SSH_SECONDS, budget.remaining())
    emit("SSH_PROBE_STARTED", user=user)
    started = budget.now()
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=min(timeout, budget.remaining()))
    except subprocess.TimeoutExpired as exc:
        error_text = exc.stderr or b""
        if isinstance(error_text, bytes):
            error_text = error_text.decode("utf-8", errors="replace")
        emit("SSH_WALL_TIMEOUT", elapsed_seconds=round(time.monotonic() - started, 3), **stderr_info(error_text, user))
        return 3
    budget.remaining()  # do not accept success returned after the shared deadline
    elapsed = budget.now() - started
    if elapsed > timeout:
        raise ProbeDeadlineError("ssh_deadline_expired")
    info = stderr_info(result.stderr, user)
    emit("SSH_PROBE_FINISHED", ssh_exit=result.returncode,
         elapsed_seconds=round(elapsed, 3), **info)
    if result.returncode == 0:
        try:
            observation = parse_host_observation(result.stdout)
        except (ValueError, TypeError, RecursionError):
            emit("HOST_OBSERVER_BLOCKED", result_class="INVALID_OR_INCOMPLETE")
            return 3
        emit("HOST_OBSERVER_RESULT", observation=observation)
        emit("SSH_AUTHENTICATED", uid=observation["uid"], user=user)
        if observation["result"] == "QUIET_IN_KNOWN_SCOPE":
            return 0
        emit("HOST_OBSERVER_BLOCKED", result_class=observation["result"])
        return 3
    emit("SSH_NOT_CONFIRMED", stdout_exported=False)
    return 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", choices=[VERIFIED_USER], help="Verified IBM login from 2026-10-04 evidence; no user cycling.")
    parser.add_argument("--execute", action="store_true", help="Use only inside a newly authorized running window.")
    args = parser.parse_args()
    if not args.execute:
        emit("PREPARED_ONLY", network_requests=0, power_mutations=False)
        return 0
    if not args.user:
        emit("PRECHECK_BLOCKED", reason="verified_user_required")
        return 3
    try:
        return probe(args.user)
    except (OSError, subprocess.SubprocessError) as exc:
        emit("LOCAL_PRECHECK_FAILED", error_type=type(exc).__name__, raw_error_exported=False)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
