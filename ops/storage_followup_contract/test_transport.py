import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import diagnostic_guest as g

@unittest.skipUnless(sys.platform=='linux','Linux flock required')
class DriverTests(unittest.TestCase):
    def test_record_hash_symlink_and_size(self):
        import prepare_driver as d
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'record';raw=b'{"state":"UNRESOLVED"}';path.write_bytes(raw);path.chmod(0o600)
            pin=hashlib.sha256(raw).hexdigest()
            self.assertEqual(d.read_record(path,pin,os.getuid()),{'state':'UNRESOLVED'})
            with self.assertRaises(AssertionError):d.read_record(path,'0'*64,os.getuid())
            link=Path(tmp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):d.read_record(link,pin,os.getuid())
            path.write_bytes(b'x'*4097)
            with self.assertRaises(AssertionError):d.read_record(path,pin,os.getuid())
    def scenario(self,case):
        import prepare_driver as d
        import hashlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'new';old=Path(tmp)/'old';base.mkdir();old.mkdir()
            raw=b'print("fake")';(base/'diagnostic_guest.py').write_bytes(raw)
            (old/'prepare_driver.py').write_text('retained');(old/'legacy.py').write_text('retained')
            claim={'run':{'id':104},'nonce':'12345678123456781234567812345678','state':'UNRESOLVED'}
            receipt=dict(claim,state='DIAGNOSTIC_COMPLETE',reason='DIAGNOSTIC_COMPLETE',running_sent=True)
            for n,obj in [('claim',claim),('receipt',receipt)]:
                (old/('autonomous-admission.'+n+'.json')).write_text(json.dumps(obj))
            old_before={p.name:p.read_bytes() for p in old.iterdir()}
            from test_storage_linux import END
            result=SimpleNamespace(returncode=0,stdout=(json.dumps(END)+'\n').encode(),stderr=b'')
            if case!='good':result=SimpleNamespace(returncode=124,stdout=b'{"probe":"jobs_before","ok":true,"value":[]}\n{broken',stderr=b'')
            args=['driver','--run-url','https://github.com/example-owner/example-repository/actions/runs/999','--start-epoch',str(d.time.time()-1)]
            with patch.object(d,'old_records',return_value={'autonomous-admission.claim.json':claim,'autonomous-admission.receipt.json':receipt}),patch.object(d,'BASE',base),patch.object(d,'OLD',old),patch.object(d,'LEGACY_LOCK',old/'legacy.py'),patch.object(d,'GUEST_SHA',hashlib.sha256(raw).hexdigest()),patch.object(d.socket,'gethostname',return_value='synthetic-executor'),patch.object(d.os,'getuid',return_value=1001),patch.object(d.signal,'signal'),patch.object(d.signal,'setitimer'),patch.object(sys,'argv',args),patch.object(d.subprocess,'run',return_value=result) as run,contextlib.redirect_stdout(io.StringIO()):
                if case=='timeout':run.side_effect=d.subprocess.TimeoutExpired('fake',65,output=result.stdout)
                if case=='good':d.main()
                else:
                    with self.assertRaises(AssertionError):d.main()
            self.assertEqual(run.call_count,1);argv=run.call_args.args[0]
            self.assertIn('/usr/bin/ssh',argv);self.assertIn('StrictHostKeyChecking=yes',argv)
            self.assertIn('/usr/bin/timeout',argv[-1]);self.assertIn('-I',argv[-1]);self.assertNotIn('runner.py',argv[-1])
            self.assertEqual(old_before,{p.name:p.read_bytes() for p in old.iterdir()})
            evidence=json.loads((base/'diagnostic-evidence.json').read_text())
            self.assertEqual(evidence['scope'],'READONLY_STORAGE_RECONCILIATION')
            if case!='good':
                self.assertEqual(evidence['rejected_lines'],1)
                self.assertEqual(evidence['observations'],[{'probe':'jobs_before','ok':True,'value':[]}])
                self.assertNotIn('broken',str(evidence))
    def test_transport_only_readonly_guest_and_offhost_evidence(self):self.scenario('good')
    def test_partial_and_timeout_preserve_safe_evidence_and_old_records(self):
        for case in ('partial','timeout'):
            with self.subTest(case=case):self.scenario(case)

if __name__=='__main__':unittest.main()
