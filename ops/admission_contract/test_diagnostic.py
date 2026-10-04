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

class DiagnosticTests(unittest.TestCase):
    def fixture(self, first='a(sasbttttuii) 0', second='a(sasbttttuii) 0', identity=None):
        unit=g.UNITS[0];self.calls=[]
        replies=iter(['Id='+unit+'\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled',
            'o "/org/freedesktop/systemd1/unit/example"','s "'+(identity or unit)+'"',first,second])
        def call(argv):self.calls.append(argv);return next(replies)
        return unit,call
    def test_missing_show_has_typed_counts_without_secret(self):
        unit,call=self.fixture('a(sasbttttuii) 1 secret-command')
        out=g.hooks(unit,call)
        self.assertFalse(out['hooks']['ExecStop']['show_present'])
        self.assertEqual(out['hooks']['ExecStop']['count'],1)
        self.assertNotIn('secret',json.dumps(out));self.assertIn('GetUnit',self.calls[1])
        self.assertFalse(any('LoadUnit' in a for a in self.calls))
    def test_exact_allowlist_and_identity(self):
        with self.assertRaises(g.Unknown):g.hooks('other.service',lambda a:self.fail())
        unit,call=self.fixture(identity='other.service')
        with self.assertRaisesRegex(g.Unknown,'DBUS_IDENTITY'):g.hooks(unit,call)
    def test_bad_signature_and_count(self):
        for raw in ['', 'as 0','a(sasbttttuii) bad']:
            unit,call=self.fixture(raw)
            with self.assertRaises(g.Unknown):g.hooks(unit,call)
    def test_empty_exact_both_properties(self):
        unit,call=self.fixture();row=g.hooks(unit,call)
        self.assertTrue(all(x['empty_exact'] for x in row['hooks'].values()))
    def test_inventory_no_content_read_or_modification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'root';code=Path(tmp)/'code';root.mkdir();code.mkdir();(root/g.OP).mkdir()
            (root/'lock').write_text('secret');(root/g.OP/'unexpected-private-name').write_text('private')
            before={str(p):p.stat().st_mtime_ns for p in Path(tmp).rglob('*')}
            with patch.object(Path,'read_bytes',side_effect=AssertionError('content read')),patch.object(Path,'read_text',side_effect=AssertionError('content read')):
                out=g.inventory(root,code)
            after={str(p):p.stat().st_mtime_ns for p in Path(tmp).rglob('*')}
            self.assertEqual(before,after);self.assertEqual(out['unlisted_operation_entries'],1)
            self.assertNotIn('private',json.dumps(out));self.assertNotIn('secret',json.dumps(out))
    def test_command_error_output_not_exposed(self):
        from types import SimpleNamespace
        with patch.object(g.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=b'',stderr=b'secret')):
            with self.assertRaises(g.Unknown) as ex:g.command(['/usr/bin/systemctl','--version'])
            self.assertNotIn('secret',str(ex.exception))
    def test_main_sanitizes_adapter_errors(self):
        out=io.StringIO()
        with patch.object(g.signal,'signal'),patch.object(g.signal,'alarm',create=True),patch.object(g.signal,'SIGALRM',14,create=True),patch.object(g.socket,'gethostname',return_value='synthetic-target'),patch.object(g.os,'geteuid',return_value=0,create=True),patch.object(g,'command',side_effect=ValueError('secret')),contextlib.redirect_stdout(out):
            self.assertEqual(g.main(),2)
        self.assertNotIn('secret',out.getvalue());self.assertEqual(json.loads(out.getvalue())['reason'],'ValueError')

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
            (old/'prepare_driver.py').write_text('retained')
            claim={'run':{'id':101},'nonce':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','state':'UNRESOLVED'}
            receipt=dict(claim,state='UNKNOWN_OR_ABORTED',reason='DRIVER_ABORT',running_sent=True)
            for n,obj in [('claim',claim),('receipt',receipt)]:
                (old/('autonomous-admission.'+n+'.json')).write_text(json.dumps(obj))
            old_before={p.name:p.read_bytes() for p in old.iterdir()}
            result=SimpleNamespace(returncode=0,stdout=b'{"phase":"READONLY_DIAGNOSTIC_COMPLETE","guest_writes":false}\n',stderr=b'')
            args=['driver','--run-url','https://github.com/example-owner/example-repository/actions/runs/999','--start-epoch',str(d.time.time()-1)]
            with patch.object(d,'old_records',return_value={'autonomous-admission.claim.json':claim,'autonomous-admission.receipt.json':receipt}),patch.object(d,'BASE',base),patch.object(d,'OLD',old),patch.object(d,'GUEST_SHA',hashlib.sha256(raw).hexdigest()),patch.object(d.socket,'gethostname',return_value='synthetic-executor'),patch.object(d.os,'getuid',return_value=1001),patch.object(d.signal,'signal'),patch.object(d.signal,'setitimer'),patch.object(sys,'argv',args),patch.object(d.subprocess,'run',return_value=result) as run,contextlib.redirect_stdout(io.StringIO()):
                d.main()
            self.assertEqual(run.call_count,1);argv=run.call_args.args[0]
            self.assertIn('/usr/bin/ssh',argv);self.assertIn('StrictHostKeyChecking=yes',argv)
            self.assertIn('/usr/bin/timeout',argv[-1]);self.assertIn('-I',argv[-1]);self.assertNotIn('runner.py',argv[-1])
            self.assertEqual(old_before,{p.name:p.read_bytes() for p in old.iterdir()})
            self.assertEqual(json.loads((base/'diagnostic-evidence.json').read_text())['scope'],'READONLY_RECONCILIATION')

if __name__=='__main__':unittest.main()
