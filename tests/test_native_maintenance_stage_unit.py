"""Actual bounded pipe + private-store adapter, simulated OCI service only."""
import copy
import os
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from ops import native_maintenance_stage_unit as unit
from ops.native_maintenance_workflow_pause import encoded
from test_native_maintenance_checkpoint_oci import FakeClient, ServiceError


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.sdk = patch.dict(sys.modules, {'oci':NS(retry=NS(NoneRetryStrategy=lambda:'NO_RETRY'),
                                                   exceptions=NS(ServiceError=ServiceError))})
        self.sdk.start()
        self.addCleanup(self.sdk.stop)
        self.client, self.guard = FakeClient(), Mock()
        self.store = unit.adapter.OCIJournalStore(self.client, 'ci_namespace', self.guard)
        self.expected = dict(source='a'*40, scope_digest='b'*64, stage='prepare',
                             run=dict(run_id=123,attempt=2,job_id=456))
        self.record = dict(version=1, kind='NATIVE_STAGE_UNIT', **self.expected,
            supervisor=dict(unit='bridge-native-ro-'+('a'*12)+'-123-2-'+('c'*16)+'.service',
                            invocation='d'*32,cgroup_inode=123))
        self.raw = encoded(self.record)
        self.retainer = unit.Retainer(self.store, self.expected)

    def writes(self): return [call for call in self.client.calls if call[0]=='PUT']

    def test_create_only_private_record_and_exact_independent_readback(self):
        sha = self.retainer.retain(self.raw)
        self.assertEqual(sha, unit.checkpoint.sha(self.raw))
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.writes()[0][2]['if_none_match'],'*')
        self.assertEqual(self.writes()[0][2]['retry_strategy'],'NO_RETRY')
        self.assertEqual(unit.read_accepted(self.store,self.expected,sha),self.raw)
        self.assertEqual(self.guard.call_count,3)
        with self.assertRaises(Exception): self.retainer.retain(self.raw)
        self.assertEqual(len(self.writes()),1)

    def test_wrong_scope_or_run_never_writes(self):
        changed=copy.deepcopy(self.record)
        changed['scope_digest']='e'*64
        with self.assertRaises(Exception): self.retainer.retain(encoded(changed))
        self.assertTrue(self.store.failed)
        self.assertEqual(self.writes(),[])

    def test_lost_committed_put_return_is_never_retried(self):
        self.client.lose='123-2.json'
        with self.assertRaises(ConnectionError): self.retainer.retain(self.raw)
        self.assertIn(unit.path(self.record),self.client.objects)
        with self.assertRaises(Exception): self.retainer.retain(self.raw)
        self.assertEqual(len(self.writes()),1)
        # Only a fresh independent read-only instance can reconcile this record.
        fresh=unit.adapter.OCIJournalStore(self.client,'ci_namespace',Mock())
        self.assertEqual(unit.read_accepted(fresh,self.expected,unit.checkpoint.sha(self.raw)),self.raw)

    def test_existing_record_is_not_a_new_retention_ack(self):
        self.retainer.retain(self.raw)
        fresh=unit.adapter.OCIJournalStore(self.client,'ci_namespace',Mock())
        with self.assertRaisesRegex(Exception,'EXISTS_RECONCILE'):
            unit.Retainer(fresh,self.expected).retain(self.raw)
        self.assertEqual(len(self.writes()),1)

    def test_public_bucket_refuses_before_write(self):
        self.client.public=True
        with self.assertRaises(Exception): self.retainer.retain(self.raw)
        self.assertEqual(self.writes(),[])

    def test_readback_must_match_separately_accepted_digest(self):
        self.retainer.retain(self.raw)
        with self.assertRaises(Exception): unit.read_accepted(self.store,self.expected,'f'*64)
        self.assertEqual(len(self.writes()),1)
        self.assertTrue(self.store.failed)

    def test_authority_loss_after_readback_prevents_ack(self):
        self.guard.side_effect=[None,None,RuntimeError('CI_RUN_CANCELLED')]
        with self.assertRaisesRegex(RuntimeError,'CI_RUN_CANCELLED'):
            self.retainer.retain(self.raw)
        self.assertTrue(self.store.failed)
        self.assertEqual(len(self.writes()),1)
        self.assertIn(unit.path(self.record),self.client.objects)

    def channels(self):
        left_read,right_write=os.pipe()
        right_read,left_write=os.pipe()
        for fd in (left_read,right_write,right_read,left_write): self.addCleanup(os.close,fd)
        return (unit.rpc.Channel(left_read,left_write,'f'*64,seconds=2),
                unit.rpc.Channel(right_read,right_write,'f'*64,seconds=2))

    def test_real_pipe_ack_follows_completed_private_readback(self):
        client_channel,server_channel=self.channels()
        client=unit.UnitClient(client_channel,self.expected)
        server=unit.UnitServer(server_channel,self.retainer)
        errors=[]
        def receive():
            try: server.accept(server_channel.receive())
            except BaseException as exc: errors.append(type(exc).__name__)
        thread=threading.Thread(target=receive)
        thread.start()
        sha=client.retain(self.raw)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(sha,unit.checkpoint.sha(self.raw))
        self.assertEqual(self.client.objects[unit.path(self.record)][0],self.raw)
        with self.assertRaises(Exception): client.retain(self.raw)
        self.assertEqual(len(self.writes()),1)

    def test_wrong_channel_scope_or_digest_ack_poisons_client(self):
        correct=dict(kind='NATIVE_STAGE_UNIT_ACK',binding='f'*64,
                     scope_digest=self.expected['scope_digest'],digest=unit.checkpoint.sha(self.raw))
        for key in ('binding','scope_digest','digest'):
            client_channel,_=self.channels()
            client=unit.UnitClient(client_channel,self.expected)
            with patch.object(client_channel,'send'), \
                 patch.object(client_channel,'receive',return_value={**correct,key:'0'*64}), \
                 self.assertRaises(Exception):
                client.retain(self.raw)
            self.assertTrue(client_channel.failed)

    def test_wrong_request_binding_blocks_store(self):
        _,server_channel=self.channels()
        server=unit.UnitServer(server_channel,self.retainer)
        with self.assertRaises(Exception):
            server.accept(dict(kind='NATIVE_STAGE_UNIT_RETAIN',binding='0'*64,data=unit.rpc.pack(self.raw)))
        self.assertTrue(server_channel.failed)
        self.assertTrue(self.store.failed)
        self.assertEqual(self.writes(),[])

    def test_lost_pipe_ack_latches_and_retains_remote_evidence(self):
        client_channel,server_channel=self.channels()
        client=unit.UnitClient(client_channel,self.expected)
        server=unit.UnitServer(server_channel,self.retainer)
        def receive():
            try:
                with patch.object(server_channel,'send',side_effect=ConnectionError('CI_ACK_LOST')):
                    server.accept(server_channel.receive())
            except Exception: pass
        thread=threading.Thread(target=receive)
        thread.start()
        with self.assertRaises(Exception): client.retain(self.raw)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(client_channel.failed)
        self.assertTrue(server_channel.failed)
        self.assertEqual(len(self.writes()),1)
        self.assertIn(unit.path(self.record),self.client.objects)
        with self.assertRaises(Exception): client.retain(self.raw)
        self.assertEqual(len(self.writes()),1)


if __name__=='__main__': unittest.main()
