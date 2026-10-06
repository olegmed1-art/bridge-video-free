"""Candidate PID1 ownership for supervisor and all its administrative children.
No launch is performed by importing this module. Source-only proposal."""
import os,re,subprocess,uuid
from pathlib import Path
from stagea_synthetic_core import need,ROOT
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
def show(unit):
 need(re.fullmatch(CONTROL,unit) or re.fullmatch(OUTER,unit))
 keys=(*PROPERTIES,*NETWORK,"ActiveState","InvocationID","MainPID","ControlGroup","ActiveEnterTimestampMonotonic","ReadOnlyPaths","ReadWritePaths")
 reply=ctl("show",unit,"--property="+",".join(keys))
 need(reply.returncode==0 and len(reply.stdout)<16384)
 return dict(line.split("=",1) for line in reply.stdout.decode().splitlines() if "=" in line)
def identity(unit,seconds=60,*,network=False):
 need(seconds in (60,300))
 state=show(unit)
 expected={**PROPERTIES,"RuntimeMaxUSec":"1min" if seconds==60 else "5min"}
 need(all(state.get(key)==value for key,value in expected.items()))
 need(state.get("ReadOnlyPaths")==str(ROOT))
 if network:
  need(all(state.get(key)==value for key,value in NETWORK.items()))
  need(state.get("ReadWritePaths")==str(ROOT/"evidence"))
 need(state.get("ActiveState")=="active" and re.fullmatch("[0-9a-f]{32}",state.get("InvocationID",""))
      and int(state.get("MainPID","0"))>0 and state.get("ControlGroup")=="/system.slice/"+unit)
 group=Path("/sys/fs/cgroup"+state["ControlGroup"])
 need(group.is_dir() and "populated 1" in (group/"cgroup.events").read_text())
 need(state.get("ActiveEnterTimestampMonotonic","").isdigit() and int(state["ActiveEnterTimestampMonotonic"])>0)
 return state,group,group.stat().st_ino
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
