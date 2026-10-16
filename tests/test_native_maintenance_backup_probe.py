import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from ops import native_maintenance_backup_probe as probe
from ops.native_maintenance_workflow_pause import Journal, Refused


class Missing(Exception):
    status = 404


class Client:
    def __init__(self, data):
        self.data = data
        self.puts = []
        self.exists = False
        self.public = False
        self.objects = []
        self.missing_bucket = False
        self.links = []
        self.repeat = False
        self.download = data
    def list_buckets(self, *_args, **_kw):
        return NS(data=[] if self.missing_bucket else [NS(name=probe.BUCKET)])
    def get_bucket(self, *_args, **_kw):
        return NS(data=NS(compartment_id=probe.TENANCY, freeform_tags={'managed_by':probe.TAG},
            public_access_type='ObjectRead' if self.public else 'NoPublicAccess',
            storage_tier='Standard', versioning='Disabled', auto_tiering='Disabled',
            is_read_only=False, kms_key_id=None))
    def list_objects(self, *_args, **_kw):
        return NS(data=NS(objects=self.objects, next_start_with='same' if self.repeat else None))
    def list_preauthenticated_requests(self, *_args, **_kw): return NS(data=self.links)
    def list_replication_policies(self, *_args, **_kw): return NS(data=[])
    def head_object(self, *_args, **_kw):
        if not self.exists: raise Missing()
        return NS(headers={'content-length':str(len(self.data)), 'opc-meta-sha256':hashlib.sha256(self.data).hexdigest()})
    def put_object(self, *args, **kw): self.puts.append((args, kw))
    def get_object(self, *_args, **_kw):
        return NS(headers={'content-length':str(len(self.data))},
                  data=NS(raw=NS(stream=lambda *_a,**_k:iter([self.download]))))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = probe.payload(self.root)
        self.client = Client(self.data)
        p = patch.dict('sys.modules', {'oci':NS(exceptions=NS(ServiceError=Missing))})
        p.start(); self.addCleanup(p.stop)
        p = patch.object(probe, 'preflight')
        self.preflight = p.start(); self.addCleanup(p.stop)
    def transfer(self):
        return probe.transfer(self.client, lambda fn,*a,**kw:fn(*a,**kw), 'private', self.data)

    def test_actual_journal_roundtrip_from_download_and_independent_append(self):
        received, _ = self.transfer()
        probe.restore(received, self.root / 'restore')
        with Journal(self.root/'original') as journal:
            self.assertEqual([r['event'] for r in journal.records], probe.EVENTS)
        with Journal(self.root/'restore') as journal:
            self.assertEqual(len(journal.records), 3)
        self.assertEqual(len(self.client.puts), 1)
        self.assertEqual(self.client.puts[0][1]['if_none_match'], '*')
        self.preflight.assert_called_once()

    def test_existing_object_is_read_not_overwritten(self):
        self.client.exists = True
        self.assertEqual(self.transfer()[0], self.data)
        self.assertEqual(self.client.puts, [])

    def test_privacy_budget_and_missing_bucket_block_before_upload(self):
        for field,value in [('public',True),('missing_bucket',True),('links',[1]),
                            ('objects',[NS(name='large',size=probe.LIMIT)]),
                            ('objects',[NS(name=str(i),size=0) for i in range(10000)])]:
            with self.subTest(field=field):
                self.client = Client(self.data)
                setattr(self.client,field,value)
                with self.assertRaises(Refused): self.transfer()
                self.assertEqual(self.client.puts, [])

    def test_duplicate_objects_and_repeated_page_tokens_refused(self):
        self.client.objects = [NS(name='same',size=1),NS(name='same',size=1)]
        with self.assertRaises(Refused): self.transfer()
        self.client.objects=[]; self.client.repeat=True
        with self.assertRaises(Refused): self.transfer()
        self.assertEqual(self.client.puts, [])

    def test_lost_put_ack_is_not_retried(self):
        with patch.object(self.client, 'put_object', side_effect=ConnectionError('lost')) as put:
            with self.assertRaises(ConnectionError): self.transfer()
            self.assertEqual(put.call_count,1)

    def test_corrupt_or_oversize_download_refused(self):
        for data in (b'x'*len(self.data), self.data+b'x'):
            self.client.download=data
            with self.assertRaises(Refused): self.transfer()

    def test_non_synthetic_or_broken_chain_refused(self):
        for data in (self.data.replace(b'SYNTHETIC_ONLY',b'PRODUCTION'),
                     self.data.replace(b'SYNTHETIC_BACKUP_PROBE',b'PRIVATE_OPERATION')):
            with self.assertRaises(Refused): probe.restore(data,self.root/'rejected')
            if (self.root/'rejected').exists():
                import shutil
                shutil.rmtree(self.root/'rejected')


if __name__ == '__main__': unittest.main()
