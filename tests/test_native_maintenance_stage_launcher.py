"""Authority boundaries of the final fixed launcher, all external effects simulated."""
import ast
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from ops import native_maintenance_stage_request as requests
from ops import native_maintenance_stage_launcher as launcher
from ops import native_maintenance_stage_rehearsal as rehearsal
from ops import native_maintenance_runtime as runtime
from ops.native_maintenance_run_guard import StageRunBinding, RehearsalRunBinding
from ops.native_maintenance_workflow_pause import encoded, digest
from test_native_maintenance_runtime import packet_fixture, API
import test_native_maintenance_runtime as runtime_tests
from test_native_maintenance_executor import SOURCE


def request_fixture():
    packet, manifest = packet_fixture()
    packet['scope'].pop('origin_run')
    request = dict(version=1,request_id='e'*32,source=SOURCE,packet=packet,assets=dict(
        source_digest='a'*64,manifest_digest=packet['scope']['manifest_digest'],
        baseline_digest=packet['baseline_digest'],envelope_digest='b'*64))
    return request, manifest


class ProvenanceTests(unittest.TestCase):
    def test_bootstrap_has_fixed_entrypoints_and_no_ambient_driver_requirement(self):
        repo = Path(__file__).resolve().parents[1]
        def git(root, command, ref):
            self.assertEqual(command,'show')
            return (repo/ref.split(':',1)[1]).read_bytes()
        for mode in ('fetch','stage','rehearsal'):
            with patch.object(launcher.bundle,'git',side_effect=git):
                code = launcher.bootstrap(repo,SOURCE,'a'*64,'b'*64,123,2,'c'*64,'d'*64,mode)
            compile(code,'outer','exec')
            tree = ast.parse(code)
            call = next(n for n in ast.walk(tree) if isinstance(n,ast.Call)
                        and isinstance(n.func,ast.Attribute) and n.func.attr == 'managed')
            inner = call.args[0].value
            compile(inner,'supervised','exec')
            if mode == 'stage': self.assertIn('from ops.native_maintenance_stage_host import main',inner)
            else: self.assertNotIn('native_maintenance_stage_host',inner)
            if mode == 'rehearsal': self.assertIn('from ops.native_maintenance_stage_rehearsal import main',inner)
        code = ('import sys;sys.path.insert(0,sys.argv[1]);'
                'import ops.native_maintenance_stage_host,ops.native_maintenance_stage_rehearsal;'
                'assert "psycopg" not in sys.modules;'
                'assert "ops.native_maintenance_runtime" not in sys.modules')
        result = subprocess.run([sys.executable,'-I','-B','-S','-c',code,str(repo)],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode())

    def run_binding(self):
        run = StageRunBinding(SOURCE,123,2,API())
        run.job_id = 456  # Explicit local simulation of prior authenticated observation.
        return run

    def test_prepare_derivation_only_inserts_observed_identity(self):
        value, manifest = request_fixture()
        raw = encoded(value)
        request = requests.AcceptedRequest(raw,digest(value),SOURCE)
        run = self.run_binding()
        packet = runtime.DerivedStagePacket(request,run,manifest)
        expected = copy.deepcopy(value['packet'])
        expected['scope']['origin_run'] = dict(run_id=123,attempt=2,job_id=456)
        self.assertEqual(packet.raw,encoded(expected))
        self.assertNotEqual(packet.accepted,request.accepted)
        packet.assert_bound(run)
        run.job_id = 789
        with self.assertRaisesRegex(Exception,'DERIVATION_CHANGED'): packet.assert_bound(run)

    def test_external_digest_not_self_hash_and_noncanonical_refused(self):
        value, _ = request_fixture()
        raw = encoded(value)
        for changed in (raw+b'\n',raw.replace(b'"apply"',b'"rollback"')):
            with self.assertRaises(Exception): requests.AcceptedRequest(changed,digest(value),SOURCE)
        for kind in ('source','assets','origin','extra'):
            changed = copy.deepcopy(value)
            if kind == 'source': changed['source'] = 'b'*40
            elif kind == 'assets': changed['assets']['manifest_digest'] = '0'*64
            elif kind == 'origin': changed['packet']['scope']['origin_run'] = dict(run_id=1,attempt=1,job_id=1)
            else: changed['approve'] = True
            with self.assertRaises(Exception): requests.AcceptedRequest(encoded(changed),digest(changed),SOURCE)

    def test_direct_packet_and_rehearsal_profile_cannot_enter_production(self):
        value, manifest = packet_fixture()
        packet = runtime.AcceptedPacket(encoded(value),digest(value),manifest)
        with patch.object(runtime,'SelfSupervisor') as host, self.assertRaisesRegex(Exception,'RUNTIME_COMPONENTS'):
            runtime.stage(packet,run=self.run_binding(),store=Mock(),connect=Mock(),api_token='CI',retain_unit=Mock())
        host.assert_not_called()
        request, _ = request_fixture()
        accepted = requests.AcceptedRequest(encoded(request),digest(request),SOURCE)
        with self.assertRaisesRegex(Exception,'DERIVATION_COMPONENTS'):
            runtime.DerivedStagePacket(accepted,RehearsalRunBinding(SOURCE,123,2,API()),manifest)

    def test_mutated_request_and_failed_run_cannot_derive(self):
        value, _ = request_fixture()
        request = requests.AcceptedRequest(encoded(value),digest(value),SOURCE)
        run = self.run_binding()
        run.failed = True
        with self.assertRaises(Exception): request.packet_bytes(run)
        run.failed = False
        request.value['packet']['scope']['operation'] = 'rollback'
        with self.assertRaisesRegex(Exception,'REQUEST_CHANGED'): request.packet_bytes(run)

    def test_rehearsal_never_accepts_production_request(self):
        value, _ = request_fixture()
        with self.assertRaisesRegex(Exception,'REHEARSAL_REQUEST_SCHEMA'):
            rehearsal.request_value(encoded(value),digest(value),SOURCE)
        readonly = dict(version=1,mode='read_only_rehearsal',source=SOURCE,plan=value['packet']['plan'])
        self.assertEqual(rehearsal.request_value(encoded(readonly),digest(readonly),SOURCE),readonly)
        with self.assertRaises(Exception): requests.AcceptedRequest(encoded(readonly),digest(readonly),SOURCE)

    def test_rehearsal_has_no_effectful_import_or_calls(self):
        tree = ast.parse(Path(rehearsal.__file__).read_text())
        forbidden = {'native_maintenance_runtime','native_maintenance_executor','native_cli_maintenance_session',
                     'native_maintenance_stage_host','Agreement'}
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                self.assertFalse(set((node.module or '').split('.')) & forbidden)
                self.assertFalse({alias.name for alias in node.names} & forbidden)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                self.assertNotIn(node.func.attr,('pause','disable_workflow','enable_workflow','permission_session','stage'))

    def test_fixed_workflows_and_context_do_not_cross_modes(self):
        for cls, mode in ((StageRunBinding,'stage'),(RehearsalRunBinding,'rehearsal')):
            env = dict(EXPECTED_MAIN=SOURCE,ACCEPTED_REQUEST_DIGEST='f'*64,GITHUB_REPOSITORY=launcher.REPOSITORY,
                GITHUB_REF='refs/heads/main',GITHUB_SHA=SOURCE,GITHUB_EVENT_NAME='workflow_dispatch',
                GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',GITHUB_JOB=mode,
                GITHUB_WORKFLOW_REF=launcher.REPOSITORY+'/'+cls.workflow+'@refs/heads/main',GITHUB_WORKFLOW_SHA=SOURCE)
            with patch.dict(os.environ,env,clear=True):
                self.assertEqual(launcher.context(mode),(cls,SOURCE,'f'*64))
                with self.assertRaises(Exception): launcher.context('rehearsal' if mode=='stage' else 'stage')
            raw = (Path(__file__).resolve().parents[1]/cls.workflow).read_bytes()
            # During draft assembly both profiles deliberately remain disabled.
            if cls.workflow_sha256 is not None:
                self.assertEqual(hashlib.sha256(raw).hexdigest(),cls.workflow_sha256)
            self.assertIn(b'persist-credentials: false',raw)
            self.assertIn(b'queue: max',raw)
            if mode == 'rehearsal': self.assertNotIn(b'actions: write',raw)


@unittest.skipUnless(os.getuid()==0,'private persistent storage needs root')
class ClaimTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'request-root'
        for item in (patch.object(requests,'ROOT',self.root),
                     patch.object(requests.storage,'trusted_parent'),
                     patch.object(requests.storage,'persistent_mount',return_value='ext4')):
            item.start(); self.addCleanup(item.stop)
        value, self.manifest = request_fixture()
        self.raw = encoded(value)
        self.sha = requests.submit_candidate(self.raw)
        self.request = requests.AcceptedRequest(self.raw,self.sha,SOURCE)
        self.run = StageRunBinding(SOURCE,123,2,API())
        self.run.job_id = 456
        self.run.assert_running = Mock()  # External API only, files are real.

    def test_create_only_submit_and_read_exact_private_bytes(self):
        self.assertEqual(requests.read_request(self.sha),self.raw)
        with self.assertRaises(FileExistsError): requests.submit_candidate(self.raw)
        file = self.root/'requests'/(self.sha+'.json')
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        file.chmod(0o644)
        with self.assertRaises(Exception): requests.read_request(self.sha)

    def test_one_request_cannot_be_reused_by_new_run(self):
        requests.claim(self.request,self.run)
        self.run.run_id = 124
        self.run.job_id = 789
        with self.assertRaises(FileExistsError): requests.claim(self.request,self.run)
        saved = json.loads((self.root/'claims'/(self.sha+'.json')).read_bytes())
        self.assertEqual(saved['run'],dict(run_id=123,attempt=2,job_id=456))

    def test_lost_post_claim_authority_preserves_receipt_and_refuses_retry(self):
        self.run.assert_running.side_effect = [None,ConnectionError('CI_LOST_AUTH')]
        with self.assertRaises(ConnectionError): requests.claim(self.request,self.run)
        self.assertTrue((self.root/'claims'/(self.sha+'.json')).exists())
        self.run.assert_running.side_effect = None
        with self.assertRaises(FileExistsError): requests.claim(self.request,self.run)

    def test_changed_or_symlink_request_refuses_before_claim(self):
        file = self.root/'requests'/(self.sha+'.json')
        file.write_bytes(self.raw+b'\n')
        with self.assertRaises(Exception): requests.claim(self.request,self.run)
        self.assertEqual(list((self.root/'claims').iterdir()),[])
        file.unlink()
        file.symlink_to('/dev/null')
        with self.assertRaises(Exception): requests.read_request(self.sha)


class StageAdmissionTests(unittest.TestCase):
    setUp = runtime_tests.RuntimeTests.setUp
    packet = runtime_tests.RuntimeTests.packet
    retain = runtime_tests.RuntimeTests.retain
    dispatch = runtime_tests.RuntimeTests.dispatch

    def test_legacy_packet_bypass_fails_before_any_host_effect(self):
        with patch.object(runtime,'SelfSupervisor') as supervisor, self.assertRaisesRegex(Exception,'RUNTIME_COMPONENTS'):
            runtime.stage(self.packet(),run=StageRunBinding(SOURCE,123,2,self.api),store=self.store,
                          connect=self.db.connect,api_token='CI',retain_unit=self.retain)
        supervisor.assert_not_called()

    def test_claim_is_consumed_before_executor_even_if_ack_is_lost(self):
        with patch.object(runtime,'MaintenanceExecutor') as executor, self.assertRaises(ConnectionError):
            self.dispatch(lambda raw: (_ for _ in ()).throw(ConnectionError('CI_LOST_ACK')))
        executor.assert_not_called()
        self.assertEqual(len(list((self.requests/'claims').iterdir())),1)
        with self.assertRaises(FileExistsError): self.dispatch()
        self.assertEqual(self.puts,[])


class LauncherWiringTests(unittest.TestCase):
    """Real request/derivation/profile parsing; explicit external transport faults."""
    def exercise(self, fault=None):
        value, manifest = request_fixture()
        raw, accepted = encoded(value),digest(value)
        api = API()
        api.run.update(path=StageRunBinding.workflow,event='workflow_dispatch')
        workflow = (Path(__file__).resolve().parents[1]/StageRunBinding.workflow).read_bytes()
        api.file.update(path=StageRunBinding.workflow,size=len(workflow),content=base64.b64encode(workflow).decode())
        api.jobs['jobs'][0]['name'] = 'stage'
        api.jobs['jobs'].append({**api.jobs['jobs'][0],'id':455,'name':'contract','status':'completed','conclusion':'success'})
        api.jobs['total_count'] = 2
        env = dict(EXPECTED_MAIN=SOURCE,ACCEPTED_REQUEST_DIGEST=accepted,GITHUB_REPOSITORY=launcher.REPOSITORY,
            GITHUB_REF='refs/heads/main',GITHUB_SHA=SOURCE,GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',GITHUB_JOB='stage',
            GITHUB_WORKFLOW_REF=launcher.REPOSITORY+'/'+StageRunBinding.workflow+'@refs/heads/main',
            GITHUB_WORKFLOW_SHA=SOURCE,GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='2',
            NATIVE_OWNER_DATABASE_URL='CI_PRIVATE_OWNER_URI',GH_TOKEN='CI_PRIVATE_TOKEN')
        scope = copy.deepcopy(value['packet']['scope'])
        scope['origin_run'] = dict(run_id=123,attempt=2,job_id=456)
        scope_digest = digest(scope)
        binding = digest(dict(version=1,mode='stage',source=SOURCE,request_digest=accepted,run_id=123,attempt=2))
        supervisor = dict(unit='bridge-native-ro-'+SOURCE[:12]+'-123-2-'+('c'*16)+'.service',
                          invocation='d'*32,cgroup_inode=123)
        unit = dict(version=1,kind='NATIVE_STAGE_UNIT',source=SOURCE,scope_digest=scope_digest,
                    stage='prepare',run=scope['origin_run'],supervisor=supervisor)
        result = dict(stage='prepare',scope_digest=scope_digest,head_digest='a'*64,outcome='UNKNOWN',
                      unit_digest=digest(unit),host_exited=False)
        completion = dict(kind='NATIVE_STAGE_COMPLETE',binding=binding,request_digest=accepted,result=result,sequence=0)
        if fault == 'binding': completion['binding']='0'*64
        process = Mock(returncode=0)
        process.stdin.closed = False
        process.wait.return_value = 1 if fault == 'host_exit' else 0
        channel = Mock()
        channel.receive.return_value = completion
        server = Mock(sequence=0)
        units = Mock()
        units.retainer.used = True
        events = []
        def retained(*args):
            events.append('backup')
            if fault == 'backup': raise ConnectionError('CI_LOST_BACKUP_ACK')
        def launch(*args,**kwargs): events.append('launch'); return process
        def exit_checked(*args):
            events.append('drained')
            if fault == 'drain': raise RuntimeError('CI_POPULATED_CGROUP')
            if fault == 'cancel_final': api.run.update(status='completed',conclusion='cancelled')
        def head_read(*args):
            events.append('head')
            if fault == 'head': raise RuntimeError('CI_HEAD_CHANGED')
            return b'CI_ARCHIVE'
        def prior(*args):
            if fault == 'prior': raise RuntimeError('CI_PRIOR_UNIT_CHANGED')
        from ops import native_maintenance_stage_unit as unit_transport
        with ExitStack() as stack:
            for patcher in (
                patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',['launcher','stage','key','known','wheels']),
                patch.object(StageRunBinding,'workflow_sha256',hashlib.sha256(workflow).hexdigest()),
                patch.object(launcher,'source_guard'),patch.object(launcher.bundle,'build',return_value=b'CI_SOURCE'),
                patch.object(launcher.driver,'build',return_value=b'CI_WHEEL'),
                patch.object(launcher,'bootstrap',return_value='CI_FIXED_CODE'),
                patch.object(launcher,'fetch_request',return_value=raw),patch.object(launcher,'oci_client',return_value=(Mock(),'namespace')),
                patch.object(launcher.adapter,'OCIJournalStore',return_value=Mock()),
                patch.object(launcher,'restore_assets',return_value=manifest),patch.object(launcher,'connection_parameters'),
                patch.object(launcher,'API',return_value=api),patch.object(launcher,'verify_prior',side_effect=prior),
                patch.object(launcher,'retain_request',side_effect=retained),
                patch.object(launcher.subprocess,'Popen',side_effect=launch),patch.object(launcher.rpc,'Channel',return_value=channel),
                patch.object(launcher.rpc,'StoreServer',return_value=server),patch.object(unit_transport,'Retainer'),
                patch.object(unit_transport,'UnitServer',return_value=units),
                patch.object(unit_transport,'read_accepted',return_value=encoded(unit)),
                patch.object(launcher.checkpoint,'accepted_latest',side_effect=head_read),
                patch.object(launcher,'verify_host_exit',side_effect=exit_checked)):
                stack.enter_context(patcher)
            output = stack.enter_context(patch('builtins.print'))
            if fault:
                with self.assertRaises(Exception): launcher.main('stage')
                output.assert_not_called()
            else:
                launcher.main('stage')
                report = json.loads(output.call_args.args[0])
                self.assertTrue(report['host_exited'] and report['prior_host_drained'] and report['independent_readback'])
                self.assertEqual(events,['backup','launch','head','drained'])
                sent = channel.send.call_args.args[0]
                self.assertEqual(sent['envelope']['credential'],'CI_PRIVATE_OWNER_URI')
                self.assertEqual(sent['envelope']['token'],'CI_PRIVATE_TOKEN')
            if fault in ('prior','backup'): self.assertNotIn('launch',events)
            if fault in ('binding','host_exit'): self.assertNotIn('head',events)

    def test_completion_needs_backup_exit_independent_reads_and_final_live_run(self):
        self.exercise()
        for fault in ('prior','backup','binding','host_exit','head','drain','cancel_final'):
            with self.subTest(fault=fault): self.exercise(fault)


if __name__=='__main__': unittest.main()
