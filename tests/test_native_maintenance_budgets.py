"""Absolute stage clocks over real pipes; legacy callers keep their limit."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ops import native_maintenance_checkpoint_transport as rpc
from ops import native_maintenance_budgets as budgets


class BudgetTests(unittest.TestCase):
    def test_isolated_pilot_packages_include_guard_dependencies(self):
        from ops import light_native_pilot_release as release
        root = Path(__file__).resolve().parents[1]
        for members in (release.HELPERS, release.EXTRA):
            with tempfile.TemporaryDirectory() as directory:
                for name in members:
                    destination = Path(directory)/name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b'' if name in ('ops/__init__.py', 'database/__init__.py')
                                            else (root/name).read_bytes())
                code = ('import sys; sys.path.insert(0, '+repr(directory)+'); '
                        'from ops.native_maintenance_run_guard import StageRunBinding; '
                        'assert StageRunBinding.duration_limit == 160')
                result = subprocess.run([sys.executable, '-I', '-c', code],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_stage_can_receive_after_legacy_deadline_without_renewing(self):
        a, b = os.pipe()
        c, d = os.pipe()
        try:
            with patch.object(rpc.time, 'monotonic', return_value=1000.):
                stage = rpc.Channel(a, d, 'a'*64, seconds=budgets.STAGE_RPC_SECONDS)
                legacy = rpc.Channel(c, b, 'a'*64)
                legacy.send({'value': 'buffered'})
            with patch.object(rpc.time, 'monotonic', return_value=1065.):
                self.assertEqual(stage.receive(), {'value': 'buffered'})
                with self.assertRaises(Exception): legacy.send({'value': 'expired'})
            self.assertEqual(stage.deadline, 1080.)
            self.assertEqual(legacy.deadline, 1060.)
            with patch.object(rpc.time, 'monotonic', return_value=1080.):
                with self.assertRaises(Exception): stage.receive()
            with patch.object(rpc.time, 'monotonic', return_value=1001.):
                with self.assertRaises(Exception): stage.receive()
            self.assertTrue(stage.failed and legacy.failed)
        finally:
            for fd in (a, b, c, d): os.close(fd)

    def test_no_arbitrary_extended_budget(self):
        for seconds in (True, 0, 81, 100, 80.0):
            with self.subTest(seconds=seconds), self.assertRaises(Exception):
                rpc.Channel(0, 1, 'a'*64, seconds=seconds)
        self.assertEqual(budgets.STAGE_PRELAUNCH_REQUIRED_SECONDS, 125)
        self.assertEqual(budgets.STAGE_LAUNCHER_SECONDS, 160)


if __name__ == '__main__': unittest.main()
