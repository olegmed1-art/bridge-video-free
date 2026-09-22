"""Prove route metadata, shared lease, exclusive drain and session restrictions."""
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from oracle_autopilot.github_db_route import header,parse_record,stop
from oracle_light_pg_tunnel_probe import options

LOCK_CHECK = '''import fcntl,json,os,stat,sys
p='/var/lib/bridge-autopilot-tunnel/'
fd=os.open(p+'route.lock',os.O_RDONLY|os.O_NOFOLLOW)
s=os.fstat(fd)
assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o644
assert json.load(open(p+'lock-identity.json'))=={'device':s.st_dev,'inode':s.st_ino}
try:
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 print('EXCLUSIVE_AVAILABLE')
except BlockingIOError:
 print('SHARED_LEASE_HELD')
'''


def main(work):
    ssh = options(Path(work))
    host = 'autopilot-db-tunnel@92.5.47.149'
    lease = subprocess.Popen(ssh+['-T',host,'route-v1'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        record = parse_record(header(lease),0)
        assert record['route']=={'version':1,'backend':'neon','database':'autopilot','epoch':0}
        lease.stdin.write(b'.')
        result = subprocess.run(ssh+['-T','ubuntu@92.5.47.149','sudo -n /usr/bin/python3 -'],input=LOCK_CHECK,
                                text=True,capture_output=True,timeout=20,check=True)
        assert result.stdout.strip()=='SHARED_LEASE_HELD'
    finally:
        stop(lease)
    for _ in range(5):
        result = subprocess.run(ssh+['-T','ubuntu@92.5.47.149','sudo -n /usr/bin/python3 -'],input=LOCK_CHECK,
                                text=True,capture_output=True,timeout=20,check=True)
        if result.stdout.strip()=='EXCLUSIVE_AVAILABLE':
            break
        time.sleep(1)
    assert result.stdout.strip()=='EXCLUSIVE_AVAILABLE'
    for args in [['-T',host,'id'],['-tt',host,'id'],['-T','-s',host,'sftp']]:
        result = subprocess.run(ssh+args,text=True,capture_output=True,timeout=20)
        assert result.returncode!=0 and 'uid=' not in result.stdout
    print(json.dumps({'route_lease_probe':'PASS','backend':'neon','shared_lock_proved':True,
                      'exclusive_drain_proved':True,'shell_pty_sftp_denied':True,'database_writes':False}))


if __name__=='__main__':
    main(sys.argv[1])
