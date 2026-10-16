"""Finite read-only Oracle systemd inventory. No installed Stop capability claim.

CLI: python3 -u ops/ibm_oracle_watchdog_inventory.py (no arguments).
Only EXPECTED_MAIN_SHA and the existing ORACLE_SSH_PRIVATE_KEY are inputs.
The caller captures the single sanitized JSON receipt; this module writes no files.

Transport follows main's ibm_trial_guest_probe sealed-memfd/strict SSH pattern,
without importing that module or its historical review-branch context guards.
Systemd semantics: upstream man/busctl.xml (typed method/property results) and
man/org.freedesktop.systemd1.xml (Timer uint64 microsecond clock properties).
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time

REPOSITORY = "olegmed1-art/bridge-video-free"
OWNER = "olegmed1-art"
OWNER_ID = "315099490"
REF = "refs/heads/main"
WORKFLOW_REF = REPOSITORY + "/.github/workflows/ibm-oracle-watchdog-inventory.yml@" + REF
ORACLE = "92.5.47.149"
ORACLE_HOST = "autopilot-lite-vnic"
ORACLE_FP = "SHA256:XBR1x74uJ41BxmDF7Y9P20GjIjNbrYXqieV4c2MC0Go"
REMOTE_SECONDS = 20
OUTER_SECONDS = 60
RECEIPT_BYTES = 12 * 1024
# Combined physical reads across both machines stay below 128 KiB. Stderr
# consumes the same budget, even though it is never retained or exported.
REMOTE_CAPTURE_BYTES = 112 * 1024
RUNNER_CAPTURE_BYTES = 16 * 1024
RUNNER_COMMANDS = 8
REMOTE_COMMANDS = 8
MAX_COMMANDS = RUNNER_COMMANDS + REMOTE_COMMANDS
MAX_UNITS_PER_MANAGER = 2
SCHEMA = "ibm-oracle-watchdog-inventory/v1"
ERRORS = frozenset({"CONTEXT", "CHECKOUT", "CURRENT_MAIN", "SECRET", "MEMFD", "HOST_PIN",
                    "DEADLINE", "OUTPUT_CAP", "COMMAND_CAP", "PROCESS", "TRANSPORT",
                    "REMOTE_SCHEMA", "ARGUMENTS", "INTERNAL"})


class InventoryError(Exception):
    pass


def need(ok, code):
    if not ok:
        raise InventoryError(code)


class Budget:
    """One absolute deadline and physical-byte counter, shared by every child."""
    def __init__(self, end, limit, commands=RUNNER_COMMANDS):
        self.end, self.limit, self.commands = end, limit, commands
        self.captured = self.started = 0


def bounded_process(argv, budget, *, payload=b"", seconds=None, env=None):
    # Never use communicate() or truncate after capture: read sizes themselves
    # are constrained by the remaining combined stdout/stderr allowance.
    need(time.monotonic() < budget.end, "DEADLINE")
    need(budget.started < budget.commands, "COMMAND_CAP")
    need(budget.captured < budget.limit, "OUTPUT_CAP")
    need(len(payload) <= 65536, "PROCESS")
    budget.started += 1
    end = budget.end if seconds is None else min(budget.end, time.monotonic() + seconds)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM, signal.SIGTERM})
    child = selector = None
    output = bytearray()
    offset = 0
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True,
                                 env=env or {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                                             "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                             "GIT_TERMINAL_PROMPT": "0"})
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        selector = selectors.DefaultSelector()
        for stream, name in ((child.stdout, "stdout"), (child.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if payload:
            os.set_blocking(child.stdin.fileno(), False)
            selector.register(child.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            child.stdin.close()
        while selector.get_map():
            remaining = end - time.monotonic()
            need(remaining > 0, "DEADLINE")
            events = selector.select(remaining)
            need(events, "DEADLINE")
            for key, _ in events:
                if key.data == "stdin":
                    try:
                        offset += os.write(key.fd, payload[offset:offset + 4096])
                    except BrokenPipeError:
                        offset = len(payload)
                    if offset == len(payload):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                room = budget.limit - budget.captured
                need(room > 0, "OUTPUT_CAP")
                part = os.read(key.fd, min(4096, room))
                budget.captured += len(part)
                if not part:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                elif key.data == "stdout":
                    output.extend(part)
                # Reaching the cap is conservatively incomplete, without an
                # extra sentinel byte read that would violate the physical cap.
                need(budget.captured < budget.limit, "OUTPUT_CAP")
        rc = child.wait(timeout=max(.001, end - time.monotonic()))
        need(time.monotonic() <= end, "DEADLINE")
        return rc, bytes(output)
    except subprocess.TimeoutExpired:
        raise InventoryError("DEADLINE") from None
    finally:
        # Children inherit blocked ALRM/TERM during the spawn handoff. Their
        # lifetime is owned here and always terminated with unmaskable SIGKILL.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM, signal.SIGTERM})
        try:
            try:
                if selector is not None:
                    selector.close()
            finally:
                if child is not None:
                    try:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.wait(timeout=1)
                    finally:
                        for stream in (child.stdin, child.stdout, child.stderr):
                            if not stream.closed:
                                stream.close()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def memory(raw, name):
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            need(count > 0, "MEMFD")
            offset += count
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SHRINK |
                    fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:
        os.close(fd)
        raise


def context(budget):
    e = os.environ
    exact = {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_REPOSITORY_OWNER": OWNER,
             "GITHUB_REPOSITORY_OWNER_ID": OWNER_ID, "GITHUB_ACTOR": OWNER,
             "GITHUB_ACTOR_ID": OWNER_ID, "GITHUB_TRIGGERING_ACTOR": OWNER,
             "GITHUB_REF": REF, "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
             "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1"}
    need(all(e.get(k) == v for k, v in exact.items()), "CONTEXT")
    head, run = e.get("GITHUB_SHA", ""), e.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[0-9a-f]{40}", head) is not None
         and e.get("EXPECTED_MAIN_SHA") == head and e.get("GITHUB_WORKFLOW_SHA") == head
         and re.fullmatch(r"[1-9][0-9]{0,19}", run) is not None, "CONTEXT")
    rc, raw = bounded_process(["/usr/bin/git", "rev-parse", "HEAD"], budget, seconds=3)
    need(rc == 0 and raw == (head + "\n").encode(), "CHECKOUT")
    rc, raw = bounded_process(["/usr/bin/git", "diff", "--quiet", "HEAD", "--"], budget, seconds=3)
    need(rc == 0 and not raw, "CHECKOUT")
    # Fixed public read. No GitHub token, inherited credential helper or auth
    # environment is passed to any subprocess.
    rc, raw = bounded_process(["/usr/bin/git", "-c", "credential.helper=", "-c", "http.extraHeader=",
                               "ls-remote", "https://github.com/" + REPOSITORY + ".git", REF],
                              budget, seconds=6)
    need(rc == 0 and raw == (head + "\t" + REF + "\n").encode(), "CURRENT_MAIN")
    return {"head": head, "run_id": run, "attempt": 1}


# Literal, reviewed source only. Nothing is fetched or imported from private
# packages. Python reads this fixed program from SSH stdin and retains no file.
REMOTE_SOURCE = r'''import ctypes,json,os,pwd,re,selectors,signal,socket,subprocess,sys,time
SCHEMA="ibm-oracle-watchdog-inventory/v1"
CAP=114688
SECONDS=20
MAX_COMMANDS=8
MAX_UNITS=2
MAX_ROWS=64
MAX_NAMES=24
NAME=re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:@(?:[a-z0-9][a-z0-9_-]{0,31})?)?\.(?:service|timer)\Z")
PATTERNS=tuple(prefix+"."+suffix for prefix in
 ("ibm-*","bridge-ibm-*","bridge-school-ibm-*","bridge-*watchdog*","bridge-*backstop*","bridge-*stop*")
 for suffix in ("service","timer"))
LOAD={"stub","loaded","not-found","bad-setting","error","merged","masked"}
ACTIVE={"active","reloading","inactive","failed","activating","deactivating","maintenance","refreshing"}
SUB={"dead","condition","start-pre","start","start-post","running","exited","reload",
 "reload-signal","reload-notify","stop","stop-watchdog","stop-sigterm","stop-sigkill",
 "stop-post","final-watchdog","final-sigterm","final-sigkill","failed","dead-before-auto-restart",
 "failed-before-auto-restart","dead-resources-pinned","auto-restart","auto-restart-queued",
 "cleaning","waiting","elapsed"}
UNITFILE={"enabled","enabled-runtime","linked","linked-runtime","alias","masked","masked-runtime",
 "static","disabled","indirect","generated","transient","bad","invalid","not-found"}
TIMER_PROPERTIES=("Unit","NextElapseUSecRealtime","NextElapseUSecMonotonic","LastTriggerUSec")
REASONS={"DEADLINE","OUTPUT_CAP","COMMAND_CAP","COMMAND_FAILED","MALFORMED_LIST","ROW_CAP",
 "UNIT_CAP","NAME_CAP","TIMER_CAP","PROPERTY_UNKNOWN","MANAGER_UNAVAILABLE","HOST_IDENTITY","INTERNAL"}
class Limited(Exception):
 pass
def canonical(name):
 if not isinstance(name,str) or len(name)>96 or not NAME.fullmatch(name):return False
 stem=name.rsplit(".",1)[0].split("@",1)[0]
 return (stem.startswith(("ibm-","bridge-ibm-","bridge-school-ibm-")) or
         stem.startswith("bridge-") and bool(set(stem.split("-")) & {"watchdog","backstop","stop"}))
def blank_manager():
 return {"status":"UNKNOWN","reasons":[],"discovered_names":[],"units":[]}
def blank():
 return {"schema":SCHEMA,"identity":"UNKNOWN","discovery":"UNKNOWN",
         "managers":{"system":blank_manager(),"user":blank_manager()},
         "installed_credentials":"UNPROVEN","independent_deadline":"UNPROVEN",
         "stop_capability":"UNPROVEN","watchdog_ready":False,
         "commands":0,"captured_bytes":0,"reason":"NONE",
         "sample_started_realtime_us":None,"sample_finished_realtime_us":None,
         "sample_started_monotonic_us":None,"sample_finished_monotonic_us":None}
def unit_blank(name):
 return {"name":name,"load":"UNKNOWN","active":"UNKNOWN","sub":"UNKNOWN","unitfile":"UNKNOWN",
         "properties":"UNKNOWN","timer":None if name.endswith(".service") else
         {"status":"UNKNOWN","trigger_unit":None,"next_realtime_us":None,"next_monotonic_us":None,
          "last_trigger_realtime_us":None}}
def lists(raw,kind):
 rows={};partial=False
 def pairs(items):
  obj={}
  for key,value in items:
   if key in obj:raise ValueError()
   obj[key]=value
  return obj
 try:
  value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
  signature="a(ssssssouso)" if kind=="list-units" else "a(ss)"
  if type(value) is not dict or set(value)!={"type","data"} or value["type"]!=signature:raise ValueError()
  data=value["data"]
  if type(data) is not list or len(data)!=1 or type(data[0]) is not list:raise ValueError()
  entries=data[0]
 except (ValueError,UnicodeError,RecursionError):return rows,"MALFORMED_LIST"
 if len(entries)>MAX_ROWS:return rows,"ROW_CAP"
 for fields in entries:
  if type(fields) is not list or len(fields)!=(10 if kind=="list-units" else 2):
   partial=True;continue
  name=fields[0]
  if kind=="list-unit-files" and type(name) is str:
   # This API returns unit-file paths. Never open or follow any returned path.
   name=name.rsplit("/",1)[-1]
  if not canonical(name) or name in rows:
   partial=True;continue
  unit=unit_blank(name)
  if kind=="list-units":
   # Description, following, object path and job details are discarded.
   for key,item,allowed in zip(("load","active","sub"),fields[2:5],(LOAD,ACTIVE,SUB)):
    if type(item) is str and item in allowed:unit[key]=item
    else:partial=True
  else:
   if type(fields[1]) is str and fields[1] in UNITFILE:unit["unitfile"]=fields[1]
   else:partial=True
  rows[name]=unit
 return rows,"MALFORMED_LIST" if partial else None
def typed(raw,types):
 try:lines=raw.decode("ascii").splitlines()
 except UnicodeDecodeError:raise Limited("PROPERTY_UNKNOWN") from None
 if len(lines)!=len(types):raise Limited("PROPERTY_UNKNOWN")
 out=[]
 for line,kind in zip(lines,types):
  if kind=="s":
   # Relevant names/states use only this canonical subset. No C escapes,
   # paths, interpolated strings, control characters or free-form data.
   match=re.fullmatch(r's "([a-z0-9.@_-]*)"',line)
   if not match:raise Limited("PROPERTY_UNKNOWN")
   out.append(match[1])
  else:
   match=re.fullmatch(r't (0|[1-9][0-9]{0,19})',line)
   if not match or int(match[1])>18446744073709551615:raise Limited("PROPERTY_UNKNOWN")
   value=int(match[1]);out.append(None if value in (0,18446744073709551615) else value)
 return out
def timer(raw):
 values=typed(raw,"sttt")
 valid=canonical(values[0]) and values[0].endswith(".service")
 return {"status":"COMPLETE" if valid else "PARTIAL","trigger_unit":values[0] if valid else None,
         "next_realtime_us":values[1],"next_monotonic_us":values[2],"last_trigger_realtime_us":values[3]}
def object_path(name):
 if not canonical(name):raise Limited("PROPERTY_UNKNOWN")
 return "/org/freedesktop/systemd1/unit/"+"".join(c if c.isalnum() else "_%02x"%ord(c) for c in name)
def list_command(manager,kind,owner):
 if (manager not in ("system","user") or kind not in ("list-units","list-unit-files")
     or not re.fullmatch(r":[0-9]{1,10}\.[0-9]{1,10}",owner)):raise Limited("INTERNAL")
 method="ListUnitsByPatterns" if kind=="list-units" else "ListUnitFilesByPatterns"
 return ["/usr/bin/busctl","--"+manager,"--no-pager","--json=short","--auto-start=no",
         "--allow-interactive-authorization=no","call",owner,"/org/freedesktop/systemd1",
         "org.freedesktop.systemd1.Manager",method,"asas","0",str(len(PATTERNS)),*PATTERNS]
def owner_command(manager):
 if manager not in ("system","user"):raise Limited("INTERNAL")
 return ["/usr/bin/busctl","--"+manager,"--no-pager","--auto-start=no",
         "--allow-interactive-authorization=no","call","org.freedesktop.DBus",
         "/org/freedesktop/DBus","org.freedesktop.DBus","GetNameOwner","s","org.freedesktop.systemd1"]
def owner_value(raw):
 try:match=re.fullmatch(rb's "(:[0-9]{1,10}\.[0-9]{1,10})"\n',raw)
 except TypeError:match=None
 if not match:raise Limited("MANAGER_UNAVAILABLE")
 return match[1].decode("ascii")
def property_command(manager,name,owner):
 if manager not in ("system","user") or not re.fullmatch(r":[0-9]{1,10}\.[0-9]{1,10}",owner):raise Limited("INTERNAL")
 # v255 get-property ignores --auto-start. A unique existing owner cannot be
 # activated, and prevents silently reconnecting to a replacement manager.
 return ["/usr/bin/busctl","--"+manager,"--no-pager",
         "get-property",owner,
         object_path(name),"org.freedesktop.systemd1.Timer",*TIMER_PROPERTIES]
class Capture:
 def __init__(self,end,uid):
  self.end=end;self.count=0;self.used=0
  self.env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C","SYSTEMD_COLORS":"0",
            "SYSTEMD_PAGERSECURE":"1","XDG_RUNTIME_DIR":"/run/user/"+str(uid),
            "DBUS_SESSION_BUS_ADDRESS":"unix:path=/run/user/"+str(uid)+"/bus"}
 def run(self,argv):
  if time.monotonic()>=self.end:raise Limited("DEADLINE")
  if self.count>=MAX_COMMANDS:raise Limited("COMMAND_CAP")
  if self.used>=CAP:raise Limited("OUTPUT_CAP")
  self.count+=1
  # Per-command responsiveness does not reset the enclosing absolute deadline.
  end=min(self.end,time.monotonic()+3)
  mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM,signal.SIGTERM})
  p=sel=None;out=bytearray()
  try:
   try:p=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          start_new_session=True,env=self.env)
   except OSError:raise Limited("COMMAND_FAILED") from None
   signal.pthread_sigmask(signal.SIG_SETMASK,mask)
   sel=selectors.DefaultSelector()
   for stream,label in ((p.stdout,"stdout"),(p.stderr,"stderr")):
    os.set_blocking(stream.fileno(),False);sel.register(stream,selectors.EVENT_READ,label)
   while sel.get_map():
    left=end-time.monotonic()
    if left<=0:raise Limited("DEADLINE")
    events=sel.select(left)
    if not events:raise Limited("DEADLINE")
    for key,_ in events:
     room=CAP-self.used
     if room<=0:raise Limited("OUTPUT_CAP")
     part=os.read(key.fd,min(4096,room));self.used+=len(part)
     if not part:sel.unregister(key.fileobj);key.fileobj.close()
     elif key.data=="stdout":out.extend(part)
     if self.used>=CAP:raise Limited("OUTPUT_CAP")
   rc=p.wait(timeout=max(.001,end-time.monotonic()))
   if time.monotonic()>end:raise Limited("DEADLINE")
   return rc,bytes(out)
  except subprocess.TimeoutExpired:raise Limited("DEADLINE") from None
  finally:
   signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM,signal.SIGTERM})
   try:
    try:
     if sel is not None:sel.close()
    finally:
     if p is not None:
      try:
       try:os.killpg(p.pid,signal.SIGKILL)
       except ProcessLookupError:pass
       p.wait(timeout=1)
      finally:
       for stream in (p.stdout,p.stderr):
        if not stream.closed:stream.close()
   finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)

def add_reason(manager,reason):
 if reason not in manager["reasons"]:manager["reasons"].append(reason)
def collect(capture):
 result=blank();result["identity"]="MATCH"
 result["sample_started_realtime_us"]=time.time_ns()//1000
 result["sample_started_monotonic_us"]=time.monotonic_ns()//1000
 for scope in ("system","user"):
  manager=result["managers"][scope];units={};successful=0;owner=None
  try:
   rc,raw=capture.run(owner_command(scope))
   if rc:raise Limited("MANAGER_UNAVAILABLE")
   owner=owner_value(raw)
  except Limited as exc:
   add_reason(manager,str(exc));continue
  for kind in ("list-units","list-unit-files"):
   try:
    rc,raw=capture.run(list_command(scope,kind,owner))
    if rc:raise Limited("MANAGER_UNAVAILABLE")
    rows,reason=lists(raw,kind);successful+=1
    if reason:add_reason(manager,reason)
    for name,row in rows.items():
     if name not in units:units[name]=row
     else:
      for key in ("load","active","sub","unitfile"):
       if row[key]!="UNKNOWN":units[name][key]=row[key]
   except Limited as exc:add_reason(manager,str(exc))
  manager["discovered_names"]=sorted(units)[:MAX_NAMES]
  if len(units)>MAX_NAMES:add_reason(manager,"NAME_CAP")
  if len(units)>MAX_UNITS:add_reason(manager,"UNIT_CAP")
  # Timers first: bounded sampling is explicit, never a claim of full coverage.
  names=sorted(units,key=lambda name:(not bool(set(name.rsplit(".",1)[0].split("@",1)[0].split("-")) & {"watchdog","backstop","stop"}),not name.endswith(".timer"),name))[:MAX_UNITS]
  manager["discovered_names"]=sorted(set(names) | set(sorted(set(units)-set(names))[:MAX_NAMES-len(names)]))
  timer_attempted=False
  for name in names:
   unit=units[name];manager["units"].append(unit)
   unit["properties"]="COMPLETE" if all(unit[k]!="UNKNOWN" for k in ("load","active","sub","unitfile")) else "PARTIAL"
   if unit["properties"]!="COMPLETE":add_reason(manager,"PROPERTY_UNKNOWN")
   if name.endswith(".timer"):
    if timer_attempted:
     add_reason(manager,"TIMER_CAP");continue
    timer_attempted=True
    if owner is None:
     add_reason(manager,"PROPERTY_UNKNOWN");continue
    try:
     rc,raw=capture.run(property_command(scope,name,owner))
     if rc:raise Limited("PROPERTY_UNKNOWN")
     unit["timer"]=timer(raw)
     if unit["timer"]["status"]!="COMPLETE":add_reason(manager,"PROPERTY_UNKNOWN")
    except Limited as exc:add_reason(manager,str(exc))
  manager["status"]="COMPLETE" if successful==2 and not manager["reasons"] else "PARTIAL" if successful else "UNKNOWN"
 result["discovery"]=("COMPLETE" if all(m["status"]=="COMPLETE" for m in result["managers"].values())
                      else "UNKNOWN" if all(m["status"]=="UNKNOWN" for m in result["managers"].values()) else "PARTIAL")
 result["commands"]=capture.count;result["captured_bytes"]=capture.used
 result["sample_finished_realtime_us"]=time.time_ns()//1000
 result["sample_finished_monotonic_us"]=time.monotonic_ns()//1000
 return result
def remote_main():
 result=blank();capture=None
 def expired(*_):raise Limited("DEADLINE")
 signal.signal(signal.SIGALRM,expired);signal.signal(signal.SIGTERM,expired)
 end=time.monotonic()+SECONDS-1
 signal.setitimer(signal.ITIMER_REAL,SECONDS-1)
 try:
  parent=os.getppid()
  if ctypes.CDLL(None).prctl(1,signal.SIGTERM)!=0 or os.getppid()!=parent:raise Limited("HOST_IDENTITY")
  uid=os.getuid()
  if (uid!=1001 or os.geteuid()!=uid or pwd.getpwuid(uid).pw_name!="ubuntu"
      or socket.gethostname()!="autopilot-lite-vnic"):raise Limited("HOST_IDENTITY")
  capture=Capture(end,uid);result=collect(capture)
 except BaseException as exc:
  reason=str(exc) if isinstance(exc,Limited) and str(exc) in REASONS else "INTERNAL"
  result=blank();result["reason"]=reason
  if capture is not None:result["commands"]=capture.count;result["captured_bytes"]=capture.used
 finally:signal.setitimer(signal.ITIMER_REAL,0)
 raw=json.dumps(result,sort_keys=True,separators=(",",":"))
 if len(raw.encode())+1>12288:raw=json.dumps(dict(blank(),reason="OUTPUT_CAP"),separators=(",",":"))
 print(raw,flush=True)
if __name__=="__main__":remote_main()
'''


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "REMOTE_SCHEMA")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(InventoryError("REMOTE_SCHEMA")))
    except (ValueError, UnicodeError, RecursionError):
        raise InventoryError("REMOTE_SCHEMA") from None


def validate_remote(raw):
    need(type(raw) is bytes and 0 < len(raw) <= RECEIPT_BYTES, "REMOTE_SCHEMA")
    result = strict_json(raw)
    # This is the same literal reviewed schema/parser source, evaluated only to
    # define standard-library functions. __main__ is deliberately not set, so
    # validation cannot execute the collector or any remote command.
    namespace = {"__name__": "inventory_schema"}
    exec(compile(REMOTE_SOURCE, "<reviewed-inventory-schema>", "exec"), namespace)
    blank = namespace["blank"]()
    need(type(result) is dict and set(result) == set(blank), "REMOTE_SCHEMA")
    for key in ("schema", "installed_credentials", "independent_deadline", "stop_capability", "watchdog_ready"):
        need(type(result[key]) is type(blank[key]) and result[key] == blank[key], "REMOTE_SCHEMA")
    need(result["identity"] in ("MATCH", "UNKNOWN") and result["reason"] in namespace["REASONS"] | {"NONE"}, "REMOTE_SCHEMA")
    need(type(result["commands"]) is int and 0 <= result["commands"] <= REMOTE_COMMANDS
         and type(result["captured_bytes"]) is int and 0 <= result["captured_bytes"] <= REMOTE_CAPTURE_BYTES, "REMOTE_SCHEMA")
    for key in ("sample_started_realtime_us", "sample_finished_realtime_us",
                "sample_started_monotonic_us", "sample_finished_monotonic_us"):
        value = result[key]
        need(value is None if result["identity"] == "UNKNOWN" else
             type(value) is int and 0 < value < 18446744073709551615, "REMOTE_SCHEMA")
    if result["identity"] == "MATCH":
        need(0 <= result["sample_finished_monotonic_us"] - result["sample_started_monotonic_us"]
             <= REMOTE_SECONDS * 1000000, "REMOTE_SCHEMA")
    managers = result["managers"]
    need(type(managers) is dict and set(managers) == {"system", "user"}, "REMOTE_SCHEMA")
    for manager in managers.values():
        need(type(manager) is dict and set(manager) == {"status", "reasons", "discovered_names", "units"}, "REMOTE_SCHEMA")
        need(manager["status"] in ("COMPLETE", "PARTIAL", "UNKNOWN")
             and type(manager["reasons"]) is list and len(manager["reasons"]) <= len(namespace["REASONS"])
             and all(type(r) is str and r in namespace["REASONS"] for r in manager["reasons"])
             and len(manager["reasons"]) == len(set(manager["reasons"])), "REMOTE_SCHEMA")
        names = manager["discovered_names"]
        need(type(names) is list and len(names) <= namespace["MAX_NAMES"]
             and all(namespace["canonical"](n) for n in names)
             and names == sorted(set(names)), "REMOTE_SCHEMA")
        units = manager["units"]
        need(type(units) is list and len(units) <= MAX_UNITS_PER_MANAGER, "REMOTE_SCHEMA")
        seen = set()
        for unit in units:
            need(type(unit) is dict and set(unit) == set(namespace["unit_blank"]("ibm-fixture.service")), "REMOTE_SCHEMA")
            name = unit["name"]
            need(namespace["canonical"](name) and name not in seen, "REMOTE_SCHEMA")
            seen.add(name)
            for key, states in (("load", "LOAD"), ("active", "ACTIVE"), ("sub", "SUB"), ("unitfile", "UNITFILE")):
                need(type(unit[key]) is str and unit[key] in namespace[states] | {"UNKNOWN"}, "REMOTE_SCHEMA")
            need(unit["properties"] in ("COMPLETE", "PARTIAL", "UNKNOWN"), "REMOTE_SCHEMA")
            if unit["properties"] == "COMPLETE":
                need(all(unit[k] != "UNKNOWN" for k in ("load", "active", "sub", "unitfile")), "REMOTE_SCHEMA")
            if name.endswith(".service"):
                need(unit["timer"] is None, "REMOTE_SCHEMA")
            else:
                timer = unit["timer"]
                need(type(timer) is dict and set(timer) == set(namespace["unit_blank"]("ibm-fixture.timer")["timer"]), "REMOTE_SCHEMA")
                need(timer["status"] in ("COMPLETE", "PARTIAL", "UNKNOWN"), "REMOTE_SCHEMA")
                trigger = timer["trigger_unit"]
                need(trigger is None or namespace["canonical"](trigger) and trigger.endswith(".service"), "REMOTE_SCHEMA")
                for key in ("next_realtime_us", "next_monotonic_us", "last_trigger_realtime_us"):
                    value = timer[key]
                    need(value is None or type(value) is int and 0 < value < 18446744073709551615, "REMOTE_SCHEMA")
                if timer["status"] == "COMPLETE":
                    need(trigger is not None, "REMOTE_SCHEMA")
            need(name in names, "REMOTE_SCHEMA")
            if manager["status"] == "COMPLETE":
                need(unit["properties"] == "COMPLETE" and
                     (unit["timer"] is None or unit["timer"]["status"] == "COMPLETE"), "REMOTE_SCHEMA")
        if manager["status"] == "COMPLETE":
            need(not manager["reasons"] and seen == set(names), "REMOTE_SCHEMA")
        if manager["status"] == "UNKNOWN":
            need(not units and not names, "REMOTE_SCHEMA")
    expected = ("COMPLETE" if all(m["status"] == "COMPLETE" for m in managers.values()) else
                "UNKNOWN" if all(m["status"] == "UNKNOWN" for m in managers.values()) else "PARTIAL")
    need(result["discovery"] == expected, "REMOTE_SCHEMA")
    if result["identity"] != "MATCH" or result["reason"] != "NONE":
        need(result["discovery"] == "UNKNOWN" and all(not m["units"] for m in managers.values()), "REMOTE_SCHEMA")
    return result


def pinned_scan(raw):
    try:
        rows = [line.split() for line in raw.decode("ascii").splitlines() if line and not line.startswith("#")]
        need(len(rows) == 1 and len(rows[0]) == 3 and rows[0][:2] == [ORACLE, "ssh-ed25519"], "HOST_PIN")
        blob = base64.b64decode(rows[0][2], validate=True)
        fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        need(fp == ORACLE_FP, "HOST_PIN")
        return (" ".join(rows[0]) + "\n").encode()
    except (ValueError, UnicodeError):
        raise InventoryError("HOST_PIN") from None


def ssh_command(keyfd, knownfd):
    prefix = "/proc/" + str(os.getpid()) + "/fd/"
    return ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i", prefix + str(keyfd),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=" + prefix + str(knownfd), "-o", "UpdateHostKeys=no",
            "-o", "VerifyHostKeyDNS=no", "-o", "CheckHostIP=no", "-o", "AddKeysToAgent=no",
            "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "-o", "IdentityAgent=none",
            "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "PreferredAuthentications=publickey", "-o", "ConnectionAttempts=1",
            "-o", "ConnectTimeout=3", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
            "ubuntu@" + ORACLE, "/usr/bin/python3 -I -B -"]


def run_inventory(budget):
    binding = context(budget)  # Before even reading the existing key.
    raw = bytearray(os.environ.pop("ORACLE_SSH_PRIVATE_KEY", "").replace("\r", "").encode())
    keyfd = knownfd = None
    try:
        need(32 < len(raw) < 32768, "SECRET")
        keyfd = memory(raw, "existing-oracle-owner-key")
        for index in range(len(raw)):
            raw[index] = 0
        rc, scanned = bounded_process(["/usr/bin/ssh-keyscan", "-T", "3", "-t", "ed25519", ORACLE], budget, seconds=4)
        need(rc == 0, "HOST_PIN")
        knownfd = memory(pinned_scan(scanned), "oracle-public-trust")
        need(context(budget) == binding, "CONTEXT")  # Fresh binding immediately before sole SSH call.
        rc, response = bounded_process(ssh_command(keyfd, knownfd), budget,
                                       payload=REMOTE_SOURCE.encode(), seconds=REMOTE_SECONDS + 7)
        need(rc == 0, "TRANSPORT")
        inventory = validate_remote(response)
        need(inventory["identity"] == "MATCH" and inventory["reason"] == "NONE", "TRANSPORT")
        return {"schema": SCHEMA, "result": "OBSERVED", "binding": binding,
                "host_pin": "MATCH", "inventory": inventory, "raw_output_exported": False}
    finally:
        for index in range(len(raw)):
            raw[index] = 0
        for fd in (knownfd, keyfd):
            if fd is not None:
                os.close(fd)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    def expired(*_):
        raise InventoryError("DEADLINE")
    old_handler = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, OUTER_SECONDS - 1)
    rc = 1
    try:
        need(not args, "ARGUMENTS")
        budget = Budget(time.monotonic() + OUTER_SECONDS - 1, RUNNER_CAPTURE_BYTES)
        receipt = run_inventory(budget)
        rc = 0
    except BaseException as exc:
        code = str(exc) if isinstance(exc, InventoryError) and str(exc) in ERRORS else "INTERNAL"
        receipt = {"schema": SCHEMA, "result": "UNKNOWN", "reason": code,
                   "installed_credentials": "UNPROVEN", "independent_deadline": "UNPROVEN",
                   "stop_capability": "UNPROVEN", "watchdog_ready": False, "raw_output_exported": False}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) + 1 > RECEIPT_BYTES:
        encoded = json.dumps({"schema": SCHEMA, "result": "UNKNOWN", "reason": "OUTPUT_CAP",
                              "installed_credentials": "UNPROVEN", "independent_deadline": "UNPROVEN",
                              "stop_capability": "UNPROVEN", "watchdog_ready": False, "raw_output_exported": False})
        rc = 1
    print(encoded, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
