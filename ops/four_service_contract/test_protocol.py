"""Synthetic service/filesystem model. No production data, credentials or commands."""
import copy
import unittest
from protocol import Protocol,TARGETS,Refused,digest,queue_gate,copy_gate

class Crash(BaseException): pass

class Faults:
    def __init__(self,point=None): self.point=point; self.trace=[]
    def __call__(self,point):
        self.trace.append(point)
        if point==self.point: raise Crash(point)

class Store:
    def __init__(self,fault): self.data={}; self.claimed=None; self.fault=fault
    def once(self,name,value):
        if name in self.data: raise Refused('EXISTS')
        self.fault('journal_write:'+name)
        self.fault('journal_file_fsync:'+name)
        self.fault('journal_publish:'+name)
        self.data[name]=copy.deepcopy(value)
        self.fault('journal_dir_fsync:'+name)
    def ensure(self,name,value):
        if name in self.data:
            if self.data[name]!=value:raise Refused('CONFLICT')
        else:self.once(name,value)
    def attempted(self):return self.claimed is not None
    def claim_value(self):return self.claimed
    def claim(self,value):
        if self.attempted():raise Refused('EXISTS')
        self.fault('claim_write'); self.fault('claim_file_fsync')
        self.claimed=copy.deepcopy(value)
        self.fault('claim_directory_fsync')

class Backend:
    def __init__(self,fault):
        self.fault=fault;self.calls=[];self.timeout=None;self.pending=False;self.now=1000
        self.current={'identity':'a'*64,'boot':'synthetic-boot','config':{'hashes':{'unit':'hash'}},
                      'bindings':{u:'synthetic-id' for u in TARGETS[:4]},
                      'links':{'/etc/systemd/system/'+('timers' if u.endswith('.timer') else 'multi-user')+
                               '.target.wants/'+u:'/etc/systemd/system/'+u for u in TARGETS},
                      'jobs':[],'states':{u:'active' for u in TARGETS},'enabled':{u:'enabled' for u in TARGETS}}
        self.protected={'dds3':'running','BEN':'active','video_data':'synthetic-untouched'}
        self.idle=True
    def preflight(self):self.local_idle();return copy.deepcopy(self.current)
    def observe(self):return copy.deepcopy(self.current)
    def reserve(self,n):
        if self.timeout=='budget':raise Refused('PHASE_BUDGET_EXHAUSTED')
    def stop_once(self):
        self.calls.append(('stop',TARGETS))
        if self.timeout=='before_submit':raise Refused('CLIENT_TIMEOUT')
        for i,u in enumerate(TARGETS):
            self.fault('before_stop_unit:%d'%i)
            self.current['jobs'].append({'id':i+1,'unit':u,'type':'stop'})
            self.fault('after_stop_unit:%d'%i)
        if self.timeout=='after_submit':raise Refused('CLIENT_TIMEOUT')
    def wait_stopped(self,n):
        if self.pending:raise Refused('STOP_JOBS_UNKNOWN')
        for i,u in enumerate(TARGETS):
            self.current['states'][u]='inactive'
            self.current['jobs']=[j for j in self.current['jobs'] if j['unit']!=u]
            self.fault('stop_job_completed:%d'%i)
        return self.observe()
    def local_idle(self):
        if not self.idle:raise Refused('OBSERVER_WORK_PRESENT')
    def assert_stopped(self):
        if self.current['jobs'] or any(v not in ('inactive','failed') for v in self.current['states'].values()):
            raise Refused('NOT_STOPPED')
    def unlink_exact(self,path,target):
        if self.current['links'].get(path)!=target:raise Refused('LINK_CONFLICT')
        self.calls.append(('unlink',path)); del self.current['links'][path]
        self.fault('unlink_before_directory_fsync:'+path)
        self.fault('unlink_after_directory_fsync:'+path)
    def restore_exact(self,path,target):
        if path in self.current['links'] and self.current['links'][path]!=target:raise Refused('LINK_CONFLICT')
        if path not in self.current['links']:
            self.calls.append(('restore',path)); self.current['links'][path]=target
        self.fault('restore_before_directory_fsync:'+path)
        self.fault('restore_after_directory_fsync:'+path)
    def reload_once(self):
        self.calls.append(('reload',None))
        if self.timeout=='reload':raise Refused('CLIENT_TIMEOUT')
        for u in TARGETS:self.current['enabled'][u]='enabled' if any(p.endswith('/'+u) for p in self.current['links']) else 'disabled'

def fixture(point=None):
    f=Faults(point);s=Store(f);b=Backend(f)
    m=b.observe();m.update(version=2,operation='synthetic-001')
    receipt={'version':2,'baseline_sha':digest(m),'verified_sha':digest(m),'operation':m['operation'],
             'guest_identity':m['identity'],'backup_identity':'b'*64,'location':'/synthetic/copy','durable':True}
    q={'project':'synthetic-project','branch':'synthetic-branch','database':'syntheticdb',
       'observed_at':1000,'counts':{'lab':{'COMPLETED':2},'control':{'CANCELLED':1}},'baseline_sha':digest(m)}
    return Protocol(m,s,b,lambda:b.now,f),receipt,q,f

class ProtocolTests(unittest.TestCase):
    def test_success_and_no_replay(self):
        p,r,q,f=fixture();self.assertEqual(p.apply(r,q)['state'],'DISABLED_VERIFIED')
        self.assertEqual(p.b.current['links'],{})
        with self.assertRaisesRegex(Refused,'ALREADY_ATTEMPTED'):p.apply(r,q)
        self.assertEqual(sum(c[0]=='stop' for c in p.b.calls),1)

    def test_crash_at_every_apply_boundary(self):
        successful,r,q,trace=fixture();successful.apply(r,q)
        self.assertGreater(len(trace.trace),75)
        for point in trace.trace:
            with self.subTest(point=point):
                p,r,q,f=fixture(point)
                with self.assertRaises(Crash):p.apply(r,q)
                f.point=None
                before=copy.deepcopy(p.b.calls)
                a=p.reconcile();self.assertEqual(a,p.reconcile());self.assertEqual(before,p.b.calls)
                if p.s.attempted():
                    with self.assertRaises(Refused):p.apply(r,q)
                    self.assertEqual(before,p.b.calls)
                if a['state'] in ('STOPPED_ORIGINAL_LINKS','PARTIAL_DISABLE','STOPPED_LINKS_REMOVED') and p.s.attempted():
                    p.restore(r);self.assertEqual(p.b.current['links'],p.m['links'])
                self.assertEqual(p.b.protected,{'dds3':'running','BEN':'active','video_data':'synthetic-untouched'})
                self.assertLessEqual(sum(c[0]=='stop' for c in p.b.calls),1)
        print('CRASH_APPLY_BOUNDARIES=%d'%len(trace.trace))

    def test_crash_at_every_restore_boundary(self):
        p,r,q,f=fixture();p.apply(r,q);f.trace=[];p.restore(r);points=list(f.trace)
        for point in points:
            with self.subTest(point=point):
                p,r,q,f=fixture();p.apply(r,q);f.point=point
                with self.assertRaises(Crash):p.restore(r)
                f.point=None;p.restore(r);p.restore(r)
                self.assertEqual(p.b.current['links'],p.m['links'])
                self.assertEqual(sum(c[0]=='stop' for c in p.b.calls),1)
        print('CRASH_RESTORE_BOUNDARIES=%d'%len(points))

    def test_partial_stop_and_pending_jobs_never_unlink(self):
        for timeout in ('before_submit','after_submit',None):
            p,r,q,f=fixture();p.b.timeout=timeout;p.b.pending=True
            with self.assertRaises(Refused):p.apply(r,q)
            self.assertEqual(p.b.current['links'],p.m['links'])
            with self.assertRaises(Refused):p.apply(r,q)
            if timeout=='after_submit':
                self.assertEqual(p.reconcile()['state'],'JOBS_PENDING_UNKNOWN')
                with self.assertRaises(Refused):p.restore(r)

    def test_all_partial_link_subsets_reconcile_restore(self):
        for mask in range(32):
            p,r,q,f=fixture();p.apply(r,q)
            p.b.current['links']={k:v for i,(k,v) in enumerate(p.m['links'].items()) if mask&(1<<i)}
            observed=p.reconcile();self.assertEqual(observed,p.reconcile())
            p.restore(r);self.assertEqual(p.b.current['links'],p.m['links'])

    def test_claim_from_other_operation_or_corruption_blocks_restore(self):
        for claim in ({'operation':'other','baseline_sha':'x'}, {}, 'broken'):
            p,r,q,f=fixture();p.apply(r,q);p.s.claimed=claim
            before=copy.deepcopy(p.b.calls)
            self.assertEqual(p.reconcile()['state'],'CLAIM_CONFLICT_UNKNOWN')
            with self.assertRaisesRegex(Refused,'CLAIM_CONFLICT'):p.restore(r)
            self.assertEqual(before,p.b.calls)

    def test_queue_rechecked_after_slow_durable_writes(self):
        p,r,q,f=fixture()
        original=p.s.once
        def slow(name,value):
            original(name,value)
            if name=='stop-intent':p.b.now+=11
        p.s.once=slow
        with self.assertRaisesRegex(Refused,'STALE'):p.apply(r,q)
        self.assertTrue(p.s.attempted());self.assertEqual(p.b.calls,[])

    def test_link_conflicts_and_boot_restart_block_restore(self):
        for damage in ('regular-file','wrong-link','extra','active','jobs','config'):
            p,r,q,f=fixture();p.apply(r,q)
            if damage in ('regular-file','wrong-link'):p.b.current['links'][next(iter(p.m['links']))]=damage
            elif damage=='extra':p.b.current['links']['/unrelated']='anything'
            elif damage=='active':p.b.current['states'][TARGETS[0]]='active'
            elif damage=='jobs':p.b.current['jobs']=[{'unit':TARGETS[0]}]
            else:p.b.current['config']={'changed':True}
            before=copy.deepcopy(p.b.calls)
            with self.assertRaises(Refused):p.restore(r)
            self.assertEqual(before,p.b.calls)

    def test_reboot_before_apply_and_receipt_tampering(self):
        for field in ('identity','boot','config','links','bindings'):
            p,r,q,f=fixture();p.b.current[field]={} if isinstance(p.b.current[field],dict) else 'different'
            with self.assertRaises(Refused):p.apply(r,q)
            self.assertFalse(p.s.attempted())
        for field,value in [('durable',False),('backup_identity','a'*64),('verified_sha','wrong')]:
            p,r,q,f=fixture();r[field]=value
            with self.assertRaises(Refused):p.apply(r,q)
            self.assertEqual(p.b.calls,[])

    def test_new_work_unknown_status_or_stale_receipt_refuses(self):
        for status in ('QUEUED','RUNNING','UNKNOWN'):
            p,r,q,f=fixture();q['counts']['lab'][status]=1
            with self.assertRaises(Refused):p.apply(r,q)
            self.assertEqual(p.b.calls,[])
        p,r,q,f=fixture();p.b.now=1011
        with self.assertRaises(Refused):p.apply(r,q)
        p,r,q,f=fixture();p.b.idle=False
        with self.assertRaises(Refused):p.apply(r,q)
        self.assertFalse(p.s.attempted())

    def test_reload_timeout_has_no_automatic_rollback(self):
        p,r,q,f=fixture();p.b.timeout='reload'
        with self.assertRaises(Refused):p.apply(r,q)
        self.assertEqual(p.b.current['links'],{})
        self.assertFalse(any(c[0]=='restore' for c in p.b.calls))
        self.assertEqual(p.reconcile()['state'],'STOPPED_LINKS_REMOVED')

if __name__=='__main__':unittest.main()
