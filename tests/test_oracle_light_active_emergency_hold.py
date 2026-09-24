import hashlib
import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'ops/oracle_light_active_emergency_hold.py'
spec = importlib.util.spec_from_file_location('emergency_hold', path)
hold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hold)


class EmergencyHold(TestCase):
    def test_restores_exact_previous_dropin(self):
        self.assertEqual(hashlib.sha256(hold.HOLD).hexdigest(),
                         'bc18cbfbd932e3dd52be6c3070a2889a162c35469a507d6a25cdfc26b331a9c6')
        self.assertEqual(hold.ACTIVE.count(b'ADMISSION_MODE=ACTIVE'), 1)

    def test_rejects_unknown_invocation_before_stop(self):
        with patch.object(hold.os, 'geteuid', return_value=0), \
             patch.object(hold.os, 'uname') as uname, \
             patch.object(hold.sys, 'argv', ['script','x'*32]), \
             patch.object(hold.subprocess, 'run') as run:
            uname.return_value.nodename = 'autopilot-lite-vnic'
            with self.assertRaisesRegex(RuntimeError, 'INPUT_INVALID'):
                hold.main()
        run.assert_not_called()
