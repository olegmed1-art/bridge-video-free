import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from ops import drive_copy_same_id_recovery as p
from ops import drive_backup_copy_once as c
from test_drive_backup_copy_once import DATA,HASH,SESSION,response


class RecoveryTests(unittest.TestCase):
    def http(self):return p.SameIdDrive(time.monotonic()+30)

    def fixture(self):
        http=Mock(deadline=time.monotonic()+30);http.create.side_effect=lambda token,data,row:row.update(create_outcome='CONFIRMED')
        row={'create_outcome':'NOT_ATTEMPTED','status':'FAIL','readback_verified':False,'second_copy_verified':False}
        def auth(http,packed,probe):probe['existing_write_scope']='PRESENT';return 'PRIVATE','owner'
        patch.object(p.ro,'observe',side_effect=auth).start();patch.object(c,'source_bytes',return_value=DATA).start()
        patch.object(c,'fresh_destination').start();patch.object(c,'verify_file').start();patch.object(c,'record').start()
        return http,row

    def tearDown(self):patch.stopall()

    def test_no_new_id_list_or_update(self):
        http=self.http();self.assertEqual(http.file_id,p.FILE_ID)
        with self.assertRaises(p.ro.Refused):http.allocate('PRIVATE')
        for url in (p.ro.API+'/files',p.ro.API+'/files/generateIds'):
            with self.assertRaises(p.ro.Refused):http.call(url,token='PRIVATE')
        for method in ('DELETE','PATCH'):
            with self.assertRaises(p.ro.Refused):http.open(c.UPLOAD,token='PRIVATE',method=method)

    def test_doc_initiation200_final200_and201_same_id(self):
        for status in (200,201):
            http=self.http();row={}
            with patch.object(c,'SIZE',len(DATA)),patch.object(c,'DIGEST',HASH),patch.object(http.opener,'open',side_effect=[response(headers={'Location':SESSION}),response(json.dumps({'id':p.FILE_ID}).encode(),status=status)]) as call:
                http.create('PRIVATE',DATA,row)
            initial,put=[x.args[0] for x in call.call_args_list]
            self.assertEqual(json.loads(initial.data)['id'],p.FILE_ID)
            self.assertEqual(put.data,DATA);self.assertEqual((http.post_count,http.put_count),(1,1))
            self.assertEqual(row['create_outcome'],'CONFIRMED');self.assertTrue(row['final_response_accepted'])
            with self.assertRaises(p.ro.Refused):http.open(SESSION,token='PRIVATE',method='PUT',body=DATA)

    def test_initiation201_or_missing_location_never_puts(self):
        for r in (response(status=201,headers={'Location':SESSION}),response(headers={})):
            http=self.http();row={}
            with patch.object(c,'SIZE',len(DATA)),patch.object(c,'DIGEST',HASH),patch.object(http.opener,'open',return_value=r) as call,self.assertRaises(p.ro.Refused):http.create('PRIVATE',DATA,row)
            self.assertEqual(call.call_count,1);self.assertFalse(row['put_attempted'])

    def test_409_stops_no_retry_and_preserves_unknown(self):
        for stage in ('POST','PUT'):
            http=self.http();row={}
            error=HTTPError('PRIVATE',409,'PRIVATE',{},None)
            replies=[error] if stage=='POST' else [response(headers={'Location':SESSION}),error]
            with patch.object(c,'SIZE',len(DATA)),patch.object(c,'DIGEST',HASH),patch.object(http.opener,'open',side_effect=replies) as call,self.assertRaises(HTTPError):http.create('PRIVATE',DATA,row)
            self.assertEqual(call.call_count,len(replies));self.assertEqual(row['http_status'],409)
            self.assertEqual(row['create_outcome'],'UNKNOWN');self.assertNotIn('PRIVATE',json.dumps(row))

    def test_existing_file_verified_without_create_or_oci_get(self):
        http,row=self.fixture()
        with patch.object(p,'exists',return_value=True):p.transfer(Mock(),http,'PRIVATE',row)
        self.assertEqual(row['recovery_outcome'],'EXISTING_FILE_READONLY');self.assertTrue(row['second_copy_verified'])
        http.create.assert_not_called();c.source_bytes.assert_not_called()

    def test_only_exact_get404_allows_recovery(self):
        for status in (403,404,409,500):
            http=Mock();http.call.side_effect=HTTPError('PRIVATE',status,'PRIVATE',{},None)
            if status==404:self.assertFalse(p.exists(http,'PRIVATE'))
            else:
                with self.assertRaises(HTTPError):p.exists(http,'PRIVATE')
            self.assertEqual(http.call.call_args.args[0],p.ro.API+'/files/'+p.FILE_ID)

    def test_two_404_then_one_create_and_verified_readback(self):
        http,row=self.fixture()
        with patch.object(p,'exists',side_effect=[False,False]) as get:p.transfer(Mock(),http,'PRIVATE',row)
        self.assertEqual(get.call_count,2);http.create.assert_called_once();http.readback.assert_called_once()
        self.assertEqual(row['drive_file_id'],p.FILE_ID);self.assertEqual(row['recovery_outcome'],'RECOVERED_VERIFIED')
        self.assertTrue(row['source_verified']);self.assertTrue(row['second_copy_verified'])

    def test_file_appears_between_checks_no_write(self):
        http,row=self.fixture()
        with patch.object(p,'exists',side_effect=[False,True]):p.transfer(Mock(),http,'PRIVATE',row)
        http.create.assert_not_called();self.assertEqual(row['recovery_outcome'],'EXISTING_FILE_READONLY')

    def test_conflict_transfer_and_intent_failure_stop(self):
        http,row=self.fixture();http.create.side_effect=HTTPError('PRIVATE',409,'PRIVATE',{},None)
        with patch.object(p,'exists',return_value=False),self.assertRaises(HTTPError):p.transfer(Mock(),http,'PRIVATE',row)
        self.assertEqual(row['recovery_outcome'],'CONFLICT_STOP');http.readback.assert_not_called()
        http,row=self.fixture();c.record.side_effect=OSError
        with patch.object(p,'exists',return_value=False),self.assertRaises(OSError):p.transfer(Mock(),http,'PRIVATE',row)
        http.create.assert_not_called();self.assertEqual(row['create_outcome'],'NOT_ATTEMPTED')

    def test_url_documented_forms_and_fixed_rejection_reasons(self):
        for url in (SESSION,c.UPLOAD+'?upload_id=xa298sd_sdlkj2&uploadType=resumable'):
            self.assertEqual(c.session_url(url),url)
        for url,code in [(None,'MISSING_OR_LENGTH'),(SESSION+'\n','CONTROL_OR_SPACE'),
            (SESSION.replace('https:','http:'),'ORIGIN'),(SESSION.replace('www.googleapis.com','www.googleapis.com.evil'),'ORIGIN'),
            (SESSION.replace('/upload/','/other/'),'PATH_OR_FRAGMENT'),(SESSION+'#x','PATH_OR_FRAGMENT'),
            (SESSION+'&other=value','QUERY_KEYS'),(SESSION+'&uploadType=resumable','UPLOAD_TYPE'),
            (SESSION+'&upload_id=two','UPLOAD_ID_FORMAT'),(SESSION.replace('synthetic-session','has.dot'),'UPLOAD_ID_FORMAT')]:
            with self.assertRaises(c.SessionURLRefused) as caught:c.session_url(url)
            self.assertEqual(caught.exception.code,code)

    def test_recovery_context_uses_new_branch_and_approval(self):
        with patch.object(c,'context',return_value=('sha',100)) as context:self.assertEqual(p.context(),('sha',100))
        context.assert_called_once_with(branch=p.BRANCH,operation=p.OPERATION)

    def test_discovery_protocol_alias_and_consistent_dual_form(self):
        queries=('upload_protocol=resumable&upload_id=synthetic-session',
                 'upload_id=synthetic-session&upload_protocol=resumable',
                 'uploadType=resumable&upload_protocol=resumable&upload_id=synthetic-session')
        for query in queries:
            url=c.UPLOAD+'?'+query;receipt={}
            self.assertEqual(c.session_url(url,receipt),url)
            self.assertTrue(receipt['session_query_shape']['has_upload_protocol'])
            self.assertEqual(receipt['session_query_shape']['unknown_key_count'],0)
            for status in (200,201):
                http=self.http();row={}
                with patch.object(c,'SIZE',len(DATA)),patch.object(c,'DIGEST',HASH),patch.object(http.opener,'open',side_effect=[response(headers={'Location':url}),response(json.dumps({'id':p.FILE_ID}).encode(),status=status)]) as call:
                    http.create('PRIVATE_TOKEN',DATA,row)
                self.assertEqual(call.call_args_list[1].args[0].full_url,url)
                self.assertEqual(row['create_outcome'],'CONFIRMED')
                self.assertNotIn('synthetic-session',json.dumps(row));self.assertNotIn('PRIVATE',json.dumps(row))

    def test_protocol_query_negative_cases_preserve_security(self):
        bad=('upload_id=synthetic',
             'upload_protocol=raw&upload_id=synthetic',
             'upload_protocol=Resumable&upload_id=synthetic',
             'upload_protocol=&upload_id=synthetic',
             'upload_protocol=resumable&upload_protocol=resumable&upload_id=synthetic',
             'uploadType=media&upload_protocol=resumable&upload_id=synthetic',
             'uploadType=resumable&upload_protocol=raw&upload_id=synthetic',
             'upload_protocol=resumable&upload_id=synthetic&upload_id=second',
             'upload_protocol=resumable&upload_id=synthetic&access_token=PRIVATE',
             'upload_protocol=resumable&upload_id=synthetic&redirect_uri=https%3A%2F%2Fevil.example',
             'upload_protocol=resumable&upload_id=synthetic&fields=id',
             'upload_protocol=resumable&upload_id=synthetic&PRIVATE_UNKNOWN=PRIVATE_VALUE')
        for query in bad:
            receipt={}
            with self.assertRaises(c.SessionURLRefused):c.session_url(c.UPLOAD+'?'+query,receipt)
            self.assertNotIn('PRIVATE',json.dumps(receipt));self.assertNotIn('evil',json.dumps(receipt))
        valid=c.UPLOAD+'?upload_protocol=resumable&upload_id=synthetic'
        for url in (valid.replace('https:','http:'),valid.replace('www.googleapis.com','evil.example'),
                    valid.replace('www.googleapis.com','www.googleapis.com:443'),
                    valid.replace('/upload/','/other/'),valid+'#fragment',valid+'\n'):
            with self.assertRaises(c.SessionURLRefused):c.session_url(url)

    def test_workflow_exact_new_gate_five_minutes_no_auth_change(self):
        text=Path('.github/workflows/native-registry-credential-probe.yml').read_text()
        for good in (p.BRANCH,p.OPERATION,'timeout-minutes: 5','github.run_attempt == 1',
                     'inputs.copy_approval == format','python -m ops.drive_copy_same_id_recovery'):
            self.assertIn(good,text)
        self.assertEqual(text.count('secrets.'),6)
        for bad in ('schedule:','push:','DATABASE_URL','PASSPHRASE','environment:','id-token: write'):
            self.assertNotIn(bad,text)


if __name__=='__main__':unittest.main()
