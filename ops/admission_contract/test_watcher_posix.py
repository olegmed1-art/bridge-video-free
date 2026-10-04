"""Linux-only isolated adapters; never uses live GitHub or SSH."""
import ctypes
import datetime as dt
import hashlib
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
from unittest.mock import patch

import watcher as w
import policy as p
from test_watcher import run, page, jobs, NONCE


def original_supervisor():
    return Path(__file__).resolve().parents[1]/'admission_contract/supervisor.py'


def isolated_engine(tmp, hang=False):
    """Real pipe/PDEATHSIG chain; fake API, boot samples and temporary driver."""
    tmp=Path(tmp)
    driver='import json,time,os\nprint(json.dumps({"phase":"FAKE_READY","pid":os.getpid()}),flush=True)\n'+('time.sleep(30)\n' if hang else 'print(json.dumps({"phase":"PREPARATION_COMPLETE_REQUEST_PARENT_STOP"}),flush=True)\n')
    (tmp/'prepare_driver.py').write_text(driver)
    wrapper=('import importlib.util,sys\nfrom pathlib import Path\n'
             f'spec=importlib.util.spec_from_file_location("safe_supervisor",{str(original_supervisor())!r})\n'
             's=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)\n'
             f's.BASE=Path({str(tmp)!r});s.preflight=lambda:None\n'
             'raise SystemExit(s.main())\n')
    (tmp/'receiver.py').write_text(wrapper)
    w.SUPERVISOR=tmp/'receiver.py'
    c=w.Clock();budget=w.Budget(c)
    t0=int(time.time())-1;budget.earliest=t0;budget.wall_start=t0;budget.mono_start=c.mono()-(c.wall()-t0)
    boot=c.wall()-.1
    class API:
        def runs(self):return page(run(t0))
        def jobs(self,ident):return jobs(ident)
    def probe(b):
        now=c.wall()
        return dict(hostname=p.HOST,username='ubuntu',uid=1000,root_uid=0,boot_id='11111111-1111-1111-1111-111111111111',uptime=now-boot,guest_epoch=now,sent=now,received=now)
    receiver=w.Receiver(NONCE)
    print(json.dumps({'state':'TEST_RECEIVER_PID','pid':receiver.proc.pid}),flush=True)
    emit=lambda state,**kw:print(json.dumps(dict(state=state,**kw)),flush=True)
    e=w.Engine(c,API(),probe,receiver,NONCE,lambda obj:w.durable_once(tmp/'claim',obj),lambda obj:w.durable_once(tmp/'receipt',obj),emit,budget=budget)
    return e.execute()


@unittest.skipUnless(sys.platform=='linux','Linux primitives required')
class LinuxAdapters(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory(prefix='watcher-contract-');self.procs=[];self.pids=[]
    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=3)
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                if stream:stream.close()
        for pid in self.pids:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
        self.tmp.cleanup()
    def spawn(self,code):
        proc=subprocess.Popen([sys.executable,'-B','-u','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        self.procs.append(proc);return proc
    def line(self,proc):
        buf=getattr(proc,'_pending',b'');end=time.monotonic()+5
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout,selectors.EVENT_READ)
            while b'\n' not in buf:
                if not sel.select(max(0,end-time.monotonic())):raise AssertionError('OUTPUT_TIMEOUT')
                raw=os.read(proc.stdout.fileno(),65536)
                if not raw:raise AssertionError('UNEXPECTED_EOF')
                buf+=raw
        first,proc._pending=buf.split(b'\n',1);return json.loads(first)
    def dead(self,pid):
        for _ in range(100):
            try:
                if Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]=='Z':return True
            except FileNotFoundError:return True
            time.sleep(.02)
        return False
    def test_real_global_claim_fsync_readback_and_restart(self):
        path=Path(self.tmp.name)/'claim'
        digest=w.durable_once(path,{'nonce':NONCE,'state':'UNRESOLVED'})
        self.assertEqual(digest,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError):w.durable_once(path,{'retry':True})
    def test_real_pipe_to_unchanged_supervisor_fake_driver(self):
        proc=self.spawn(f'import test_watcher_posix as t;raise SystemExit(0 if t.isolated_engine({self.tmp.name!r}) else 2)')
        output,stderr=proc.communicate(timeout=8)
        self.assertEqual(proc.returncode,0,stderr.decode())
        rows=[json.loads(x) for x in output.splitlines()]
        self.assertEqual(rows[-1]['state'],'STOP_NOW')
        self.assertEqual(rows[-1]['reason'],'PREPARATION_COMPLETE')
        self.assertTrue((Path(self.tmp.name)/'receipt').exists())
    def test_watcher_death_kills_receiver_and_driver(self):
        proc=self.spawn(f'import test_watcher_posix as t;t.isolated_engine({self.tmp.name!r},True)')
        while True:
            row=self.line(proc)
            if row['state']=='TEST_RECEIVER_PID':self.pids.append(row['pid'])
            if row['state']=='SUPERVISOR' and row['result'].get('state')=='EXECUTION_STARTING':self.pids.append(row['result']['pid'])
            if row['state']=='SUPERVISOR' and row['result'].get('state')=='DRIVER_PROGRESS':break
        proc.kill();proc.wait(timeout=3)
        self.assertEqual(len(self.pids),2)
        for pid in self.pids:self.assertTrue(self.dead(pid))
        self.assertTrue((Path(self.tmp.name)/'claim').exists())
        self.assertFalse((Path(self.tmp.name)/'receipt').exists())
    def test_parent_death_bootstrap_for_watcher(self):
        code='import watcher as w,subprocess,sys,time,json;p=subprocess.Popen(w.child_argv([sys.executable,"-B","-c","import time;time.sleep(30)"]));print(json.dumps({"pid":p.pid}),flush=True);time.sleep(30)'
        proc=self.spawn(code);pid=self.line(proc)['pid'];self.pids.append(pid)
        time.sleep(.1);proc.kill();proc.wait(timeout=3);self.assertTrue(self.dead(pid))
    def test_http_helper_entire_call_timeout(self):
        c=w.Clock();api=w.GitHub(w.Budget(c))
        def fake_argv(_):return [sys.executable,'-B','-c','import time;time.sleep(30)']
        started=time.monotonic()
        with patch.object(w,'child_argv',side_effect=fake_argv):
            with self.assertRaises(subprocess.TimeoutExpired):api.runs()
        self.assertLess(time.monotonic()-started,5)
    def test_ssh_entire_call_timeout(self):
        c=w.Clock();b=w.Budget(c)
        def fake_argv(_):return [sys.executable,'-B','-c','import time;time.sleep(30)']
        started=time.monotonic()
        with patch.object(w,'child_argv',side_effect=fake_argv):self.assertIsNone(w.ssh_snapshot(b))
        self.assertLess(time.monotonic()-started,7)


if __name__=='__main__':unittest.main(verbosity=2)
