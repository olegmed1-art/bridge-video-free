"""Hermetic Linux process tests. Temporary fake driver; no cloud/network calls."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import supervisor as s


@unittest.skipUnless(sys.platform == 'linux', 'Linux kernel primitives required')
class ContainmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='admission-contract-')
        self.procs = []
        self.pids = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=3)
            for stream in (p.stdin, p.stdout, p.stderr):
                if stream: stream.close()
        for pid in self.pids:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass
        self.tmp.cleanup()

    def popen(self, code):
        p = subprocess.Popen([sys.executable, '-B', '-u', '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.procs.append(p)
        return p

    def line(self, p, timeout=3):
        # Unbuffered os.read avoids TextIO read-ahead hiding additional lines.
        buf = getattr(p, '_pending', b'')
        end = time.monotonic() + timeout
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout, selectors.EVENT_READ)
            while b'\n' not in buf:
                if not sel.select(max(0, end-time.monotonic())):
                    raise AssertionError('OUTPUT_TIMEOUT')
                chunk = os.read(p.stdout.fileno(), 65536)
                if not chunk: raise AssertionError('UNEXPECTED_EOF')
                buf += chunk
        first, p._pending = buf.split(b'\n', 1)
        return json.loads(first)

    def send(self, p, obj):
        p.stdin.write(json.dumps(obj)+'\n'); p.stdin.flush()

    def dead(self, pid):
        for _ in range(100):
            try:
                text = Path('/proc') .joinpath(str(pid),'stat').read_text()
                if text.rsplit(')',1)[1].split()[0] == 'Z': return True
            except FileNotFoundError: return True
            time.sleep(0.02)
        return False

    def start_fake(self, pdeath=True):
        # Grandchild has optional parent-death behavior matching bounded worker.
        grand = "import time; time.sleep(30)"
        if pdeath:
            grand = "import ctypes,os,signal,time,sys; r=ctypes.CDLL(None).prctl(1,signal.SIGKILL); os._exit(125) if r!=0 or os.getppid()!=int(sys.argv[1]) else None; time.sleep(30)"
        fake = ('import os,sys,subprocess,json,time\n'
                f'g=subprocess.Popen([sys.executable,"-B","-u","-c",{grand!r},str(os.getpid())])\n'
                'time.sleep(0.1)\n'
                'print(json.dumps({"phase":"FAKE_READY","driver_pid":os.getpid(),"grandchild_pid":g.pid}),flush=True)\n'
                'time.sleep(30)\n')
        Path(self.tmp.name,'prepare_driver.py').write_text(fake)
        code = ('import supervisor as s,sys\nfrom pathlib import Path\n'
                f's.BASE=Path({self.tmp.name!r})\n'
                's.preflight=lambda: None\n'
                'sys.argv=["supervisor","--mode","live","--nonce","generic_containment_001"]\n'
                'raise SystemExit(s.main())\n')
        p = self.popen(code)
        self.assertEqual(self.line(p)['state'],'RECEIVER_READY')
        t0=time.time()
        url='https://github.com/example-owner/example-repository/actions/runs/99999999999'
        b=dict(type='BIND',nonce='generic_containment_001',simulation=False,run_url=url,sha=s.SHA,t0=t0)
        self.send(p,b); self.assertEqual(self.line(p)['state'],'BOUND')
        r=dict(b,type='RUNNING',job_url=url+'/job/99999999999',accepted_at=t0,running_at=t0,observed_at=t0)
        self.send(p,r)
        self.assertEqual(self.line(p)['state'],'EXECUTION_STARTING')
        ready=self.line(p)['result']
        self.pids.extend([ready['driver_pid'],ready['grandchild_pid']])
        return p,ready

    def test_kernel_alarm_interrupts_blocked_admission_thread(self):
        code = ('import supervisor as s,signal,time\n'
                'g=s.Gate("generic_alarm_001",False,time.time(),time.monotonic())\n'
                'g.state="BOUND";g.bound={"t0":time.time()-119.8};g.mono_t0=time.monotonic()-119.8\n'
                'signal.signal(signal.SIGALRM,signal.SIG_DFL);s.hard_timer(g)\n'
                'time.sleep(30)\n')
        p=self.popen(code); started=time.monotonic()
        self.assertEqual(p.wait(timeout=3),-signal.SIGALRM)
        self.assertLess(time.monotonic()-started,2)

    def test_kernel_alarm_interrupts_blocked_prepare_thread(self):
        code = ('import supervisor as s,signal,time\n'
                'g=s.Gate("generic_alarm_002",False,time.time(),time.monotonic())\n'
                'g.state="EXECUTING";g.bound={"t0":time.time()-269.8};g.mono_t0=time.monotonic()-269.8\n'
                'signal.signal(signal.SIGALRM,signal.SIG_DFL);s.hard_timer(g)\n'
                'time.sleep(30)\n')
        p=self.popen(code);self.assertEqual(p.wait(timeout=3),-signal.SIGALRM)

    def test_cancel_kills_entire_group_without_grandchild_pdeath(self):
        p,ready=self.start_fake(pdeath=False)
        self.send(p,dict(type='CANCEL',nonce='generic_containment_001',simulation=False))
        stop=self.line(p);self.assertEqual(stop['state'],'STOP_NOW');self.assertEqual(stop['reason'],'PARENT_CANCEL')
        self.assertEqual(p.wait(timeout=3),2)
        for pid in (ready['driver_pid'],ready['grandchild_pid']):self.assertTrue(self.dead(pid))

    def test_supervisor_sigkill_cascades_parent_death(self):
        p,ready=self.start_fake()
        p.kill();self.assertEqual(p.wait(timeout=3),-signal.SIGKILL)
        for pid in (ready['driver_pid'],ready['grandchild_pid']):self.assertTrue(self.dead(pid))

    def test_supervisor_alarm_cascades_parent_death(self):
        p,ready=self.start_fake()
        os.kill(p.pid,signal.SIGALRM);self.assertEqual(p.wait(timeout=3),-signal.SIGALRM)
        for pid in (ready['driver_pid'],ready['grandchild_pid']):self.assertTrue(self.dead(pid))

    def test_wrong_parent_bootstrap_refuses_execution(self):
        # Exercises the exact bootstrap text captured from launch, not a copy.
        from unittest.mock import patch
        class Gate:
            bound={'t0':time.time(),'run_url':'synthetic'}
            mono_t0=time.monotonic()
            def tick(self,*args):return 'NONE'
        captured={}
        with patch.object(s,'preflight'),patch.object(s.subprocess,'Popen',side_effect=lambda argv,**kw: captured.update(argv=argv)):
            s.launch(Gate())
        argv=captured['argv'];argv[5]='0'
        p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.procs.append(p);self.assertEqual(p.wait(timeout=3),125)


if __name__=='__main__':unittest.main(verbosity=2)
