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
from types import SimpleNamespace

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
        for mode in ('fetch','drain','first_install','candidate','inspect','stage','rehearsal'):
            with patch.object(launcher.bundle,'git',side_effect=git):
                code = launcher.bootstrap(repo,SOURCE,'a'*64,'b'*64,123,2,'c'*64,'d'*64,mode)
            compile(code,'outer','exec')
            tree = ast.parse(code)
            call = next(n for n in ast.walk(tree) if isinstance(n,ast.Call)
                        and isinstance(n.func,ast.Attribute) and n.func.attr in ('managed','managed_stage'))
            self.assertEqual(call.func.attr, 'managed_stage' if mode in ('stage','rehearsal') else 'managed')
            inner = call.args[0].value
            compile(inner,'supervised','exec')
            if mode == 'stage': self.assertIn('from ops.native_maintenance_stage_host import main',inner)
            else: self.assertNotIn('native_maintenance_stage_host',inner)
            if mode == 'rehearsal': self.assertIn('from ops.native_maintenance_stage_rehearsal import main',inner)
        code = ('import sys;sys.path.insert(0,sys.argv[1]);'
                'import ops.native_maintenance_stage_host,ops.native_maintenance_stage_rehearsal,ops.native_maintenance_stage_inspect;'
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
            self.assertEqual(hashlib.sha256(raw).hexdigest(),cls.workflow_sha256)
            self.assertIn(b'persist-credentials: false',raw)
            self.assertIn(b'queue: max',raw)
            if mode == 'rehearsal': self.assertNotIn(b'actions: write',raw)

    def test_stage_profile_accepts_only_fixed_manual_run_and_jobs(self):
        api=API()
        api.run.update(path=StageRunBinding.workflow,event='workflow_dispatch')
        raw=(Path(__file__).resolve().parents[1]/StageRunBinding.workflow).read_bytes()
        api.file.update(path=StageRunBinding.workflow,size=len(raw),
                        content=base64.b64encode(raw).decode())
        api.jobs['jobs'][0]['name']='stage'
        api.jobs['jobs'].append({**api.jobs['jobs'][0],'id':455,'name':'contract',
                                 'status':'completed','conclusion':'success'})
        api.jobs['total_count']=2
        stage=StageRunBinding(SOURCE,123,2,api)
        stage.assert_running()
        self.assertEqual(stage.job_id,456)
        api.run['event']='push'
        with self.assertRaises(Exception):stage.assert_running()
        self.assertTrue(stage.failed)
        api.run['event']='workflow_dispatch'
        with self.assertRaisesRegex(Exception,'ALREADY_FAILED'):stage.assert_running()
        api.jobs['jobs'][0]['name']='rehearsal'
        with self.assertRaises(Exception):StageRunBinding(SOURCE,123,2,api).assert_running()

    def test_stage_authentication_precedes_root_request_fetch(self):
        env=dict(EXPECTED_MAIN=SOURCE,ACCEPTED_REQUEST_DIGEST='f'*64,
            GITHUB_REPOSITORY=launcher.REPOSITORY,GITHUB_REF='refs/heads/main',
            GITHUB_SHA=SOURCE,GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
            GITHUB_JOB='stage',GITHUB_WORKFLOW_REF=launcher.REPOSITORY+'/'+
            StageRunBinding.workflow+'@refs/heads/main',GITHUB_WORKFLOW_SHA=SOURCE,
            GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='2',GH_TOKEN='CI')
        api=Mock();api.get.side_effect=ConnectionError('CANCELLED')
        with patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',
             ['launcher','stage','key','known','wheels']),patch.object(launcher,'source_guard'), \
             patch.object(launcher.bundle,'build',return_value=b'CI_SOURCE'), \
             patch.object(launcher.driver,'build',return_value=b'CI_WHEELS'), \
             patch.object(launcher,'MeasuredAPI',return_value=api), \
             patch.object(launcher,'fetch_request') as fetch, \
             patch.object(launcher,'bootstrap') as bootstrap, \
             patch.object(launcher,'oci_client') as oci:
            with self.assertRaises(ConnectionError):launcher.main('stage')
            fetch.assert_not_called();bootstrap.assert_not_called();oci.assert_not_called()


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
        self.root.mkdir(mode=0o700)
        for name in ('requests','claims'): (self.root/name).mkdir(mode=0o700)
        (self.root/'VERSION').write_bytes(requests.VERSION)
        (self.root/'VERSION').chmod(0o600)
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

    def test_missing_namespace_or_claim_ledger_never_reprovisions_on_fetch_or_submit(self):
        for name in ('requests','claims','VERSION'):
            path = self.root/name
            saved = self.root.parent/('saved-'+name)
            path.rename(saved)
            with patch.object(requests,'staged_candidate') as staged:
                with self.assertRaises(Exception): requests.resolve_request(self.sha,SOURCE)
                with self.assertRaises(Exception): requests.submit_candidate(self.raw)
                staged.assert_not_called()
            self.assertFalse(path.exists())
            saved.rename(path)
        with patch.object(requests,'ROOT',self.root.parent/'missing-root'):
            with self.assertRaises(FileNotFoundError): requests.resolve_request(self.sha,SOURCE)
            with self.assertRaises(FileNotFoundError): requests.submit_candidate(self.raw)
            self.assertFalse(requests.ROOT.exists())

    def test_explicit_first_install_is_exclusive_and_never_repairs(self):
        fresh = self.root.parent/'first-install'
        with patch.object(requests,'ROOT',fresh), patch.object(requests.os,'uname',return_value=SimpleNamespace(nodename='autopilot-lite-vnic')):
            accepted = hashlib.sha256(requests.first_install_intent(SOURCE)).hexdigest()
            with self.assertRaises(Exception): requests.first_install(SOURCE,'0'*64)
            self.assertFalse(fresh.exists())
            requests.first_install(SOURCE,accepted)
            requests.validate_namespace()
            with self.assertRaises(FileExistsError): requests.first_install(SOURCE,accepted)
            (fresh/'claims').rmdir()  # Empty disposable fixture; simulate ledger loss.
            with self.assertRaises(FileExistsError): requests.first_install(SOURCE,accepted)
            self.assertFalse((fresh/'claims').exists())

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


@unittest.skipUnless(os.getuid()==0,'staged file ownership needs root in disposable fixture')
class StagingTests(unittest.TestCase):
    setUp = ClaimTests.setUp

    def stage(self, raw=None):
        raw = self.raw if raw is None else raw
        parent = self.root.parent/'home'
        parent.mkdir(mode=0o755)
        home = parent/'ubuntu'; home.mkdir(mode=0o755)
        try: os.chown(home,1001,1001)
        except OSError as exc:
            if exc.errno == 22 and os.environ.get('GITHUB_ACTIONS') != 'true':
                self.skipTest('local UID namespace has no 1001 mapping; CI sudo must run this case')
            raise
        directory = home/requests.STAGING_NAME; directory.mkdir(mode=0o700); os.chown(directory,1001,1001)
        file = directory/(hashlib.sha256(raw).hexdigest()+'.json')
        file.write_bytes(raw); file.chmod(0o600); os.chown(file,1001,1001)
        for item in (patch.object(requests,'STAGING_PARENT',parent),
                     patch.object(requests.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1001,pw_dir=str(home)))):
            item.start(); self.addCleanup(item.stop)
        return directory,file

    def test_missing_root_imports_only_exact_private_canonical_data(self):
        self.stage()
        (self.root/'requests'/(self.sha+'.json')).unlink()  # Disposable fixture only.
        self.assertEqual(requests.resolve_request(self.sha,SOURCE),self.raw)
        self.assertEqual(requests.read_request(self.sha),self.raw)
        self.assertEqual(list((self.root/'claims').iterdir()),[])

    def test_corrupt_existing_root_never_uses_staging_or_overwrites(self):
        self.stage()
        file = self.root/'requests'/(self.sha+'.json')
        file.write_bytes(b'CORRUPTED_FIXTURE')
        with patch.object(requests,'staged_candidate') as staged, self.assertRaises(Exception):
            requests.resolve_request(self.sha,SOURCE)
        staged.assert_not_called()
        self.assertEqual(file.read_bytes(),b'CORRUPTED_FIXTURE')

    def test_stage_mode_owner_link_digest_and_source_faults_refuse(self):
        directory,file = self.stage()
        file.chmod(0o644)
        with self.assertRaises(Exception): requests.staged_candidate(self.sha,SOURCE)
        file.chmod(0o600); os.chown(file,0,0)
        with self.assertRaises(Exception): requests.staged_candidate(self.sha,SOURCE)
        os.chown(file,1001,1001)
        directory.chmod(0o755)
        with self.assertRaises(Exception): requests.staged_candidate(self.sha,SOURCE)
        directory.chmod(0o700)
        os.link(file,directory/'extra-link')
        with self.assertRaises(Exception): requests.staged_candidate(self.sha,SOURCE)
        (directory/'extra-link').unlink()
        with self.assertRaises(Exception): requests.staged_candidate(self.sha,'b'*40)
        file.write_bytes(self.raw+b'\n')
        with self.assertRaises(Exception): requests.staged_candidate(self.sha,SOURCE)

    def test_separate_readonly_schema_can_be_imported_without_stage_authority(self):
        value,_ = request_fixture()
        readonly = encoded(dict(version=1,mode='read_only_rehearsal',source=SOURCE,plan=value['packet']['plan']))
        self.stage(readonly)
        sha = hashlib.sha256(readonly).hexdigest()
        self.assertEqual(requests.resolve_request(sha,SOURCE),readonly)
        with self.assertRaises(Exception): requests.AcceptedRequest(readonly,sha,SOURCE)


class LauncherWiringTests(unittest.TestCase):
    """Real request/derivation/profile parsing; explicit external transport faults."""
    def test_first_install_is_separate_authenticated_choice_and_never_fetches_or_runs_stage(self):
        api = API()
        cls = RehearsalRunBinding
        workflow = (Path(__file__).resolve().parents[1]/cls.workflow).read_bytes()
        api.run.update(path=cls.workflow,event='workflow_dispatch')
        api.file.update(path=cls.workflow,size=len(workflow),content=base64.b64encode(workflow).decode())
        api.jobs['jobs'][0]['name'] = 'rehearsal'
        api.jobs['jobs'].append({**api.jobs['jobs'][0],'id':455,'name':'contract','status':'completed','conclusion':'success'})
        api.jobs['total_count'] = 2
        old_get = api.get
        api.get = lambda path: copy.deepcopy(api.file) if path.startswith('/contents/'+cls.workflow) else old_get(path)
        accepted = hashlib.sha256(requests.first_install_intent(SOURCE)).hexdigest()
        env = dict(EXPECTED_MAIN=SOURCE,ACCEPTED_REQUEST_DIGEST=accepted,GITHUB_REPOSITORY=launcher.REPOSITORY,
            GITHUB_REF='refs/heads/main',GITHUB_SHA=SOURCE,GITHUB_EVENT_NAME='workflow_dispatch',
            GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',GITHUB_JOB='rehearsal',
            GITHUB_WORKFLOW_REF=launcher.REPOSITORY+'/'+cls.workflow+'@refs/heads/main',GITHUB_WORKFLOW_SHA=SOURCE,
            GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='2',GH_TOKEN='CI_TOKEN',REQUEST_STORE_ACTION='first_install')
        with patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',['launcher','rehearsal','key','known','wheels']), \
             patch.object(launcher,'source_guard'),patch.object(launcher.bundle,'build',return_value=b'CI_SOURCE'), \
             patch.object(launcher.driver,'build',return_value=b'CI_WHEELS'),patch.object(launcher,'MeasuredAPI',return_value=api), \
             patch.object(launcher,'bootstrap',return_value='CI_FIRST_INSTALL') as bootstrap, \
             patch.object(launcher.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'NATIVE_REQUEST_STORE_PROVISIONED\n')), \
             patch.object(launcher,'fetch_request') as fetch,patch.object(launcher,'oci_client') as oci,patch('builtins.print') as output:
            launcher.main('rehearsal')
            self.assertEqual(bootstrap.call_args.args[-1],'first_install')
            fetch.assert_not_called();oci.assert_not_called()
            self.assertEqual(json.loads(output.call_args.args[0])['audit'],'NATIVE_REQUEST_STORE_FIRST_INSTALL_PASS')
            os.environ['ACCEPTED_REQUEST_DIGEST']='0'*64
            bootstrap.reset_mock()
            with self.assertRaises(Exception): launcher.main('rehearsal')
            bootstrap.assert_not_called()

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
        if fault == 'rehearsal_refusal':
            completion = dict(kind='NATIVE_REHEARSAL_REFUSED',binding=binding,request_digest=accepted,
                              phase='owner_snapshot',code='REFUSED')
        if fault and fault.startswith('stage_refusal'):
            completion = dict(kind='NATIVE_STAGE_REFUSED',binding=binding,request_digest=accepted,
                              phase='runtime',code='DATABASE_NOT_DRAINED')
            if fault == 'stage_refusal_secret': completion['code']='postgres://secret'
            if fault == 'stage_refusal_binding': completion['binding']='0'*64
            if fault == 'stage_refusal_request': completion['request_digest']='0'*64
        process = Mock(returncode=0)
        process.stdin.closed = False
        process.wait.return_value = 1 if fault == 'host_exit' else 0
        channel = Mock()
        channel.binding = binding
        channel.deadline = 1210.
        channel.receive.side_effect = [dict(kind='NATIVE_STAGE_READY', binding=binding, request_digest=accepted), completion]
        server = Mock(sequence=0)
        units = Mock()
        units.retainer.used = True
        events = []
        clock = [1000.]
        wall_start = launcher.time.time()
        def retained(*args):
            events.append('backup')
            if fault == 'slow_prelaunch': clock[0] = 1041.
            if fault == 'backup': raise ConnectionError('CI_LOST_BACKUP_ACK')
        def launch(*args,**kwargs):
            events.append('launch')
            if fault == 'late_completion': clock[0] = 1310.
            if fault == 'slow_popen':
                clock[0] = 1100.
                channel.deadline = 1310.
            return process
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
                patch.object(launcher.time,'monotonic',side_effect=lambda: clock[0]),
                patch.object(launcher.time,'time',side_effect=lambda: wall_start + clock[0] - 1000.),
                patch.object(StageRunBinding,'workflow_sha256',hashlib.sha256(workflow).hexdigest()),
                patch.object(launcher,'source_guard'),patch.object(launcher.bundle,'build',return_value=b'CI_SOURCE'),
                patch.object(launcher.driver,'build',return_value=b'CI_WHEEL'),
                patch.object(launcher,'bootstrap',return_value='CI_FIXED_CODE'),
                patch.object(launcher,'fetch_request',return_value=raw),patch.object(launcher,'oci_client',return_value=(Mock(),'namespace')),
                patch.object(launcher,'MeasuredStore',return_value=Mock()),
                patch.object(launcher,'restore_assets',return_value=manifest),patch.object(launcher,'connection_parameters'),
                patch.object(launcher,'MeasuredAPI',return_value=api),patch.object(launcher,'verify_prior',side_effect=prior),
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
                if fault == 'stage_refusal':
                    self.assertEqual(launcher.PHASE,'host_runtime:DATABASE_NOT_DRAINED')
                if fault.startswith('stage_refusal'):
                    self.assertNotIn('head',events)
                    server.serve_once.assert_not_called()
            else:
                launcher.main('stage')
                report = json.loads(output.call_args.args[0])
                self.assertTrue(report['host_exited'] and report['prior_host_drained'] and report['independent_readback'])
                self.assertEqual(events,['backup','launch','head','drained'])
                sent = channel.send.call_args_list[0].args[0]
                self.assertEqual(sent['envelope']['credential'],'CI_PRIVATE_OWNER_URI')
                self.assertEqual(sent['envelope']['token'],'CI_PRIVATE_TOKEN')
            if fault in ('prior','backup','slow_prelaunch'): self.assertNotIn('launch',events)
            if fault in ('binding','host_exit','slow_popen'): self.assertNotIn('head',events)
            if fault == 'slow_popen':
                self.assertEqual(channel.send.call_count,1)  # envelope only; never START

    def test_completion_needs_backup_exit_independent_reads_and_final_live_run(self):
        self.exercise()
        for fault in ('prior','backup','binding','host_exit','head','drain','cancel_final','rehearsal_refusal','slow_prelaunch','late_completion','slow_popen',
                      'stage_refusal','stage_refusal_secret','stage_refusal_binding','stage_refusal_request'):
            with self.subTest(fault=fault): self.exercise(fault)


class StageDiagnosticTests(unittest.TestCase):
    def test_host_refusal_preserves_original_error_and_redacts_secrets(self):
        from ops import native_maintenance_stage_host as host
        for exc, code in ((RuntimeError('DATABASE_NOT_DRAINED'),'DATABASE_NOT_DRAINED'),
                          (RuntimeError('RUNTIME_PRIOR_SET_RECONCILIATION_REQUIRED'),'RUNTIME_PRIOR_SET_RECONCILIATION_REQUIRED'),
                          (RuntimeError('postgres://private:password@host'),'REFUSED'),
                          (KeyError('private_field'),'KEY_ERROR')):
            for broken_pipe in (False, True):
                with self.subTest(code=code,broken_pipe=broken_pipe):
                    channel=Mock()
                    if broken_pipe: channel.send.side_effect=BrokenPipeError('private transport')
                    def failed(*args):
                        args[-1].update(channel=channel,phase='runtime')
                        raise exc
                    with patch.object(host,'API'),patch.object(host,'_main',side_effect=failed), \
                         self.assertRaises(type(exc)) as caught:
                        host.main(SOURCE,123,2,'a'*64,'b'*64,'c'*64,{'token':'private-token'})
                    self.assertIs(caught.exception,exc)
                    channel.send.assert_called_once_with(dict(kind='NATIVE_STAGE_REFUSED',
                        binding='b'*64,request_digest='a'*64,phase='runtime',code=code))


class RehearsalDiagnosticTests(unittest.TestCase):
    def test_secret_exception_is_redacted_and_failure_stays_failure(self):
        for message, expected in (('postgres://private:password@host', 'REFUSED'),
                                  ('DATABASE_NOT_DRAINED', 'DATABASE_NOT_DRAINED')):
            channel = Mock()
            def failed(*args):
                args[-1].update(channel=channel,phase='backend_drain')
                raise RuntimeError(message)
            with patch.object(rehearsal,'_main',side_effect=failed), self.assertRaises(SystemExit) as failure:
                rehearsal.main(SOURCE,123,2,'a'*64,'b'*64,'c'*64,{})
            self.assertEqual(failure.exception.code,2)
            self.assertEqual(channel.send.call_args.args[0],dict(kind='NATIVE_REHEARSAL_REFUSED',
                binding='b'*64,request_digest='a'*64,phase='backend_drain',code=expected))

    def test_drain_codes_and_sqlstate_categories_never_expose_details(self):
        import psycopg
        cases = [(RuntimeError('DRAIN_BACKEND_IDENTITY'),'DRAIN_BACKEND_IDENTITY'),
                 (RuntimeError('DRAIN_HOLD_CHANGED'),'DRAIN_HOLD_CHANGED'),
                 (KeyError('private_column'),'KEY_ERROR'),
                 (TypeError('private_value'),'TYPE_ERROR'),
                 (psycopg.errors.InsufficientPrivilege('private SQL and URI'),'DB_PRIVILEGE_ERROR'),
                 (psycopg.errors.UndefinedTable('private SQL and URI'),'DB_QUERY_ERROR'),
                 (psycopg.OperationalError('private URI'),'DB_DRIVER_ERROR'),
                 (psycopg.ProgrammingError('private SQL'),'DB_DRIVER_ERROR'),
                 (psycopg.InterfaceError('private host'),'DB_DRIVER_ERROR'),
                 (RuntimeError('postgres://private:password@host'),'REFUSED')]
        for exc, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(rehearsal.failure_code(exc),expected)
                self.assertIn(expected,rehearsal.SAFE_CODES)

    def test_unavailable_channel_never_prints_original_exception(self):
        repo = Path(__file__).resolve().parents[1]
        code = ("from ops import native_maintenance_stage_rehearsal as r\n"
                "def fail(*args): raise RuntimeError('postgres://private:password@host')\n"
                "r._main=fail\nr.main('a'*40,1,1,'b'*64,'c'*64,'d'*64,{})")
        result = subprocess.run([sys.executable,'-c',code],cwd=repo,capture_output=True)
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(result.stderr,b'')

    def test_failed_channel_is_not_reopened_or_retried(self):
        channel = Mock()
        channel.send.side_effect = TimeoutError('private detail')
        def failed(*args):
            args[-1].update(channel=channel,phase='checkpoint')
            raise RuntimeError('RPC_TIMEOUT')
        with patch.object(rehearsal,'_main',side_effect=failed), self.assertRaises(SystemExit) as failure:
            rehearsal.main(SOURCE,123,2,'a'*64,'b'*64,'c'*64,{})
        self.assertEqual(failure.exception.code,2)
        self.assertEqual(channel.send.call_count,1)


if __name__=='__main__': unittest.main()

class LauncherBudgetTests(unittest.TestCase):
    def api(self):
        api=API()
        api.run.update(path=StageRunBinding.workflow,event='workflow_dispatch')
        raw=(Path(__file__).resolve().parents[1]/StageRunBinding.workflow).read_bytes()
        api.file.update(path=StageRunBinding.workflow,size=len(raw),content=base64.b64encode(raw).decode())
        api.jobs['jobs'][0]['name']='stage'
        api.jobs['jobs'].append({**api.jobs['jobs'][0],'id':455,'name':'contract','status':'completed','conclusion':'success'})
        api.jobs['total_count']=2
        return api

    def test_fixed_stage_budgets_do_not_renew(self):
        from ops.native_maintenance_run_guard import RunBinding
        with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1000):
            host=StageRunBinding(SOURCE,123,2,self.api())
            runner=StageRunBinding(SOURCE,123,2,self.api(),launcher=True)
            self.assertEqual(host.deadline,1120)
            self.assertEqual(runner.deadline,1310)
            with self.assertRaises(Exception):RunBinding(SOURCE,123,2,self.api(),seconds=100)
            with self.assertRaises(TypeError):StageRunBinding(SOURCE,123,2,self.api(),seconds=100)
            with self.assertRaises(Exception):StageRunBinding(SOURCE,123,2,self.api(),launcher=1)
        with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1119):
            host.assert_running();runner.assert_running()
        with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1120):
            with self.assertRaisesRegex(Exception,'EXPIRED'):host.assert_running()
            runner.assert_running()
        self.assertEqual(runner.deadline,1310)
        with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1310):
            with self.assertRaisesRegex(Exception,'EXPIRED'):runner.assert_running()
        with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1001):
            with self.assertRaisesRegex(Exception,'ALREADY_FAILED'):runner.assert_running()

    def test_cancellation_near_either_deadline_latches(self):
        for launcher_mode,elapsed in ((False,119),(True,309)):
            api=self.api()
            with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1000):
                run=StageRunBinding(SOURCE,123,2,api,launcher=launcher_mode);run.assert_running()
            api.run.update(status='completed',conclusion='cancelled')
            with patch('ops.native_maintenance_run_guard.time.monotonic',return_value=1000+elapsed):
                with self.assertRaisesRegex(Exception,'RUN_NOT_RUNNING'):run.assert_running()
                api.run.update(status='in_progress',conclusion=None)
                with self.assertRaisesRegex(Exception,'ALREADY_FAILED'):run.assert_running()

    def test_refusal_diagnostics_do_not_echo_arbitrary_errors(self):
        self.assertEqual(launcher.failure_code(RuntimeError('RUN_BINDING_EXPIRED')),'RUN_BINDING_EXPIRED')
        self.assertEqual(launcher.failure_code(RuntimeError('postgresql://secret')),'REFUSED')
        self.assertEqual(launcher.failure_code(subprocess.TimeoutExpired('secret-command',1)),'PROCESS_TIMEOUT')
        for code in ('API_RESPONSE_INCOMPLETE', 'CHECKPOINT_BUDGET_REFUSED', 'RPC_ARCHIVE_BINDING'):
            self.assertEqual(launcher.failure_code(RuntimeError(code)), code)
            self.assertEqual(launcher.failure_code(RuntimeError(code + ': private payload')), 'REFUSED')
        self.assertEqual(launcher.failure_code(TimeoutError('private endpoint')), 'TRANSPORT_TIMEOUT')
        self.assertEqual(launcher.failure_code(launcher.http.client.RemoteDisconnected('private endpoint')),
                         'TRANSPORT_FAILURE')

    def test_validated_rpc_refusal_preserves_poison_and_redacts_payload(self):
        scope, binding = 'a'*64, 'b'*64
        channel = SimpleNamespace(binding=binding, alive=Mock(), send=Mock(), failed=False)
        store = Mock()
        failure = RuntimeError('CHECKPOINT_CAS_CONFLICT')
        store.compare_head.side_effect = failure
        server = launcher.ObservedStoreServer(channel, scope, store, Mock())
        head = encoded(dict(version=1, scope_digest=scope, sequence=1, previous=None, archive_digest='c'*64))
        record = dict(version=1, binding=binding, scope=scope, sequence=1,
                      method='compare_head', args=[None, launcher.rpc.pack(head)])
        with patch.object(launcher, 'REFUSAL_RPC', None):
            with self.assertRaises(RuntimeError) as caught:
                server.accept(record)
            self.assertIs(caught.exception, failure)
            self.assertEqual(launcher.REFUSAL_RPC, dict(operation='compare_head', sequence=1))
            self.assertTrue(server.failed)
            self.assertTrue(channel.failed)
            channel.send.assert_not_called()
            with self.assertRaisesRegex(Exception, 'RPC_SERVER_UNAVAILABLE'):
                server.accept(record)
            store.compare_head.assert_called_once()

    def test_oci_service_refusal_is_sanitized_and_never_acknowledged(self):
        class ServiceError(Exception):
            def __init__(self, status):
                super().__init__('private credential and object path')
                self.status = status

        sdk = SimpleNamespace(ServiceError=ServiceError)
        with patch.dict(sys.modules, {'oci.exceptions': sdk}):
            for status in (401, 403, 409, 412, 429, 500, 503):
                failure = ServiceError(status)
                channel = SimpleNamespace(binding='b'*64, alive=Mock(), send=Mock(), failed=False)
                store = Mock(spec=['assert_private'])
                store.assert_private.side_effect = failure
                server = launcher.ObservedStoreServer(channel, 'a'*64, store, Mock())
                record = dict(version=1, binding='b'*64, scope='a'*64, sequence=1,
                              method='assert_private', args=[])
                with patch.object(launcher, 'REFUSAL_RPC', None):
                    with self.assertRaises(ServiceError) as caught:
                        server.accept(record)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(launcher.failure_code(caught.exception), 'OCI_SERVICE_' + str(status))
                    self.assertEqual(launcher.REFUSAL_RPC, dict(operation='assert_private', sequence=1))
                    self.assertTrue(server.failed)
                    self.assertTrue(channel.failed)
                    channel.send.assert_not_called()
                    with self.assertRaisesRegex(Exception, 'RPC_SERVER_UNAVAILABLE'):
                        server.accept(record)
                    store.assert_private.assert_called_once()
            for status in ('private payload', True, 599, None):
                self.assertEqual(launcher.failure_code(ServiceError(status)), 'OCI_SERVICE_FAILURE')
            other = RuntimeError('private payload')
            other.status = 403
            self.assertEqual(launcher.failure_code(other), 'REFUSED')

    def test_invalid_rpc_fields_never_enter_refusal_context(self):
        channel = SimpleNamespace(binding='b'*64, alive=Mock(), send=Mock(), failed=False)
        store = Mock()
        server = launcher.ObservedStoreServer(channel, 'a'*64, store, Mock())
        with patch.object(launcher, 'REFUSAL_RPC', None):
            with self.assertRaisesRegex(Exception, 'RPC_REQUEST_IDENTITY'):
                server.accept(dict(version=1, binding='b'*64, scope='a'*64, sequence='private',
                                   method='private endpoint', args=['private payload']))
            self.assertIsNone(launcher.REFUSAL_RPC)
            self.assertTrue(server.failed)
            channel.send.assert_not_called()
            self.assertEqual(store.mock_calls, [])

    def test_extended_launcher_binding_cannot_enter_host_executor(self):
        value,manifest=request_fixture()
        request=requests.AcceptedRequest(encoded(value),digest(value),SOURCE)
        run=StageRunBinding(SOURCE,123,2,self.api(),launcher=True)
        run.assert_running()
        packet=runtime.DerivedStagePacket(request,run,manifest)
        with patch.object(runtime,'SelfSupervisor') as supervisor,self.assertRaisesRegex(Exception,'RUNTIME_COMPONENTS'):
            runtime.stage(packet,run=run,store=Mock(),connect=Mock(),api_token='CI',retain_unit=Mock())
        supervisor.assert_not_called()

    def clock_api(self, age=200):
        from datetime import datetime, timezone
        api = self.api()
        api.jobs['jobs'][0]['started_at'] = datetime.fromtimestamp(2_000_000_000-age, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        return api

    def test_authenticated_job_age_only_shortens_and_never_renews_launcher(self):
        api = self.clock_api()
        with patch.object(launcher.time, 'monotonic', return_value=1000.), patch.object(launcher.time, 'time', return_value=2_000_000_000.):
            run = StageRunBinding(SOURCE,123,2,api,launcher=True)
            run.assert_running()
            self.assertEqual(run.deadline,1250.)  # 480 - 200 - 30, already < prelaunch 270.
            self.assertLess(run.deadline-1000., launcher.STAGE_PRELAUNCH_REQUIRED_SECONDS)
        with patch.object(launcher.time, 'monotonic', return_value=1010.), patch.object(launcher.time, 'time', return_value=2_000_000_010.):
            run.assert_running()
            self.assertEqual(run.deadline,1250.)

    def test_job_start_missing_future_old_or_changed_refuses_and_latches(self):
        for value, code in [(None,'INVALID'),('bad','INVALID'),('2033-05-18T03:33:21Z','FUTURE')]:
            api=self.clock_api()
            api.jobs['jobs'][0]['started_at']=value
            with patch.object(launcher.time,'monotonic',return_value=1000.), patch.object(launcher.time,'time',return_value=2_000_000_000.):
                run=StageRunBinding(SOURCE,123,2,api,launcher=True)
                with self.assertRaisesRegex(Exception,code): run.assert_running()
                self.assertTrue(run.failed)
        api=self.clock_api(451)
        with patch.object(launcher.time,'monotonic',return_value=1000.), patch.object(launcher.time,'time',return_value=2_000_000_000.):
            run=StageRunBinding(SOURCE,123,2,api,launcher=True)
            with self.assertRaisesRegex(Exception,'EXPIRED'): run.assert_running()
        api=self.clock_api()
        with patch.object(launcher.time,'monotonic',return_value=1000.), patch.object(launcher.time,'time',return_value=2_000_000_000.):
            run=StageRunBinding(SOURCE,123,2,api,launcher=True);run.assert_running()
            api.jobs['jobs'][0]['started_at']=self.clock_api(199).jobs['jobs'][0]['started_at']
            with self.assertRaisesRegex(Exception,'CLOCK_CHANGED'):run.assert_running()
            self.assertEqual(run.deadline,1250.)

    def test_wall_clock_jump_in_either_direction_cannot_extend_job_lifetime(self):
        for advance in (4.,16.):
            with patch.object(launcher.time,'monotonic',return_value=1000.), patch.object(launcher.time,'time',return_value=2_000_000_000.):
                run=StageRunBinding(SOURCE,123,2,self.clock_api(),launcher=True);run.assert_running()
            with patch.object(launcher.time,'monotonic',return_value=1010.), patch.object(launcher.time,'time',return_value=2_000_000_000.+advance):
                with self.assertRaisesRegex(Exception,'CLOCK_DRIFT'):run.assert_running()
                self.assertEqual(run.deadline,1250.)
                self.assertTrue(run.failed)
