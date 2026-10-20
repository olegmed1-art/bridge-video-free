"""Behavior of the actual privileged inventory glue and both wire boundaries.

Synthetic records only. Linux-root cases exercise real Agreement, Snapshot and
read-only flock; Windows skips are explicitly not production evidence.
"""
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_light_native_retirement_inventory import fixture, r
from test_light_native_retirement_inventory_transport import runner, reference


class BootstrapBoundaryTests(unittest.TestCase):
    def test_actual_bootstrap_refuses_unpinned_or_extra_wire_before_any_read(self):
        blobs=dict(controller=b'{}',runtime=b'synthetic-runtime',payload=b'{}')
        pins=[r.sha(blobs[key]) for key in ('controller','runtime','payload')]
        code=runner.bootstrap('a'*40,*pins,123,1)
        original={key:base64.b64encode(raw).decode() for key,raw in blobs.items()}
        original['token']='SYNTHETIC_NOT_A_CREDENTIAL'
        for fault in ('credential','driver','controller','runtime','payload'):
            with self.subTest(fault=fault):
                wire=deepcopy(original)
                if fault in ('credential','driver'):wire[fault]='PRIVATE_SENTINEL'
                else:wire[fault]=base64.b64encode(b'bad-pin').decode()
                stdin=SimpleNamespace(buffer=io.BytesIO(r.encoded(wire)))
                stdout=io.StringIO()
                # Either extraction or a protected file access is a test failure.
                with patch.object(os,'geteuid',return_value=0,create=True), \
                     patch.object(os,'uname',return_value=SimpleNamespace(nodename='autopilot-lite-vnic'),create=True), \
                     patch('sys.stdin',stdin),patch('sys.stdout',stdout), \
                     patch('tempfile.TemporaryDirectory',side_effect=AssertionError('unexpected extraction')) as extraction, \
                     patch('builtins.open',side_effect=AssertionError('unexpected protected read')) as read:
                    with self.assertRaises(SystemExit) as stopped:
                        exec(compile(code,'actual-inventory-bootstrap','exec'),{})
                self.assertEqual(stopped.exception.code,2)
                extraction.assert_not_called();read.assert_not_called()
                self.assertEqual(json.loads(stdout.getvalue()),
                    {'audit':'LIGHT_NATIVE_ISSUER_INPUT_REFUSED','reason':'OBSERVATION_REFUSED'})


@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:1)()==0,
                     'actual Agreement/Snapshot/flock composition requires Linux root')
class InventoryLiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        from ops import light_native_retirement_live as live
        from ops import light_native_lane_controller as owner
        from ops import light_native_bounded as bounded
        from ops import native_maintenance_agreement as agreement
        from ops import light_native_retirement as real_r
        import tempfile
        self.live,self.owner,self.bounded,self.agreement,self.r=live,owner,bounded,agreement,real_r
        self.directory=tempfile.TemporaryDirectory(prefix='inventory-live-',dir='/root')
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);self.root.chmod(0o700)
        memory,self.args,_,_=fixture()
        memory.files.update({'issuers/cycle.lock':b'','cycles/cycle.lock':b''})
        for relative,raw in memory.files.items():
            path=self.root/relative
            parent=self.root
            for part in relative.split('/')[:-1]:
                parent=parent/part;parent.mkdir(mode=0o700,exist_ok=True)
            path.write_bytes(raw);path.chmod(0o600)
        self.controller=b'synthetic-controller'
        self.runtime=next(raw for name,raw in memory.files.items() if name.endswith('/runtime-package.json'))
        self.value=reference()
        self.value.update(accepted_controller_sha256=r.sha(self.controller),
            accepted_runtime_sha256=r.sha(self.runtime),policy_sha256=self.args[0])
        self.now=946684850.0
        self.value['agreement'].update(not_before=self.stamp(self.now-1),expires_at=self.stamp(self.now+900))
        self.source_checks=0;self.snapshot_reads=0
        self.guard=SimpleNamespace(assert_current=lambda:None)
        self.stack=[]
        self.addCleanup(lambda:[p.stop() for p in reversed(self.stack)])
        self.replace(owner,'ROOT',self.root)
        self.replace(owner.install,'RETAINED_SOURCE',self.args[2])
        self.replace(owner,'validate_package',lambda *args:None)
        self.replace(os,'uname',lambda:SimpleNamespace(nodename='autopilot-lite-vnic'))
        self.replace(agreement.time,'time',lambda:self.now)
        self.replace(agreement.time,'monotonic',lambda:self.now)
        self.replace(owner.release.staging,'require_current_main',self.source_check)
        self.replace(bounded,'run',lambda operation:operation())
        read=real_r.Snapshot.read
        def counted(view,*args,**kwargs):
            self.snapshot_reads+=1
            return read(view,*args,**kwargs)
        self.replace(real_r.Snapshot,'read',counted)

    @staticmethod
    def stamp(value):
        return datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def replace(self,target,name,value):
        item=patch.object(target,name,value);item.start();self.stack.append(item)

    def source_check(self,source):
        self.source_checks+=1

    def observe(self):
        self.value['agreement']['operation_digest']=r.sha(r.encoded(r.issuer_inventory_scope(self.value)))
        self.value['accepted_agreement_sha256']=r.sha(r.encoded(self.value['agreement']))
        raw=r.encoded(self.value)
        return self.live.inventory_issuer_inputs(self.controller,self.runtime,raw,r.sha(raw),self.guard)

    def test_real_snapshot_and_locks_observe_without_changing_original_bytes(self):
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        value=self.observe()
        self.assertEqual(value['state'],'OBSERVED')
        self.assertGreater(self.snapshot_reads,0)
        self.assertEqual(before,{p.relative_to(self.root).as_posix():p.read_bytes()
                                for p in self.root.rglob('*') if p.is_file()})

    def test_invalid_current_authority_never_reaches_snapshot(self):
        original=deepcopy(self.value)
        for fault in ('expired','future','owner','coverage'):
            with self.subTest(fault=fault):
                self.value=deepcopy(original);a=self.value['agreement']
                if fault=='expired':a.update(not_before=self.stamp(self.now-900),expires_at=self.stamp(self.now))
                elif fault=='future':a.update(not_before=self.stamp(self.now+1),expires_at=self.stamp(self.now+900))
                elif fault=='owner':a['owner']='other-owner'
                else:a['coverage']=a['coverage'][:-1]
                value=self.observe()
                self.assertEqual(value['audit'],'LIGHT_NATIVE_ISSUER_INPUT_REFUSED')
                self.assertEqual(self.snapshot_reads,0)

    def test_expiry_during_final_source_check_never_returns_observed(self):
        def expire(source):
            self.source_checks+=1
            if self.source_checks==2:self.now+=901
        self.replace(self.owner.release.staging,'require_current_main',expire)
        self.assertEqual(self.observe()['reason'],'COORDINATION_AGREEMENT_EXPIRED')
        self.assertGreater(self.snapshot_reads,0)

    def test_expiry_during_lock_exit_never_returns_observed(self):
        original=self.live.observation_locks
        @contextmanager
        def expire(root):
            with original(root):yield
            self.now+=901
        self.replace(self.live,'observation_locks',expire)
        self.assertEqual(self.observe()['reason'],'COORDINATION_AGREEMENT_EXPIRED')

    def test_busy_real_lock_cannot_be_misreported_as_observed(self):
        import fcntl
        with (self.root/'issuers/cycle.lock').open('rb') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.observe()['audit'],'LIGHT_NATIVE_ISSUER_INPUT_REFUSED')

    def test_original_bytes_drift_during_final_source_check_refuses(self):
        def drift(source):
            self.source_checks+=1
            if self.source_checks==2:
                path=next(self.root.glob('*/before.json'));path.write_bytes(b'changed')
        self.replace(self.owner.release.staging,'require_current_main',drift)
        # A pinned reread rejects changed content before metadata comparison.
        self.assertEqual(self.observe()['reason'],'LANE_RETIREMENT_NOT_ACCEPTED')
        self.assertEqual(self.source_checks,2)

    def test_bounded_unknown_propagates_without_inventing_observation(self):
        def unknown(operation):raise RuntimeError('LANE_RETIREMENT_UNKNOWN')
        self.replace(self.bounded,'run',unknown)
        with self.assertRaisesRegex(RuntimeError,'LANE_RETIREMENT_UNKNOWN'):self.observe()
        self.assertEqual(self.snapshot_reads,0)

    def test_outer_main_rejects_corrupt_oversize_or_authority_promoted_reply(self):
        from ops import light_native_lane_run_guard as guard
        from ops import light_native_pilot_release as release
        from ops import native_maintenance_store_runner as store
        observed=self.observe()
        raw=r.encoded(self.value)
        pins=[r.sha(value) for value in (self.controller,self.runtime,raw)]
        self.replace(guard,'local_context',lambda *a,**kw:(self.value['source'],123,1))
        self.replace(runner,'dispatch_inputs',lambda *a:(raw,pins,self.value))
        self.replace(store,'source_check',lambda *a:None)
        self.replace(self.owner,'package',lambda *a:self.controller)
        self.replace(release,'package',lambda *a:self.runtime)
        promoted=dict(observed,new_task_authorized=True)
        for output in (b'{}',b'{',b'x'*4097,r.encoded(promoted),r.encoded(dict(observed,private='PRIVATE_SENTINEL'))):
            with self.subTest(output=output[:40]):
                with patch.dict(os.environ,GH_TOKEN='SYNTHETIC',GITHUB_EVENT_PATH='unused'), \
                     patch.object(sys,'argv',['inventory','unused-key','unused-known']), \
                     patch.object(runner.subprocess,'run',return_value=SimpleNamespace(stdout=output,returncode=0)), \
                     patch('sys.stdout',new_callable=io.StringIO) as stdout:
                    with self.assertRaises((RuntimeError,ValueError)):
                        runner.main()
                    self.assertEqual(stdout.getvalue(),'')


if __name__=='__main__':unittest.main()
