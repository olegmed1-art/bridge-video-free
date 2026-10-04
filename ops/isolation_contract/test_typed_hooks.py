import os
import subprocess
import time
import unittest
import uuid
from unittest.mock import patch
import guest
from protocol import TARGETS,Refused

class TypedHooks(unittest.TestCase):
    def setup_guest(self,answers=None):
        self.unit=TARGETS[0];self.calls=[]
        replies=iter(answers or ['o "/org/freedesktop/systemd1/unit/example"','s "'+self.unit+'"','a(sasbttttuii) 0','a(sasbttttuii) 0'])
        g=guest.Guest(time.monotonic()+20)
        def command(argv):self.calls.append(argv);return next(replies)
        g.command=command
        return g,{'Id':self.unit,'LoadState':'loaded'}
    def test_four_only_timer_preserved(self):
        self.assertEqual(len(TARGETS),4)
        self.assertTrue(all(u.endswith('.service') for u in TARGETS))
        self.assertIn('synthetic-watch-healthcheck.timer',guest.PRESERVE)
    def test_omitted_show_requires_both_typed_zero(self):
        g,row=self.setup_guest();g.stop_hook_proof(self.unit,row)
        self.assertEqual(len(self.calls),4);self.assertIn('GetUnit',self.calls[0])
        self.assertFalse(any('LoadUnit' in x for x in self.calls))
    def test_present_hook_rejects_without_dbus(self):
        g,row=self.setup_guest();row['ExecStop']='secret-command'
        with self.assertRaises(Refused) as e:g.stop_hook_proof(self.unit,row)
        self.assertEqual(self.calls,[]);self.assertNotIn('secret',str(e.exception))
    def test_bad_property_rejects(self):
        for raw in ['','as 0','a(sasbttttuii) 1 secret','a(sasbttttuii) 0 trailing']:
            g,row=self.setup_guest(['o "/org/freedesktop/systemd1/unit/example"','s "'+TARGETS[0]+'"',raw])
            with self.assertRaises(Refused) as e:g.stop_hook_proof(self.unit,row)
            self.assertNotIn('secret',str(e.exception))
    def test_wrong_identity_and_scope(self):
        g,row=self.setup_guest(['o "/org/freedesktop/systemd1/unit/example"','s "other.service"'])
        with self.assertRaises(Refused):g.stop_hook_proof(self.unit,row)
        g,row=self.setup_guest()
        with self.assertRaises(Refused):g.stop_hook_proof('other.service',row)

@unittest.skipUnless(os.name=='posix' and os.environ.get('IBM_V2_SYNTHETIC_SYSTEMD')=='1','Disposable systemd CI')
class RealTypedHooks(unittest.TestCase):
    def test_real_empty_and_nonempty_exec_arrays(self):
        for has_hook in (False,True):
            unit='typed-hook-contract-'+uuid.uuid4().hex+'.service'
            args=['systemd-run','--unit='+unit,'--property=RuntimeMaxSec=15']
            if has_hook:args+=['--property=ExecStop=/bin/true']
            try:
                subprocess.run(args+['/bin/sleep','15'],capture_output=True,check=True,timeout=5)
                g=guest.Guest(time.monotonic()+15)
                with patch.object(guest,'ALL',(unit,)),patch.object(guest,'TARGETS',(unit,)):
                    row=g.units()[unit]
                    if has_hook:
                        row.pop('ExecStop',None);row.pop('ExecStopPost',None)
                        with self.assertRaisesRegex(Refused,'STOP_HOOK_NOT_PROVEN_EMPTY_ExecStop'):g.stop_hook_proof(unit,row)
                    else:g.stop_hook_proof(unit,row)
            finally:
                subprocess.run(['systemctl','stop',unit],capture_output=True,timeout=5)
                subprocess.run(['systemctl','reset-failed',unit],capture_output=True,timeout=3)

if __name__=='__main__':unittest.main()
