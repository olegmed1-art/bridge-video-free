import copy
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
import writer as w


def request():
    return dict(version=1, operation='RETAIN_RECONCILIATION_EVIDENCE_ONLY',
        policy_sha256='a'*64, plan_sha256='b'*64,
        predecessor=dict(sequence=2, plan_sha256='c'*64, terminal_sha256='d'*64),
        files={'issuers/cycle.lock':w.digest(b''),'cycles/cycle.lock':w.digest(b''),
            'cycles/'+ 'b'*64+'/incident.json':w.digest(b'incident'),
            'cycles/'+ 'b'*64+'/prepare-done.json':None},
        directories={'issuers':['cycle.lock'], 'cycles':['cycle.lock','b'*64],
            'cycles/'+'b'*64:['incident.json']},
        proofs={k:'e'*64 for k in w.REQUIRED_PROOFS}, observation='f'*64,
        not_before=100, expires_at=200, root_identity=[1,2], snapshot_sha256='0'*64)


class Schema(unittest.TestCase):
    def check_bad(self, change):
        r=request(); change(r); raw=w.encoded(r)
        with self.assertRaises((w.Refused,TypeError)): w.validate(raw,w.digest(raw))
    def test_valid(self):
        raw=w.encoded(request()); self.assertEqual(w.validate(raw,w.digest(raw)),request())
    def test_wrong_approval(self):
        with self.assertRaises(w.Refused): w.validate(w.encoded(request()),'0'*64)
    def test_missing_proof(self): self.check_bad(lambda r:r['proofs'].pop('provider_readback'))
    def test_boolean_proof(self): self.check_bad(lambda r:r['proofs'].update(failed_plan_no_execution=True))
    def test_extra_field(self): self.check_bad(lambda r:r.update(activate=True))
    def test_wrong_action(self): self.check_bad(lambda r:r.update(operation='REPLAY'))
    def test_expiry_too_long(self): self.check_bad(lambda r:r.update(expires_at=999))
    def test_bool_version(self): self.check_bad(lambda r:r.update(version=True))
    def test_path_escape(self): self.check_bad(lambda r:r['files'].update({'../../etc/x':None}))
    def test_absolute_path(self): self.check_bad(lambda r:r['files'].update({'/etc/x':None}))
    def test_duplicate_json(self):
        with self.assertRaises(w.Refused): w.decode(b'{"x":1,"x":2}')
    def test_nonfinite(self):
        with self.assertRaises(w.Refused): w.decode(b'{"x":NaN}')
    def test_record_cannot_authorize(self):
        r=json.loads(w.record(request(),'a'*64))
        for k in ('issue_allowed','replay_allowed','acknowledgement_allowed','retirement_allowed','incident_closed'):
            self.assertIs(r[k],False)
        self.assertNotIn('task_executed',r)
        self.assertNotIn('COMPLETE',r.values())


@unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,'Linux root fixture required')
class Filesystem(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.tmp.name); self.root.chmod(0o700)
        self.r=request()
        for name in ('issuers','cycles','cycles/'+'b'*64):
            (self.root/name).mkdir(mode=0o700)
        for path,pin in self.r['files'].items():
            if pin is not None:
                p=self.root/path;p.write_bytes(b'incident' if p.name=='incident.json' else b'');p.chmod(0o600)
        self.r['root_identity']=list(w.identity(self.root.stat()))
        with w.root_handle(str(self.root)) as fd:
            self.r['snapshot_sha256']=w.digest(w.encoded(w.snapshot(fd,self.r)))
        self.target=self.root/'cycles'/('b'*64)/w.NAME
        self.original=self.root/'cycles'/('b'*64)/'incident.json'
        self.observer=lambda:(150,'f'*64)
    def tearDown(self): self.tmp.cleanup()
    def run_writer(self):
        raw=w.encoded(self.r)
        return w.commit(str(self.root),raw,w.digest(raw),self.observer)
    def refused(self):
        with self.assertRaises((w.Refused,OSError)): self.run_writer()
    def test_create_preserves_original_metadata(self):
        before=self.original.stat();self.assertEqual(self.run_writer(),'RECORDED_EVIDENCE_ONLY')
        after=self.original.stat();self.assertEqual(before,after)
        self.assertEqual(self.target.stat().st_nlink,1)
        self.assertEqual(self.target.stat().st_mode&0o777,0o600)
        self.assertEqual(set(self.target.parent.iterdir()),{self.target,self.original})
    def test_lost_reply_idempotent(self):
        self.run_writer();st=self.target.stat()
        self.assertEqual(self.run_writer(),'ALREADY_RECORDED');self.assertEqual(st,self.target.stat())
    def test_different_request_conflicts(self):
        self.run_writer();before=self.target.read_bytes();self.r['proofs']['source_review']='0'*64
        self.refused();self.assertEqual(self.target.read_bytes(),before)
    def test_partial_record_preserved(self):
        self.target.write_bytes(b'{');self.target.chmod(0o600);self.refused()
        self.assertEqual(self.target.read_bytes(),b'{')
    def test_empty_record_preserved(self):
        self.target.touch(mode=0o600);self.refused();self.assertTrue(self.target.exists())
    def test_symlink_output(self):
        self.target.symlink_to(self.original);self.refused();self.assertEqual(self.original.read_bytes(),b'incident')
    def test_hardlink_output(self):
        os.link(self.original,self.target);self.refused()
    def test_original_symlink(self):
        self.original.unlink();self.original.symlink_to(self.root/'issuers'/'cycle.lock');self.refused()
    def test_original_hardlink(self):
        os.link(self.original,self.root/'alias');self.refused()
    def test_original_changed(self): self.original.write_bytes(b'changed');self.refused();self.assertFalse(self.target.exists())
    def test_missing_original(self): self.original.unlink();self.refused()
    def test_unexpected_phase(self):
        p=self.original.parent/'prepare-done.json';p.write_bytes(b'done');p.chmod(0o600);self.refused()
    def test_unexpected_inventory(self):
        (self.original.parent/'unknown').touch(mode=0o600);self.refused()
    def test_file_mode(self): self.original.chmod(0o644);self.refused()
    def test_directory_mode(self): self.original.parent.chmod(0o755);self.refused()
    def test_root_identity(self): self.r['root_identity'][1]+=1;self.refused()
    def test_root_symlink(self):
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        raw=w.encoded(self.r)
        with self.assertRaises(OSError):w.commit(str(alias),raw,w.digest(raw),self.observer)
    def test_lock_missing_not_created(self):
        p=self.root/'issuers'/'cycle.lock';p.unlink();self.refused();self.assertFalse(p.exists())
    def test_lock_symlink(self):
        p=self.root/'issuers'/'cycle.lock';p.unlink();p.symlink_to(self.original);self.refused()
    def test_lock_held(self):
        import fcntl
        with (self.root/'issuers'/'cycle.lock').open('rb') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);self.refused()
        self.assertFalse(self.target.exists())
    def test_expired_evidence(self):self.observer=lambda:(201,'f'*64);self.refused();self.assertFalse(self.target.exists())
    def test_wrong_evidence(self):self.observer=lambda:(150,'0'*64);self.refused()
    def test_missing_live_adapter(self):
        def unavailable():raise w.Refused('PROVIDER_READBACK_UNAVAILABLE')
        self.observer=unavailable;self.refused();self.assertFalse(self.target.exists())
    def test_cas_guard_drift(self):
        def drift():self.original.write_bytes(b'changed');return 150,'f'*64
        self.observer=drift;self.refused();self.assertFalse(self.target.exists())
    def test_lock_replaced(self):
        def drift():
            p=self.root/'issuers'/'cycle.lock';p.unlink();p.touch(mode=0o600);return 150,'f'*64
        self.observer=drift;self.refused();self.assertFalse(self.target.exists())
    def test_partial_write_no_cleanup(self):
        actual=os.write
        def fail(fd,data):actual(fd,data[:7]);raise OSError('injected crash')
        with patch.object(w.os,'write',fail):self.refused()
        self.assertEqual(self.target.stat().st_size,7);self.refused()
    def test_short_writes_completed(self):
        actual=os.write
        with patch.object(w.os,'write',lambda fd,data:actual(fd,data[:7])):
            self.assertEqual(self.run_writer(),'RECORDED_EVIDENCE_ONLY')
    def test_fsync_failure_preserves(self):
        with patch.object(w.os,'fsync',side_effect=OSError('injected fsync')):self.refused()
        self.assertTrue(self.target.exists());self.assertEqual(self.run_writer(),'ALREADY_RECORDED')
    def test_postwrite_drift_retains_record(self):
        calls=[]
        def drift():
            calls.append(1)
            if len(calls)==3:self.original.write_bytes(b'changed')
            return 150,'f'*64
        self.observer=drift;self.refused();self.assertTrue(self.target.exists())
    def test_retry_rechecks_evidence(self):
        self.run_writer();self.observer=lambda:(201,'f'*64);self.refused()
    def test_no_automatic_parent_creation(self):
        self.original.unlink();self.original.parent.rmdir();self.refused()
        self.assertFalse(self.original.parent.exists())
    def test_wrong_owner(self):os.chown(self.original,1001,1001);self.refused()
    def test_same_content_inode_replacement(self):
        self.original.unlink();self.original.write_bytes(b'incident');self.original.chmod(0o600)
        self.refused();self.assertFalse(self.target.exists())
    def test_output_deleted_at_final_guard(self):
        calls=[]
        def observe():
            calls.append(1)
            if len(calls)==3:self.target.unlink()
            return 150,'f'*64
        self.observer=observe;self.refused()
    def test_output_replaced_at_final_guard(self):
        calls=[];old=[]
        def observe():
            calls.append(1)
            if len(calls)==3:
                old.append(self.target.open('rb'))
                raw=self.target.read_bytes();self.target.unlink();self.target.write_bytes(raw);self.target.chmod(0o600)
            return 150,'f'*64
        self.observer=observe
        try:self.refused()
        finally:
            for stream in old:stream.close()
    def test_retry_output_modified_at_final_guard(self):
        self.run_writer();calls=[]
        def observe():
            calls.append(1)
            if len(calls)==3:self.target.write_bytes(b'changed')
            return 150,'f'*64
        self.observer=observe;self.refused()


if __name__=='__main__':unittest.main(verbosity=2)
