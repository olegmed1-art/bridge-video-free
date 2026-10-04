"""Read-only reconciliation transport; historical filename is supervisor ABI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import time
import ctypes
import stat

BASE = Path('/nonexistent/synthetic-preflight-diagnostic')
OLD = Path('/nonexistent/synthetic-four')
LEGACY_LOCK = Path('/nonexistent/synthetic-preparation/prepare_driver.py')
GUEST_SHA = '988d274a61421703684def0ab3bcd95a1fcc308655c9551e0577de131d3c045b'
BOOTSTRAP = ('import ctypes,os,signal,sys; r=ctypes.CDLL(None).prctl(1,signal.SIGKILL); '
             'os._exit(125) if r!=0 or os.getppid()!=int(sys.argv[1]) else None; '
             'os.execv(sys.argv[2],sys.argv[2:])')

EXISTING_HASHES={'autonomous-admission.claim.json':'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee','autonomous-admission.receipt.json':'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'}

def read_record(path,pin,owner=1001):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno());assert stat.S_ISREG(st.st_mode) and st.st_uid==owner and st.st_nlink==1 and st.st_size<=4096 and not st.st_mode&0o022
        raw=f.read(4097)
    assert hashlib.sha256(raw).hexdigest()==pin
    return json.loads(raw)

def old_records():
    directory=OLD.lstat();assert stat.S_ISDIR(directory.st_mode) and directory.st_uid==1001 and not directory.st_mode&0o022
    return {name:read_record(OLD/name,pin) for name,pin in EXISTING_HASHES.items()}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-url',required=True);parser.add_argument('--start-epoch',type=float,required=True)
    args=parser.parse_args()
    assert socket.gethostname()=='synthetic-executor' and os.getuid()==1001
    assert re.fullmatch(r'https://github.com/example-owner/example-repository/actions/runs/[1-9][0-9]*',args.run_url)
    elapsed=time.time()-args.start_epoch;assert 0<=elapsed<120
    signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.setitimer(signal.ITIMER_REAL,min(75,270-elapsed))
    records=old_records();receipt=records['autonomous-admission.receipt.json'];claim=records['autonomous-admission.claim.json']
    assert receipt['run']['id']==claim['run']['id']==102
    assert receipt['nonce']==claim['nonce']=='dddddddddddddddddddddddddddddddd'
    assert receipt['running_sent'] is True and receipt['reason']=='DRIVER_ABORT'
    assert receipt['state']=='UNKNOWN_OR_ABORTED' and claim['state']=='UNRESOLVED'
    import fcntl
    lock=open(OLD/'prepare_driver.py','rb');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    legacy_lock=open(LEGACY_LOCK,'rb');fcntl.flock(legacy_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    raw=(BASE/'diagnostic_guest.py').read_bytes();assert hashlib.sha256(raw).hexdigest()==GUEST_SHA
    command=shlex.join(['/usr/bin/timeout','--signal=TERM','--kill-after=1s','60s','/usr/bin/sudo','-n','/usr/bin/python3','-I','-B','-u','-c',raw.decode()])
    ssh=['/usr/bin/ssh','-F','/dev/null','-i','/nonexistent/synthetic-key','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlgorithms=ssh-ed25519','-o','UserKnownHostsFile=/nonexistent/synthetic-hosts','-o','ConnectTimeout=3','-o','ConnectionAttempts=1','-o','ForwardAgent=no','ubuntu@192.0.2.1',command]
    result=subprocess.run(['/usr/bin/python3','-I','-B','-u','-c',BOOTSTRAP,str(os.getpid()),*ssh],capture_output=True,timeout=65,start_new_session=True)
    assert len(result.stdout)<=65536 and len(result.stderr)<=8192
    # Preserve sanitized guest observations offhost even on an explicit UNKNOWN.
    rows=[json.loads(line) for line in result.stdout.splitlines()]
    allowed={'ledger','units','dependencies','outside_dependency_metadata','unit_file_metadata','config_guard',
             'links','manager_jobs','expected_observer_idle','runtime_bindings','diagnostics'}
    units=('synthetic-lab.service','synthetic-lab-control-bridge.service','synthetic-lab-control.service','synthetic-lab-observer.service')
    allowed.update('hooks:'+u for u in units);allowed.update('runtime_inventory:'+u for u in units)
    assert rows and all(isinstance(row,dict) and (row.get('probe') in allowed or
        row.get('state')=='READONLY_COLLECTION_COMPLETE') for row in rows)
    record={'scope':'READONLY_RECONCILIATION','run_url':args.run_url,'guest_code_sha':GUEST_SHA,'returncode':result.returncode,'observations':rows}
    data=(json.dumps(record,sort_keys=True)+'\n').encode()
    path=BASE/'diagnostic-evidence.json';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
    fd=os.open(BASE,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    assert path.read_bytes()==data
    assert old_records()==records
    print(json.dumps({'phase':'READONLY_EVIDENCE','data':record,'sha256':hashlib.sha256(data).hexdigest()}),flush=True)
    assert result.returncode==0 and rows[-1].get('state')=='READONLY_COLLECTION_COMPLETE' and rows[-1].get('preflight_pass_asserted') is False
    print(json.dumps({'phase':'READONLY_DIAGNOSTIC_COMPLETE','guest_writes':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'state':'ABORT_REQUEST_PARENT_STOP','reason':type(exc).__name__,'scope':'READONLY_RECONCILIATION','guest_writes':False}),flush=True)
        raise SystemExit(2)
