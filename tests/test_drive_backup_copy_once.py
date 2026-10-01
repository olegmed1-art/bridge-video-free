"""Offline synthetic transport and orchestration checks; never reads real secrets."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import unittest
from unittest.mock import Mock,patch
from ops import drive_backup_copy_once as p

DATA=b'Salted__'+b'synthetic cipher'*32
HASH=hashlib.sha256(DATA).hexdigest()
SESSION=p.UPLOAD+'?uploadType=resumable&upload_id=synthetic-session'


def response(body=b'',status=200,headers=None):
    r=Mock(status=status,headers=headers or {}); r.read=io.BytesIO(body).read
    r.__enter__=Mock(return_value=r); r.__exit__=Mock(return_value=False)
    return r


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.size=patch.object(p,'SIZE',len(DATA)); self.size.start()
        self.digest=patch.object(p,'DIGEST',HASH); self.digest.start()
        self.addCleanup(patch.stopall)

    def drive(self): return p.Drive(time.monotonic()+60)

    def test_read_hash_length_prefix_and_deadline(self):
        self.assertEqual(p.read_cipher(io.BytesIO(DATA),time.monotonic()+5,retain=True),DATA)
        for data in (DATA[:-1],DATA+b'x',DATA[:-1]+b'x'):
            with self.assertRaises(p.ro.Refused):p.read_cipher(io.BytesIO(data),time.monotonic()+5)
        with self.assertRaises(p.ro.Refused):p.read_cipher(io.BytesIO(DATA),0)
        wrong=b'NotSalt!'+DATA[8:]
        with patch.object(p,'DIGEST',hashlib.sha256(wrong).hexdigest()),self.assertRaises(p.ro.Refused):
            p.read_cipher(io.BytesIO(wrong),time.monotonic()+5)

    def test_session_url_allowlist(self):
        self.assertEqual(p.session_url(SESSION),SESSION)
        for url in (SESSION.replace('https:','http:'),SESSION.replace('www.googleapis.com','evil.example'),
                    SESSION.replace('/upload/','/other/'),SESSION+'&x=y',SESSION+'&upload_id=two',
                    SESSION+'#fragment',SESSION.replace('synthetic-session',''),
                    SESSION.replace('www.googleapis.com','user@www.googleapis.com'),
                    SESSION.replace('www.googleapis.com','www.googleapis.com:443')):
            with self.subTest(url=url),self.assertRaises((p.ro.Refused,ValueError)):p.session_url(url)

    def test_oci_exact_read_only_and_close(self):
        client=Mock(); client.get_namespace.return_value.data=p.NAMESPACE
        client.get_bucket.return_value.data=Mock(name='ignored')
        bucket=client.get_bucket.return_value.data
        for k,v in dict(name=p.BUCKET,namespace=p.NAMESPACE,compartment_id=p.TENANCY,
                        public_access_type='NoPublicAccess',storage_tier='Standard').items():setattr(bucket,k,v)
        r=client.get_object.return_value; r.status=200
        r.headers={'content-length':str(len(DATA))}; r.data.raw=io.BytesIO(DATA)
        self.assertEqual(p.source_bytes(client,time.monotonic()+5),DATA)
        client.get_object.assert_called_once_with(namespace_name=p.NAMESPACE,bucket_name=p.BUCKET,object_name=p.KEY)
        r.data.close.assert_called_once()
        self.assertEqual([x[0] for x in client.mock_calls],['get_namespace','get_bucket','get_object','get_object().data.close'])

    def test_create_single_session_single_put_no_retry(self):
        d=self.drive(); d.file_id='generated-id'; row={}
        with patch.object(d.opener,'open',side_effect=[response(headers={'Location':SESSION}),
            response(json.dumps({'id':d.file_id}).encode(),status=201)]) as call:
            d.create('PRIVATE_TOKEN',DATA,row)
        self.assertEqual(row['create_outcome'],'CONFIRMED')
        self.assertEqual(call.call_count,2)
        initial,put=[x.args[0] for x in call.call_args_list]
        self.assertEqual(initial.method,'POST'); self.assertEqual(put.method,'PUT'); self.assertEqual(put.data,DATA)
        self.assertEqual(json.loads(initial.data)['id'],'generated-id')
        self.assertEqual(json.loads(initial.data)['parents'],[p.ro.FOLDER])
        with self.assertRaises(p.ro.Refused):d.create('PRIVATE_TOKEN',DATA,row)
        self.assertNotIn('PRIVATE',json.dumps(row))

    def test_ambiguous_init_or_put_stays_unknown_and_never_retries(self):
        for stage in ('init','put'):
            d=self.drive();d.file_id='id';row={}
            replies=[TimeoutError('PRIVATE')] if stage=='init' else [response(headers={'Location':SESSION}),TimeoutError('PRIVATE')]
            with patch.object(d.opener,'open',side_effect=replies) as call,self.assertRaises(TimeoutError):
                d.create('PRIVATE',DATA,row)
            self.assertEqual(row['create_outcome'],'UNKNOWN');self.assertEqual(call.call_count,len(replies))
            with self.assertRaises(p.ro.Refused):d.create('PRIVATE',DATA,row)

    def test_invalid_session_never_sends_ciphertext(self):
        d=self.drive();d.file_id='id';row={}
        with patch.object(d.opener,'open',return_value=response(headers={'Location':'https://evil.example'})) as call, self.assertRaises(p.ro.Refused):
            d.create('PRIVATE',DATA,row)
        self.assertEqual(call.call_count,1);self.assertFalse(d.put)

    def test_cipher_mismatch_prevents_any_mutation(self):
        d=self.drive();d.file_id='id'
        with patch.object(d.opener,'open') as call,self.assertRaises(p.ro.Refused):d.create('PRIVATE',DATA+b'x',{})
        call.assert_not_called()

    def test_http_disallows_delete_patch_external_and_redirects(self):
        d=self.drive()
        for method,url in [('DELETE',p.UPLOAD),('PATCH',p.UPLOAD),('PUT',SESSION),('POST',p.UPLOAD),('GET','https://evil.example')]:
            with self.assertRaises(p.ro.Refused):d.open(url,token='PRIVATE',method=method)
        with self.assertRaises(p.ro.Refused):p.ro.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_generate_id_once_and_validate(self):
        d=self.drive()
        with patch.object(d,'call',return_value={'ids':['generated-id']}):self.assertEqual(d.allocate('PRIVATE'),'generated-id')
        with self.assertRaises(p.ro.Refused):d.allocate('PRIVATE')
        for ids in ([],['one','two'],['../outside'],[None]):
            d=self.drive()
            with patch.object(d,'call',return_value={'ids':ids}),self.assertRaises(p.ro.Refused):d.allocate('PRIVATE')

    def test_independent_readback_bytes_and_encoding(self):
        for data,headers,passed in [(DATA,{'Content-Length':str(len(DATA))},True),
            (DATA[:-1],{'Content-Length':str(len(DATA))},False),
            (DATA,{'Content-Length':str(len(DATA)),'Content-Encoding':'gzip'},False)]:
            d=self.drive();d.file_id='id'
            with patch.object(d.opener,'open',return_value=response(data,headers=headers)) as call:
                if passed:d.readback('PRIVATE')
                else:
                    with self.assertRaises(p.ro.Refused):d.readback('PRIVATE')
            self.assertEqual(call.call_args.args[0].full_url,p.ro.API+'/files/id?alt=media')

    def transfer_fixture(self):
        d=Mock(deadline=time.monotonic()+60);d.call.return_value={'files':[]};d.allocate.return_value='generated-id'
        d.create.side_effect=lambda token,cipher,row:row.update(create_outcome='CONFIRMED')
        row={'create_outcome':'NOT_ATTEMPTED'}
        def observe(http,packed,probe):probe['existing_write_scope']='PRESENT';return 'PRIVATE','owner'
        patch.object(p.ro,'observe',side_effect=observe).start()
        patch.object(p,'source_bytes',return_value=DATA).start()
        patch.object(p,'fresh_destination').start();patch.object(p,'verify_file').start()
        return d,row

    def test_intent_unknown_durable_before_create_and_order(self):
        d,row=self.transfer_fixture();events=[]
        def record(value):
            events.append('intent');self.assertEqual(value['create_outcome'],'UNKNOWN')
            self.assertEqual(value['drive_file_id'],'generated-id');d.create.assert_not_called()
        d.create.side_effect=lambda token,cipher,value:(events.append('create'),value.update(create_outcome='CONFIRMED'))
        with patch.object(p,'record',side_effect=record):p.transfer(Mock(),d,'PRIVATE',row)
        self.assertEqual(events,['intent','create']);self.assertEqual(row['status'],'PASS')
        d.readback.assert_called_once();self.assertTrue(row['second_copy_verified'])

    def test_failed_intent_prevents_create(self):
        d,row=self.transfer_fixture()
        with patch.object(p,'record',side_effect=OSError),self.assertRaises(OSError):p.transfer(Mock(),d,'PRIVATE',row)
        d.create.assert_not_called();self.assertEqual(row['create_outcome'],'NOT_ATTEMPTED')

    def test_duplicates_or_pagination_prevent_create(self):
        for result in ({'files':[{'id':'old'}]},{'files':[],'nextPageToken':'more'},{}):
            d,row=self.transfer_fixture();d.call.return_value=result
            with self.assertRaises(p.ro.Refused):p.transfer(Mock(),d,'PRIVATE',row)
            d.create.assert_not_called()

    def test_privacy_failure_before_mutation_and_after_confirmed_create(self):
        d,row=self.transfer_fixture()
        p.fresh_destination.side_effect=p.ro.Refused
        with self.assertRaises(p.ro.Refused):p.transfer(Mock(),d,'PRIVATE',row)
        d.create.assert_not_called()
        d,row=self.transfer_fixture();p.verify_file.side_effect=p.ro.Refused
        with patch.object(p,'record'),self.assertRaises(p.ro.Refused):p.transfer(Mock(),d,'PRIVATE',row)
        self.assertEqual(row['create_outcome'],'CONFIRMED');d.readback.assert_not_called()

    def test_file_identity_size_owner_parent_and_acl(self):
        d=self.drive();d.file_id='generated-id'
        value=dict(id=d.file_id,name=p.NAME,mimeType='application/octet-stream',size=str(p.SIZE),parents=[p.ro.FOLDER],
                   shared=False,trashed=False,ownedByMe=True,owners=[dict(emailAddress=p.ro.OWNER,permissionId='owner',me=True)])
        with patch.object(d,'call',return_value=value),patch.object(p.ro,'owner_acl') as acl:p.verify_file(d,'PRIVATE','owner')
        acl.assert_called_once()
        for key,new in [('id','wrong'),('name','wrong'),('size','0'),('parents',[]),('shared',True),('trashed',True),('driveId','shared-drive'),('owners',[])]:
            changed=copy.deepcopy(value);changed[key]=new
            with patch.object(d,'call',return_value=changed),self.assertRaises(p.ro.Refused):p.verify_file(d,'PRIVATE','owner')

    def env(self):
        sha='a'*40
        return dict(EXPECTED_REVIEW=sha,EXPECTED_MAIN=p.ro.MAIN,GITHUB_SHA=sha,GITHUB_WORKFLOW_SHA=sha,
            GITHUB_REPOSITORY=p.ro.REPO,GITHUB_REF='refs/heads/'+p.BRANCH,GITHUB_RUN_ID='123',
            GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_ACTOR='olegmed1-art',GITHUB_TRIGGERING_ACTOR='olegmed1-art',
            GITHUB_RUN_ATTEMPT='1',COPY_OPERATION=p.OPERATION,COPY_APPROVAL=p.OPERATION+':'+sha+':one-create-approved',
            GITHUB_WORKFLOW_REF=p.ro.REPO+'/.github/workflows/native-registry-credential-probe.yml@refs/heads/'+p.BRANCH,
            COPY_STARTED='1000',COPY_DEADLINE='1250')

    def test_context_exact_identity_and_bounded_approval(self):
        with patch.dict(os.environ,self.env(),clear=True),patch.object(p.subprocess,'run',return_value=Mock(returncode=0,stdout='a'*40)),patch.object(p.time,'time',return_value=1010):
            self.assertEqual(p.context(),('a'*40,225))
        for field in self.env():
            env=self.env();env[field]='wrong'
            with patch.dict(os.environ,env,clear=True),patch.object(p.subprocess,'run',return_value=Mock(returncode=0,stdout='a'*40)),self.assertRaises((p.ro.Refused,ValueError)):
                p.context()

    def test_workflow_only_existing_credentials_and_explicit_approval(self):
        text=Path('.github/workflows/native-registry-credential-probe.yml').read_text()
        for good in ('timeout-minutes: 5','github.run_attempt == 1',p.BRANCH,p.OPERATION,
                     'inputs.expected_review_sha == github.sha','persist-credentials: false',
                     'inputs.copy_approval == format','ulimit -c 0','--kill-after=5s 250s'):
            self.assertIn(good,text)
        self.assertEqual(set(re.findall(r'secrets\.([A-Z_]+)',text)),{'GOOGLE_DRIVE_OAUTH_JSON',
            'OCI_CLI_USER','OCI_CLI_TENANCY','OCI_CLI_FINGERPRINT','OCI_CLI_KEY_CONTENT','OCI_CLI_REGION'})
        for bad in ('upload-artifact','DATABASE_URL','PASSPHRASE','id-token: write','environment:',
                    'google-github-actions/auth','schedule:','push:'):
            self.assertNotIn(bad,text)

    def test_secrets_removed_before_git_and_failures_sanitized(self):
        values={'GOOGLE_DRIVE_OAUTH_JSON':'PRIVATE',**{'OCI_CLI_'+k:'PRIVATE' for k in ('USER','TENANCY','FINGERPRINT','KEY_CONTENT','REGION')}}
        def context():
            self.assertFalse(set(values)&set(os.environ));raise RuntimeError('PRIVATE')
        with patch.dict(os.environ,values,clear=True),patch.object(p,'context',side_effect=context), \
             patch.object(p.signal,'setitimer',create=True),patch.object(p.signal,'ITIMER_REAL',0,create=True),patch.object(p,'make_client') as client:
            row=p.run()
        self.assertEqual(row['status'],'FAIL');self.assertEqual(row['create_outcome'],'NOT_ATTEMPTED')
        self.assertNotIn('PRIVATE',json.dumps(row));client.assert_not_called()

    def test_record_flush_fsync_and_safe_receipt(self):
        output=Mock();output.__enter__=Mock(return_value=output);output.__exit__=Mock(return_value=False)
        with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':'synthetic-summary'}),patch('builtins.open',return_value=output),patch.object(p.os,'fsync') as sync:
            p.record({'create_outcome':'UNKNOWN','drive_file_id':'generated-id'})
        output.flush.assert_called_once();sync.assert_called_once_with(output.fileno.return_value)

    def test_missing_scope_or_source_hash_failure_never_creates(self):
        d,row=self.transfer_fixture()
        def no_scope(http,packed,probe):probe['existing_write_scope']='NOT_PROVEN';return 'PRIVATE','owner'
        p.ro.observe.side_effect=no_scope
        with self.assertRaises(p.ro.Refused):p.transfer(Mock(),d,'PRIVATE',row)
        d.create.assert_not_called();p.source_bytes.assert_not_called()
        d,row=self.transfer_fixture();p.source_bytes.side_effect=p.ro.Refused
        with self.assertRaises(p.ro.Refused):p.transfer(Mock(),d,'PRIVATE',row)
        d.create.assert_not_called()


if __name__=='__main__':unittest.main()
