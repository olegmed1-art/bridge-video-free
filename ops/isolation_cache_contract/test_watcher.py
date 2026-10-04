import copy
import datetime as dt
import importlib.util
import itertools
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import policy as p
import watcher as w

NONCE='1a'*16


class FakeClock:
    def __init__(self):self.t=1000.;self.m=20.
    def wall(self):return self.t
    def mono(self):return self.m
    def sleep(self,n):self.t+=n;self.m+=n


def run(created=1001,ident=101):
    return dict(id=ident,repository={'full_name':p.REPO},head_repository={'full_name':p.REPO},head_branch=p.BRANCH,head_sha=p.SHA,workflow_id=p.WORKFLOW,path=p.PATH,event='workflow_dispatch',run_attempt=1,html_url=f'https://github.com/{p.REPO}/actions/runs/{ident}',created_at=dt.datetime.fromtimestamp(created,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),status='in_progress',conclusion=None)


def page(*runs):return {'total_count':len(runs),'workflow_runs':list(runs)}


def jobs(ident=101):
    rows=[]
    for i,name in enumerate(sorted(p.NAMES),10):
        rows.append(dict(id=i,run_id=ident,name=name,status='in_progress' if name=='manual-console-trial' else 'completed',conclusion=None if name=='manual-console-trial' else 'success' if name=='contract' else 'skipped',html_url=f'https://github.com/{p.REPO}/actions/runs/{ident}/job/{i}'))
    return {'total_count':len(rows),'jobs':rows}


def sample(sent=1020,up=10):
    return dict(hostname=p.HOST,username='ubuntu',uid=1000,root_uid=0,boot_id='11111111-1111-1111-1111-111111111111',uptime=up,guest_epoch=sent+.1,sent=sent,received=sent+.2)


class FakeAPI:
    def __init__(self,clock,change=None):self.clock=clock;self.change=change;self.calls=0
    def runs(self):
        self.calls+=1
        self.clock.sleep(.1)
        rows=page() if self.clock.wall()<1001 else page(run())
        if self.change:rows=self.change(rows,self.calls)
        return rows
    def jobs(self,ident):self.clock.sleep(.1);return jobs(ident)


class FakeReceiver:
    def __init__(self,clock,outcome='ISOLATION_COMPLETE'):
        self.clock=clock;self.outcome=outcome;self.events=[dict(state='RECEIVER_READY',nonce=NONCE,simulation=False)];self.sent=[];self.closed=False
    def send(self,msg):
        self.sent.append(msg.copy())
        if msg['type']=='BIND':self.events.append(dict(state='BOUND',nonce=NONCE,simulation=False))
        elif msg['type']=='RUNNING':
            if self.outcome=='hang':return
            self.events.extend([dict(state='EXECUTION_STARTING',nonce=NONCE,simulation=False),dict(state='STOP_NOW',reason=self.outcome,nonce=NONCE,simulation=False)])
    def event(self,timeout):
        if self.events:return self.events.pop(0)
        self.clock.sleep(timeout);return None
    def close(self):self.closed=True


class PolicyTests(unittest.TestCase):
    def test_old_run_not_selected(self):self.assertIsNone(p.select_run(page(run(999)),1000,1002))
    def test_old_and_boundary_run(self):
        for now in (1121,1122):
            with self.assertRaises(p.Refused):p.select_run(page(run()),1000,now)
        self.assertEqual(p.select_run(page(run()),1000,1120.999)['id'],101)
    def test_wrong_sha_rerun_branch_repo_workflow(self):
        for key,value in [('head_sha','wrong'),('run_attempt',2),('head_branch','main'),('event','push'),('workflow_id',1),('path','other.yml'),('repository',{'full_name':'other/repo'})]:
            row=run();row[key]=value
            with self.assertRaises(p.Refused,msg=key):p.select_run(page(row),1000,1002)
    def test_ambiguous_run_pagination_changed_binding(self):
        for rows in [page(run(),run(1001,102)),dict(page(run()),total_count=101)]:
            with self.assertRaises(p.Refused):p.select_run(rows,1000,1002)
        with self.assertRaises(p.Refused):p.select_run(page(run(1001,102)),1000,1002,{'id':101,'t0':1001})
    def test_jobs(self):
        self.assertIsNotNone(p.validate_jobs(jobs(),101))
        for name in ['probe','oracle-probe','trial-start','trial-stop']:
            rows=jobs()
            next(j for j in rows['jobs'] if j['name']==name)['conclusion']='success'
            with self.assertRaises(p.Refused):p.validate_jobs(rows,101)
        rows=jobs();rows['jobs'][0]['name']='unknown'
        with self.assertRaises(p.Refused):p.validate_jobs(rows,101)
    def test_old_boot_identity_clock_and_boundary(self):
        for key,value in [('uptime',30),('hostname','wrong'),('username','root'),('root_uid',1),('boot_id','wrong'),('guest_epoch',1030),('received',1030),('uptime',float('nan'))]:
            obs=sample();obs[key]=value
            with self.assertRaises(p.Refused,msg=key):p.validate_boot(obs,1001)
        with self.assertRaises(p.Refused):p.validate_boot(sample(1121,110),1001)
        p.validate_boot(sample(),1001)
    def test_two_samples_changed_or_frozen_boot(self):
        a=sample();b=sample(1021,11)
        p.validate_boot(b,1001,a)
        for key,value in [('boot_id','22222222-2222-2222-2222-222222222222'),('uptime',10),('uptime',11.9)]:
            c=b.copy();c[key]=value
            with self.assertRaises(p.Refused):p.validate_boot(c,1001,a)


class EngineTests(unittest.TestCase):
    def setup_engine(self,probe_change=None,api_change=None,outcome='ISOLATION_COMPLETE',claim_fault=None):
        clock=FakeClock();receiver=FakeReceiver(clock,outcome);api=FakeAPI(clock,api_change);claims=[];receipts=[];events=[]
        def probe(budget):
            sent=clock.wall();clock.sleep(.2)
            obs=sample(sent,sent-1005)
            return probe_change(obs) if probe_change else obs
        def claim(obj):
            if claim_fault:raise claim_fault
            claims.append(obj)
        engine=w.Engine(clock,api,probe,receiver,NONCE,claim,receipts.append,lambda state,**kw:events.append(dict(state=state,**kw)))
        return engine,receiver,claims,receipts,events
    def test_end_to_end_no_model_turn_and_exact_binding(self):
        e,r,claims,receipts,events=self.setup_engine()
        self.assertTrue(e.execute())
        self.assertEqual([x['type'] for x in r.sent],['BIND','RUNNING','CANCEL'])
        self.assertEqual(len(claims),1);self.assertTrue(r.closed)
        self.assertEqual(receipts[0]['state'],'ISOLATION_COMPLETE')
        self.assertEqual(events[-1]['state'],'STOP_NOW')
        self.assertFalse(next(x for x in events if x['state']=='SSH_READY_EVIDENCE')['provider_running_asserted'])
        # Feed exactly the generated packets into original b7ad gate, no edits.
        file=Path(__file__).resolve().parent/'supervisor.py'
        spec=importlib.util.spec_from_file_location('unchanged_supervisor',file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        g=module.Gate(NONCE,False,1000,20)
        self.assertEqual(g.receive(r.sent[0],1008.1,28.1),'BOUND')
        packet=r.sent[1];observed=packet['observed_at']
        self.assertEqual(g.receive(packet,observed,observed-980),'START')
    def test_no_ssh_expires_without_running(self):
        e,r,c,receipt,events=self.setup_engine(probe_change=lambda x:None)
        self.assertFalse(e.execute());self.assertFalse(any(x['type']=='RUNNING' for x in r.sent));self.assertEqual(events[-1]['state'],'STOP_NOW');self.assertTrue(r.closed)
    def test_old_boot_no_signal(self):
        e,r,*_=self.setup_engine(probe_change=lambda x:dict(x,uptime=200))
        self.assertFalse(e.execute());self.assertFalse(any(x['type']=='RUNNING' for x in r.sent))
    def test_claim_partial_or_existing_blocks_before_bind(self):
        for error in (FileExistsError(),OSError('fsync')):
            e,r,c,receipt,events=self.setup_engine(claim_fault=error)
            self.assertFalse(e.execute());self.assertFalse(any(x['type']=='BIND' for x in r.sent));self.assertTrue(r.closed)
    def test_ambiguous_run_after_bind_aborts(self):
        def change(rows,count):
            return page(run(),run(1002,102)) if count>=3 else rows
        e,r,*_=self.setup_engine(api_change=change)
        self.assertFalse(e.execute());self.assertFalse(any(x['type']=='RUNNING' for x in r.sent))
    def test_driver_partial_unknown_never_retries(self):
        e,r,c,receipt,events=self.setup_engine(outcome='DRIVER_ABORT')
        self.assertFalse(e.execute());self.assertEqual(sum(x['type']=='RUNNING' for x in r.sent),1)
        self.assertTrue(receipt[0]['reconciliation_required']);self.assertEqual(receipt[0]['state'],'UNKNOWN_OR_ABORTED')
    def test_timeout_270_stop_now(self):
        e,r,c,receipt,events=self.setup_engine(outcome='hang')
        self.assertFalse(e.execute());self.assertLessEqual(e.clock.wall(),1271.201);self.assertEqual(events[-1]['state'],'STOP_NOW');self.assertTrue(r.closed)
    def test_api_failure_closes_receiver(self):
        e,r,*_=self.setup_engine();e.api.runs=lambda:(_ for _ in ()).throw(TimeoutError())
        self.assertFalse(e.execute());self.assertTrue(r.closed)
    def test_clock_jump(self):
        c=FakeClock();b=w.Budget(c);c.t+=3
        with self.assertRaises(p.Refused):b.check()


class HTTPQuotaTests(unittest.TestCase):
    def response(self,remaining='42',**changes):
        from types import SimpleNamespace
        m=dict(status=200,bytes=2,remaining=remaining,reset='2000000000',pagination=False,elapsed=.1);m.update(changes)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'meta':m,'data':{'ok':True}}).encode())
    def test_prearm_shared_quota_floor(self):
        for n in ['0','2','3','41']:
            a=w.GitHub(w.Budget(FakeClock()))
            with patch.object(w.subprocess,'run',return_value=self.response(n)):
                with self.assertRaisesRegex(p.Refused,'HTTP_QUOTA_INSUFFICIENT'):a.runs()
        a=w.GitHub(w.Budget(FakeClock()))
        with patch.object(w.subprocess,'run',return_value=self.response('42')):self.assertEqual(a.runs(),{'ok':True})
    def test_later_floor_and_total_limit_unchanged(self):
        a=w.GitHub(w.Budget(FakeClock()));a.calls=1
        with patch.object(w.subprocess,'run',return_value=self.response('2')):
            with self.assertRaisesRegex(p.Refused,'HTTP_QUOTA_INSUFFICIENT'):a.jobs(101)
        a.calls=39;a.budget.bound={}
        with patch.object(w.subprocess,'run',return_value=self.response('3')) as call:
            self.assertEqual(a.jobs(101),{'ok':True})
            with self.assertRaisesRegex(p.Refused,'HTTP_CALL_BUDGET'):a.jobs(101)
            self.assertEqual(call.call_count,1)
    def test_http_failure_metadata(self):
        for change in [dict(status=403),dict(status=429),dict(bytes=1048577),dict(pagination=True),dict(error='TimeoutError')]:
            a=w.GitHub(w.Budget(FakeClock()))
            with patch.object(w.subprocess,'run',return_value=self.response(**change)):
                with self.assertRaisesRegex(p.Refused,'HTTP_RESPONSE') as ex:a.jobs(101)
                self.assertIn('runs/101/jobs',str(ex.exception));self.assertNotIn('secret',str(ex.exception))
    def test_discovery_preserves_ten_calls(self):
        a=w.GitHub(w.Budget(FakeClock()));a.calls=30
        with patch.object(w.subprocess,'run') as call:
            with self.assertRaisesRegex(p.Refused,'DISCOVERY_BUDGET_RESERVE'):a.runs()
            call.assert_not_called()
    def test_actual_helper_http_error_metadata_allowlist(self):
        import ast,contextlib,io,inspect,textwrap,urllib.error
        tree=ast.parse(Path(w.__file__).read_text())
        helper=next(n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='helper' for t in n.targets))
        class FakeOpener:
            def open(self,*args,**kwargs):
                raise urllib.error.HTTPError('https://api.github.com/example',403,'private-message',{'X-RateLimit-Remaining':'2','X-RateLimit-Reset':'2000000000','Authorization':'secret'},None)
        out=io.StringIO()
        with patch.object(w.urllib.request,'build_opener',return_value=FakeOpener()),patch.object(w.sys,'argv',['helper','https://api.github.com/example']),contextlib.redirect_stdout(out):exec(helper,{})
        envelope=json.loads(out.getvalue())
        self.assertEqual(envelope['meta']['status'],403);self.assertEqual(envelope['meta']['remaining'],'2');self.assertIsNone(envelope['data'])
        self.assertNotIn('secret',out.getvalue());self.assertNotIn('private-message',out.getvalue())
    def test_missing_or_bad_quota(self):
        for value in [None,'','nan',42]:
            a=w.GitHub(w.Budget(FakeClock()))
            with patch.object(w.subprocess,'run',return_value=self.response(value)):
                with self.assertRaisesRegex(p.Refused,'HTTP_QUOTA_METADATA'):a.runs()


class DurableTests(unittest.TestCase):
    def test_global_claim_one_winner_and_restart_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'claim';results=[]
            # Windows lacks O_NOFOLLOW/O_DIRECTORY; model their zero values for
            # this local syscall test, while Linux adapter test uses real flags.
            with patch.object(os,'O_NOFOLLOW',getattr(os,'O_NOFOLLOW',0),create=True),patch.object(os,'O_DIRECTORY',getattr(os,'O_DIRECTORY',0),create=True):
                original_open=os.open
                def open_(name,flags,*args):
                    if Path(name)==path.parent:raise OSError('directory fsync unavailable')
                    return original_open(name,flags,*args)
                def claim(i):
                    try:w.durable_once(path,{'run':i});results.append('winner')
                    except FileExistsError:results.append('exists')
                    except OSError:results.append('partial')
                with patch.object(os,'open',side_effect=open_):
                    ts=[threading.Thread(target=claim,args=(i,)) for i in range(8)]
                    for t in ts:t.start()
                    for t in ts:t.join()
                    self.assertEqual(results.count('partial'),1);self.assertEqual(results.count('exists'),7)
                    with self.assertRaises(FileExistsError):w.durable_once(path,{'retry':True})


if __name__=='__main__':unittest.main(verbosity=2)
