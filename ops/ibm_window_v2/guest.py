"""Fixed Linux adapter; reads bounded metadata and never emits command stderr/env."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import socket
import subprocess
import time
from urllib.parse import urlsplit
from protocol import TARGETS, require, Refused
from durable import exact_link

PRESERVE=('synthetic-watch.service','synthetic-watch-healthcheck.service','synthetic-media-container.service','docker.service')
BASE=('network-online.target','sysinit.target','basic.target','system.slice')
ALL=TARGETS+PRESERVE+BASE
DEPS=('Requires','Requisite','Wants','BindsTo','PartOf','RequiredBy','RequisiteOf',
      'BoundBy','ConsistsOf','PropagatesStopTo','StopPropagatedFrom','TriggeredBy','Triggers')
STATIC=('Id','Names','LoadState','FragmentPath','DropInPaths','RefuseManualStop','StopWhenUnneeded',
        'FailureAction','SuccessAction','JobTimeoutAction','OnFailure','OnSuccess','NeedDaemonReload')+DEPS
RUNTIME=('ActiveState','SubState','UnitFileState','MainPID','ControlPID','ControlGroup',
         'ExecMainCode','ExecMainStatus','InvocationID','Result','ExecStartPre','ExecStop','ExecStopPost')
LIMIT=1024*1024

def bounded(path):
    with open(path,'rb') as f: raw=f.read(LIMIT+1)
    require(len(raw)<=LIMIT,'READ_TOO_LARGE')
    return raw

def identity(): return hashlib.sha256(bounded('/etc/machine-id').strip()).hexdigest()
def boot(): return bounded('/proc/sys/kernel/random/boot_id').decode().strip()

class Guest:
    def __init__(self, deadline, root='/opt/synthetic-site/synthetic-lab-observer'):
        self.deadline,self.root=deadline,root
    def reserve(self, seconds): require(self.deadline-time.monotonic()>=seconds,'PHASE_BUDGET_EXHAUSTED')
    def command(self,args,limit=3):
        self.reserve(.1)
        # Whole worker and these clients are in the supervisor's isolated process group.
        try:
            from runner import parent_death_kill
            parent=os.getpid()
            p=subprocess.run(args,capture_output=True,timeout=min(limit,self.deadline-time.monotonic()),
                             preexec_fn=lambda:parent_death_kill(parent))
        except subprocess.TimeoutExpired:
            raise Refused('CLIENT_TIMEOUT_OUTCOME_UNKNOWN') from None
        require(len(p.stdout)<=LIMIT and len(p.stderr)<=LIMIT,'COMMAND_OUTPUT_TOO_LARGE')
        require(p.returncode==0,'COMMAND_FAILED_OUTCOME_UNKNOWN')
        return p.stdout.decode('utf-8','strict')
    def units(self):
        raw=self.command(['/usr/bin/systemctl','show','--all','--no-pager',
                          '--property='+','.join(STATIC+RUNTIME),*ALL])
        rows={}
        for block in raw.strip().split('\n\n'):
            row=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
            require(row.get('Id') in ALL and row['Id'] not in rows,'UNIT_ROWS_UNEXPECTED')
            rows[row['Id']]=row
        require(set(rows)==set(ALL),'UNIT_ROWS_MISSING')
        return rows
    def jobs(self):
        raw=self.command(['/usr/bin/systemctl','list-jobs','--no-legend','--no-pager','--plain'])
        jobs=[]
        for line in raw.splitlines():
            parts=line.split()
            require(len(parts)==4 and parts[0].isdigit() and
                    re.fullmatch(r'[A-Za-z0-9_.@\\:-]+',parts[1]) and
                    parts[3] in ('waiting','running'),'JOBS_FORMAT_UNKNOWN')
            jobs.append({'id':int(parts[0]),'unit':parts[1],'type':parts[2],'state':parts[3]})
        return jobs
    def links(self):
        rows={}; count=0
        for root in ('/etc/systemd/system','/run/systemd/system'):
            for base,dirs,files in os.walk(root,followlinks=False):
                count+=len(dirs)+len(files); require(count<=10000,'LINK_SCAN_LIMIT')
                for name in dirs+files:
                    p=Path(base)/name
                    if p.is_symlink() and (name in TARGETS or p.resolve().name in TARGETS):
                        target=os.readlink(p)
                        require(str(p.parent) in ('/etc/systemd/system/multi-user.target.wants',
                                '/etc/systemd/system/timers.target.wants') and name in TARGETS
                                and Path(target).name==name,'UNEXPECTED_ENABLEMENT_LINK')
                        rows[str(p)]=target
        return rows
    def config(self,rows):
        hashes={}; props={}
        for u,row in rows.items():
            require(set(STATIC+('ActiveState','UnitFileState'))<=set(row),'SYSTEMD_PROPERTY_MISSING')
            require(row['LoadState']=='loaded' and row['NeedDaemonReload']=='no','LOAD_OR_CONFIG_PENDING')
            props[u]={k:row[k] for k in STATIC}
            for path in filter(None,[row['FragmentPath']]+row['DropInPaths'].split()):
                require(path.startswith(('/etc/systemd/','/run/systemd/','/usr/lib/systemd/','/lib/systemd/')),'UNIT_PATH_UNEXPECTED')
                hashes[path]=hashlib.sha256(bounded(path)).hexdigest()
        for u in TARGETS:
            r=rows[u]
            require(r['Names'].split()==[u] and r['RefuseManualStop']=='no','UNIT_ALIAS_OR_STOP_REFUSED')
            if u.endswith('.service'):
                require('ExecStop' in r and 'ExecStopPost' in r,'STOP_HOOK_PROPERTY_MISSING')
                require(not r['ExecStop'].strip() and not r['ExecStopPost'].strip(),'STOP_HOOK_UNEXPECTED')
            for key in ('FailureAction','SuccessAction','JobTimeoutAction'):
                require(r[key]=='none','SYSTEM_ACTION_UNEXPECTED')
            require(not r['OnFailure'] and not r['OnSuccess'],'FAILURE_HANDLER_UNEXPECTED')
            for key in ('Requires','Requisite','Wants'):
                require(set(r[key].split())<=set(TARGETS+BASE+('docker.service',)),'DEPENDENCY_UNEXPECTED')
            for key in ('BindsTo','PartOf','RequiredBy','RequisiteOf','BoundBy','ConsistsOf',
                        'PropagatesStopTo','StopPropagatedFrom','TriggeredBy'):
                require(set(r[key].split())<=set(TARGETS),'STOP_OR_ACTIVATION_DEPENDENCY_UNEXPECTED')
            require(set(r['Triggers'].split())<=({'synthetic-watch-healthcheck.service'} if u.endswith('.timer') else set()),'TRIGGER_UNEXPECTED')
        for u in BASE+('docker.service',): require(rows[u]['StopWhenUnneeded']=='no','UPSTREAM_STOP_WHEN_UNNEEDED')
        return {'hashes':hashes,'properties':props}
    def local_idle(self):
        p=Path(self.root)
        require(str(p).startswith('/opt/synthetic-site/') and p.is_dir(),'OBSERVER_ROOT_UNKNOWN')
        for x in [p,p/'jobs',p/'jobs'/'pending',p/'jobs'/'running']:
            require(x.is_dir() and not x.is_symlink(),'OBSERVER_QUEUE_UNKNOWN')
        for state in ('pending','running'):
            require(next((p/'jobs'/state).iterdir(),None) is None,'OBSERVER_WORK_PRESENT')
    def runtime_bindings(self,rows):
        bindings={}
        for unit in TARGETS[:4]:
            row=rows[unit]; pid=int(row['MainPID'])
            require(pid>0 and row['ActiveState']=='active','RUNTIME_BINDING_UNKNOWN')
            env=dict(x.split(b'=',1) for x in bounded('/proc/%d/environ'%pid).split(b'\0') if b'=' in x)
            out={}
            if unit in TARGETS[:2]:
                dsn=urlsplit(env.get(b'SYNTHETIC_LAB_DATABASE_URL',b'').decode())
                require(dsn.hostname=='database.synthetic.invalid'
                        and dsn.path=='/syntheticdb' and dsn.username=='synthetic_worker','DATABASE_BINDING_MISMATCH')
                key='SYNTHETIC_LAB_WORKER_ID' if unit==TARGETS[0] else 'SYNTHETIC_LAB_CONTROL_BRIDGE_ID'
                value=env.get(key.encode(),b'').decode()
                out['id']=value or (socket.gethostname()+':'+str(pid) if unit==TARGETS[0]
                                   else 'synthetic-control-bridge-'+socket.gethostname())
                require(re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',out['id']),'ID_FORMAT_UNKNOWN')
                out['prod_binding_matches']=True
            else:
                root=env.get(b'SYNTHETIC_LAB_OBSERVER_STATE_ROOT',b'/opt/synthetic-site/synthetic-lab-observer').decode()
                argv=bounded('/proc/%d/cmdline'%pid).decode().split('\0')
                for i,arg in enumerate(argv):
                    if arg=='--state-root': root=argv[i+1]
                    if arg.startswith('--state-root='): root=arg.split('=',1)[1]
                require(root==self.root,'EFFECTIVE_OBSERVER_ROOT_MISMATCH')
                out['observer_root']=root
            group=row['ControlGroup']; require(group.startswith('/system.slice/'),'CGROUP_UNKNOWN')
            cg=Path('/sys/fs/cgroup')/group.lstrip('/')
            require((cg/'cgroup.procs').is_file(),'CGROUP_V2_REQUIRED')
            pids=set(); count=0
            for f in cg.rglob('cgroup.procs'):
                count+=1; require(count<=64,'CGROUP_SCAN_LIMIT')
                pids.update(int(x) for x in bounded(f).split())
            require(pids=={pid},'CHILD_PROCESS_OR_RUNTIME_CHANGED')
            require(int(self.command(['/usr/bin/systemctl','show','--value','--property=MainPID',unit]).strip())==pid,
                    'PID_CHANGED_DURING_READ')
            bindings[unit]=out
        return bindings
    def snapshot(self,preflight=False):
        rows=self.units(); config=self.config(rows)
        require(rows['synthetic-watch-healthcheck.service']['ActiveState'] in ('inactive','failed'),'WATCH_HEALTHCHECK_ACTIVE')
        value={'identity':identity(),'boot':boot(),'config':config,'links':self.links(),'jobs':self.jobs(),
               'states':{u:rows[u]['ActiveState'] for u in TARGETS},
               'enabled':{u:rows[u]['UnitFileState'] for u in TARGETS},
               'preserved':{u:{k:rows[u][k] for k in ('ActiveState','SubState','ExecMainCode','ExecMainStatus','InvocationID','Result')}
                            for u in PRESERVE}}
        if preflight:
            self.local_idle(); value['bindings']=self.runtime_bindings(rows)
            require(all(row['UnitFileState'] in ('enabled','disabled','static') for u,row in rows.items() if u in TARGETS),'ENABLEMENT_UNEXPECTED')
        return value
    def preflight(self): return self.snapshot(True)
    def observe(self): return self.snapshot(False)
    def stop_once(self):
        self.command(['/usr/bin/systemctl','--no-block','--job-mode=fail','stop',*TARGETS],limit=3)
    def wait_stopped(self,seconds):
        end=min(self.deadline,time.monotonic()+seconds)
        while time.monotonic()<end:
            current=self.observe()
            require(all(j['unit'] in TARGETS and j['type']=='stop' for j in current['jobs']),'UNEXPECTED_JOB_DURING_STOP')
            if not current['jobs'] and all(current['states'][u] in ('inactive','failed') for u in TARGETS): return current
            time.sleep(min(.2,max(0,end-time.monotonic())))
        raise Refused('STOP_JOBS_OR_STATE_UNKNOWN')
    def assert_stopped(self):
        rows=self.units()
        require(not self.jobs() and all(rows[u]['ActiveState'] in ('inactive','failed') for u in TARGETS),'STOP_STATE_CHANGED')
    def unlink_exact(self,path,target): exact_link(path,target)
    def restore_exact(self,path,target): exact_link(path,target,restore=True)
    def reload_once(self): self.command(['/usr/bin/systemctl','daemon-reload'],limit=3)
