"""Incident authority and private transport failures; offline only."""
import ast
import base64
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from ops.incident import light_restore_authority_20260928 as auth
from ops.incident import light_restore_runner_20260928 as runner

EXECUTION='a'*40
PROFILE=b'name: fixed fixture\n'

def request():
    now=int(time.time())
    stamp=lambda n:datetime.fromtimestamp(n,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return auth.encoded(dict(version=1,purpose='complete_existing_restore_AFTER_only',source=auth.SOURCE,
        scope=auth.SCOPE,head=auth.HEAD,pair=auth.PAIR,execution=EXECUTION,nonce='b'*32,
        not_before=stamp(now-30),expires_at=stamp(now+1770),owner=auth.OWNER,
        coverage=['direct_owner_sql','host_administration','workflow_administration','workflow_reruns','main_pushes'],
        evidence='offline fixture, not an approval'))


class API:
    def __init__(self):
        self.head=EXECUTION;self.main=auth.SOURCE;self.job=22
    def get(self,path):
        if path=='/actions/runs/11':
            return dict(id=11,run_attempt=1,head_sha=self.head,head_branch=auth.BRANCH,
                event='workflow_dispatch',status='in_progress',conclusion=None,path=auth.WORKFLOW,
                repository={'full_name':auth.REPO},head_repository={'full_name':auth.REPO},
                actor=dict(login=auth.OWNER,id=auth.OWNER_ID),triggering_actor=dict(login=auth.OWNER,id=auth.OWNER_ID))
        if path.endswith('/jobs?per_page=100'):
            return dict(total_count=2,jobs=[dict(id=21 if n=='contract' else self.job,name=n,run_id=11,run_attempt=1,
                head_sha=EXECUTION,status='completed' if n=='contract' else 'in_progress',
                conclusion='success' if n=='contract' else None) for n in ('contract','restore')])
        if path=='/git/ref/heads/main':return dict(ref='refs/heads/main',object=dict(type='commit',sha=self.main))
        if path.startswith('/contents/'):
            return dict(type='file',path=auth.WORKFLOW,encoding='base64',size=len(PROFILE),content=base64.b64encode(PROFILE).decode())
        raise AssertionError(path)


class TransportTests(unittest.TestCase):
    def authority(self,api):
        return auth.Authority(request(),EXECUTION,11,1,api,auth.sha(PROFILE),host=True)

    def test_live_execution_and_historical_main_are_both_required(self):
        api=API();a=self.authority(api);a.assert_live()
        self.assertEqual(a.run_identity,dict(run_id=11,attempt=1,job_id=22))
        for field,value in [('head',auth.SOURCE),('main',EXECUTION),('job',23)]:
            api=API();a=self.authority(api);a.assert_live();setattr(api,field,value)
            with self.assertRaises(RuntimeError):a.assert_live()
            self.assertTrue(a.failed)

    def test_deadline_is_not_renewed_and_failure_latches(self):
        a=self.authority(API());deadline=a.deadline;a.assert_live();self.assertEqual(a.deadline,deadline)
        with patch.object(auth.time,'monotonic',return_value=deadline+1):
            with self.assertRaises(RuntimeError):a.assert_current()
        with self.assertRaises(RuntimeError):a.assert_current()

    def test_authorization_refuses_long_window_changed_scope_and_missing_coverage(self):
        for key,value in [('scope','c'*64),('owner','other'),('coverage',[])]:
            data=json.loads(request());data[key]=value
            with self.assertRaises(RuntimeError):auth.authorization(auth.encoded(data),EXECUTION)
        data=json.loads(request());data['expires_at']='2099-01-01T00:00:00Z'
        with self.assertRaises(RuntimeError):auth.authorization(auth.encoded(data),EXECUTION)

    def test_context_rejects_main_rerun_and_wrong_job(self):
        env=dict(ACCEPTED_EXECUTION_SHA=EXECUTION,INCIDENT_AUTHORIZATION=request().decode(),
            GITHUB_REPOSITORY=auth.REPO,GITHUB_REF='refs/heads/'+auth.BRANCH,GITHUB_SHA=EXECUTION,
            GITHUB_WORKFLOW_SHA=EXECUTION,GITHUB_WORKFLOW_REF=auth.REPO+'/'+auth.WORKFLOW+'@refs/heads/'+auth.BRANCH,
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR=auth.OWNER,GITHUB_TRIGGERING_ACTOR=auth.OWNER,
            GITHUB_JOB='restore',GITHUB_RUN_ID='11',GITHUB_RUN_ATTEMPT='1')
        runner.context(env)
        for key,value in [('GITHUB_REF','refs/heads/main'),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_JOB','contract')]:
            with self.assertRaises(RuntimeError):runner.context({**env,key:value})

    def test_generated_inner_refuses_without_secret_output(self):
        files={k:(runner.ROOT/'ops/incident'/p).read_bytes() for k,p in runner.NAMES.items()}
        outer,codes=runner.bootstrap(files,EXECUTION,11,1,auth.sha(PROFILE),'d'*64)
        tree=ast.parse(outer)
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                  and n.func.attr in ('managed_stage','managed'))
        inner=ast.literal_eval(call.args[0])
        # Host identity gate refuses outside Oracle before examining the payload.
        result=subprocess.run([sys.executable,'-I','-B','-S','-c',inner],input=b'private-token-value',
                              capture_output=True,timeout=10)
        self.assertEqual(result.returncode,2)
        self.assertNotIn(b'private-token-value',result.stdout+result.stderr)
        size=int.from_bytes(result.stdout[:4],'big');report=json.loads(result.stdout[4:])
        self.assertEqual(size,len(result.stdout)-4)
        self.assertEqual(report['kind'],'INCIDENT_REFUSED')
        self.assertFalse(report['effects_possible'])
        self.assertEqual(report['binding'],'d'*64)

    def test_workflow_has_only_contract_and_incident_restore_jobs(self):
        import re
        raw=(runner.ROOT/auth.WORKFLOW).read_text()
        jobs=raw.split('jobs:',1)[1]
        self.assertEqual(re.findall(r'^  ([a-z_]+):$',jobs,re.M),['contract','restore'])
        command='python ops/incident/light_restore_runner_20260928.py'
        self.assertEqual(raw.count(command),1)
        self.assertIn(command,jobs.split('  restore:',1)[1])
        self.assertIn('INCIDENT_AUTHORIZATION:',jobs.split('  restore:',1)[1])
        self.assertNotIn('native_maintenance_stage_launcher.py',raw)

    def test_retention_unknown_ack_never_retries(self):
        class Store:
            guard=lambda self:None
            assert_private=lambda self:None
            _budget=lambda self,n:None
            writes=0
            def _read(self,*a):return None
            def _put(self,*a):self.writes+=1;raise ConnectionError('lost')
        store=Store()
        with self.assertRaises(ConnectionError):runner.retain_once(store,'fixture',b'fixture')
        self.assertEqual(store.writes,1)


if __name__=='__main__':unittest.main()
