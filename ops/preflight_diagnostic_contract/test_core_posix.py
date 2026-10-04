"""Hermetic Linux tests; optional systemd case runs only in disposable CI."""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import durable
import runner
from protocol import Refused,encoded

POSIX=os.name=='posix'

@unittest.skipUnless(POSIX,'Linux contract required; Windows is not POSIX evidence')
class PosixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ibm-v2-synthetic-')
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_durable_publish_and_no_overwrite(self):
        p=self.root/'record.json';raw=encoded({'synthetic':True})
        durable.write_once(p,raw);self.assertEqual(durable.read_bytes(p),raw)
        with self.assertRaises(Refused):durable.write_once(p,b'changed')
        self.assertEqual(p.read_bytes(),raw)
    def test_fsync_failures_never_publish_torn_final(self):
        for failure in (1,2):
            p=self.root/('record%d.json'%failure);calls=[0];original=os.fsync
            def fault(fd):
                calls[0]+=1
                if calls[0]==failure:raise OSError('synthetic fsync failure')
                original(fd)
            raw=encoded({'synthetic':'x'*10000})
            with patch.object(durable.os,'fsync',side_effect=fault):
                with self.assertRaises(OSError):durable.write_once(p,raw)
            self.assertTrue(not p.exists() or p.read_bytes()==raw)
            if p.exists():durable.sync_verified(p,raw)
            else:durable.write_once(p,raw)
            self.assertEqual(durable.read_bytes(p),raw)
    def test_process_crashes_at_fsync_keep_final_valid_or_absent(self):
        for cut in (1,2):
            p=self.root/('crash%d.json'%cut)
            code="""import os,durable
n=0
real=os.fsync
def crash(fd):
 global n
 n+=1
 real(fd)
 if n==CUT: os._exit(86)
durable.os.fsync=crash
durable.write_once(PATH,b'{"synthetic":true}\\n')
""".replace('CUT',str(cut)).replace('PATH',repr(str(p)))
            result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).parent,timeout=5)
            self.assertEqual(result.returncode,86)
            self.assertTrue(not p.exists() or p.read_bytes()==b'{"synthetic":true}\n')
    def test_real_symlink_remove_restore_and_regular_conflict(self):
        p=self.root/'synthetic.service';target='/synthetic/original.service';p.symlink_to(target)
        def parent(path):return Path(path),os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
        with patch.object(durable,'link_parent',side_effect=parent):
            durable.exact_link(str(p),target);self.assertFalse(os.path.lexists(p))
            durable.exact_link(str(p),target,True);durable.exact_link(str(p),target,True)
            self.assertEqual(os.readlink(p),target)
            p.unlink();p.write_text('synthetic regular data')
            with self.assertRaises(Refused):durable.exact_link(str(p),target)
            self.assertEqual(p.read_text(),'synthetic regular data')
    def test_partial_real_links_with_fsync_fault_restore(self):
        for cut in range(5):
            directory=self.root/str(cut);directory.mkdir()
            paths=[directory/('unit%d.service'%i) for i in range(5)]
            for p in paths:p.symlink_to('/synthetic/'+p.name)
            def parent(path):return Path(path),os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            with patch.object(durable,'link_parent',side_effect=parent):
                for i,p in enumerate(paths):
                    if i==cut:
                        with patch.object(durable.os,'fsync',side_effect=OSError('synthetic crash')):
                            with self.assertRaises(OSError):durable.exact_link(str(p),'/synthetic/'+p.name)
                        break
                    durable.exact_link(str(p),'/synthetic/'+p.name)
                for p in paths:durable.exact_link(str(p),'/synthetic/'+p.name,True)
                self.assertTrue(all(os.readlink(p)=='/synthetic/'+p.name for p in paths))
    def test_lock_excludes_second_worker(self):
        root=self.root/'evidence'
        s=durable.Store(root,'synthetic-001',True)
        try:
            with self.assertRaises(BlockingIOError):durable.Store(root,'synthetic-002',True)
        finally:os.close(s.lockfd)
    def test_verified_copy_resyncs_existing_and_restores_exact_bytes(self):
        m={'operation':'synthetic-001','identity':'a'*64,'synthetic':True};raw=encoded(m)
        r=durable.verified_copy(self.root,raw,'b'*64)
        with patch.object(durable,'fsync_dir',wraps=durable.fsync_dir) as synced:
            self.assertEqual(r,durable.verified_copy(self.root,raw,'b'*64));self.assertGreaterEqual(synced.call_count,2)
        self.assertEqual(durable.read_bytes(r['location']),raw)
    def test_copy_fsync_failure_never_returns_receipt(self):
        m={'operation':'synthetic-001','identity':'a'*64}
        with patch.object(durable,'fsync_dir',side_effect=OSError('synthetic storage failure')):
            with self.assertRaises(OSError):durable.verified_copy(self.root,encoded(m),'b'*64)
        self.assertFalse((self.root/'synthetic-001.receipt.json').exists())
    def test_whole_process_timeout(self):
        started=time.monotonic()
        result=runner.supervise([sys.executable,'-c','import time; time.sleep(30)'],.25)
        self.assertEqual(result['reason'],'PROCESS_DEADLINE');self.assertLess(time.monotonic()-started,1.5)
        self.assertTrue(result['worker_exit_observed']);self.assertFalse(result['systemd_jobs_cancelled'])
    def test_timeout_kills_descendant_process_group(self):
        path=self.root/'late-write'
        child='import time,pathlib;time.sleep(1);pathlib.Path('+repr(str(path))+').write_text("bad")'
        code='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);time.sleep(30)'
        result=runner.supervise([sys.executable,'-c',code],.2)
        self.assertEqual(result['reason'],'PROCESS_DEADLINE');time.sleep(1.1)
        self.assertFalse(path.exists())
    def test_supervisor_death_kills_worker(self):
        path=self.root/'orphan-write'
        child='import time,pathlib;time.sleep(1);pathlib.Path('+repr(str(path))+').write_text("bad")'
        code='import runner,sys;runner.supervise([sys.executable,"-c",'+repr(child)+'],20)'
        p=subprocess.Popen([sys.executable,'-c',code],cwd=Path(__file__).parent,stdout=subprocess.DEVNULL)
        time.sleep(.2);p.kill();p.wait(timeout=2);time.sleep(1.1)
        self.assertFalse(path.exists())
    def test_client_timeout_is_unknown(self):
        from guest import Guest
        g=Guest(time.monotonic()+5)
        with self.assertRaisesRegex(Refused,'CLIENT_TIMEOUT_OUTCOME_UNKNOWN'):
            g.command([sys.executable,'-c','import time;time.sleep(30)'],limit=.1)
    def test_parent_death_cascade_reaches_guest_client(self):
        ready=self.root/'client-ready';late=self.root/'client-late'
        client='import time,pathlib;pathlib.Path('+repr(str(ready))+').write_text("ready");time.sleep(1);pathlib.Path('+repr(str(late))+').write_text("bad")'
        worker='import guest,time,sys;guest.Guest(time.monotonic()+10).command([sys.executable,"-c",'+repr(client)+'],limit=5)'
        supervisor='import runner,sys;runner.supervise([sys.executable,"-c",'+repr(worker)+'],10)'
        p=subprocess.Popen([sys.executable,'-c',supervisor],cwd=Path(__file__).parent,stdout=subprocess.DEVNULL)
        try:
            end=time.monotonic()+3
            while not ready.exists() and time.monotonic()<end:time.sleep(.02)
            self.assertTrue(ready.exists(),'client must run before supervisor is killed')
            p.kill();p.wait(timeout=2);time.sleep(1.1)
            self.assertFalse(late.exists())
        finally:
            if p.poll() is None:p.kill();p.wait(timeout=2)
    def test_worker_kernel_alarm_interrupts_blocked_file_open(self):
        fifo=self.root/'blocked-request';os.mkfifo(fifo)
        args=[sys.executable,str(Path(runner.__file__).resolve()),'copy','--operation','synthetic-001',
              '--request',str(fifo),'--run-url','https://github.com/example-owner/example-repository/actions/runs/1',
              '--start-epoch','1000','--internal-worker','--budget','.2']
        started=time.monotonic();p=subprocess.run(args,capture_output=True,timeout=2)
        self.assertEqual(p.returncode,-signal.SIGALRM);self.assertLess(time.monotonic()-started,1.5)
    def test_scope_rejects_arbitrary_link(self):
        with self.assertRaisesRegex(Refused,'LINK_SCOPE'):durable.link_parent(str(self.root/'unrelated'))

@unittest.skipUnless(POSIX and os.environ.get('IBM_V2_SYNTHETIC_SYSTEMD')=='1',
                     'Disposable Ubuntu CI synthetic systemd test only')
class SyntheticSystemdTests(unittest.TestCase):
    def test_real_synthetic_service_timer_target_common_config_contract(self):
        import guest
        name='ibm-v2-synthetic-'+uuid.uuid4().hex
        units=(name+'.service',name+'.timer','basic.target')
        try:
            subprocess.run(['systemd-run','--unit='+name,'--on-active=120',
                            '--property=RuntimeMaxSec=10','/bin/sleep','2'],
                           check=True,capture_output=True,timeout=5)
            g=guest.Guest(time.monotonic()+15)
            with patch.object(guest,'ALL',units):rows=g.units()
            self.assertEqual(set(rows),set(units))
            for row in rows.values():self.assertTrue(set(guest.STATIC)<=set(row))
            # Exercise common config schema and real fragment hashing. Production
            # target-specific dependency predicates remain a separate live gate.
            rows['docker.service']=dict(rows[name+'.service'])
            with patch.object(guest,'TARGETS',()),patch.object(guest,'BASE',()):
                config=g.config(rows)
            self.assertTrue(config['hashes'])
        finally:
            subprocess.run(['systemctl','stop',*units[:2]],capture_output=True,timeout=5)
            subprocess.run(['systemctl','reset-failed',*units[:2]],capture_output=True,timeout=3)
    def test_systemd_common_property_shape_and_job_parser(self):
        import guest
        g=guest.Guest(time.monotonic()+10)
        with patch.object(guest,'ALL',('basic.target','system.slice','systemd-tmpfiles-clean.timer')):
            rows=g.units()
        for name,row in rows.items():
            self.assertEqual(set(guest.STATIC)-set(row),set(),name)
        self.assertIsInstance(g.jobs(),list)

    def test_manager_job_survives_client_timeout_then_finishes(self):
        self.assertEqual(os.geteuid(),0,'Run only on disposable CI with sudo python3')
        name='ibm-v2-synthetic-'+uuid.uuid4().hex+'.service'
        try:
            subprocess.run(['systemd-run','--unit='+name,'--property=Type=simple',
                            '--property=RuntimeMaxSec=20',
                            '--property=ExecStop=/bin/sleep 2','/bin/sleep','60'],check=True,capture_output=True,timeout=5)
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run(['systemctl','stop',name],capture_output=True,timeout=.1)
            raw=subprocess.check_output(['systemctl','list-jobs','--no-legend','--no-pager','--plain'],timeout=3).decode()
            self.assertIn(name,raw)
            end=time.monotonic()+5
            while time.monotonic()<end:
                state=subprocess.check_output(['systemctl','show','--value','--property=ActiveState',name],timeout=1).decode().strip()
                if state in ('inactive','failed'):break
                time.sleep(.1)
            self.assertIn(state,('inactive','failed'))
        finally:
            subprocess.run(['systemctl','stop',name],capture_output=True,timeout=5)
            subprocess.run(['systemctl','reset-failed',name],capture_output=True,timeout=3)

if __name__=='__main__':unittest.main()
