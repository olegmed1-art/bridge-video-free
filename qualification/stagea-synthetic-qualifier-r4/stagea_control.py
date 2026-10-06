"""Candidate PID1 ownership for supervisor and all its administrative children.
No launch is performed by importing this module. Source-only proposal."""
import os,re,subprocess,uuid
from pathlib import Path
from stagea_synthetic_core import need,ROOT,Refused
CONTROL=r"bridge-stagea-control-[0-9a-f]{12}-[0-9]{1,20}-[0-9]{1,6}-[0-9a-f]{16}\.service"
OUTER=r"bridge-stagea-containment-[0-9a-f]{12}-[0-9]{1,20}-[0-9]{1,6}-[0-9a-f]{16}\.service"
PROPERTIES={"Type":"exec","ExitType":"cgroup","KillMode":"control-group",
 "SendSIGKILL":"yes","FinalKillSignal":"9","Restart":"no","NoNewPrivileges":"yes",
 "ProtectControlGroups":"yes","LimitCORE":"0","TimeoutStopUSec":"2s","RuntimeMaxUSec":"1min",
 "RuntimeRandomizedExtraUSec":"0","ExecStop":"","ExecStopPost":"",
 "ProtectSystem":"strict","CapabilityBoundingSet":"","AmbientCapabilities":""}
NETWORK={"PrivateNetwork":"yes","RestrictAddressFamilies":"AF_UNIX"}
def ctl(*args):
 return subprocess.run(["/usr/bin/systemctl",*args],stdout=subprocess.PIPE,
   stderr=subprocess.DEVNULL,timeout=5,env={"PATH":"/usr/bin:/bin"},close_fds=True)
IDENTITY_CODES=frozenset({"UNIT_NAME","SHOW_QUERY","SHOW_FORMAT","STOP_QUERY","STOP_REPLY_COUNT",
 "STOP_EXECSTOP_TYPED","STOP_EXECSTOPPOST_TYPED","READ_ONLY_PATHS","READ_WRITE_PATHS",
 "PRIVATE_NETWORK","ADDRESS_FAMILIES","ACTIVE_STATE","INVOCATION_ID","MAIN_PID","CONTROL_GROUP",
 "CGROUP_READ","CGROUP_POPULATED","ACTIVATION_TIMESTAMP","SECONDS",
 *("PROFILE_"+key for key in PROPERTIES)})
class IdentityRefused(Refused):
 def __init__(self,code):
  need(code in IDENTITY_CODES);self.code=code
  super().__init__("IDENTITY_PREDICATE_REFUSED")
def identity_need(value,code):
 if not value:raise IdentityRefused(code)
def show(unit):
 identity_need(re.fullmatch(CONTROL,unit) or re.fullmatch(OUTER,unit),"UNIT_NAME")
 keys=(*PROPERTIES,*NETWORK,"ActiveState","InvocationID","MainPID","ControlGroup","ActiveEnterTimestampMonotonic","ReadOnlyPaths","ReadWritePaths")
 try:
  reply=ctl("show",unit,"--property="+",".join(keys))
 except Exception:raise IdentityRefused("SHOW_QUERY") from None
 identity_need(reply.returncode==0 and len(reply.stdout)<16384,"SHOW_QUERY")
 try:
  state={}
  for line in reply.stdout.decode().splitlines():
   key,value=line.split("=",1)
   identity_need(key in keys and key not in state,"SHOW_FORMAT");state[key]=value
 except IdentityRefused:raise
 except Exception:raise IdentityRefused("SHOW_FORMAT") from None
 # systemctl v255 omits empty Exec arrays. Missing text alone is NOT proof.
 # Query only these two typed properties; do not dump the service environment.
 object_path="/org/freedesktop/systemd1/unit/"+unit.replace("-","_2d").replace(".","_2e")
 try:
  stops=subprocess.run(["/usr/bin/busctl","--system","--no-pager","get-property",
   "org.freedesktop.systemd1",object_path,"org.freedesktop.systemd1.Service","ExecStop","ExecStopPost"],
   stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5,env={"PATH":"/usr/bin:/bin"},close_fds=True)
 except Exception:raise IdentityRefused("STOP_QUERY") from None
 identity_need(stops.returncode==0 and len(stops.stdout)<16384,"STOP_QUERY")
 rows=stops.stdout.splitlines()
 identity_need(len(rows)==2,"STOP_REPLY_COUNT")
 for key,row,code in zip(("ExecStop","ExecStopPost"),rows,("STOP_EXECSTOP_TYPED","STOP_EXECSTOPPOST_TYPED")):
  identity_need(row==b"a(sasbttttuii) 0" and state.get(key,"")=="",code)
  state[key]="" # Only after successful, exact typed empty-array proof.
 return state
def identity(unit,seconds=60,*,network=False):
 identity_need(seconds in (60,300),"SECONDS")
 state=show(unit)
 expected={**PROPERTIES,"RuntimeMaxUSec":"1min" if seconds==60 else "5min"}
 for key,value in expected.items():identity_need(state.get(key)==value,"PROFILE_"+key)
 identity_need(state.get("ReadOnlyPaths")==str(ROOT),"READ_ONLY_PATHS")
 if network:
  identity_need(state.get("PrivateNetwork")==NETWORK["PrivateNetwork"],"PRIVATE_NETWORK")
  identity_need(state.get("RestrictAddressFamilies")==NETWORK["RestrictAddressFamilies"],"ADDRESS_FAMILIES")
  identity_need(state.get("ReadWritePaths")==str(ROOT/"evidence"),"READ_WRITE_PATHS")
 identity_need(state.get("ActiveState")=="active","ACTIVE_STATE")
 identity_need(re.fullmatch("[0-9a-f]{32}",state.get("InvocationID","")),"INVOCATION_ID")
 try:main_pid=int(state.get("MainPID","0"))
 except Exception:raise IdentityRefused("MAIN_PID") from None
 identity_need(main_pid>0,"MAIN_PID")
 identity_need(state.get("ControlGroup")=="/system.slice/"+unit,"CONTROL_GROUP")
 group=Path("/sys/fs/cgroup"+state["ControlGroup"])
 try:populated=group.is_dir() and "populated 1" in (group/"cgroup.events").read_text()
 except Exception:raise IdentityRefused("CGROUP_READ") from None
 identity_need(populated,"CGROUP_POPULATED")
 activation=state.get("ActiveEnterTimestampMonotonic","")
 identity_need(activation.isdigit() and int(activation)>0,"ACTIVATION_TIMESTAMP")
 try:inode=group.stat().st_ino
 except Exception:raise IdentityRefused("CGROUP_READ") from None
 return state,group,inode
def assert_self(source,*,network=False):
 need(os.getuid()==0 and os.getgid()==0)
 line=Path("/proc/self/cgroup").read_text().strip()
 prefix="0::/system.slice/"
 need(line.startswith(prefix))
 unit=line[len(prefix):]
 need(re.fullmatch(CONTROL,unit) and unit.startswith("bridge-stagea-control-"+source[:12]+"-"))
 # Direct/unmanaged calls fail before any administrative child is launched.
 need("NoNewPrivs:\t1" in Path("/proc/self/status").read_text())
 state,group,inode=identity(unit,network=network)
 need(int(state["MainPID"])==os.getpid())
 return state,group,inode
def new_unit(source,run,*,outer=False):
 need(re.fullmatch("[0-9a-f]{40}",source) and re.fullmatch("[0-9]{1,20}-[0-9]{1,6}",run))
 prefix="bridge-stagea-containment-" if outer else "bridge-stagea-control-"
 unit=prefix+source[:12]+"-"+run+"-"+uuid.uuid4().hex[:16]+".service"
 reply=ctl("show",unit,"--property=LoadState","--value")
 need(reply.stdout.strip()==b"not-found")
 return unit
def command(unit,code,*,outer=False,network=False):
 need(type(code) is str and re.fullmatch(OUTER if outer else CONTROL,unit))
 seconds=300 if outer else 60
 args=["/usr/bin/systemd-run","--quiet","--wait","--pipe","--unit="+unit,
  "--service-type=exec","--expand-environment=no","--property=ExitType=cgroup",
  "--property=KillMode=control-group","--property=SendSIGKILL=yes",
  "--property=FinalKillSignal=SIGKILL","--property=TimeoutStopSec=2s",
  "--property=RuntimeMaxSec="+str(seconds)+"s","--property=RuntimeRandomizedExtraSec=0","--property=Restart=no",
  "--property=NoNewPrivileges=yes","--property=ProtectControlGroups=yes","--property=LimitCORE=0",
  "--property=ProtectSystem=strict","--property=CapabilityBoundingSet=",
  "--property=AmbientCapabilities=","--property=ReadOnlyPaths="+str(ROOT)]
 if network:args+=["--property=PrivateNetwork=yes","--property=RestrictAddressFamilies=AF_UNIX",
                  "--property=ReadWritePaths="+str(ROOT/"evidence")]
 return args+["/usr/bin/python3","-I","-B","-S","-c",code]
def gated_code(root,body):
 # Fixed activation frame lets the EXTERNAL caller verify the live control
 # profile/identity before enabling the actual candidate supervisor.
 return ("import sys\nsys.stdout.buffer.write(b'GATE\\n');sys.stdout.buffer.flush()\n"
         "if sys.stdin.buffer.read(1)!=b'G':raise SystemExit(78)\n"
         "sys.path.insert(0,"+repr(str(root))+")\n"+body)
