"""Offline transport gates; no production dispatch or SSH execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ops/incident/light_after_probe_runner_20260928.py'
spec = importlib.util.spec_from_file_location('incident_runner', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def env(self):
        return dict(ACCEPTED_PROBE_SHA='a'*40,EXPECTED_MAIN=runner.probe.SOURCE,
            GITHUB_SHA='a'*40,GITHUB_WORKFLOW_SHA='a'*40,GITHUB_REPOSITORY=runner.REPOSITORY,
            GITHUB_REF='refs/heads/'+runner.BRANCH,
            GITHUB_WORKFLOW_REF=runner.REPOSITORY+'/'+runner.WORKFLOW+'@refs/heads/'+runner.BRANCH,
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',
            GITHUB_TRIGGERING_ACTOR='olegmed1-art',GITHUB_JOB='probe',GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1')

    def test_only_exact_reviewed_owner_manual_branch_context(self):
        env = self.env()
        self.assertEqual(runner.context(env), ('a'*40,123,1))
        for key, value in [('GITHUB_EVENT_NAME','pull_request'),('GITHUB_REF','refs/heads/main'),
                ('GITHUB_TRIGGERING_ACTOR','other'),('ACCEPTED_PROBE_SHA','b'*40),
                ('EXPECTED_MAIN','b'*40),('GITHUB_JOB','attest'),('GITHUB_WORKFLOW_SHA','b'*40)]:
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'PROBE_DISPATCH_CONTEXT'):
                runner.context({**env,key:value})

    def report(self, **extra):
        return dict(audit='LIGHT_AFTER_READ_ONLY_PROBE',source=runner.probe.SOURCE,
            production_mutations=False,resume_authorized=False,historical_cause_proven=False,
            binding='b'*64,status='PASS',phase='complete',**extra)

    def test_bound_report_only_and_no_refusal_detail_leak(self):
        good = self.report()
        self.assertEqual(runner.validate_report(json.dumps(good).encode(),0,'b'*64),good)
        for bad in [{**good,'binding':'c'*64},{**good,'resume_authorized':True},
                    {**good,'production_mutations':0},{**good,'secret':'password'},
                    {**good,'status':'REFUSED','code':'secret-token'}]:
            with self.assertRaises(RuntimeError):
                runner.validate_report(json.dumps(bad).encode(),0,'b'*64)
        refused={**good,'status':'REFUSED','phase':'owner_backend_drain','code':'DATABASE_NOT_DRAINED'}
        self.assertEqual(runner.validate_report(json.dumps(refused).encode(),2,'b'*64),refused)
        for phase in ('supervisor', 'supervisor_final'):
            report = {**refused, 'phase': phase, 'code': 'RUNTIME_OTHER_SUPERVISOR_ACTIVE'}
            self.assertEqual(runner.validate_report(json.dumps(report).encode(),2,'b'*64), report)
        with self.assertRaises(RuntimeError):
            runner.validate_report(json.dumps(good).encode(),2,'b'*64)

    def test_live_api_gates_validate_jobs_source_and_owner(self):
        run=dict(id=123,run_attempt=1,head_sha='a'*40,head_branch=runner.BRANCH,
            event='workflow_dispatch',status='in_progress',conclusion=None,path=runner.WORKFLOW,
            repository={'full_name':runner.REPOSITORY},head_repository={'full_name':runner.REPOSITORY},
            actor={'login':'olegmed1-art','id':runner.OWNER_ID},
            triggering_actor={'login':'olegmed1-art','id':runner.OWNER_ID})
        rows=[dict(id=i+1,name=name,run_id=123,run_attempt=1,head_sha='a'*40,
                   status=status,conclusion=conclusion) for i,(name,status,conclusion) in enumerate(
                       [('contract','completed','success'),('probe','in_progress',None)])]
        api=Mock();api._token='private'
        api.get.side_effect=[run,{'total_count':2,'jobs':rows}]
        with patch.object(runner,'Transport'),patch.object(runner,'source_matches') as main:
            runner.live_context(api,'a'*40,123,1)
            main.assert_called_once()
        for bad in [{**run,'head_sha':'c'*40},{**run,'status':'completed'},
                    {**run,'triggering_actor':{'login':'other','id':runner.OWNER_ID}}]:
            api.get.side_effect=[bad]
            with self.assertRaises(RuntimeError):
                runner.live_context(api,'a'*40,123,1)
        api.get.side_effect=[run,{'total_count':2,'jobs':[rows[0],{**rows[1],'conclusion':'success'}]}]
        with self.assertRaises(RuntimeError):
            runner.live_context(api,'a'*40,123,1)

    def test_bootstrap_real_subprocess_refusal_is_sanitized(self):
        # Execute the generated inner code under an inert lifetime harness;
        # never call systemd/SSH or live probe functions in this test.
        fake_lifetime=b'''def managed_stage(code, encoded, source, run):
 exec(code, {'__name__':'__main__'})
'''
        helper=runner.HELPER.read_bytes()
        with patch.object(runner.bundle,'git',return_value=fake_lifetime):
            code=runner.bootstrap(ROOT,helper,'123-1','b'*64)
        result=subprocess.run([sys.executable,'-I','-B','-S','-c',code],
                              input=b'private-secret-invalid-json',capture_output=True,timeout=10)
        self.assertEqual(result.returncode,2)
        value=runner.validate_report(result.stdout,2,'b'*64)
        self.assertEqual(value['status'],'REFUSED')
        self.assertNotIn(b'private-secret',result.stdout+result.stderr)
        self.assertEqual(result.stderr,b'')

    def test_real_bootstrap_is_bounded_and_uses_original_lifetime(self):
        code=runner.bootstrap(ROOT,runner.HELPER.read_bytes(),'123-1','b'*64)
        self.assertIn('lifetime.managed_stage(',code)
        compile(code,'<bootstrap>','exec')
        self.assertLess(len(code.encode()),98304)
        lifetime=runner.bundle.git(ROOT,'show',runner.probe.SOURCE+':ops/native_maintenance_lifetime.py')
        self.assertIn(b"'--property=KillMode=control-group'",lifetime)
        self.assertIn(b'STAGE_RUNTIME_SECONDS = 140',lifetime)

    def test_workflow_pr_contract_has_no_live_secrets(self):
        text=(ROOT/runner.WORKFLOW).read_text()
        contract=text.split('  contract:',1)[1].split('  probe:',1)[0]
        self.assertNotIn('secrets.',contract)
        self.assertIn("github.event_name == 'workflow_dispatch'",text.split('  probe:',1)[1])
        self.assertIn('inputs.expected_main_sha == github.sha',text)
        self.assertIn('fetch-depth: 0',text)


if __name__ == '__main__':
    unittest.main()
