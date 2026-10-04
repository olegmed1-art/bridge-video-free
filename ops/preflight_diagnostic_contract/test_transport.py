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
    def test_transport_only_readonly_guest_and_offhost_evidence(self):
        import prepare_driver as d
        import hashlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'new';old=Path(tmp)/'old';base.mkdir();old.mkdir()
            raw=b'print("fake")';(base/'diagnostic_guest.py').write_bytes(raw)
            (old/'prepare_driver.py').write_text('retained');(old/'legacy.py').write_text('retained')
            claim={'run':{'id':102},'nonce':'dddddddddddddddddddddddddddddddd','state':'UNRESOLVED'}
            receipt=dict(claim,state='UNKNOWN_OR_ABORTED',reason='DRIVER_ABORT',running_sent=True)
            for n,obj in [('claim',claim),('receipt',receipt)]:
                (old/('autonomous-admission.'+n+'.json')).write_text(json.dumps(obj))
            old_before={p.name:p.read_bytes() for p in old.iterdir()}
            result=SimpleNamespace(returncode=0,stdout=b'{"state":"READONLY_COLLECTION_COMPLETE","preflight_pass_asserted":false}\n',stderr=b'')
            args=['driver','--run-url','https://github.com/example-owner/example-repository/actions/runs/999','--start-epoch',str(d.time.time()-1)]
            with patch.object(d,'old_records',return_value={'autonomous-admission.claim.json':claim,'autonomous-admission.receipt.json':receipt}),patch.object(d,'BASE',base),patch.object(d,'OLD',old),patch.object(d,'LEGACY_LOCK',old/'legacy.py'),patch.object(d,'GUEST_SHA',hashlib.sha256(raw).hexdigest()),patch.object(d.socket,'gethostname',return_value='synthetic-executor'),patch.object(d.os,'getuid',return_value=1001),patch.object(d.signal,'signal'),patch.object(d.signal,'setitimer'),patch.object(sys,'argv',args),patch.object(d.subprocess,'run',return_value=result) as run,contextlib.redirect_stdout(io.StringIO()):
                d.main()
            self.assertEqual(run.call_count,1);argv=run.call_args.args[0]
            self.assertIn('/usr/bin/ssh',argv);self.assertIn('StrictHostKeyChecking=yes',argv)
            self.assertIn('/usr/bin/timeout',argv[-1]);self.assertIn('-I',argv[-1]);self.assertNotIn('runner.py',argv[-1])
            self.assertEqual(old_before,{p.name:p.read_bytes() for p in old.iterdir()})
            self.assertEqual(json.loads((base/'diagnostic-evidence.json').read_text())['scope'],'READONLY_RECONCILIATION')

if __name__=='__main__':unittest.main()
