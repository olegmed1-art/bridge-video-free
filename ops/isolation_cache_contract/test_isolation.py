import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from protocol import Refused,TARGETS
from test_protocol import fixture
from isolation_rules import validate_mounts,classify_jobs

def mounts():
    common={k:'' for k in ('PartOf','BindsTo','PropagatesStopTo','StopPropagatedFrom','Upholds','UpheldBy','Conflicts','ConflictedBy')}
    return {'-.mount':dict(common,Id='-.mount',Names='-.mount',StopWhenUnneeded='no',LoadState='loaded',ActiveState='active',Where='/'),
            'tmp.mount':dict(common,Id='tmp.mount',Names='tmp.mount',StopWhenUnneeded='no',LoadState='not-found',ActiveState='inactive',FragmentPath='',DropInPaths='')}

class IsolationTests(unittest.TestCase):
    def test_exact_mounts_reject_generalization(self):
        validate_mounts(mounts())
        for unit,key,value in [('-.mount','Where','/other'),('tmp.mount','LoadState','loaded'),('-.mount','StopWhenUnneeded','yes'),('tmp.mount','UpheldBy','unexpected.service')]:
            m=mounts();m[unit][key]=value
            with self.assertRaises(Refused):validate_mounts(m)
        m=mounts();m['extra.mount']=m['-.mount']
        with self.assertRaises(Refused):validate_mounts(m)
    def test_boot_jobs_are_not_assumed_unrelated(self):
        for unit in ('cloud-final.service','cloud-init.target','scratch.mount','disk.device','unknown.service',TARGETS[0]):
            result=classify_jobs([{'unit':unit,'type':'start','state':'running'}],TARGETS,())
            self.assertTrue(result)
    def test_fresh_sql_acquired_after_preflight(self):
        p,r,q,f=fixture();original=p.b.preflight;events=[]
        def slow():
            p.b.now+=20;events.append('preflight');return original()
        def fresh(sha):
            events.append('sql');q.update(observed_at=p.b.now,baseline_sha=sha);return q
        p.b.preflight=slow
        self.assertEqual(p.apply(r,fresh)['state'],'DISABLED_VERIFIED')
        self.assertEqual(events,['preflight','sql'])
        p.restore(r)
        self.assertTrue(all(v=='inactive' for v in p.b.current['states'].values()))
    def test_new_manager_job_after_claim_blocks_stop(self):
        p,r,q,f=fixture();old=p.s.once
        def inject(name,value):
            old(name,value)
            if name=='stop-intent':p.b.current['jobs']=[{'unit':'cloud-final.service'}]
        p.s.once=inject
        with self.assertRaisesRegex(Refused,'NEW_SYSTEMD_JOB'):p.apply(r,q)
        self.assertTrue(p.s.attempted());self.assertEqual(p.b.calls,[])
    def test_sql_failure_and_busy_do_not_claim(self):
        for busy in (False,True):
            p,r,q,f=fixture()
            def acquire(sha):
                if not busy:raise Refused('SQL_UNAVAILABLE')
                q['counts']['lab']['RUNNING']=1;return q
            with self.assertRaises(Refused):p.apply(r,acquire)
            self.assertFalse(p.s.attempted());self.assertEqual(p.b.calls,[])

@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()==0,'Linux root disposable fixture required')
class SQLPrivilegeTests(unittest.TestCase):
    def test_real_child_uid_groups_nnp_before_import(self):
        import queue_probe as q,guest,pwd
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);base.chmod(0o755)
            code='''import os
assert os.getuid()==65534 and os.geteuid()==65534
assert os.getgid()==65534 and not os.getgroups()
assert 'NoNewPrivs:\\t1' in open('/proc/self/status').read()
class C:
 def __enter__(self): return self
 def __exit__(self,*a): pass
 def cursor(self): return self
 def execute(self,sql):
  assert 'assistant_lab.job' in sql and 'assistant_lab.control_command' in sql
  assert sql.lstrip().startswith('SELECT') and self.read_only is True
 def fetchone(self):return (1000.,'syntheticdb','synthetic_principal',{'COMPLETED':2},{'CANCELLED':1})
def connect(dsn,**kw):
 assert 'default_transaction_read_only=on' in kw['options'] and kw['connect_timeout']==3
 return C()
'''
            (base/'psycopg.py').write_text(code);(base/'psycopg.py').chmod(0o644)
            py=Path('/usr/bin/python3').resolve();original=Path.resolve
            def resolve(p,*a,**kw):
                if str(p)=='/proc/123/exe':return py
                return original(p,*a,**kw)
            def bounded(path):
                if path.endswith('/status'):return b'Uid:\t65534\t65534\t65534\t65534\nGid:\t65534\t65534\t65534\t65534\n'
                return b'ASSISTANT_LAB_DATABASE_URL=postgresql://synthetic_principal:SECRET_SENTINEL@synthetic.invalid/syntheticdb\0'
            g=SimpleNamespace(units=lambda:{'synthetic-lab.service':{'MainPID':'123','ActiveState':'active'}},reserve=lambda n:None,command=lambda a:'123')
            with patch.object(q,'SITE',str(base)),patch.object(pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=65534,pw_gid=65534)),patch.object(guest,'bounded',side_effect=bounded),patch.object(Path,'resolve',resolve):
                result=q.read_queue(g,'a'*64)
            self.assertEqual(result['counts']['lab'],{'COMPLETED':2})
            self.assertNotIn('SECRET_SENTINEL',json.dumps(result))
            self.assertFalse((base/'__pycache__').exists())

if __name__=='__main__':unittest.main()
