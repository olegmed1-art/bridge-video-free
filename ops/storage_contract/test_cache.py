import copy,unittest
from test_watcher import FakeClock,FakeReceiver,run,page,jobs,sample,NONCE
import watcher as w

class CacheTests(unittest.TestCase):
    def case(self,failures=0,change=None,initial_missing=0,api_latency=.1):
        c=FakeClock();r=FakeReceiver(c);trace=[];events=[];claims=[];receipts=[]
        state={'run_calls':0,'job_calls':0,'probes':0,'boot_wait':False}
        class API:
            def runs(self):
                state['run_calls']+=1;trace.append(('run',c.wall()));c.sleep(api_latency)
                v=page() if c.wall()<1001 else page(run())
                if state['probes']>=failures+2:
                    if change=='cancel':v=page(dict(run(),status='completed',conclusion='cancelled'))
                    if change=='rerun':v=page(dict(run(),run_attempt=2))
                    if change=='sha':v=page(dict(run(),head_sha='wrong'))
                    if change=='ambiguous':v=page(run(),run(1002,102))
                    if change=='timeout':raise TimeoutError()
                    if change=='stale':c.sleep(11)
                return v
            def jobs(self,ident):
                state['job_calls']+=1;trace.append(('jobs',c.wall()));c.sleep(api_latency)
                if state['job_calls']<=initial_missing:return {'total_count':0,'jobs':[]}
                v=jobs(ident)
                if state['probes']>=failures+2:
                    row=next(j for j in v['jobs'] if j['name']=='manual-console-trial')
                    if change=='job_id':
                        row['id']+=100;row['html_url']=row['html_url'].rsplit('/',1)[0]+'/'+str(row['id'])
                    if change=='job_terminal':row.update(status='completed',conclusion='cancelled')
                return v
        def probe(budget):
            state['probes']+=1;trace.append(('ssh',c.wall()))
            sent=c.wall();c.sleep(.2)
            if state['probes']<=failures:return None
            v=sample(sent,sent-1005)
            if change=='boot' and state['probes']>=failures+2:v['boot_id']='22222222-2222-2222-2222-222222222222'
            return v
        e=w.Engine(c,API(),probe,r,NONCE,claims.append,receipts.append,lambda state,**kw:events.append({'state':state,**kw}))
        return e,r,trace,state,events
    def test_no_api_between_first_and_second_valid_ssh(self):
        for failures in (0,1,10,25):
            e,r,trace,state,events=self.case(failures)
            self.assertTrue(e.execute())
            first=next(i for i,x in enumerate(trace) if x[0]=='ssh')
            final=max(i for i,x in enumerate(trace) if x[0]=='ssh')
            self.assertTrue(all(x[0]=='ssh' for x in trace[first:final+1]))
            self.assertEqual([x[0] for x in trace[final+1:]],['run','jobs'])
            self.assertEqual(state['job_calls'],2)
            self.assertEqual(state['run_calls'],4) # two discovery, initial, final
    def test_cancel_rerun_identity_job_and_boot_races_refuse(self):
        for change in ('cancel','rerun','sha','ambiguous','timeout','stale','job_id','job_terminal','boot'):
            e,r,*_=self.case(10,change)
            self.assertFalse(e.execute(),change)
            self.assertFalse(any(x['type']=='RUNNING' for x in r.sent),change)
    def test_four_checkpoint_pairs_plus_final_pair(self):
        e,r,trace,state,events=self.case(initial_missing=3)
        self.assertTrue(e.execute())
        self.assertEqual(state['job_calls'],5)
        self.assertEqual(state['run_calls']-2,5)
        e,r,trace,state,events=self.case(initial_missing=4)
        self.assertFalse(e.execute());self.assertEqual(state['probes'],0)
        self.assertEqual(state['job_calls'],4)
    def test_no_ssh_admission_timeout_still_closes(self):
        e,r,trace,state,events=self.case(failures=1000)
        self.assertFalse(e.execute());self.assertTrue(r.closed)
        self.assertFalse(any(x['type']=='RUNNING' for x in r.sent))
        self.assertLess(e.clock.wall(),1121)
        self.assertEqual(state['job_calls'],1)

if __name__=='__main__':unittest.main()
