import itertools
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import supervisor as s

NONCE = 'simulation_20261003_001'
URL = 'https://github.com/example-owner/example-repository/actions/runs/99999999999'


def bind(t0=1000):
    return dict(type='BIND', nonce=NONCE, simulation=True, run_url=URL, sha=s.SHA, t0=t0)


def running(t0=1000, observed=1001):
    return dict(bind(t0), type='RUNNING', job_url=URL+'/job/99999999999', accepted_at=t0, running_at=observed, observed_at=observed)


class GateTests(unittest.TestCase):
    def test_exact_host_pin(self):
        good=b'256 SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA host (ED25519)\n'
        self.assertTrue(s.exact_ed25519(good+b'3072 SHA256:other host (RSA)\n'))
        self.assertFalse(s.exact_ed25519(good+b'256 SHA256:other host (ED25519)\n'))
        self.assertFalse(s.exact_ed25519(good+good))
        self.assertFalse(s.exact_ed25519(b''))

    def gate(self):
        return s.Gate(NONCE, True, 1000, 50)

    def bound(self):
        g=self.gate(); self.assertEqual(g.receive(bind(),1000,50),'BOUND'); return g

    def test_happy_path(self):
        g=self.bound(); self.assertEqual(g.receive(running(),1001,51),'START'); self.assertEqual(g.starts,1)

    def test_missing_fields_fail_closed(self):
        for key in running():
            g=self.bound(); msg=running(); del msg[key]
            self.assertEqual(g.receive(msg,1001,51),'STOP',key)
            self.assertEqual(g.starts,0)

    def test_wrong_identity_and_nonfinite(self):
        for key,value in [('nonce','wrong'),('simulation',False),('sha','wrong'),('run_url',URL+'0'),('job_url',URL+'0/job/2'),('t0',float('nan')),('accepted_at',float('inf')),('running_at',True),('observed_at',None)]:
            g=self.bound(); msg=running(); msg[key]=value
            self.assertEqual(g.receive(msg,1001,51),'STOP',(key,value))

    def test_stale_future_and_time_order(self):
        for a,r,o,w in [(1000,1001,1022,1022),(1000,1002,1002,1001),(1002,1001,1001,1001),(999,1000,1000,1001)]:
            g=self.bound(); msg=running(); msg.update(accepted_at=a,running_at=r,observed_at=o)
            self.assertEqual(g.receive(msg,w,50+w-1000),'STOP')

    def test_boundary_120_and_monotonic(self):
        for wall,mono in [(1120,170),(1001,170),(1120,51)]:
            g=self.bound(); self.assertEqual(g.receive(running(observed=wall),wall,mono),'STOP')
        g=self.bound();self.assertEqual(g.receive(running(observed=1119.999),1119.999,169.999),'START')

    def test_prepare_boundary(self):
        g=self.bound();g.receive(running(),1001,51)
        self.assertEqual(g.tick(1269.999,319.999),'NONE')
        self.assertEqual(g.tick(1270,320),'STOP')
        self.assertEqual(g.reason,'PREPARE_DEADLINE')

    def test_arm_expiry_disconnect_cancel(self):
        g=self.gate();self.assertEqual(g.tick(1300,350),'STOP')
        for bound in (False,True):
            g=self.bound() if bound else self.gate()
            self.assertEqual(g.receive(dict(type='CANCEL',nonce=NONCE,simulation=True),1001,51),'STOP')
            self.assertEqual(g.receive(running(),1002,52),'NONE')
            self.assertEqual(g.starts,0)

    def test_no_unbound_start_or_duplicate_restart(self):
        g=self.gate();self.assertEqual(g.receive(running(),1001,51),'STOP')
        g=self.bound();g.receive(running(),1001,51)
        self.assertEqual(g.receive(running(),1002,52),'STOP')
        self.assertEqual(g.receive(bind(),1003,53),'NONE');self.assertEqual(g.starts,1)

    def test_exhaustive_short_event_sequences(self):
        checked=0
        for seq in itertools.product(('bind','run','cancel','late'),repeat=5):
            g=self.gate(); wall,mono=1000,50
            for event in seq:
                if event=='late':wall,mono=1120,170;g.tick(wall,mono)
                else:
                    msg=bind() if event=='bind' else running(observed=wall) if event=='run' else dict(type='CANCEL',nonce=NONCE,simulation=True)
                    g.receive(msg,wall,mono)
                self.assertLessEqual(g.starts,1)
                if g.state=='TERMINAL':
                    starts=g.starts;g.receive(running(observed=wall),wall,mono);self.assertEqual(g.starts,starts)
            checked+=1
        self.assertEqual(checked,1024)

    def test_duplicate_json_keys(self):
        with self.assertRaises(ValueError):
            json.loads('{"t0":1000,"t0":1001}',object_pairs_hook=s.unique_object)

    def test_kernel_timer_fixed_deadlines(self):
        import types
        fake_signal=types.SimpleNamespace(ITIMER_REAL=0,setitimer=lambda *args: calls.append(args))
        calls=[]; g=self.gate()
        with patch.object(s,'signal',fake_signal),patch.object(s.time,'time',return_value=1001),patch.object(s.time,'monotonic',return_value=51):
            s.hard_timer(g);self.assertEqual(calls[-1],(0,299))
            g.receive(bind(),1000,50);s.hard_timer(g);self.assertEqual(calls[-1],(0,119))
            g.receive(running(),1001,51);s.hard_timer(g);self.assertEqual(calls[-1],(0,269))


class ProcessTests(unittest.TestCase):
    def run_sim(self,packet_builder):
        proc=subprocess.Popen([sys.executable,'-B','-u',str(Path(s.__file__)),'--mode','simulation','--nonce',NONCE],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        ready=json.loads(proc.stdout.readline());self.assertEqual(ready['state'],'RECEIVER_READY')
        t0=time.time();packets=packet_builder(t0)
        begin=time.perf_counter()
        stdout,stderr=proc.communicate(''.join(json.dumps(p)+'\n' for p in packets),timeout=3)
        self.assertEqual(stderr,'')
        events=[json.loads(x) for x in stdout.splitlines()]
        self.assertEqual(events[-1]['state'],'SIMULATED_STOP_NOW')
        self.assertFalse(events[-1]['child_started'])
        return proc.returncode,events,round((time.perf_counter()-begin)*1000,3)

    def test_end_to_end_happy_mock(self):
        rc,events,ms=self.run_sim(lambda t:[bind(t),running(t,t)])
        self.assertEqual(rc,0);self.assertIn('SIMULATED_EXECUTION_STARTING',[x['state'] for x in events])
        print(json.dumps({'dryrun_local_roundtrip_ms':ms,'real_child_invocations':0,'ssh_calls':0}),flush=True)

    def test_end_to_end_stale(self):
        rc,events,ms=self.run_sim(lambda t:[bind(t-30),running(t-30,t-25)])
        self.assertEqual(rc,2);self.assertEqual(events[-1]['reason'],'STALE_OR_INVALID_RUNNING')

    def test_end_to_end_eof(self):
        rc,events,ms=self.run_sim(lambda t:[])
        self.assertEqual(rc,2);self.assertEqual(events[-1]['reason'],'CHANNEL_CLOSED_OR_ERROR')

    def test_simulation_never_calls_live_adapters(self):
        import io
        class Stdin:
            buffer=io.BytesIO((json.dumps(bind(time.time()))+'\n').encode())
        with patch.object(sys,'argv',['supervisor','--mode','simulation','--nonce',NONCE]),patch.object(sys,'stdin',Stdin()),patch.object(s,'preflight',side_effect=AssertionError('NO_PREFLIGHT')) as pf,patch.object(s,'launch',side_effect=AssertionError('NO_LAUNCH')) as launch,patch('builtins.print'):
            self.assertEqual(s.main(),2);pf.assert_not_called();launch.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
