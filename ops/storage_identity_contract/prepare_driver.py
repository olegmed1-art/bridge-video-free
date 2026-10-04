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
from types import SimpleNamespace

BASE = Path('/nonexistent/synthetic-storage-identity')
OLD = Path('/nonexistent/synthetic-storage-followup')
LEGACY_LOCK = Path('/nonexistent/synthetic-preparation/prepare_driver.py')
GUEST_SHA = '215e0f3a3844064ea02c64ff7b9db1ed6d8d24a3331bb30a2247fb7131c78125'
BOOTSTRAP = ('import ctypes,os,signal,sys; r=ctypes.CDLL(None).prctl(1,signal.SIGKILL); '
             'os._exit(125) if r!=0 or os.getppid()!=int(sys.argv[1]) else None; '
             'os.execv(sys.argv[2],sys.argv[2:])')

EXISTING_HASHES={'autonomous-admission.claim.json':'4444444444444444444444444444444444444444444444444444444444444444','autonomous-admission.receipt.json':'5555555555555555555555555555555555555555555555555555555555555555'}

ALLOWED_PROBES={'seed_units_after','fstab_after','uv_journal','mountinfo_after','unit_file_hashes_after','frame_a_before','frame_a_after','frame_b_before','frame_b_after','full_unit_graph_after','disk_correlation','uv_path_metadata','settle','full_unit_graph','graph_recheck','noauto_candidate','vdd_signatures','uv_startup','journal_classes', 'path_mount_coverage', 'runtime_identity_comparison', 'jobs_after', 'expected_uuid_link', 'identity', 'containers', 'seed_units', 'fstab', 'runtime_units_after', 'mountinfo', 'kernel_classes', 'block_inventory', 'jobs_before', 'graph_boundary', 'ledger_presence', 'resolved_path_coverage', 'docker_root', 'process_paths', 'neighbor_units', 'unit_file_hashes'}

def parse_observations(raw):
    assert len(raw)<=400000
    rows=[];rejected=0
    for line in raw.splitlines():
        try:
            row=json.loads(line)
            assert isinstance(row,dict)
            if row.get('probe') in ALLOWED_PROBES:
                assert type(row.get('ok')) is bool
                assert set(row)=={'probe','ok','value'} if row['ok'] else set(row) in ({'probe','ok','error'},{'probe','ok','error','returncode'})
                if 'returncode' in row:assert row['error']=='CommandFailure' and type(row['returncode']) is int and -255<=row['returncode']<=255
                if not row['ok']:assert isinstance(row['error'],str) and re.fullmatch('[A-Za-z_]{1,80}',row['error'])
            else:
                assert row.get('state')=='READONLY_STORAGE_COLLECTION_ENDED'
                assert set(row)=={'state','all_top_level_probes_returned','failed_probes','guest_application_writes','service_actions','evidence_complete_asserted','graph_and_path_completeness_require_review'}
                assert row['guest_application_writes'] is False and row['service_actions'] is False and row['evidence_complete_asserted'] is False
            rows.append(row)
        except (ValueError,AssertionError,TypeError):rejected+=1
    return rows,rejected

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
    assert receipt['run']['id']==claim['run']['id']==105
    assert receipt['nonce']==claim['nonce']=='87654321876543218765432187654321'
    assert receipt['running_sent'] is True and receipt['reason']=='DIAGNOSTIC_COMPLETE'
    assert receipt['state']=='DIAGNOSTIC_COMPLETE' and claim['state']=='UNRESOLVED'
    import fcntl
    lock=open(OLD/'prepare_driver.py','rb');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    legacy_lock=open(LEGACY_LOCK,'rb');fcntl.flock(legacy_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    raw=(BASE/'diagnostic_guest.py').read_bytes();assert hashlib.sha256(raw).hexdigest()==GUEST_SHA
    command=shlex.join(['/usr/bin/timeout','--signal=TERM','--kill-after=1s','60s','/usr/bin/sudo','-n','/usr/bin/python3','-I','-B','-u','-c',raw.decode()])
    ssh=['/usr/bin/ssh','-F','/dev/null','-i','/nonexistent/synthetic-key','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlgorithms=ssh-ed25519','-o','UserKnownHostsFile=/nonexistent/synthetic-hosts','-o','ConnectTimeout=3','-o','ConnectionAttempts=1','-o','ForwardAgent=no','ubuntu@192.0.2.1',command]
    try:
        result=subprocess.run(['/usr/bin/python3','-I','-B','-u','-c',BOOTSTRAP,str(os.getpid()),*ssh],capture_output=True,timeout=65,start_new_session=True)
    except subprocess.TimeoutExpired as exc:
        result=SimpleNamespace(returncode=124,stdout=exc.stdout or b'',stderr=b'')
    assert len(result.stdout)<=400000 and len(result.stderr)<=8192
    # Preserve sanitized guest observations offhost even on an explicit UNKNOWN.
    rows,rejected=parse_observations(result.stdout)
    record={'scope':'READONLY_STORAGE_RECONCILIATION','run_url':args.run_url,'guest_code_sha':GUEST_SHA,'returncode':result.returncode,'observations':rows,'rejected_lines':rejected}
    data=(json.dumps(record,sort_keys=True)+'\n').encode()
    path=BASE/'diagnostic-evidence.json';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
    fd=os.open(BASE,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    assert path.read_bytes()==data
    assert old_records()==records
    print(json.dumps({'phase':'READONLY_EVIDENCE','data':record,'sha256':hashlib.sha256(data).hexdigest()}),flush=True)
    assert result.returncode==0 and rejected==0 and rows and rows[-1].get('state')=='READONLY_STORAGE_COLLECTION_ENDED' and rows[-1].get('evidence_complete_asserted') is False
    print(json.dumps({'phase':'READONLY_DIAGNOSTIC_COMPLETE','guest_writes':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'state':'ABORT_REQUEST_PARENT_STOP','reason':type(exc).__name__,'scope':'READONLY_STORAGE_RECONCILIATION','guest_writes':False}),flush=True)
        raise SystemExit(2)
