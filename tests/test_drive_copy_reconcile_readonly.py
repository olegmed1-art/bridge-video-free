"""Exact-ID reconciliation and redacted create diagnostics, synthetic transport only."""
import io
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from ops import drive_copy_reconcile_readonly as p
from ops import drive_backup_copy_once as c
from test_drive_backup_copy_once import DATA,HASH,SESSION,response


class ReconcileTests(unittest.TestCase):
    def http(self):return p.Readonly(time.monotonic()+30)

    def test_readonly_endpoint_boundary(self):
        http=self.http()
        for url in (p.ro.API+'/files',p.ro.API+'/files/generateIds',c.UPLOAD,SESSION,
                    p.ro.API+'/files/other',p.ro.API+'/files/'+p.ro.FOLDER,'https://evil.example'):
            with patch.object(http.opener,'open') as call,self.assertRaises(p.ro.Refused):http.call(url,token='PRIVATE')
            call.assert_not_called()
        with self.assertRaises(p.ro.Refused):http.call(p.FILE_URL,form={'x':'y'},token='PRIVATE')

    def test_exact_get_and_bounded_response(self):
        http=self.http()
        with patch.object(http.opener,'open',return_value=response(b'{"id":"exact"}')) as call:
            self.assertEqual(http.call(p.FILE_URL,token='PRIVATE'),{'id':'exact'})
        req=call.call_args.args[0];self.assertEqual(req.method,'GET');self.assertEqual(req.full_url,p.FILE_URL)
        with patch.object(http.opener,'open',return_value=response(b'x'*(p.ro.LIMIT+1))),self.assertRaises(p.ro.Refused):
            http.call(p.FILE_URL,token='PRIVATE')

    def test_original_oauth_no_scope_expansion_and_exact_owner(self):
        http=Mock();http.call.side_effect=[{'access_token':'PRIVATE','token_type':'Bearer'},
            {'user':{'emailAddress':p.ro.OWNER,'permissionId':'owner'}}]
        packed=json.dumps(dict(client_id='synthetic',client_secret='PRIVATE',refresh_token='PRIVATE'))
        self.assertEqual(p.authenticate(http,packed),('PRIVATE','owner'))
        self.assertEqual(set(http.call.call_args_list[0].kwargs['form']),{'grant_type','client_id','client_secret','refresh_token'})
        self.assertEqual(http.call.call_args_list[0].args[0],p.ro.TOKEN_URL)

    def fixture(self):
        http=Mock();http.call.return_value={'object':{'sha':p.ro.MAIN}}
        return http,dict(status='FAIL',second_copy_verified=False,readback_verified=False)

    def test_404_is_observation_not_backup_or_absence_claim(self):
        http,row=self.fixture()
        with patch.object(p,'authenticate',return_value=('PRIVATE','owner')),patch.object(c,'verify_file',side_effect=HTTPError('PRIVATE',404,'PRIVATE',{},None)):
            p.reconcile(http,'PRIVATE',row)
        self.assertEqual(row['status'],'OBSERVED');self.assertEqual(row['observation'],'NOT_FOUND_OR_NOT_VISIBLE')
        self.assertFalse(row['second_copy_verified']);http.readback.assert_not_called();self.assertNotIn('PRIVATE',json.dumps(row))

    def test_success_requires_independent_hash_and_pre_post_acl(self):
        http,row=self.fixture()
        with patch.object(p,'authenticate',return_value=('PRIVATE','owner')),patch.object(c,'verify_file') as verify:
            p.reconcile(http,'PRIVATE',row)
        self.assertEqual(verify.call_count,2);http.readback.assert_called_once_with('PRIVATE')
        self.assertEqual(row['status'],'PASS');self.assertTrue(row['second_copy_verified'])

    def test_metadata_acl_or_hash_failure_cannot_pass(self):
        for point in ('metadata','hash'):
            http,row=self.fixture()
            if point=='hash':http.readback.side_effect=p.ro.Refused
            with patch.object(p,'authenticate',return_value=('PRIVATE','owner')),patch.object(c,'verify_file',side_effect=p.ro.Refused if point=='metadata' else None),self.assertRaises(p.ro.Refused):
                p.reconcile(http,'PRIVATE',row)
            self.assertFalse(row['second_copy_verified'])

    def test_readback_only_exact_bytes_no_files(self):
        http=self.http()
        with patch.object(c,'SIZE',len(DATA)),patch.object(c,'DIGEST',HASH),patch.object(http.opener,'open',return_value=response(DATA,headers={'Content-Length':str(len(DATA))})) as call:
            http.readback('PRIVATE')
        self.assertEqual(call.call_args.args[0].full_url,p.FILE_URL+'?alt=media')

    def test_context_binds_every_field(self):
        sha='a'*40
        env=dict(EXPECTED_REVIEW=sha,EXPECTED_MAIN=p.ro.MAIN,GITHUB_SHA=sha,GITHUB_WORKFLOW_SHA=sha,
            GITHUB_REPOSITORY=p.ro.REPO,GITHUB_REF='refs/heads/'+p.BRANCH,GITHUB_RUN_ID='123',
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
            GITHUB_RUN_ATTEMPT='1',RECONCILE_OPERATION=p.OPERATION,
            GITHUB_WORKFLOW_REF=p.ro.REPO+'/.github/workflows/native-registry-credential-probe.yml@refs/heads/'+p.BRANCH)
        with patch.dict(os.environ,env,clear=True),patch.object(p.subprocess,'run',return_value=Mock(returncode=0,stdout=sha)):
            self.assertEqual(p.context(),sha)
        for key in env:
            bad=dict(env);bad[key]='wrong'
            with patch.dict(os.environ,bad,clear=True),patch.object(p.subprocess,'run') as git,self.assertRaises(p.ro.Refused):p.context()
            git.assert_not_called()

    def test_secret_removed_before_context_failure_sanitized(self):
        def context():
            self.assertNotIn('GOOGLE_DRIVE_OAUTH_JSON',os.environ);raise HTTPError('PRIVATE',403,'PRIVATE',{},None)
        with patch.dict(os.environ,{'GOOGLE_DRIVE_OAUTH_JSON':'PRIVATE'},clear=True),patch.object(p,'context',side_effect=context),patch.object(p.signal,'signal'),patch.object(p.signal,'alarm',create=True),patch.object(p.signal,'SIGALRM',14,create=True):
            row=p.run()
        self.assertEqual(row['http_status'],403);self.assertEqual(row['failure_class'],'HTTP_ERROR')
        self.assertNotIn('PRIVATE',json.dumps(row));self.assertFalse(row['uploads'])

    def test_workflow_one_secret_two_minutes_no_write_entrypoint(self):
        text=Path('.github/workflows/native-registry-credential-probe.yml').read_text()
        for good in (p.BRANCH,p.OPERATION,'timeout-minutes: 2','github.run_attempt == 1','95s python -m ops.drive_copy_reconcile_readonly','persist-credentials: false'):
            self.assertIn(good,text)
        self.assertEqual(text.count('secrets.'),1)
        for bad in ('OCI_CLI_','DATABASE_URL','PASSPHRASE','schedule:','push:','environment:', 'copy_approval','python -m ops.drive_backup_copy_once'):
            self.assertNotIn(bad,text)

    def test_create_diagnostics_distinguish_failures_without_secrets(self):
        cases=[('INIT_POST',[HTTPError('PRIVATE',403,'PRIVATE',{},None)],False,'HTTP_ERROR',403),
               ('SESSION_VALIDATION',[response(headers={'Location':'https://PRIVATE.example'})],False,'GUARD_REFUSED',200),
               ('CONTENT_PUT',[response(headers={'Location':SESSION}),TimeoutError('PRIVATE')],True,'TIMEOUT',None),
               ('FINAL_REPLY',[response(headers={'Location':SESSION}),response(b'{"id":"wrong"}')],True,'GUARD_REFUSED',200)]
        for stage,replies,put,kind,status in cases:
            d=c.Drive(time.monotonic()+30);d.file_id='id';row={}
            with patch.object(c,'SIZE',len(DATA)),patch.object(c,'DIGEST',HASH),patch.object(d.opener,'open',side_effect=replies),self.assertRaises(BaseException):d.create('PRIVATE',DATA,row)
            self.assertEqual(row['create_stage'],stage);self.assertEqual(row['put_attempted'],put)
            self.assertEqual(row['failure_class'],kind);self.assertEqual(row['http_status'],status)
            self.assertEqual(row['create_outcome'],'UNKNOWN');self.assertNotIn('PRIVATE',json.dumps(row))

    def test_documented_session_forms_without_relaxing_security(self):
        for query in ('uploadType=resumable&upload_id=xa298sd_sdlkj2','upload_id=xa298sd_sdlkj2&uploadType=resumable'):
            self.assertEqual(c.session_url(c.UPLOAD+'?'+query),c.UPLOAD+'?'+query)
        for suffix in ('&uploadType=resumable','&extra=x','#fragment','&upload_id=second'):
            with self.assertRaises(p.ro.Refused):c.session_url(SESSION+suffix)


if __name__=='__main__':unittest.main()
