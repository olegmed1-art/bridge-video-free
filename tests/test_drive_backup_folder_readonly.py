"""Synthetic OAuth/API fixtures only; no runtime credentials or network."""
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import unittest
from unittest.mock import Mock,patch
from ops import drive_backup_folder_readonly as p


class ProbeTests(unittest.TestCase):
    def env(self):
        sha='a'*40
        return dict(EXPECTED_REVIEW=sha,EXPECTED_MAIN=p.MAIN,GITHUB_SHA=sha,GITHUB_WORKFLOW_SHA=sha,
            GITHUB_REPOSITORY=p.REPO,GITHUB_REF='refs/heads/'+p.BRANCH,GITHUB_RUN_ID='123',
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
            GITHUB_RUN_ATTEMPT='1',DRIVE_PROBE_OPERATION=p.OPERATION,
            GITHUB_WORKFLOW_REF=p.REPO+'/.github/workflows/native-registry-credential-probe.yml@refs/heads/'+p.BRANCH)

    def fixture(self):
        def folder(identifier):
            return dict(id=identifier,mimeType='application/vnd.google-apps.folder',trashed=False,
                shared=False,ownedByMe=True,parents=[p.PARENT],capabilities={'canAddChildren':True},
                owners=[dict(emailAddress=p.OWNER,permissionId='synthetic-owner',me=True)])
        acl={'permissions':[dict(id='synthetic-owner',type='user',role='owner',emailAddress=p.OWNER)]}
        return [{'object':{'sha':p.MAIN}},dict(access_token='PRIVATE_TOKEN',token_type='Bearer',scope='https://www.googleapis.com/auth/drive'),
            {'user':{'emailAddress':p.OWNER,'permissionId':'synthetic-owner'}},folder(p.FOLDER),
            json.loads(json.dumps(acl)),folder(p.PARENT),json.loads(json.dumps(acl)),{'object':{'sha':p.MAIN}}]

    def observe(self,replies):
        row=dict(gates={},existing_write_scope='NOT_PROVEN',status='FAIL')
        http=Mock(); http.call.side_effect=replies
        credential=json.dumps(dict(client_id='synthetic',client_secret='PRIVATE_SECRET',refresh_token='PRIVATE_REFRESH'))
        try: p.observe(http,credential,row)
        except BaseException: row['status']='FAIL'
        return row,http

    def test_complete_readonly_contract_and_no_scope_expansion(self):
        row,http=self.observe(self.fixture())
        self.assertEqual(row['status'],'PASS')
        self.assertEqual(set(row['gates'].values()),{'PASS'})
        self.assertEqual(len(http.call.call_args_list),8)
        calls=http.call.call_args_list
        self.assertEqual(calls[1].args[0],p.TOKEN_URL)
        self.assertEqual(set(calls[1].kwargs['form']),{'grant_type','client_id','client_secret','refresh_token'})
        self.assertNotIn('PRIVATE',json.dumps(row))
        self.assertEqual(row['existing_write_scope'],'PRESENT')

    def test_each_context_field_rejects_drift(self):
        for field in self.env():
            env=self.env();env[field]='wrong'
            with patch.dict(os.environ,env,clear=True),patch.object(subprocess,'run') as run,self.assertRaises(p.Refused):
                p.context()
            run.assert_not_called()
        with patch.dict(os.environ,self.env(),clear=True),patch.object(subprocess,'run',return_value=Mock(returncode=0,stdout='a'*40)):
            self.assertEqual(p.context(),'a'*40)

    def test_wrong_identity_folder_parent_and_capabilities_refuse(self):
        for index,field,value in [(2,'user',{'emailAddress':'wrong','permissionId':'synthetic-owner'}),
            (3,'id','wrong'),(3,'parents',['wrong']),(3,'trashed',True),(3,'shared',True),
            (3,'ownedByMe',False),(3,'driveId','shared-drive'),(3,'owners',[]),
            (3,'capabilities',{'canAddChildren':False}),(5,'id','wrong'),(5,'shared',True)]:
            data=self.fixture();data[index][field]=value
            row,http=self.observe(data)
            self.assertEqual(row['status'],'FAIL')
            self.assertLess(http.call.call_count,8)

    def test_incomplete_or_nonowner_acl_never_passes(self):
        for index in (4,6):
            for acl in ({},{'permissions':[]},{'permissions':[{'type':'anyone','role':'reader'}]},
                        {'permissions':self.fixture()[index]['permissions'],'nextPageToken':'more'},
                        {'permissions':self.fixture()[index]['permissions']*2}):
                data=self.fixture();data[index]=acl
                row,http=self.observe(data);self.assertEqual(row['status'],'FAIL')

    def test_missing_grant_scope_does_not_claim_upload_scope(self):
        data=self.fixture();del data[1]['scope']
        row,http=self.observe(data)
        self.assertEqual(row['status'],'PASS')
        self.assertEqual(row['existing_write_scope'],'NOT_PROVEN')

    def test_http_allowlist_redirect_and_response_limit(self):
        http=p.Http(time.monotonic()+10)
        for url,kw in [('https://evil.example/',{}),(p.API+'/files',{}),
                       (p.API+'/files/'+p.FOLDER,{'form':{'x':'y'}}),
                       (p.TOKEN_URL,{'token':'PRIVATE'})]:
            with self.assertRaises(p.Refused):http.call(url,**kw)
        with self.assertRaises(p.Refused):p.NoRedirect().redirect_request(None,None,None,None,None,None)
        response=Mock(status=200);response.read.return_value=b'x'*(p.LIMIT+1)
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch.object(http.opener,'open',return_value=response),self.assertRaises(p.Refused):
            http.call(p.MAIN_URL)
        response.read.assert_called_once_with(p.LIMIT+1)

    def test_refusal_sanitized_and_secret_removed_before_context(self):
        env=self.env();env['GOOGLE_DRIVE_OAUTH_JSON']='PRIVATE_CREDENTIAL'
        with patch.dict(os.environ,env,clear=True),patch.object(p,'context',side_effect=RuntimeError('PRIVATE')) as context, \
             patch.object(p.signal,'signal'),patch.object(p.signal,'alarm',create=True),patch.object(p.signal,'SIGALRM',14,create=True), \
             patch.object(p,'Http') as http:
            row=p.run();self.assertNotIn('GOOGLE_DRIVE_OAUTH_JSON',os.environ)
        self.assertEqual(row['status'],'FAIL');http.assert_not_called()
        self.assertNotIn('PRIVATE',json.dumps(row))

    def test_workflow_only_existing_drive_secret_dispatch_and_bounded(self):
        text=Path('.github/workflows/native-registry-credential-probe.yml').read_text()
        for good in ('timeout-minutes: 2','github.run_attempt == 1',p.BRANCH,p.OPERATION,
                     'inputs.expected_review_sha == github.sha','persist-credentials: false'):
            self.assertIn(good,text)
        self.assertEqual(text.count('secrets.'),1)
        for bad in ('upload-artifact','OCI_CLI_','DATABASE_URL','id-token: write','google-github-actions/auth','schedule:','push:'):
            self.assertNotIn(bad,text)


if __name__=='__main__':unittest.main()
