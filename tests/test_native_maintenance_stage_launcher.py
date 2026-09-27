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
        for mode in ('fetch','drain','first_install','candidate','stage','rehearsal'):
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
             patch.object(launcher,'API',return_value=api), \
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
             patch.object(launcher.driver,'build',return_value=b'CI_WHEELS'),patch.object(launcher,'API',return_value=api), \
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
        for fault in ('prior','backup','binding','host_exit','head','drain','cancel_final','rehearsal_refusal'):
            with self.subTest(fault=fault): self.exercise(fault)


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
