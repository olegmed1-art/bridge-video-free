"""Host assembly with real journals and EXPLICIT simulated external authorities."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
import copy
import hashlib
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops import native_maintenance_runtime as runtime
from ops import native_maintenance_stage_request as requests
from ops import native_maintenance_executor as executor
from ops import native_maintenance_workflow_api as workflow_api
from ops.native_maintenance_run_guard import RunBinding, StageRunBinding, CheckpointRunBinding, PersistentAPI
from ops.native_maintenance_workflow_pause import digest, encoded
from test_native_maintenance_recovery_assets import fixture
from test_native_maintenance_coordination import Database, agreement_record
from test_native_maintenance_executor import HOLD, SOURCE
from test_native_maintenance_checkpoint import MemoryStore
from test_native_maintenance_run_guard import FakeAPI
from test_native_maintenance_workflow_api import Response


class API(FakeAPI, PersistentAPI):
    def __init__(self):
        FakeAPI.__init__(self)
        PersistentAPI.__init__(self, 'CI-only')

    def get(self, path):
        self.calls.append(path)
        if path == '/actions/workflows/7':
            return {**self.workflow_row, 'url': workflow_api.BASE + path}
        if path.startswith('/actions/workflows/7/runs?'):
            return dict(total_count=0, workflow_runs=[])
        if path == '/actions/runs/' + str(self.run['id']):
            return copy.deepcopy(self.run)
        if path == '/actions/runs/' + str(self.run['id']) + '/attempts/2/jobs?per_page=100':
            return copy.deepcopy(self.jobs)
        if path.startswith('/contents/' + StageRunBinding.workflow):
            return copy.deepcopy(self.file)
        return super().get(path)


def packet_fixture():
    _, manifest, ids, _ = fixture()
    plan = dict(version=1, repository=workflow_api.REPOSITORY, source=SOURCE, workflows=[
        dict(id=7, path='.github/workflows/ci-only.yml', state='active', updated_at='2026-09-26T00:00:00Z')])
    target = runtime.engine.Target(**{**runtime.assets.EXPECTED_TARGET,
        'neon': runtime.engine.NeonBinding(**runtime.assets.EXPECTED_TARGET['neon'])})
    scope = executor.operation_scope(target=target, operation='apply', manifest_digest=ids[2],
        plan_digest=digest(plan), source=SOURCE,
        expected_route=dict(version=1, backend='neon', database='autopilot', epoch=0), approved_hold=HOLD,
        origin_run=dict(run_id=123, attempt=2, job_id=456), staged=True, observed_admission=True)
    value = dict(version=1, stage='prepare', scope=scope, plan=plan, baseline_digest=ids[3],
        agreement=agreement_record(scope), prior_units=[], accepted_head_digest=None, expected_outcome=None)
    return value, manifest


class PacketTests(unittest.TestCase):
    def test_stage_profile_is_uninstalled_and_diagnostics_cannot_dispatch(self):
        value, manifest = packet_fixture()
        raw = encoded(value)
        packet = runtime.AcceptedPacket(raw, runtime.checkpoint.sha(raw), manifest)
        api = FakeAPI()
        stage = StageRunBinding(SOURCE, 123, 2, api)
        with patch.object(StageRunBinding, 'workflow_sha256', None), self.assertRaisesRegex(Exception, 'STAGE_PROFILE_NOT_INSTALLED'):
            stage.assert_running()
        self.assertTrue(stage.failed)
        self.assertEqual(api.calls, [])
        for cls in (RunBinding, CheckpointRunBinding):
            with patch.object(runtime, 'SelfSupervisor') as supervisor, self.assertRaisesRegex(Exception, 'RUNTIME_COMPONENTS'):
                runtime.stage(packet, run=cls(SOURCE,123,2,api), store=MemoryStore(),
                              connect=Mock(), api_token='CI', retain_unit=Mock())
            supervisor.assert_not_called()

    def test_global_orphan_scan_blocks_active_or_populated_foreign_scope(self):
        supervisor = runtime.SelfSupervisor.__new__(runtime.SelfSupervisor)
        supervisor.unit = 'bridge-native-ro-'+SOURCE[:12]+'-123-2-'+('c'*16)+'.service'
        supervisor.assert_alive = Mock()
        other = 'bridge-native-ro-'+('b'*12)+'-122-1-'+('d'*16)+'.service'
        rows = f'{supervisor.unit} loaded active running Test\n{other} loaded failed failed Test\n'.encode()
        with patch.object(Path, 'iterdir', return_value=[Path('/sys/fs/cgroup/system.slice', supervisor.unit)]) as groups, \
             patch.object(runtime.lifetime, 'ctl', return_value=NS(returncode=0, stdout=rows)), \
             patch.object(runtime.lifetime, 'show', return_value=dict(ActiveState='active',MainPID='9')) as show:
            with self.assertRaisesRegex(Exception,'OTHER_SUPERVISOR_ACTIVE'): supervisor.assert_exclusive()
            show.return_value = dict(ActiveState='failed',MainPID='0')
            with patch.object(Path,'exists',return_value=True), patch.object(Path,'is_dir',return_value=True), \
                 patch.object(Path,'read_text',return_value='populated 1'):
                with self.assertRaisesRegex(Exception,'OTHER_CGROUP_ACTIVE'): supervisor.assert_exclusive()
            with patch.object(Path,'exists',return_value=False): supervisor.assert_exclusive()
            groups.return_value = [Path('/sys/fs/cgroup/system.slice', supervisor.unit),
                                   Path('/sys/fs/cgroup/system.slice', other.replace('-122-', '-999-'))]
            with self.assertRaisesRegex(Exception, 'UNLISTED_CGROUP'): supervisor.assert_exclusive()

    def test_valid_packet_and_no_self_approval_for_changed_bytes(self):
        value, manifest = packet_fixture()
        raw = encoded(value)
        packet = runtime.AcceptedPacket(raw, runtime.checkpoint.sha(raw), manifest)
        packet.assert_current()
        for bad in (raw+b'\n', raw.replace(b'"apply"', b'"rollback"')):
            with self.assertRaises(Exception):
                runtime.AcceptedPacket(bad, runtime.checkpoint.sha(raw), manifest)
        packet.value['scope']['operation'] = 'rollback'
        with self.assertRaises(Exception): packet.assert_current()

    def test_scope_baseline_head_and_stage_are_reconciled_before_files(self):
        original, manifest = packet_fixture()
        for kind in ('baseline', 'scope', 'head', 'stage', 'outcome', 'extra', 'agreement'):
            value = copy.deepcopy(original)
            if kind == 'baseline': value['baseline_digest'] = '0'*64
            if kind == 'scope': value['scope']['target']['recipient'] = 'wrong'
            if kind == 'head': value['accepted_head_digest'] = 'a'*64
            if kind == 'stage': value['stage'] = 'activate'
            if kind == 'outcome': value['expected_outcome'] = 'AFTER'
            if kind == 'extra': value['permission'] = True
            if kind == 'agreement': value['agreement']['operation_digest'] = 'f'*64
            raw = encoded(value)
            with self.subTest(kind=kind), patch.object(os, 'mkdir') as mkdir, self.assertRaises(Exception):
                runtime.AcceptedPacket(raw, runtime.checkpoint.sha(raw), manifest)
            mkdir.assert_not_called()

    def test_unit_identity_cannot_move_to_another_run(self):
        record = dict(version=1, kind='NATIVE_STAGE_UNIT', stage='prepare', source=SOURCE,
            scope_digest='b'*64, run=dict(run_id=123, attempt=2, job_id=456),
            supervisor=dict(unit='bridge-native-ro-'+SOURCE[:12]+'-123-2-'+('c'*16)+'.service',
                            invocation='d'*32, cgroup_inode=101))
        runtime.unit_record(record)
        record['run']['run_id'] = 124
        with self.assertRaises(Exception): runtime.unit_record(record)


@unittest.skipUnless(os.getuid() == 0, 'persistent host storage requires root; CI runs this suite with sudo')
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        root = self.parent / runtime.storage.NAME
        root.mkdir(mode=0o700)
        for name, data in [('VERSION', runtime.storage.VERSION), ('lock', b'')]:
            path = root/name
            path.write_bytes(data)
            path.chmod(0o600)
        self.requests = self.parent/'requests-root'
        self.requests.mkdir(mode=0o700)
        for name in ('requests','claims'): (self.requests/name).mkdir(mode=0o700)
        (self.requests/'VERSION').write_bytes(requests.VERSION)
        (self.requests/'VERSION').chmod(0o600)
        self.value, self.manifest = packet_fixture()
        self.api = API()
        self.addCleanup(self.api.close)
        # Explicit simulated installed workflow profile; production remains disabled.
        self.api.run.update(path=StageRunBinding.workflow, event='workflow_dispatch')
        self.api.file['path'] = StageRunBinding.workflow
        self.api.jobs['jobs'][0]['name'] = 'stage'
        self.api.jobs['jobs'].append({**self.api.jobs['jobs'][0], 'id':455, 'name':'contract',
                                     'status':'completed', 'conclusion':'success'})
        self.api.jobs['total_count'] = 2
        self.db = Database()
        self.store = MemoryStore()
        self.receipts, self.puts = [], []
        self.row = copy.deepcopy(self.value['plan']['workflows'][0])
        self.api.workflow_row = self.row
        def remote(request, *, timeout):
            # Real Transport.request must route all stage GETs through the
            # borrowed API. Only the original PUT transport is simulated here.
            self.assertEqual(request.get_method(), 'PUT')
            self.assertEqual(timeout, 4)
            path = request.full_url.removeprefix(workflow_api.BASE)
            action = path.rsplit('/', 1)[-1]
            self.puts.append(action)
            self.row.update(state='disabled_manually' if action=='disable' else 'active',
                            updated_at='2026-09-26T00:00:01Z' if action=='disable' else '2026-09-26T00:00:02Z')
            return Response(b'', request.full_url, 204)
        def supervisor(source, run):
            return NS(record=dict(unit='bridge-native-ro-'+source[:12]+'-'+str(run.run_id)+'-2-'+('c'*16)+'.service',
                                  invocation='d'*32, cgroup_inode=run.run_id), assert_alive=Mock(), assert_exclusive=Mock())
        patches = [patch.object(StageRunBinding, 'workflow_sha256', hashlib.sha256(base64.b64decode(self.api.file['content'])).hexdigest()),
                   patch.object(runtime.storage, 'PARENT', self.parent),
                   patch.object(requests, 'ROOT', self.requests),
                   patch.object(runtime.storage, 'persistent_mount', return_value='ext4'),
                   patch.object(runtime.storage, 'trusted_parent'),
                   patch.object(runtime.os, 'uname', return_value=NS(nodename='autopilot-lite-vnic')),
                   patch.object(runtime, 'SelfSupervisor', side_effect=supervisor),
                   patch.object(runtime.hold, 'attest', return_value=HOLD),
                   patch.object(runtime.engine, 'identity'),
                   patch.object(runtime.coordination.PriorSupervisors, 'assert_drained'),
                   patch.object(workflow_api.urllib.request, 'build_opener', return_value=NS(open=remote))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def packet(self):
        raw = encoded(self.value)
        return runtime.AcceptedPacket(raw, runtime.checkpoint.sha(raw), self.manifest)

    def retain(self, raw):
        self.receipts.append(json.loads(raw))
        return runtime.checkpoint.sha(raw)

    def dispatch(self, retain=None):
        run = StageRunBinding(SOURCE, self.api.run['id'], 2, self.api)
        run.assert_running()
        value = copy.deepcopy(self.value)
        if value['stage'] == 'prepare': value['scope'].pop('origin_run')
        req_value = dict(version=1,request_id='e'*32,source=SOURCE,packet=value,assets=dict(
            source_digest='a'*64,manifest_digest=value['scope']['manifest_digest'],
            baseline_digest=value['baseline_digest'],envelope_digest='b'*64))
        raw = encoded(req_value)
        path = self.requests/'requests'/(runtime.checkpoint.sha(raw)+'.json')
        if not path.exists():
            path.write_bytes(raw)
            path.chmod(0o600)
        request = requests.AcceptedRequest(raw,runtime.checkpoint.sha(raw),SOURCE)
        packet = runtime.DerivedStagePacket(request,run,self.manifest)
        return runtime.stage(packet, run=run, store=self.store, connect=self.db.connect,
                             api_token='CI-only', retain_unit=retain or self.retain)

    def next_stage(self, stage, head, outcome=None):
        self.value.update(stage=stage, prior_units=copy.deepcopy(self.receipts),
                          accepted_head_digest=head, expected_outcome=outcome)
        self.api.run['id'] += 1
        for job in self.api.jobs['jobs']:
            job['id'] += 2
            job['run_id'] = self.api.run['id']

    def test_stage_borrows_same_read_api_for_source_and_workflow_observations(self):
        original = workflow_api.Transport.__init__
        borrowed = []
        def capture(transport, token, *, read_api=None):
            borrowed.append(read_api)
            original(transport, token, read_api=read_api)
        with patch.object(workflow_api.Transport, '__init__', new=capture):
            self.dispatch()
        self.assertEqual(borrowed, [self.api, self.api])
        self.assertIn('/actions/workflows/7', self.api.calls)
        self.assertIn('/git/ref/heads/main', self.api.calls)
        self.assertEqual(self.puts, ['disable'])

    def test_prepare_execute_restore_composes_real_executor_and_separate_release(self):
        with patch.object(executor, 'permission_session', return_value='AFTER') as sql:
            prepared = self.dispatch()
            sql.assert_not_called()
            self.assertEqual(self.puts, ['disable'])
            self.next_stage('execute', prepared['head_digest'])
            executed = self.dispatch()
            self.assertEqual(sql.call_count, 1)
            self.assertEqual(self.puts, ['disable'])
            self.next_stage('restore', executed['head_digest'], 'AFTER')
            with patch.object(runtime.engine, 'inspect', return_value='AFTER') as inspect:
                restored = self.dispatch()
                self.assertGreater(inspect.call_count, 1)
        self.assertEqual(self.puts, ['disable', 'enable'])
        self.assertEqual(restored['outcome'], 'AFTER')
        self.assertFalse(restored['host_exited'])
        self.assertEqual(len(self.receipts), 3)
        self.assertEqual(self.db.active, {})

    def test_lost_unit_ack_preserves_local_record_and_prevents_all_dispatch(self):
        def lost(raw):
            self.retain(raw)
            raise ConnectionError('CI_LOST_ACK')
        with patch.object(runtime, 'MaintenanceExecutor') as construct, self.assertRaises(ConnectionError):
            self.dispatch(lost)
        construct.assert_not_called()
        self.assertEqual(self.puts, [])
        path = self.parent/runtime.storage.NAME/digest(self.value['scope'])/'units'/'123-2.json'
        self.assertEqual(json.loads(path.read_bytes()), self.receipts[0])
        with self.assertRaises(FileExistsError): self.dispatch()
        self.assertEqual(self.puts, [])

    def test_wrong_ack_blocks_executor_and_cannot_be_retried(self):
        with patch.object(runtime, 'MaintenanceExecutor') as construct, self.assertRaises(Exception):
            self.dispatch(lambda raw: '0'*64)
        construct.assert_not_called()
        self.assertEqual(self.puts, [])

    def test_omitted_uncertain_stage_or_missing_local_record_refuses(self):
        prepared = self.dispatch()
        self.next_stage('execute', prepared['head_digest'])
        with patch.object(executor, 'permission_session', side_effect=ConnectionError('CI_LOST_RETURN')):
            with self.assertRaises(Exception): self.dispatch()
        self.next_stage('restore', runtime.checkpoint.sha(next(iter(self.store.heads.values()))[0]), 'AFTER')
        # Accepted remote records may not omit the uncertain execution unit.
        self.value['prior_units'] = self.value['prior_units'][:1]
        with self.assertRaisesRegex(Exception, 'PRIOR_SET'):
            self.dispatch()
        self.assertEqual(self.puts, ['disable'])
        self.value['prior_units'] = copy.deepcopy(self.receipts)
        units = self.parent/runtime.storage.NAME/digest(self.value['scope'])/'units'
        (units/'124-2.json').unlink()  # Fault injection in this disposable directory only.
        with self.assertRaisesRegex(Exception, 'PRIOR_SET'):
            self.dispatch()

    def test_restore_database_drift_never_enables_workflow(self):
        prepared = self.dispatch()
        self.next_stage('restore', prepared['head_digest'], 'BEFORE')
        with patch.object(runtime.engine, 'inspect', return_value='DRIFT'), self.assertRaises(Exception):
            self.dispatch()
        self.assertEqual(self.puts, ['disable'])

    def test_restore_stale_head_refuses_before_enable(self):
        self.dispatch()
        self.next_stage('restore', '0'*64, 'BEFORE')
        with patch.object(runtime.engine, 'inspect') as inspect, self.assertRaises(Exception):
            self.dispatch()
        inspect.assert_not_called()
        self.assertEqual(self.puts, ['disable'])


if __name__ == '__main__': unittest.main()
