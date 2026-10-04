"""Pure fail-closed policy. SSH_READY is not a provider Running assertion."""
import datetime as dt
import math
import re

REPO = 'example-owner/example-repository'
BRANCH = 'review/synthetic-power-contract'
SHA = '1111111111111111111111111111111111111111'
WORKFLOW = 42
PATH = '.github/workflows/synthetic-power-probe.yml'
HOST = 'synthetic-compute'
NAMES = {'contract', 'probe', 'oracle-probe', 'trial-start', 'manual-console-trial', 'trial-stop'}


class Refused(Exception):
    pass


def require(test, reason):
    if not test:
        raise Refused(reason)


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def epoch(text):
    require(isinstance(text, str) and text.endswith('Z'), 'INVALID_TIMESTAMP')
    try:
        value = dt.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        raise Refused('INVALID_TIMESTAMP')
    require(finite(value), 'INVALID_TIMESTAMP')
    return value


def validate_run(run, now, earliest):
    require(isinstance(run, dict), 'RUN_SCHEMA')
    require(type(run.get('id')) is int and run['id'] > 0, 'RUN_ID')
    require(run.get('repository', {}).get('full_name') == REPO, 'REPOSITORY')
    require(run.get('head_repository', {}).get('full_name') == REPO, 'HEAD_REPOSITORY')
    require(run.get('head_branch') == BRANCH and run.get('head_sha') == SHA, 'BRANCH_OR_SHA')
    require(run.get('workflow_id') == WORKFLOW and run.get('path') == PATH, 'WORKFLOW')
    require(run.get('event') == 'workflow_dispatch' and run.get('run_attempt') == 1, 'EVENT_OR_RERUN')
    require(run.get('html_url') == f'https://github.com/{REPO}/actions/runs/{run["id"]}', 'RUN_URL')
    created = epoch(run.get('created_at'))
    require(earliest <= created <= now and now - created < 120, 'RUN_TIME')
    require(run.get('status') in ('queued', 'in_progress', 'requested', 'waiting', 'pending'), 'RUN_TERMINAL')
    require(run.get('conclusion') is None, 'RUN_CONCLUSION')
    return created


def select_run(page, earliest, now, bound=None):
    require(isinstance(page, dict) and isinstance(page.get('workflow_runs'), list), 'RUN_LIST_SCHEMA')
    rows = page['workflow_runs']
    require(type(page.get('total_count')) is int and page['total_count'] == len(rows) and len(rows) < 100, 'RUN_PAGINATION_OR_TRUNCATION')
    fresh = [r for r in rows if epoch(r.get('created_at')) >= earliest]
    require(len({r.get('id') for r in fresh}) == len(fresh), 'DUPLICATE_RUN_ID')
    require(len(fresh) <= 1, 'AMBIGUOUS_RUN')
    if not fresh:
        require(bound is None, 'BOUND_RUN_DISAPPEARED')
        return None
    run = fresh[0]
    t0 = validate_run(run, now, earliest)
    if bound is not None:
        require(run['id'] == bound['id'] and t0 == bound['t0'], 'BOUND_RUN_CHANGED')
    return {'id': run['id'], 't0': t0, 'url': run['html_url']}


def validate_jobs(page, run_id):
    require(isinstance(page, dict) and isinstance(page.get('jobs'), list), 'JOBS_SCHEMA')
    jobs = page['jobs']
    require(page.get('total_count') == len(jobs) and len(jobs) <= 6, 'JOB_PAGINATION_OR_COUNT')
    require(all(isinstance(j, dict) for j in jobs), 'JOB_SCHEMA')
    names = [j.get('name') for j in jobs]
    require(len(set(names)) == len(names) and set(names) <= NAMES, 'UNKNOWN_OR_DUPLICATE_JOB')
    for j in jobs:
        require(j.get('run_id') == run_id and type(j.get('id')) is int and j['id'] > 0, 'JOB_IDENTITY')
    if set(names) != NAMES:
        return None  # GitHub has not materialized all jobs yet; never admit.
    by = {j['name']: j for j in jobs}
    for name in ('probe', 'oracle-probe', 'trial-start', 'trial-stop'):
        j = by[name]
        require(j.get('status') == 'completed' and j.get('conclusion') == 'skipped', 'OTHER_JOB_NOT_SKIPPED')
    contract = by['contract']
    if contract.get('status') != 'completed':
        return None
    require(contract.get('conclusion') == 'success', 'CONTRACT_FAILED')
    job = by['manual-console-trial']
    require(job.get('status') != 'completed' and job.get('conclusion') is None, 'MANUAL_JOB_TERMINAL')
    if job.get('status') != 'in_progress':
        return None
    require(job.get('html_url') == f'https://github.com/{REPO}/actions/runs/{run_id}/job/{job["id"]}', 'JOB_URL')
    return {'id': job['id'], 'url': job['html_url']}


def validate_boot(sample, t0, previous=None):
    require(isinstance(sample, dict), 'SSH_SCHEMA')
    require(set(sample) == {'hostname', 'username', 'uid', 'root_uid', 'boot_id', 'uptime', 'guest_epoch', 'sent', 'received'}, 'SSH_FIELDS')
    require(sample['hostname'] == HOST and sample['username'] == 'ubuntu' and type(sample['uid']) is int and sample['uid'] > 0 and sample['root_uid'] == 0, 'SSH_IDENTITY')
    require(isinstance(sample['boot_id'], str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', sample['boot_id']), 'BOOT_ID')
    require(all(finite(sample[k]) for k in ('uptime', 'guest_epoch', 'sent', 'received')), 'SSH_TIME_TYPE')
    lo, hi, up = sample['sent'], sample['received'], sample['uptime']
    require(t0 <= lo <= hi and hi-lo <= 6 and 0 < up < 120, 'SSH_RTT_OR_UPTIME')
    require(lo-2 <= sample['guest_epoch'] <= hi+2, 'GUEST_CLOCK_SKEW')
    # Guest clock cannot make an old boot appear fresh. /proc/uptime is mapped
    # to a conservative local interval, widened 50ms for reported precision.
    boot_lo, boot_hi = lo-up-0.05, hi-up+0.05
    require(boot_lo >= t0 and boot_hi <= hi, 'OLD_OR_UNCERTAIN_BOOT')
    require(hi-t0 < 120, 'ADMISSION_EXPIRED')
    if previous is not None:
        require(previous['boot_id'] == sample['boot_id'], 'BOOT_CHANGED')
        require(lo >= previous['received'] and up > previous['uptime'], 'UPTIME_NOT_INCREASING')
        delta = up-previous['uptime']
        require(max(0,lo-previous['received']-0.1) <= delta <= hi-previous['sent']+0.1, 'UPTIME_INCONSISTENT')
    return sample

"""One-shot Oracle watcher. No Start/Stop API, no token or private-key reads."""
import argparse
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request


BASE = Path('/nonexistent/synthetic-storage-identity')
SUPERVISOR = BASE / 'supervisor.py'
SUPERVISOR_SHA = '0155d84f0a80a5d86f93d86eb73082f857ac25e6ad6202ffba24cd2b4f120190'
CLAIM = Path('/nonexistent/synthetic-storage-identity') / 'autonomous-admission.claim.json'
RECEIPT = Path('/nonexistent/synthetic-storage-identity') / 'autonomous-admission.receipt.json'
BOOTSTRAP = ('import ctypes,os,signal,sys; r=ctypes.CDLL(None).prctl(1,signal.SIGKILL); '
             'os._exit(125) if r!=0 or os.getppid()!=int(sys.argv[1]) else None; '
             'os.execv(sys.argv[2],sys.argv[2:])')


def canonical(obj):
    return (json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def durable_once(path, obj):
    """Global O_EXCL claim: crash/partial/any existing record blocks restart."""
    raw = canonical(obj)
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(raw); out.flush(); os.fsync(out.fileno())
    fd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    require(path.read_bytes() == raw, 'DURABLE_READBACK')
    return hashlib.sha256(raw).hexdigest()


class Budget:
    def __init__(self, clock, arm=300):
        self.clock = clock
        self.wall_start, self.mono_start = clock.wall(), clock.mono()
        self.earliest = math.ceil(self.wall_start)
        self.wall_end, self.mono_end = self.wall_start+arm, self.mono_start+arm
        self.bound = None
        self.executing = False

    def bind(self, run):
        require(self.bound is None, 'DUPLICATE_BIND')
        self.bound = run
        elapsed = self.clock.wall()-run['t0']
        require(0 <= elapsed < 120, 'ADMISSION_EXPIRED')
        self.mono_t0 = self.clock.mono()-elapsed
        self.wall_end, self.mono_end = run['t0']+120, self.mono_t0+120

    def start(self):
        require(self.bound is not None and not self.executing and self.left() > 0, 'ADMISSION_EXPIRED')
        self.executing = True
        self.wall_end, self.mono_end = self.bound['t0']+270, self.mono_t0+270

    def left(self):
        drift = abs((self.clock.wall()-self.wall_start)-(self.clock.mono()-self.mono_start))
        require(drift <= 2, 'LOCAL_CLOCK_JUMP')
        return min(self.wall_end-self.clock.wall(), self.mono_end-self.clock.mono())

    def check(self, reserve=0):
        require(self.left() > reserve, 'DEADLINE_OR_INSUFFICIENT_BUDGET')


class Clock:
    wall = staticmethod(time.time)
    mono = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refused('HTTP_REDIRECT')


class GitHub:
    def __init__(self, budget, audit=lambda **kw: None):
        self.budget, self.calls, self.audit = budget, 0, audit

    def get(self, tail):
        self.budget.check(5)
        require(self.calls < 40, 'HTTP_CALL_BUDGET')
        require(self.budget.bound is not None or self.calls < 30, 'DISCOVERY_BUDGET_RESERVE')
        self.calls += 1
        endpoint_class = 'run_list' if tail.startswith('workflows/') else 'jobs'
        self.audit(phase='request_started', call=self.calls, endpoint_class=endpoint_class)
        url = f'https://api.github.com/repos/{REPO}/actions/'+tail
        # A killable helper caps DNS + redirects + response body together.
        # urllib's socket timeout alone would not cap a slow-drip response.
        helper = '''import json,sys,time,urllib.request,urllib.error
started=time.monotonic()
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('REDIRECT')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
request=urllib.request.Request(sys.argv[1],headers={'Accept':'application/vnd.github+json','User-Agent':'bounded-admission-readonly','Cache-Control':'no-cache','X-GitHub-Api-Version':'2022-11-28'},method='GET')
meta={'status':None,'bytes':0,'remaining':None,'reset':None,'pagination':False}
try:
 with opener.open(request,timeout=3) as r:
  meta.update(status=r.status,remaining=r.headers.get('X-RateLimit-Remaining'),reset=r.headers.get('X-RateLimit-Reset'),pagination='rel="next"' in r.headers.get('Link',''))
  raw=r.read(1048577);meta['bytes']=len(raw)
  obj=json.loads(raw) if len(raw)<=1048576 else None
except urllib.error.HTTPError as e:
 meta.update(status=e.code,remaining=e.headers.get('X-RateLimit-Remaining'),reset=e.headers.get('X-RateLimit-Reset'));obj=None
except Exception as e:
 meta['error']=type(e).__name__;obj=None
meta['elapsed']=round(time.monotonic()-started,3)
print(json.dumps({'meta':meta,'data':obj},separators=(',',':')))

'''
        result = subprocess.run(child_argv(['/usr/bin/python3','-I','-B','-u','-c',helper,url]),capture_output=True,timeout=4,start_new_session=True)
        require(result.returncode == 0 and len(result.stdout)<=2097152,'HTTP_HELPER_FAILED_OR_SIZE')
        self.budget.check()
        envelope=json.loads(result.stdout)
        meta=envelope.get('meta',{})
        self.audit(phase='response_metadata', call=self.calls, endpoint_class=endpoint_class,
                   status=meta.get('status') if type(meta.get('status')) is int else None,
                   remaining=int(meta['remaining']) if isinstance(meta.get('remaining'),str) and meta['remaining'].isdigit() else None,
                   reset=int(meta['reset']) if isinstance(meta.get('reset'),str) and meta['reset'].isdigit() else None)
        # Allowlisted scalars only, never HTTP body, headers or stderr.
        safe={k:meta.get(k) for k in ('status','bytes','remaining','reset','pagination','elapsed','error')}
        detail=json.dumps({'endpoint':tail.split('?')[0],**safe},sort_keys=True)
        require(meta.get('status')==200 and not meta.get('pagination') and type(meta.get('bytes')) is int and meta['bytes']<=1048576 and 'error' not in meta,'HTTP_RESPONSE '+detail)
        remaining=meta.get('remaining')
        require(isinstance(remaining,str) and remaining.isdigit(),'HTTP_QUOTA_METADATA '+detail)
        # Reserve the full remaining process budget plus the 3-call floor,
        # before the first response can produce WATCHER_READY.
        floor=42 if self.calls==1 else 3
        require(int(remaining)>=floor,'HTTP_QUOTA_INSUFFICIENT '+detail)
        return envelope['data']

    def runs(self):
        date = dt.datetime.fromtimestamp(self.budget.earliest, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        query = urllib.parse.urlencode({'branch':BRANCH,'event':'workflow_dispatch','created':'>='+date,'per_page':100})
        return self.get(f'workflows/{WORKFLOW}/runs?'+query)

    def jobs(self, run_id):
        return self.get(f'runs/{run_id}/jobs?filter=latest&per_page=100')


def child_argv(argv):
    return ['/usr/bin/python3','-I','-B','-u','-c',BOOTSTRAP,str(os.getpid()),*argv]


def ssh_snapshot(budget):
    budget.check(7)
    # Only system interpreter and scalar observations; never installer/venv/env.
    remote = ("import os,pwd,socket,time,json,pathlib,subprocess; "
              "r=subprocess.run(['/usr/bin/sudo','-n','/usr/bin/id','-u'],capture_output=True,text=True,timeout=1,check=True); "
              "print(json.dumps({'hostname':socket.gethostname(),'username':pwd.getpwuid(os.getuid()).pw_name,'uid':os.getuid(),'root_uid':int(r.stdout.strip()),'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'uptime':float(pathlib.Path('/proc/uptime').read_text().split()[0]),'guest_epoch':time.time()}))")
    import shlex
    command = shlex.join(['/usr/bin/timeout','--signal=TERM','--kill-after=1s','4s','/usr/bin/python3','-I','-B','-c',remote])
    argv = ['/usr/bin/ssh','-F','/dev/null','-i','/nonexistent/synthetic-key','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlgorithms=ssh-ed25519','-o','UserKnownHostsFile=/nonexistent/synthetic-hosts','-o','ConnectTimeout=3','-o','ConnectionAttempts=1','-o','ForwardAgent=no','ubuntu@192.0.2.1',command]
    sent = budget.clock.wall()
    try:
        proc = subprocess.run(child_argv(argv), capture_output=True, timeout=6, start_new_session=True)
    except subprocess.TimeoutExpired:
        return None
    received = budget.clock.wall()
    budget.check()
    require(len(proc.stdout)<=2048 and len(proc.stderr)<=8192, 'SSH_OUTPUT_SIZE')
    if proc.returncode:
        if b'Host key verification failed' in proc.stderr or b'REMOTE HOST IDENTIFICATION HAS CHANGED' in proc.stderr:
            raise Refused('SSH_HOST_KEY')
        require(proc.returncode == 255, 'SSH_COMMAND_FAILED')
        return None  # Boot connectivity can be pending; hard admission still runs.
    value = json.loads(proc.stdout)
    require(isinstance(value,dict), 'SSH_SCHEMA')
    value.update(sent=sent,received=received)
    return value


class Receiver:
    def __init__(self, nonce):
        self.events = queue.Queue(maxsize=64)
        self.proc = subprocess.Popen(child_argv(['/usr/bin/python3','-I','-B','-u',str(SUPERVISOR),'--mode','live','--nonce',nonce,'--arm-seconds','300']), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
        def read():
            total=0
            while True:
                line=self.proc.stdout.readline(1048577)
                if not line:
                    self.events.put({'state':'RECEIVER_EOF'});return
                total+=len(line)
                if total>2097152:
                    self.events.put({'state':'RECEIVER_OUTPUT_LIMIT'});return
                try:obj=json.loads(line)
                except (ValueError,UnicodeError):obj={'state':'RECEIVER_BAD_OUTPUT'}
                self.events.put(obj)
        threading.Thread(target=read,daemon=True).start()

    def send(self, packet):
        self.proc.stdin.write(canonical(packet));self.proc.stdin.flush()

    def event(self, timeout):
        try:return self.events.get(timeout=timeout)
        except queue.Empty:return None

    def close(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid,signal.SIGKILL)
        self.proc.wait(timeout=2)


class Engine:
    """Dependency-injected orchestration shared with fake end-to-end tests."""
    def __init__(self, clock, api, probe, receiver, nonce, claim, receipt, emit, timer=lambda:None, budget=None):
        self.clock, self.api, self.probe, self.receiver = clock, api, probe, receiver
        self.nonce, self.claim, self.receipt, self.emit, self.timer = nonce, claim, receipt, emit, timer
        self.budget = budget or Budget(clock)
        self.bound=None
        self.claimed=False
        self.sent_running=False

    def event(self, expected, cap=3):
        end=self.clock.mono()+cap
        while self.clock.mono()<end:
            self.budget.check()
            e=self.receiver.event(min(0.1,end-self.clock.mono()))
            if e is None:continue
            self.emit('SUPERVISOR',result=e)
            require(e.get('nonce')==self.nonce and e.get('simulation') is False,'RECEIVER_IDENTITY')
            require(e.get('state')==expected,'RECEIVER_UNEXPECTED_STATE')
            return e
        raise Refused('RECEIVER_ACK_TIMEOUT')

    def current(self):
        r=select_run(self.api.runs(),self.budget.earliest,self.clock.wall(),self.bound)
        return r

    def execute(self):
        reason='UNKNOWN';success=False
        try:
            self.event('RECEIVER_READY',10)
            self.emit('WATCHER_READY',earliest_run_epoch=self.budget.earliest,arm_expires_at=self.budget.wall_end)
            while self.bound is None:
                self.budget.check(5)
                run=self.current()
                if run is None:self.clock.sleep(min(8,self.budget.left()));continue
                self.budget.bind(run);self.timer()
                self.claim({'state':'UNRESOLVED','nonce':self.nonce,'run':run,'sha':SHA,'at':self.clock.wall()})
                self.claimed=True;self.bound=run
                packet={'type':'BIND','nonce':self.nonce,'simulation':False,'run_url':run['url'],'sha':SHA,'t0':run['t0']}
                self.receiver.send(packet);self.event('BOUND')
                self.emit('RUN_BOUND',run=run)
            # Cache validated job identity during read-only SSH boot waiting.
            # Four checkpoint pairs plus one final pair fit the reserved ten GETs.
            job=None
            for checkpoint in range(4):
                self.budget.check(15)
                self.current()
                job=validate_jobs(self.api.jobs(self.bound['id']),self.bound['id'])
                if job is not None:break
                if checkpoint<3:self.clock.sleep(4)
            require(job is not None,'JOB_CHECKPOINT_BUDGET_EXHAUSTED')
            first=None
            while True:
                self.budget.check(15)
                sample=self.probe(self.budget)
                if sample is None:self.clock.sleep(3);continue
                validate_boot(sample,self.bound['t0'],first)
                if first is None:first=sample;self.clock.sleep(1);continue
                # Refresh immutable run and selected jobs AFTER the second SSH.
                self.current()
                again=validate_jobs(self.api.jobs(self.bound['id']),self.bound['id'])
                require(again==job,'JOB_CHANGED_AFTER_SSH')
                require(self.clock.wall()-sample['received']<=10,'SSH_EVIDENCE_STALE')
                self.budget.check(8)
                self.emit('SSH_READY_EVIDENCE',run_id=self.bound['id'],boot_id=sample['boot_id'],observed_at=sample['received'],provider_running_asserted=False)
                # Adapter fields keep the unchanged supervisor wire schema.
                # accepted_at is conservative run creation, NOT provider acceptance.
                packet.update(type='RUNNING',job_url=job['url'],accepted_at=self.bound['t0'],running_at=sample['received'],observed_at=self.clock.wall())
                self.receiver.send(packet);self.sent_running=True
                self.budget.start();self.timer()
                break
            while True:
                self.budget.check()
                e=self.receiver.event(min(0.2,self.budget.left()))
                if e is None:continue
                self.emit('SUPERVISOR',result=e)
                require(e.get('nonce')==self.nonce and e.get('simulation') is False,'RECEIVER_IDENTITY')
                require(e.get('state') in ('EXECUTION_STARTING','DRIVER_PROGRESS','STOP_NOW'),'RECEIVER_FAILED')
                if e['state']=='STOP_NOW':
                    reason=e.get('reason','UNKNOWN')
                    success=reason=='DIAGNOSTIC_COMPLETE'
                    break
        except Exception as exc:
            reason=str(exc) if isinstance(exc,Refused) else type(exc).__name__
        finally:
            self.emit('STOP_NOW',reason=reason,run_id=self.bound['id'] if self.bound else None,claimed=self.claimed,running_sent=self.sent_running)
            try:self.receiver.send({'type':'CANCEL','nonce':self.nonce,'simulation':False})
            except Exception:pass
            try:self.receiver.close()
            except Exception:success=False
            if self.claimed:
                try:self.receipt({'state':'DIAGNOSTIC_COMPLETE' if success else 'UNKNOWN_OR_ABORTED','nonce':self.nonce,'run':self.bound,'reason':reason,'running_sent':self.sent_running,'at':self.clock.wall(),'reconciliation_required':True})
                except Exception:success=False
        return success


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--nonce',required=True)
    args=parser.parse_args()
    require(re.fullmatch(r'[0-9a-f]{32}',args.nonce) is not None,'NONCE')
    clock=Clock();budget=Budget(clock)
    def emit(state,**kw):print(json.dumps({'state':state,'nonce':args.nonce,'at':time.time(),**kw}),flush=True)
    def timer():signal.setitimer(signal.ITIMER_REAL,max(0.001,budget.left()))
    receiver=None
    try:
        require(sys.platform=='linux' and socket.gethostname()=='synthetic-executor' and os.getuid()==1001,'WRONG_EXECUTOR')
        parent=os.getppid()
        require(ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0 and os.getppid()==parent,'WATCHER_PARENT_DEATH_SETUP')
        signal.signal(signal.SIGALRM,signal.SIG_DFL);timer()
        require(not os.path.lexists(CLAIM) and not os.path.lexists(RECEIPT),'RECONCILIATION_REQUIRED')
        s=SUPERVISOR.lstat()
        require(stat.S_ISREG(s.st_mode) and s.st_uid==1001 and s.st_nlink==1 and not(s.st_mode&0o022),'SUPERVISOR_METADATA')
        require(hashlib.sha256(SUPERVISOR.read_bytes()).hexdigest()==SUPERVISOR_SHA,'SUPERVISOR_HASH')
        spec=importlib.util.spec_from_file_location('checked_supervisor',SUPERVISOR)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.preflight()
        api=GitHub(budget,lambda **kw:emit('HTTP_AUDIT',**kw))
        # Check anonymous route before WATCHER_READY, without publishing or power.
        require(select_run(api.runs(),budget.earliest,clock.wall()) is None,'RUN_ALREADY_EXISTS_BEFORE_READY')
        receiver=Receiver(args.nonce)
        engine=Engine(clock,api,ssh_snapshot,receiver,args.nonce,lambda obj:durable_once(CLAIM,obj),lambda obj:durable_once(RECEIPT,{**obj,'http_calls':api.calls}),emit,timer,budget)
        return 0 if engine.execute() else 2
    except Exception as exc:
        emit('STOP_NOW',reason=str(exc) if isinstance(exc,Refused) else type(exc).__name__,running_sent=False)
        if receiver is not None:
            try:receiver.close()
            except Exception:pass
        return 2


if __name__=='__main__':raise SystemExit(main())
