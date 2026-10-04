#!/usr/bin/env python3
"""Linux-only fixed-host phases, each supervised as a separate process group.

No cloud APIs. A timeout kills clients, NOT systemd manager jobs. Always reconcile.
The off-host operator must enforce the independent power cutoff as well.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import ctypes

CAPS={'prepare':45,'export':15,'copy':15,'apply':65,'reconcile':20,'restore':35,'recover':20,'diagnostic':20}
ROOT='/nonexistent/synthetic-maint-v3'
STOP_RESERVE=180

def parent_death_kill(expected_parent):
    """Linux per-process containment, not a permission or system policy change."""
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=expected_parent:
        os._exit(125)

def package_sha():
    h=hashlib.sha256()
    for name in ('runner.py','protocol.py','durable.py','guest.py'):
        h.update(name.encode()+b'\0'+(Path(__file__).parent/name).read_bytes())
    return h.hexdigest()

def supervise(command,budget):
    """All actual operations run in the child. Returns by budget+1s if OS schedules.

    Non-interruptible kernel I/O may outlive SIGKILL. Never claim it was cancelled;
    the retained flock and independent parent Stop contain that unresolved case.
    """
    if os.name!='posix': return {'state':'REFUSED','reason':'LINUX_REQUIRED'}
    start=time.monotonic()
    parent=os.getpid()
    p=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True,
                       preexec_fn=lambda:parent_death_kill(parent))
    try:
        raw,_=p.communicate(timeout=max(.01,budget-(time.monotonic()-start)))
    except subprocess.TimeoutExpired:
        try: os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        try: p.communicate(timeout=.5); exited=True
        except subprocess.TimeoutExpired: exited=False
        return {'state':'UNKNOWN','reason':'PROCESS_DEADLINE','worker_exit_observed':exited,
                'requires_reconciliation':True,'systemd_jobs_cancelled':False}
    if p.returncode != 0 or len(raw)>1024*1024:
        return {'state':'UNKNOWN','reason':'WORKER_EXIT_OR_OUTPUT','requires_reconciliation':True}
    try: result=json.loads(raw)
    except Exception: return {'state':'UNKNOWN','reason':'WORKER_OUTPUT_UNKNOWN','requires_reconciliation':True}
    return result

def worker(args):
    # Imported only inside the supervised child: FS imports also consume its budget.
    from protocol import Protocol, require, digest, encoded, copy_gate
    from durable import Store, read_bytes, verified_copy, write_once
    from guest import Guest, identity, boot
    deadline=time.monotonic()+args.budget
    request=json.loads(read_bytes(args.request))
    require(type(request) is dict,'REQUEST_SHAPE')
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{5,63}',args.operation),'OPERATION_ID')
    require(re.fullmatch(r'https://github.com/example-owner/example-repository/actions/runs/[0-9]+',args.run_url),'RUN_URL')
    if args.mode=='copy':
        require(set(request)=={'manifest','backup_directory','expected_baseline_sha'},'COPY_REQUEST')
        m=request['manifest']
        require(digest(m)==request['expected_baseline_sha'] and m['operation']==args.operation,'COPY_PIN')
        receipt=verified_copy(request['backup_directory'],encoded(m),identity())
        return {'state':'OFFHOST_COPY_VERIFIED','receipt':receipt}
    require(socket.gethostname()=='synthetic-compute' and os.geteuid()==0,'GUEST_HOST_OR_UID')
    require(not os.path.lexists('/nonexistent/synthetic-maint-v2/mutation-intent.json'),'PRIOR_V2_MUTATION_RECONCILE_ONLY')
    s=Store(ROOT,args.operation,create=args.mode in ('prepare','recover'))
    g=Guest(deadline,request.get('observer_root','/nonexistent/synthetic-school/synthetic-lab-observer'))
    if args.mode=='prepare':
        require(set(request)<= {'observer_root'},'PREPARE_REQUEST')
        require(not s.attempted(),'PRIOR_ATTEMPT_RECONCILE_ONLY')
        m=g.preflight()
        require(not m['jobs'],'BASELINE_JOBS_PENDING')
        m.update(version=2,operation=args.operation,run_url=args.run_url,package_sha=package_sha(),
                 observer_root=g.root,prepared_at=time.time())
        s.once('baseline',m)
        return {'state':'PREPARED_NO_MUTATION','manifest':m,'baseline_sha':digest(m)}
    if args.mode=='recover':
        require(set(request)=={'manifest','copy_receipt'},'RECOVER_REQUEST')
        m=request['manifest']; copy_gate(request['copy_receipt'],m)
        require(m['identity']==identity() and m['operation']==args.operation,'RECOVER_IDENTITY')
        # Evidence recovery is explicit, never an apply replay; retains damaged originals.
        if not s.attempted(): s.claim({'operation':args.operation,'baseline_sha':digest(m)})
        claim=json.loads(read_bytes(Path(ROOT)/'mutation-intent.json'))
        require(claim=={'operation':args.operation,'baseline_sha':digest(m)},'GLOBAL_CLAIM_CONFLICT')
        s.ensure('baseline-recovery',m)
        return {'state':'BASELINE_RECOVERED_RECONCILE_REQUIRED','baseline_sha':digest(m)}
    require('baseline_sha' in request,'BASELINE_PIN_REQUIRED')
    m=None
    for name in ('baseline','baseline-recovery'):
        path=s.directory/(name+'.json')
        if path.exists():
            try: candidate=json.loads(read_bytes(path))
            except (ValueError,UnicodeError): continue
            if digest(candidate)==request['baseline_sha']: m=candidate; break
    require(m is not None and m['operation']==args.operation and m['identity']==identity(),'BASELINE_PIN_MISMATCH')
    require(m['version']==2 and m['package_sha']==package_sha(),'PACKAGE_DRIFT')
    g.root=m['observer_root']
    p=Protocol(m,s,g,time.time)
    if args.mode=='export':
        return {'state':'BASELINE_EXPORTED','manifest':m,'baseline_sha':digest(m)}
    if args.mode=='apply':
        require(set(request)=={'baseline_sha','copy_receipt','queue_receipt'},'APPLY_REQUEST')
        require(m['run_url']==args.run_url and m['boot']==boot(),'APPLY_WINDOW_CHANGED')
        return p.apply(request['copy_receipt'],request['queue_receipt'])
    if args.mode=='restore':
        require(set(request)=={'baseline_sha','copy_receipt'},'RESTORE_REQUEST')
        return p.restore(request['copy_receipt'])
    if args.mode=='reconcile':
        require(set(request)=={'baseline_sha'},'RECONCILE_REQUEST')
        return p.reconcile()
    if args.mode=='diagnostic':
        require(set(request)=={'baseline_sha'},'DIAGNOSTIC_REQUEST')
        return diagnostics(g)
    raise RuntimeError('MODE_UNREACHABLE')

def diagnostics(g):
    rows=g.units(); uv=rows['synthetic-video-container.service']
    typed=[]
    for entry in re.findall(r'\{(.*?)\}',uv.get('ExecStartPre','')):
        item={}
        for key in ('path','code','status','ignore_errors','pid'):
            match=re.search(r'(?:^|;\s*)'+key+r'=([^;]+)',entry.strip())
            if match:item[key]=match.group(1).strip()
        typed.append(item)
    containers={}
    fmt=('{{json .Id}} {{json .State.Status}} {{.State.Running}} {{.State.ExitCode}} '
         '{{.State.OOMKilled}} {{.RestartCount}} {{ne .State.Error ""}}')
    for name in ('synthetic-dds-runtime','synthetic-video-container'):
        try: containers[name]=g.command(['/usr/bin/docker','inspect','--format',fmt,name])
        except Exception: containers[name]={'state':'INSPECT_UNAVAILABLE'}
    raw=g.command(['/usr/bin/journalctl','-u','synthetic-video-container.service','-n','15','--no-pager','-o','json'])
    codes=set()
    for line in raw.splitlines():
        text=str(json.loads(line).get('MESSAGE','')).lower()
        for phrase,code in (('no such file','NOT_FOUND'),('permission denied','PERMISSION_DENIED'),
                            ('failed at step','SYSTEMD_STEP_FAILURE'),('invalid mount','INVALID_MOUNT')):
            if phrase in text:codes.add(code)
    rules={}
    for root in ('/etc/tmpfiles.d','/run/tmpfiles.d','/usr/lib/tmpfiles.d'):
        for path in Path(root).glob('*.conf'):
            from guest import bounded
            raw=bounded(path)
            if b'/run/synthetic-school' in raw: rules[str(path)]=hashlib.sha256(raw).hexdigest()
    clients={}
    import stat
    for name in ('/usr/bin/psql','/usr/bin/python3','/nonexistent/synthetic-school/synthetic-lab/.venv/bin/python'):
        path=Path(name)
        if path.exists():
            resolved=path.resolve();st=resolved.stat()
            clients[name]={'exists':True,'resolved':str(resolved),'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode)),'regular':stat.S_ISREG(st.st_mode)}
        else:clients[name]={'exists':False}
    return {'state':'DIAGNOSTICS_ONLY','client_metadata':clients,'uv_pre':typed,'uv_status':{k:uv.get(k) for k in
            ('ExecMainCode','ExecMainStatus','InvocationID','Result','ActiveState','SubState')},
            'containers':containers,'uv_error_classes':sorted(codes),'tmpfiles_hashes':rules,
            'run_bridge_school_exists':Path('/run/synthetic-school').exists()}

def main():
    a=argparse.ArgumentParser()
    a.add_argument('mode',choices=CAPS)
    a.add_argument('--operation',required=True)
    a.add_argument('--request',required=True)
    a.add_argument('--run-url',required=True)
    a.add_argument('--start-epoch',required=True,type=float)
    a.add_argument('--internal-worker',action='store_true',help=argparse.SUPPRESS)
    a.add_argument('--budget',type=float,help=argparse.SUPPRESS)
    args=a.parse_args()
    if args.internal_worker:
        # Kernel-delivered default signal also covers Python file reads/scans/fsync.
        signal.signal(signal.SIGALRM,signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL,min(args.budget,CAPS[args.mode]))
        try: result=worker(args)
        except Exception as exc:
            from protocol import Refused
            result={'state':'UNKNOWN','reason':str(exc) if isinstance(exc,Refused) else type(exc).__name__,
                    'requires_reconciliation':True}
        print(json.dumps(result,sort_keys=True)); return
    if os.name!='posix':
        print('{"state":"REFUSED","reason":"LINUX_REQUIRED"}'); return
    cap=CAPS[args.mode]
    elapsed=time.time()-args.start_epoch
    if args.mode=='apply' and elapsed>300:
        print('{"state":"REFUSED","reason":"APPLY_ADMISSION_CLOSED"}'); return
    if not 0<=elapsed<=600 or 600-elapsed<cap+STOP_RESERVE:
        print('{"state":"REFUSED","reason":"POWER_RESERVE_INSUFFICIENT"}'); return
    command=[sys.executable,str(Path(__file__).resolve()),args.mode,'--operation',args.operation,
             '--request',args.request,'--run-url',args.run_url,'--start-epoch',str(args.start_epoch),
             '--internal-worker','--budget',str(cap)]
    result=supervise(command,cap)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__': main()
