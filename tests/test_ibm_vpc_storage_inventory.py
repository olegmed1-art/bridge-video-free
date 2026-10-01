from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import time
import unittest
from unittest import mock
import urllib.error
import urllib.parse

from ops import ibm_vpc_storage_inventory as inv
from ops import ibm_vpc_oracle_probe as oracle


def fixture():
    return [
        {"id": inv.INSTANCE_ID, "name": inv.INSTANCE_NAME, "status": "stopped", "zone": {"name": "eu-de-2"},
         "boot_volume_attachment": {"id": "attachment", "volume": {"id": inv.VOLUME_ID}}},
        {"id": inv.VOLUME_ID, "zone": {"name": "eu-de-2"}, "status": "available", "capacity": 100,
         "profile": {"name": "general-purpose"}, "storage_generation": 1, "encryption": "provider_managed",
         "health_state": "ok", "busy": False, "attachment_state": "attached",
         "volume_attachments": [{"id": "attachment", "type": "boot", "instance": {"id": inv.INSTANCE_ID},
                                 "delete_volume_on_instance_delete": True}]},
        {"id": inv.IMAGE_ID, "name": inv.IMAGE_NAME, "source_volume": {"id": inv.VOLUME_ID},
         "encryption": "none", "status": "pending", "file": {}},
        {"snapshots": []},
    ]


def next_url(start="page2", **changes):
    q = {**inv.FIXED_QUERY, "source_volume.id": inv.VOLUME_ID, "limit": "50", "start": start, **changes}
    return inv.ORIGIN + inv.SNAPSHOTS_PATH + "?" + urllib.parse.urlencode(q)


def jwt():
    claims = {"iam_id": oracle.SERVICE_ID, "account": {"bss": oracle.ACCOUNT_ID}, "exp": time.time() + 3600}
    return 'header.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'


class InventoryTests(unittest.TestCase):
    def setUp(self):
        # Any forgotten mock must fail locally, never contact a cloud endpoint.
        p = mock.patch('socket.socket.connect', side_effect=AssertionError('LIVE_NETWORK_FORBIDDEN'))
        p.start()
        self.addCleanup(p.stop)

    def run_inventory(self, responses=None):
        with mock.patch.object(inv, 'request_json', side_effect=responses or fixture()) as transport:
            result = inv.safe_inventory('TOKEN_SENTINEL')
        return result, transport

    def test_exact_get_allowlist_and_no_body(self):
        result, transport = self.run_inventory()
        self.assertEqual('PASS', result['result'])
        self.assertEqual('pending', result['image']['status'])
        self.assertIsNone(result['image']['file_size_gb'])
        self.assertEqual(0, result['snapshot_count'])
        self.assertEqual(4, transport.call_count)
        for call in transport.call_args_list:
            req = call.args[0]
            self.assertEqual('GET', req.get_method())
            self.assertIsNone(req.data)
            url = urllib.parse.urlsplit(req.full_url)
            self.assertEqual('eu-de.iaas.cloud.ibm.com', url.netloc)
            self.assertIn(url.path, (inv.INSTANCE_PATH, inv.VOLUME_PATH, inv.SNAPSHOTS_PATH, inv.IMAGE_PATH))
            self.assertEqual(['2026-09-22'], urllib.parse.parse_qs(url.query)['version'])
        self.assertEqual([inv.VOLUME_ID], urllib.parse.parse_qs(url.query)['source_volume.id'])

    def test_mutation_and_unscoped_paths_refused_before_transport(self):
        for path in ['/v1/images', '/v1/volumes', inv.INSTANCE_PATH+'/actions', '/v1/snapshots/other',
                     'https://evil.invalid', inv.VOLUME_PATH+'?extra=1']:
            with self.subTest(path=path), mock.patch.object(inv, 'request_json') as transport:
                with self.assertRaises(inv.InventoryError): inv.get('token', path)
                transport.assert_not_called()

    def test_target_mismatches_stop_before_next_request(self):
        for target, field, value in [(0,'id','wrong'), (0,'name','wrong'), (0,'zone',{'name':'us-south-1'}),
                                    (0,'boot_volume_attachment',{}), (1,'id','wrong'),
                                    (1,'volume_attachments',[]), (1,'volume_attachments',[{'type':'data'}])]:
            data = fixture(); data[target][field] = value
            report, transport = self.run_inventory(data)
            self.assertEqual('BLOCKED', report['result'])
            self.assertLessEqual(transport.call_count, target+1)

    def test_filtered_pagination_rebuilds_url_and_rejects_widening(self):
        data = fixture(); data[3]['next'] = {'href': next_url()}; data.append({'snapshots': []})
        report, transport = self.run_inventory(data)
        self.assertTrue(report['snapshots_complete'])
        self.assertEqual(next_url(), transport.call_args.args[0].full_url)
        for href in [next_url().replace('eu-de.iaas.cloud.ibm.com','evil.invalid'),
                     next_url(**{'source_volume.id':'other'}), next_url(limit='100'),
                     next_url()+'&start=duplicate', next_url().replace('/snapshots','/images'),
                     next_url()+'&unknown=1', next_url().replace('https:','http:')]:
            data = fixture(); data[3]['next'] = {'href': href}
            report, transport = self.run_inventory(data)
            self.assertEqual('BLOCKED', report['result'])
            self.assertEqual(4, transport.call_count)

    def test_page_limit_and_cycle_not_empty_success(self):
        for second in ['page2','page3']:
            data = fixture(); data[3]['next'] = {'href': next_url()}
            data.append({'snapshots': [], 'next': {'href': next_url(second)}})
            report, transport = self.run_inventory(data)
            self.assertEqual('BLOCKED', report['result'])
            self.assertNotIn('snapshot_count', report)
            self.assertEqual(5, transport.call_count)

    def test_snapshot_source_schema_and_duplicates(self):
        good = {'id':'r010-11111111-1111-1111-1111-111111111111','source_volume':{'id':inv.VOLUME_ID},
                'lifecycle_state':'stable','bootable':True}
        data = fixture(); data[3]['snapshots'] = [good]
        report, _ = self.run_inventory(data)
        self.assertEqual(1, report['snapshot_count'])
        for entries in [[{**good,'source_volume':{'id':'other'}}], [good,good], [None], [good]*51]:
            data = fixture(); data[3]['snapshots'] = entries
            self.assertEqual('BLOCKED', self.run_inventory(data)[0]['result'])

    def test_secrets_and_arbitrary_fields_never_in_report(self):
        data=fixture(); data[1].update(encryption_key={'crn':'SECRET_KEY_CRN'}, name='SECRET_NAME', user_tags=['SECRET_TAG'])
        report, _=self.run_inventory(data)
        report['unexpected']='SECRET_REMOTE'
        sanitized=inv.sanitize_report(report)
        self.assertTrue(sanitized['encryption_key_present'])
        self.assertNotIn('SECRET', json.dumps(sanitized))
        for report in [{'result':'BLOCKED','reason':'secret_safe_format'}, {'result':'PASS','secret':'SECRET'}]:
            with self.assertRaises(inv.InventoryError): inv.sanitize_report(report)

    def test_http_denial_is_explicit_no_body_or_retry(self):
        for code in [401,403,404,429,500]:
            opener=mock.Mock(); opener.open.side_effect=urllib.error.HTTPError('url',code,'SECRET',{},io.BytesIO(b'SECRET_BODY'))
            with mock.patch.object(inv.urllib.request,'build_opener',return_value=opener):
                report=inv.safe_inventory('token')
            self.assertEqual({'result':'BLOCKED','reason':f'http_{code}'},report)
            self.assertEqual(1,opener.open.call_count)

    def test_redirects_bodies_and_schema(self):
        for code in [301,302,303,307,308]:
            with self.assertRaisesRegex(inv.InventoryError,'redirect_refused'):
                inv.NoRedirect().redirect_request(None,None,code,'SECRET',{},'https://evil.invalid')
        for body, expected in [(b'x'*(inv.MAX_BYTES+1),'response_too_large'),(b'<SECRET>','invalid_json'),(b'[]','invalid_schema')]:
            response=mock.MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=body
            opener=mock.Mock();opener.open.return_value=response
            with mock.patch.object(inv.urllib.request,'build_opener',return_value=opener):
                self.assertEqual(expected,inv.safe_inventory('token')['reason'])

    def test_unknown_values_and_wrong_types_fail_closed(self):
        for key,value in [('profile',{'name':'future'}),('storage_generation',3),('busy','false'),('capacity',True),('encryption','future')]:
            data=fixture();data[1][key]=value
            self.assertEqual('BLOCKED',self.run_inventory(data)[0]['result'])

    def test_ssh_uses_existing_route_and_token_only_stdin(self):
        report,_=self.run_inventory(); token=jwt(); output=io.StringIO()
        with mock.patch.dict(inv.os.environ,{'IBM_CLOUD_API_KEY':'KEY_SENTINEL'}), \
             mock.patch.object(inv,'request_json',return_value={'access_token':token}) as iam, \
             mock.patch.object(inv.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(report),'SECRET_STDERR')) as run, \
             contextlib.redirect_stdout(output):
            self.assertEqual(0,inv.main(['--ssh-key','key','--known-hosts','hosts']))
        req=iam.call_args.args[0]
        self.assertEqual('https://iam.cloud.ibm.com/identity/token',req.full_url)
        self.assertEqual('POST',req.method)
        command=run.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes',command)
        self.assertIn('UserKnownHostsFile=hosts',command)
        self.assertIn(inv.ORACLE_HOST,command)
        self.assertNotIn(token,' '.join(command))
        self.assertEqual({'token':token},json.loads(run.call_args.kwargs['input']))
        self.assertNotIn('KEY_SENTINEL',run.call_args.kwargs['input'])
        self.assertNotIn('SECRET',output.getvalue())

    def test_workflow_mode_gates_and_default(self):
        text=Path('.github/workflows/ibm-vpc-power-probe.yml').read_text()
        self.assertIn('default: status',text)
        self.assertIn("inputs.mode == 'storage_inventory' && inputs.test_oracle == false",text)
        self.assertIn("github.ref == 'refs/heads/review/ibm-storage-inventory-20261001'",text)
        self.assertEqual(2,text.count("(inputs.mode == 'status' || inputs.mode == '')"))
        self.assertNotIn('pull_request_target:',text)

    def test_exact_image_states_and_optional_pending_file_size(self):
        for status in inv.IMAGE_STATES:
            data=fixture();data[2]['status']=status
            if status not in {'pending','failed'}: data[2]['file']={'size':7}
            report,transport=self.run_inventory(data)
            self.assertEqual('PASS',report['result'])
            self.assertEqual(status,report['image']['status'])
            self.assertEqual(report,inv.sanitize_report(report))
            self.assertEqual(inv.ORIGIN+inv.IMAGE_PATH+'?version=2026-09-22&generation=2',transport.call_args_list[2].args[0].full_url)

    def test_image_wrong_identity_encryption_or_schema_blocks(self):
        for key,value in [('id','other'),('name','other'),('source_volume',{'id':'other'}),
                          ('encryption','user_managed'),('encryption_key',{'crn':'SECRET'}),
                          ('status','unknown'),('file',{'size':True}),('file',{'size':None}),
                          ('file',{'size':-1}),('file',None)]:
            data=fixture();data[2][key]=value
            report,transport=self.run_inventory(data)
            self.assertEqual('BLOCKED',report['result'])
            self.assertEqual(3,transport.call_count)
            self.assertNotIn('SECRET',json.dumps(report))
        data=fixture();data[2]['status']='available'
        self.assertEqual('image_file_size_missing',self.run_inventory(data)[0]['reason'])

    def test_image_get_403_is_explicit_and_no_list_fallback(self):
        data=fixture()[:2]+[inv.InventoryError('http_403')]
        report,transport=self.run_inventory(data)
        self.assertEqual({'result':'BLOCKED','reason':'http_403'},report)
        self.assertEqual(3,transport.call_count)

    def test_remote_image_output_is_sanitized(self):
        report,_=self.run_inventory();report['image']['extra']='SECRET'
        self.assertNotIn('SECRET',json.dumps(inv.sanitize_report(report)))
        for key,value in [('id','other'),('name','SECRET'),('source_volume_match',False),
                          ('provider_managed',False),('file_size_gb',23),('file_size_present',1)]:
            changed=copy.deepcopy(report);changed['image'][key]=value
            with self.assertRaises(inv.InventoryError): inv.sanitize_report(changed)


if __name__ == '__main__':
    unittest.main()
