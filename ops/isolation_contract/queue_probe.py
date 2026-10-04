"""Reuse existing psycopg modules ONLY after dropping to the worker's UID/GID.

No installation or credential persistence. DSN passes in a pipe, never argv/logs.
"""
import json, os, stat, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlsplit
from protocol import require

SITE='/nonexistent/synthetic-school/synthetic-lab/.venv/lib/python3.12/site-packages'
SQL="""SELECT extract(epoch from clock_timestamp())::float8,current_database(),current_user,
 (SELECT coalesce(jsonb_object_agg(status,n),'{}'::jsonb) FROM
   (SELECT status,count(*)::int n FROM assistant_lab.job GROUP BY status) s),
 (SELECT coalesce(jsonb_object_agg(status,n),'{}'::jsonb) FROM
   (SELECT status,count(*)::int n FROM assistant_lab.control_command GROUP BY status) s)"""
CHILD=r'''
import os,sys,json
assert os.getuid()==os.geteuid()!=0 and os.getgid()==os.getegid()!=0 and not os.getgroups()
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
assert 'NoNewPrivs:\t1' in open('/proc/self/status').read()
x=json.load(sys.stdin)
sys.path.insert(0,x['site'])
try:
 import psycopg
 with psycopg.connect(x['dsn'],connect_timeout=3,
     options='-c default_transaction_read_only=on -c statement_timeout=2000 -c lock_timeout=500',
     application_name='ibm-isolation-readonly') as conn:
  conn.read_only=True
  with conn.cursor() as cur:
   cur.execute(x['sql']); row=cur.fetchone()
 assert row[1:3]==('syntheticdb','synthetic_principal')
 result={'observed_at':float(row[0]),'counts':{'lab':row[3],'control':row[4]}}
 assert all(type(v) is dict and all(type(k) is str and k in ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED') and type(n) is int and n>=0 for k,n in v.items()) for v in result['counts'].values())
 print(json.dumps(result),flush=True)
except Exception:
 print('{"state":"READONLY_SQL_UNAVAILABLE"}',flush=True);sys.exit(2)
'''

def read_queue(g,baseline_sha):
    import pwd,ctypes
    from guest import bounded
    from runner import parent_death_kill
    row=g.units()['synthetic-lab.service'];pid=int(row['MainPID'])
    require(pid>0 and row['ActiveState']=='active','SQL_WORKER_UNAVAILABLE')
    identity=pwd.getpwnam('synthetic-lab')
    require(identity.pw_uid>0 and identity.pw_gid>0,'SQL_ACCOUNT_UNSAFE')
    status=dict(line.split(':',1) for line in bounded('/proc/%d/status'%pid).decode().splitlines() if ':' in line)
    require(set(map(int,status['Uid'].split()))=={identity.pw_uid} and
            set(map(int,status['Gid'].split()))=={identity.pw_gid},'SQL_RUNTIME_ACCOUNT_MISMATCH')
    env=dict(x.split(b'=',1) for x in bounded('/proc/%d/environ'%pid).split(b'\0') if b'=' in x)
    dsn=env.get(b'ASSISTANT_LAB_DATABASE_URL',b'').decode();p=urlsplit(dsn)
    require(p.scheme in ('postgres','postgresql') and p.hostname=='synthetic.invalid'
            and p.path=='/syntheticdb' and p.username=='synthetic_principal','SQL_BINDING_MISMATCH')
    executable=Path('/usr/bin/python3').resolve();st=executable.stat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,'SQL_SYSTEM_PYTHON_UNSAFE')
    require(Path('/proc/%d/exe'%pid).resolve()==executable and Path(SITE).is_dir(),'SQL_RUNTIME_UNAVAILABLE')
    g.reserve(12);parent=os.getpid()
    def contain():
        parent_death_kill(parent)
        if ctypes.CDLL(None).prctl(38,1,0,0,0)!=0:os._exit(125)
    result=subprocess.run([str(executable),'-I','-S','-B','-u','-c',CHILD],
        input=json.dumps({'site':SITE,'dsn':dsn,'sql':SQL}).encode(),capture_output=True,
        timeout=7,env={'PATH':'/usr/bin:/bin','LANG':'C','PYTHONDONTWRITEBYTECODE':'1'},
        cwd='/',user=identity.pw_uid,group=identity.pw_gid,extra_groups=[],
        preexec_fn=contain)
    require(result.returncode==0 and len(result.stdout)<=4096,'READONLY_SQL_UNAVAILABLE')
    value=json.loads(result.stdout)
    require(set(value)=={'observed_at','counts'},'SQL_OUTPUT_UNKNOWN')
    require(int(g.command(['/usr/bin/systemctl','show','--value','--property=MainPID','synthetic-lab.service']).strip())==pid,'SQL_WORKER_CHANGED')
    return {'project':'synthetic-project','branch':'synthetic-branch','database':'syntheticdb',
            'baseline_sha':baseline_sha,**value}
