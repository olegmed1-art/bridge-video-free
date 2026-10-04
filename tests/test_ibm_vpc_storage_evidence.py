import copy
import io
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from ops import ibm_vpc_storage_evidence as p

def fixture():
    return {'id': p.INSTANCE_ID, 'name': p.INSTANCE_NAME, 'status': 'stopped',
            'zone': {'name': 'eu-de-2'}, 'profile': {'name': 'bx2d-2x8', 'secret': 'DO_NOT_PRINT'},
            'disks': [{'id': '1234-disk', 'name': 'scratch', 'size': 260,
                       'interface_type': 'virtio_blk', 'resource_type': 'instance_disk',
                       'href': 'https://evil.invalid/DO_NOT_PRINT', 'secret': 'DO_NOT_PRINT'}],
            'boot_volume_attachment': {'id': 'boot-attachment', 'name': 'boot',
                                       'volume': {'id': 'boot-volume', 'name': 'root'}},
            'volume_attachments': [{'id': 'boot-attachment', 'name': 'boot'}],
            'user_data': 'DO_NOT_PRINT', 'keys': ['DO_NOT_PRINT'], 'network_interfaces': ['DO_NOT_PRINT']}

class StorageTests(unittest.TestCase):
    def test_exact_two_gets_and_allowlist(self):
        with patch.object(p, 'verify_identity') as identity, patch.object(p, 'read_json', side_effect=[fixture(), fixture()]) as send:
            result=p.collect('TEST_TOKEN_DO_NOT_PRINT')
        identity.assert_called_once_with('TEST_TOKEN_DO_NOT_PRINT')
        self.assertEqual(2, send.call_count)
        for call in send.call_args_list:
            request=call.args[0]
            self.assertEqual('GET', request.method)
            self.assertIsNone(request.data)
            self.assertEqual(p._instance_url('eu-de', p.INSTANCE_ID), request.full_url)
        self.assertNotIn('DO_NOT_PRINT', json.dumps(result))
        self.assertEqual('UNKNOWN', result['observations'][0]['instance']['guest_device_correlation'])
        self.assertEqual('REFERENCE_ONLY',result['observations'][0]['instance']['volume_attachments'][0]['volume']['reason'])

    def test_target_state_zone_and_malformed_refused(self):
        cases=[]
        for k,v in [('id','another-instance'),('name','another-name'),('status','running'),('zone',{'name':'eu-de-1'}),('disks',None),('disks', [{}]),('volume_attachments',None),('profile',{}),('boot_volume_attachment',{})]:
            f=fixture();f[k]=v;cases.append(f)
        for f in cases:
            with self.subTest(case=f),patch.object(p,'verify_identity'),patch.object(p,'read_json',return_value=f) as send:
                with self.assertRaises(p.BoundedClientError):p.collect('token')
                self.assertEqual(1,send.call_count)

    def test_drift_is_not_success(self):
        b=fixture();b['disks'][0]['id']='changed-disk'
        with patch.object(p,'verify_identity'),patch.object(p,'read_json',side_effect=[fixture(),b]):
            with self.assertRaisesRegex(p.BoundedClientError,'storage_metadata_drift'):p.collect('token')

    def test_failure_not_retried_and_no_body_disclosure(self):
        out=io.StringIO()
        with patch.object(p,'obtain_token',return_value='TOKEN'),patch.object(p,'verify_identity'),patch.object(p,'read_json',side_effect=p.BoundedClientError('provider_http_403')) as send,patch('sys.stderr',out):
            self.assertEqual(3,p.main())
        self.assertEqual(1,send.call_count)
        self.assertEqual('IBM_STORAGE_EVIDENCE_RESULT=REFUSED reason=provider_http_403\n',out.getvalue())

    def test_identity_failure_before_vpc_get(self):
        with patch.object(p,'verify_identity',side_effect=p.BoundedClientError('identity_invalid')),patch.object(p,'read_json') as send:
            with self.assertRaises(p.BoundedClientError):p.collect('token')
        send.assert_not_called()

    def test_schema_bounds(self):
        cases=[]
        for field,n in [('disks',33),('volume_attachments',65)]:
            f=fixture();f[field]=f[field]*n;cases.append(f)
        for field,val in [('size',True),('size',0),('name','secret\nvalue'),('resource_type','volume')]:
            f=fixture();f['disks'][0][field]=val;cases.append(f)
        f=fixture();f['disks']*=2;cases.append(f)
        for f in cases:
            with self.subTest(case=f),self.assertRaises(p.BoundedClientError):p.storage(f)

    def test_absent_list_not_empty_and_present_empty_not_vdd_proof(self):
        f=fixture();f['disks']=[]
        self.assertEqual([],p.storage(f)['disks'])
        del f['disks']
        with self.assertRaises(p.BoundedClientError):p.storage(f)

    def test_unexpected_exception_does_not_disclose(self):
        out=io.StringIO()
        with patch.object(p,'obtain_token',side_effect=RuntimeError('DO_NOT_PRINT')),patch('sys.stderr',out):
            self.assertEqual(4,p.main())
        self.assertNotIn('DO_NOT_PRINT',out.getvalue())

    def test_redirect_is_refused(self):
        with self.assertRaisesRegex(p.BoundedClientError,'storage_redirect_refused'):
            p.NoRedirect().redirect_request(None,None,302,'secret',{},'https://evil.invalid/secret')

    def test_iam_has_same_resident_credential_contract_and_redirect_guard(self):
        with patch.object(p,'transport_json',return_value={'access_token':'x'*32}) as send:
            self.assertEqual('x'*32,p.obtain_token('SYNTHETIC_KEY'))
        req=send.call_args.args[0]
        self.assertEqual(p.IAM_URL,req.full_url)
        self.assertEqual('POST',req.method)
        self.assertEqual({'grant_type':['urn:ibm:params:oauth:grant-type:apikey'],'apikey':['SYNTHETIC_KEY']},p.urllib.parse.parse_qs(req.data.decode()))
        with patch.object(p.urllib.request,'build_opener') as build:
            build.return_value.open.side_effect=p.BoundedClientError('storage_redirect_refused')
            with self.assertRaisesRegex(p.BoundedClientError,'storage_redirect_refused'):p.transport_json(req)
            self.assertIsInstance(build.call_args.args[0],p.NoRedirect)

    def test_bad_key_and_token_refused(self):
        for key in ('','bad key'):
            with patch.object(p,'transport_json') as send,self.assertRaises(p.BoundedClientError):p.obtain_token(key)
            send.assert_not_called()
        for token in (None,'short','x '*20):
            with patch.object(p,'transport_json',return_value={'access_token':token}),self.assertRaises(p.BoundedClientError):p.obtain_token('KEY')

    def test_transport_is_exact_bounded_and_ignores_raw_extras(self):
        request=p.urllib.request.Request(p._instance_url('eu-de',p.INSTANCE_ID),method='GET')
        response=io.BytesIO(json.dumps(fixture()).encode())
        with patch.object(p.urllib.request,'build_opener') as build:
            build.return_value.open.return_value=response
            result=p.storage(p.read_json(request))
            self.assertEqual(20,build.return_value.open.call_args.kwargs['timeout'])
            self.assertIsInstance(build.call_args.args[0],p.NoRedirect)
        self.assertNotIn('DO_NOT_PRINT',json.dumps(result))

    def test_wrong_endpoint_and_post_blocked_before_transport(self):
        for request in (p.urllib.request.Request('https://evil.invalid',method='GET'),
                        p.urllib.request.Request(p._instance_url('eu-de',p.INSTANCE_ID),method='POST')):
            with patch.object(p.urllib.request,'build_opener') as build,self.assertRaises(p.BoundedClientError):p.read_json(request)
            build.assert_not_called()

    def test_raw_error_and_oversize_invalid_body_do_not_leak(self):
        request=p.urllib.request.Request(p._instance_url('eu-de',p.INSTANCE_ID),method='GET')
        for body in (b'DO_NOT_PRINT',b'[]',b'x'*(1024*1024+1)):
            with patch.object(p.urllib.request,'build_opener') as build:
                build.return_value.open.return_value=io.BytesIO(body)
                with self.assertRaises(p.BoundedClientError) as caught:p.read_json(request)
            self.assertNotIn('DO_NOT_PRINT',str(caught.exception))
        error=p.urllib.error.HTTPError(request.full_url,403,'DO_NOT_PRINT',{},io.BytesIO(b'DO_NOT_PRINT'))
        with patch.object(p.urllib.request,'build_opener') as build:
            build.return_value.open.side_effect=error
            with self.assertRaisesRegex(p.BoundedClientError,'^storage_http_403$'):p.read_json(request)

if __name__=='__main__':unittest.main()
