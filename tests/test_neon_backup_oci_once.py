"""Synthetic only. OCI HTTP/socket transport blocked; no credentials read."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from ops import neon_backup_oci_once as o

BODY = b'Salted__' + b'synthetic-encrypted-fixture' * 10


class Error(Exception):
    def __init__(self, status):
        super().__init__('PRIVATE_DIAGNOSTIC_CREDENTIAL')
        self.status = status


class StorageTests(unittest.TestCase):
    def env(self):
        return dict(EXPECTED_REVIEW='a'*40, GITHUB_WORKFLOW_SHA='a'*40,
            GITHUB_WORKFLOW_REF=o.validation.context_guard.REPOSITORY +
            '/.github/workflows/native-registry-credential-probe.yml@refs/heads/' + o.validation.context_guard.BRANCH,
            OCI_WRITE_APPROVAL=o.TOKEN + ':' + 'a'*40 + ':policies-reviewed:one-create-approved',
            GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1')

    def client(self):
        client = Mock()
        client.get_namespace.return_value = NS(data=o.NAMESPACE)
        client.get_bucket.return_value = NS(data=NS(name=o.BUCKET, namespace=o.NAMESPACE,
            compartment_id=o.TENANCY, public_access_type='NoPublicAccess', storage_tier='Standard',
            versioning='Disabled', auto_tiering='Disabled', kms_key_id=None))
        client.put_object.return_value = NS(status=200)
        raw = io.BytesIO(BODY)
        stream = NS(raw=NS(read=Mock(side_effect=lambda size, **kw: raw.read(size))), close=Mock())
        client.get_object.return_value = NS(headers={'content-length':str(len(BODY))}, data=stream)
        return client

    def prepared(self, client):
        storage = o.Storage({'synthetic':'PRIVATE_KEY'})
        storage.record_intent = Mock()
        runner = NS(deadline=time.monotonic()+60)
        with patch.dict(os.environ,self.env(),clear=True), \
             patch.object(o,'credential_config',return_value=({},'synthetic')), \
             patch.object(o,'make_client',return_value=client):
            storage.prepare(runner)
        return storage, runner

    def exchange(self, storage, runner):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,self.env(),clear=True):
            path = Path(directory)/'encrypted'; path.write_bytes(BODY)
            storage.exchange(path,hashlib.sha256(BODY).digest(),runner)
            self.assertEqual(path.read_bytes(),BODY)

    def test_one_put_and_exact_streamed_get(self):
        client = self.client(); storage,runner = self.prepared(client)
        self.exchange(storage,runner)
        self.assertEqual(storage.state['put_outcome'],'CONFIRMED')
        self.assertTrue(storage.state['readback_verified'])
        self.assertEqual(storage.credentials,{})
        self.assertEqual(storage.state['object_key'],'neon-backups/v1/123-1-'+'a'*40+'.dump.enc')
        kw=client.put_object.call_args.kwargs
        self.assertEqual(kw['if_none_match'],'*')
        self.assertEqual(kw['content_length'],len(BODY))
        self.assertEqual(client.get_object.call_args.kwargs['object_name'],kw['object_name'])
        self.assertEqual([c[0] for c in client.mock_calls],['get_namespace','get_bucket','put_object','get_object'])
        client.get_object.return_value.data.close.assert_called_once()

    def test_intent_flushed_before_put_and_failure_stops_write(self):
        client=self.client(); storage,runner=self.prepared(client)
        events=[]
        storage.record_intent.side_effect=lambda: events.append('intent')
        client.put_object.side_effect=lambda **kw: (events.append('put') or NS(status=200))
        self.exchange(storage,runner)
        self.assertEqual(events,['intent','put'])
        client=self.client(); storage,runner=self.prepared(client)
        storage.record_intent=o.Storage.record_intent.__get__(storage)
        with patch.dict(os.environ,self.env(),clear=True),self.assertRaises(o.StorageFailure):
            self.exchange(storage,runner)
        client.put_object.assert_not_called()
        self.assertEqual(storage.state['put_outcome'],'NOT_ATTEMPTED')
        self.assertEqual(storage.state['status'],'INTENT_RECORD_FAILED')
        with tempfile.TemporaryDirectory() as directory:
            summary=Path(directory)/'summary'
            with patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':str(summary)},clear=True):
                storage.record_intent()
            self.assertIn('oci-backup-create-intent-v1',summary.read_text())

    def test_all_write_gates_before_credentials(self):
        for field in ('EXPECTED_REVIEW','GITHUB_WORKFLOW_SHA','GITHUB_WORKFLOW_REF','OCI_WRITE_APPROVAL'):
            env=self.env(); env[field]='wrong'
            with patch.dict(os.environ,env,clear=True), patch.object(o,'credential_config') as creds, \
                 self.assertRaises(o.StorageFailure):
                o.Storage({}).prepare(NS(deadline=time.monotonic()+20))
            creds.assert_not_called()

    def test_bucket_target_privacy_fail_closed_no_put(self):
        for field in ('name','namespace','compartment_id','public_access_type','storage_tier',
                      'versioning','auto_tiering','kms_key_id'):
            client=self.client(); setattr(client.get_bucket.return_value.data,field,'wrong')
            with self.assertRaises(o.StorageFailure): self.prepared(client)
            client.put_object.assert_not_called()
        client=self.client(); client.get_namespace.return_value.data='wrong'
        with self.assertRaises(o.StorageFailure): self.prepared(client)
        client.get_bucket.assert_not_called()

    def test_write_error_preserves_reconciliation_no_retry_or_get(self):
        for error,expected in ((Error(412),'COLLISION_STOP'),(Error(403),'ACCESS_DENIED'),
                               (Error(500),'WRITE_OUTCOME_UNKNOWN'),
                               (o.validation.Refused(),'WRITE_OUTCOME_UNKNOWN')):
            client=self.client(); storage,runner=self.prepared(client)
            client.put_object.side_effect=error
            with self.assertRaises(o.StorageFailure): self.exchange(storage,runner)
            self.assertEqual(storage.state['status'],expected)
            self.assertEqual(storage.state['ciphertext_sha256'],hashlib.sha256(BODY).hexdigest())
            self.assertIsNotNone(storage.state['object_key'])
            self.assertNotIn('PRIVATE',json.dumps(storage.state))
            client.put_object.assert_called_once(); client.get_object.assert_not_called()

    def test_corrupt_short_oversize_encoded_readback_refused_preserves_remote(self):
        for kind in ('corrupt','short','oversize','encoding','header','get_error'):
            client=self.client(); storage,runner=self.prepared(client)
            response=client.get_object.return_value
            if kind=='get_error': client.get_object.side_effect=Error(403)
            elif kind=='header': response.headers['content-length']='999999999'
            elif kind=='encoding': response.headers['content-encoding']='gzip'
            else:
                data={'corrupt':b'x'*len(BODY),'short':BODY[:-1],'oversize':BODY+b'x'}[kind]
                stream=io.BytesIO(data)
                response.data.raw.read.side_effect=lambda size,**kw: stream.read(size)
            with self.assertRaises(o.StorageFailure): self.exchange(storage,runner)
            self.assertEqual(storage.state['put_outcome'],'CONFIRMED')
            self.assertFalse(storage.state['readback_verified'])
            self.assertFalse(storage.state['remote_delete'])
            client.put_object.assert_called_once(); client.get_object.assert_called_once()
            if kind!='get_error': response.data.close.assert_called_once()

    def test_deadline_stops_before_network(self):
        storage=o.Storage({})
        with patch.dict(os.environ,self.env(),clear=True), patch.object(o,'credential_config') as creds, \
             self.assertRaises(o.StorageFailure): storage.prepare(NS(deadline=0))
        creds.assert_not_called()

    def test_credentials_popped_before_supervisor_children(self):
        env={'OCI_CLI_'+f:'SYNTHETIC_ONLY' for f in o.OCI_FIELDS}
        with patch.dict(os.environ,env,clear=True),patch.object(o.validation,'main',return_value=2) as main:
            o.main()
            self.assertFalse(any(k.startswith('OCI_CLI_') for k in os.environ))
            self.assertEqual(main.call_args.kwargs['operation'],o.TOKEN)

    def test_workflow_no_old_approval_artifact_drive_or_remote_delete(self):
        text=Path('.github/workflows/native-registry-credential-probe.yml').read_text()
        self.assertNotIn(o.validation.TOKEN,text)
        self.assertIn('policies-reviewed:one-create-approved',text)
        self.assertIn('oracle-light-backup-mutation',text)
        self.assertIn("'oci==2.187.1'",text)
        for forbidden in ('upload-artifact','GOOGLE_DRIVE_OAUTH_JSON','issues: write','schedule:'):
            self.assertNotIn(forbidden,text)
        tree=ast.parse(Path(o.__file__).read_text())
        methods={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Attribute)
                 and n.func.value.attr=='client'}
        self.assertEqual(methods,{'get_namespace','get_bucket','put_object','get_object'})


class RealSDKTests(unittest.TestCase):
    def test_real_sdk_synthetic_signer_transport_contract_no_network(self):
        import oci
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.assertEqual(oci.__version__,'2.187.1')
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(
            serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()).decode()
        config=dict(tenancy=o.TENANCY,region=o.REGION,user='ocid1.user.oc1..synthetic',fingerprint='00:'*15+'00')
        case=StorageTests(); responses=case.client()
        with patch('socket.socket.connect',side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch('socket.create_connection',side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch('oci._vendor.requests.sessions.Session.request',side_effect=AssertionError('HTTP_FORBIDDEN')):
            client=o.make_client(config,key)
            self.assertIsInstance(client.retry_strategy,oci.retry.NoneRetryStrategy)
            replies=[responses.get_namespace.return_value,responses.get_bucket.return_value,
                     responses.put_object.return_value,responses.get_object.return_value]
            with patch.object(client.base_client,'call_api',side_effect=replies) as transport:
                storage,runner=case.prepared(client)
                case.exchange(storage,runner)
            self.assertEqual([c.kwargs['method'] for c in transport.call_args_list],['GET','GET','PUT','GET'])
            put=transport.call_args_list[2].kwargs
            headers={k.lower():v for k,v in put['header_params'].items()}
            self.assertEqual(headers['if-none-match'],'*')
            self.assertEqual(put['path_params']['objectName'],storage.state['object_key'])


if __name__=='__main__': unittest.main()
