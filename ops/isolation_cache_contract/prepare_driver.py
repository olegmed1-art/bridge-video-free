"""Oracle-only preparation transport. No power calls or consumer mutations."""
import argparse,base64,hashlib,json,os,re,shlex,signal,socket,subprocess,sys,time
from pathlib import Path
PIN='e7df12e79de2b9c4c4ff77727e335f774fe34d977dfada36d6ab468218c2b802'
NAMES=('runner.py','protocol.py','durable.py','guest.py','isolation_rules.py','queue_probe.py')
OP='synthetic-isolation-001'
BASE=Path('/nonexistent/synthetic-isolation-cache')
REMOTE=r'''
import base64,hashlib,json,os,signal,socket,stat,sys
from pathlib import Path
signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(20)
assert socket.gethostname()=='synthetic-compute' and os.geteuid()==0
x=json.load(sys.stdin);assert x['phase'] in ('stage','settle','prepare','apply','canary','reconcile','restore','diagnostic')
p=Path('/nonexistent/synthetic-maint-v3-code/18722386bcdb')
def syncdir(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(fd)
 finally:os.close(fd)
def directory(p):
 if not p.exists():p.mkdir(mode=0o700);syncdir(p.parent)
 s=p.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not(s.st_mode&0o022)
def exactfile(p,raw):
 if os.path.lexists(p):
  s=p.lstat();assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not(s.st_mode&0o022)
  assert p.read_bytes()==raw
 else:
  fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
  syncdir(p.parent);assert p.read_bytes()==raw
names=('runner.py','protocol.py','durable.py','guest.py','isolation_rules.py','queue_probe.py')
if x['phase']=='stage':
 raw={n:base64.b64decode(x['files'][n],validate=True) for n in names}
 h=hashlib.sha256()
 for n in names:h.update(n.encode()+b'\0'+raw[n])
 assert h.hexdigest()==x['pin']
 directory(p.parent);directory(p)
 for n in names:exactfile(p/n,raw[n])
 print(json.dumps({'state':'CODE_STAGED','sha':h.hexdigest()}));sys.exit(0)
h=hashlib.sha256()
for n in names:h.update(n.encode()+b'\0'+(p/n).read_bytes())
assert h.hexdigest()==x['pin']
request=p/(x['phase']+'-synthetic-isolation-001.json')
exactfile(request,(json.dumps(x['request'],sort_keys=True,separators=(',',':'))+'\n').encode())
signal.alarm(0)
os.execv('/usr/bin/python3',['/usr/bin/python3','-B',str(p/'runner.py'),x['phase'],
 '--operation','synthetic-isolation-001','--request',str(request),'--run-url',x['run_url'],
 '--start-epoch',str(x['t0'])])
'''

def execute_phases(remote,copy_manifest,emit,reserve_postchecks):
 settled=remote('settle',82,{});emit('settle',settled)
 assert settled.get('state')=='BOOT_JOBS_SETTLED'
 prepared=remote('prepare',52,{});emit('prepare',prepared)
 if prepared.get('state')!='PREPARED_NO_MUTATION':raise RuntimeError('PREPARE_GATE_REFUSED')
 copied=copy_manifest(prepared);emit('offhost_copy',copied)
 assert copied.get('state')=='OFFHOST_COPY_VERIFIED'
 assert copied['receipt']['verified_sha']==prepared['baseline_sha']
 reserve_postchecks()
 applied=remote('apply',72,{'baseline_sha':prepared['baseline_sha'],'copy_receipt':copied['receipt']});emit('apply',applied)
 if applied.get('state')!='DISABLED_VERIFIED':raise RuntimeError('APPLY_NOT_VERIFIED_RECONCILE_REQUIRED')
 canary=remote('canary',22,{'baseline_sha':prepared['baseline_sha']});emit('canary',canary)
 assert canary.get('state')=='CPU_CANARY_PASS'
 diagnostic=remote('diagnostic',27,{'baseline_sha':prepared['baseline_sha']});emit('post_diagnostic',diagnostic)
 assert diagnostic.get('state')=='DIAGNOSTICS_ONLY'
 emit('ISOLATION_COMPLETE_REQUEST_PARENT_STOP',{'baseline_sha':prepared['baseline_sha'],'service_mutations':True,'targets':4})

def main():
 a=argparse.ArgumentParser();a.add_argument('--run-url',required=True);a.add_argument('--start-epoch',required=True,type=float)
 args=a.parse_args()
 elapsed=time.time()-args.start_epoch;assert 0<=elapsed<120
 deadline=time.monotonic()+270-elapsed
 signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.setitimer(signal.ITIMER_REAL,270-elapsed)
 assert socket.gethostname()=='synthetic-executor' and os.getuid()==1001
 import fcntl,stat
 old=Path('/nonexistent/synthetic-preparation')
 legacy_lock=open(old/'prepare_driver.py','rb');fcntl.flock(legacy_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 evidence_pins={old/'autonomous-admission.claim.json':'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',old/'autonomous-admission.receipt.json':'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',Path('/nonexistent/synthetic-diagnostic/diagnostic-evidence.json'):'ae7ebe6c0c9fdab8b6104f526ae7f164c85652f30a4925ee74de130189139b69'}
 for path,pin in evidence_pins.items():
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  with os.fdopen(fd,'rb') as f:
   st=os.fstat(f.fileno());assert stat.S_ISREG(st.st_mode) and st.st_uid==1001 and st.st_nlink==1 and not(st.st_mode&0o022) and st.st_size<=1048576
   assert hashlib.sha256(f.read(1048577)).hexdigest()==pin

 diag=Path('/nonexistent/synthetic-preflight-diagnostic/diagnostic-evidence.json')
 fd=os.open(diag,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  st=os.fstat(f.fileno());assert stat.S_ISREG(st.st_mode) and st.st_uid==1001 and st.st_nlink==1 and not(st.st_mode&0o022) and st.st_size<=1048576
  assert hashlib.sha256(f.read(1048577)).hexdigest()=='1111111111111111111111111111111111111111111111111111111111111111'
 prior=Path('/nonexistent/synthetic-four')
 prior_lock=open(prior/'prepare_driver.py','rb');fcntl.flock(prior_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 for name,pin in {'autonomous-admission.claim.json':'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee','autonomous-admission.receipt.json':'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'}.items():
  fd=os.open(prior/name,os.O_RDONLY|os.O_NOFOLLOW)
  with os.fdopen(fd,'rb') as f:
   st=os.fstat(f.fileno());assert stat.S_ISREG(st.st_mode) and st.st_uid==1001 and st.st_nlink==1 and not(st.st_mode&0o022) and st.st_size<=4096
   assert hashlib.sha256(f.read(4097)).hexdigest()==pin
 assert re.fullmatch(r'https://github.com/example-owner/example-repository/actions/runs/[0-9]+',args.run_url)
 h=hashlib.sha256()
 for n in NAMES:h.update(n.encode()+b'\0'+(BASE/n).read_bytes())
 assert h.hexdigest()==PIN
 sys.path.insert(0,str(BASE));import runner,durable
 ssh=['/usr/bin/ssh','-F','/dev/null','-i','/nonexistent/synthetic-key',
      '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
      '-o','HostKeyAlgorithms=ssh-ed25519','-o','UserKnownHostsFile=/nonexistent/synthetic-hosts',
      '-o','ConnectTimeout=4','-o','ConnectionAttempts=1','-o','ForwardAgent=no','ubuntu@192.0.2.1']
 def emit(phase,result):print(json.dumps({'phase':phase,'elapsed':round(time.time()-args.start_epoch,3),'result':result}),flush=True)
 def call(argv,cap,raw=None):
  assert deadline-time.monotonic()>cap+2
  parent=os.getpid()
  p=subprocess.run(argv,input=raw,capture_output=True,timeout=cap,preexec_fn=lambda:runner.parent_death_kill(parent))
  if p.returncode:raise RuntimeError('COMMAND_FAILED_'+str(p.returncode))
  assert len(p.stdout)<=1048576
  return p.stdout
 def remote(phase,cap,request=None):
  x={'phase':phase,'pin':PIN,'run_url':args.run_url,'t0':args.start_epoch,'request':request}
  if phase=='stage':x['files']={n:base64.b64encode((BASE/n).read_bytes()).decode() for n in NAMES}
  command=shlex.join(['/usr/bin/timeout','--signal=TERM','--kill-after=1s',str(cap-2)+'s','sudo','-n','/usr/bin/python3','-B','-c',REMOTE])
  return json.loads(call(ssh+[command],cap,json.dumps(x).encode()))
 # SSH can be tried only by this explicitly invoked driver after parent runURL/T0.
 identity=call(ssh+['hostname; sudo -n id -u'],10).decode().splitlines()
 assert identity==['synthetic-compute','0'];emit('identity',{'verified':True})
 assert time.time()-args.start_epoch<150
 staged=remote('stage',22);assert staged['sha']==PIN;emit('stage',staged)
 def copy_manifest(prepared):
  request={'manifest':prepared['manifest'],'backup_directory':str(BASE/'baselines'),'expected_baseline_sha':prepared['baseline_sha']}
  cp=BASE/('copy-'+OP+'.json');durable.write_once(cp,(json.dumps(request,sort_keys=True,separators=(',',':'))+'\n').encode())
  return json.loads(call(['/usr/bin/python3','-B',str(BASE/'runner.py'),'copy','--operation',OP,'--request',str(cp),
             '--run-url',args.run_url,'--start-epoch',str(args.start_epoch)],20))
 def reserve_postchecks():assert deadline-time.monotonic()>72+22+27+10
 execute_phases(remote,copy_manifest,emit,reserve_postchecks)


if __name__=='__main__':
 try:main()
 except Exception as exc:
  print(json.dumps({'state':'ABORT_REQUEST_PARENT_STOP','reason':type(exc).__name__,'service_mutations':'UNKNOWN_RECONCILE_REQUIRED'}),flush=True)
  sys.exit(2)
