"""Fixed owner workflow auth and private SSH bootstrap binding."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import time
import unittest

from ops import light_native_pilot_run_guard as owner
from ops import light_native_pilot_owner_runner as runner
from ops.native_maintenance_run_guard import OWNER, OWNER_ID, REPOSITORY


class API:
    def __init__(self):
        source='a'*40
        principal={'login':OWNER,'id':OWNER_ID}
        self.run={'id':123,'run_attempt':2,'head_sha':source,'path':owner.WORKFLOW,
                  'head_branch':'main','event':'workflow_dispatch',
                  'repository':{'full_name':REPOSITORY},
                  'head_repository':{'full_name':REPOSITORY},
                  'actor':principal,'triggering_actor':principal,
                  'status':'in_progress','conclusion':None}
        raw=(Path(__file__).resolve().parents[1]/owner.WORKFLOW).read_bytes()
        self.file={'type':'file','path':owner.WORKFLOW,'encoding':'base64',
                   'size':len(raw),'content':base64.b64encode(raw).decode()}
        self.main={'ref':'refs/heads/main','object':{'type':'commit','sha':source}}
        step={'id':456,'name':'step','run_id':123,'run_attempt':2,
              'head_sha':source,'status':'in_progress','conclusion':None}
        self.jobs={'total_count':2,'jobs':[step,{**step,'id':455,'name':'contract',
                   'status':'completed','conclusion':'success'}]}

    def get(self,path):
        suffix='/actions/runs/123'
        result={suffix:self.run,
                '/contents/'+owner.WORKFLOW+'?ref='+'a'*40:self.file,
                '/git/ref/heads/main':self.main,
                suffix+'/attempts/2/jobs?per_page=100':self.jobs}[path]
        return copy.deepcopy(result)


class OwnerRunGuardTests(unittest.TestCase):
    def test_fixed_workflow_profile_and_binding(self):
        raw=(Path(__file__).resolve().parents[1]/owner.WORKFLOW).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),owner.WORKFLOW_SHA256)
        api=API(); guard=owner.OwnerRunBinding('a'*40,123,2,api)
        guard.assert_running();guard.assert_running()
        self.assertEqual(guard.job_id,456)
        self.assertEqual(owner.OwnerRunBinding.job_names,('contract','step'))

    def test_latches_cancellation_main_and_blob_drift(self):
        for fault in ('cancel','main','blob','contract','expired'):
            with self.subTest(fault=fault):
                api=API(); guard=owner.OwnerRunBinding('a'*40,123,2,api)
                if fault=='cancel':api.run['status']='completed'
                if fault=='main':api.main['object']['sha']='b'*40
                if fault=='blob':api.file['content']=base64.b64encode(b'x').decode();api.file['size']=1
                if fault=='contract':api.jobs['jobs'][1]['conclusion']='failure'
                if fault=='expired':guard.deadline=time.monotonic()-1
                with self.assertRaises(Exception):guard.assert_running()
                self.assertTrue(guard.failed)
                with self.assertRaisesRegex(Exception,'ALREADY_FAILED'):guard.assert_running()

    def test_local_exact_manual_context(self):
        source='a'*40
        env={'GITHUB_SHA':source,'GITHUB_REPOSITORY':REPOSITORY,
             'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch',
             'GITHUB_WORKFLOW_REF':REPOSITORY+'/'+owner.WORKFLOW+'@refs/heads/main',
             'GITHUB_WORKFLOW_SHA':source,'GITHUB_JOB':'step',
             'GITHUB_ACTOR':OWNER,'GITHUB_TRIGGERING_ACTOR':OWNER,
             'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'2'}
        self.assertEqual(owner.local_context(env),(source,123,2))
        for key,bad in [('GITHUB_JOB','contract'),('GITHUB_EVENT_NAME','push'),
                        ('GITHUB_TRIGGERING_ACTOR','other'),('GITHUB_WORKFLOW_SHA','b'*40)]:
            changed=dict(env);changed[key]=bad
            with self.subTest(key=key),self.assertRaises(Exception):owner.local_context(changed)

    def test_bootstrap_embeds_fixed_authenticated_guard(self):
        package=json.dumps({'source':'a'*40,'version':1,
                            'helpers':{'ops/native_maintenance_lifetime.py':'x'}}).encode()
        code=runner.bootstrap(package,'a'*40,hashlib.sha256(package).hexdigest(),
                              'b'*64,'c'*64,123,2)
        self.assertIn('Owner',owner.OwnerRunBinding.__name__)
        self.assertIn('light_native_pilot_run_guard import authenticated',code)
        self.assertIn('run_guard.assert_running()',code)
        compile(code,'bootstrap','exec')


if __name__=='__main__':unittest.main()
