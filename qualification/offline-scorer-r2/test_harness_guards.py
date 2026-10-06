"""Synthetic harness regressions, separate from the immutable 68 scorer cases."""
from types import SimpleNamespace
import unittest
from qualify import AuditGuard, QualificationControls

class HarnessGuardTests(unittest.TestCase):
    def test_caught_denied_io_still_prevents_qualification(self):
        guard = AuditGuard()
        try:
            guard.audit("socket.connect", ())  # Callback injection; no real socket opened.
        except RuntimeError:
            pass
        self.assertEqual(guard.denied_count, 1)
        with self.assertRaisesRegex(RuntimeError, "prohibited I/O"):
            guard.require_clean()

    def test_all_caught_attempts_stay_counted_with_bounded_diagnostics(self):
        guard = AuditGuard()
        for i in range(20):
            try:
                guard.audit("subprocess.Popen", ())
            except RuntimeError:
                pass
        self.assertEqual(guard.denied_count, 20)
        self.assertEqual(len(guard.denied_events), 16)
        with self.assertRaises(RuntimeError):
            guard.require_clean()

    def test_empty_reason_non_strict_xpass_cannot_be_pass(self):
        controls = QualificationControls()
        controls.passed = {"synthetic-" + str(i) for i in range(68)}
        # A non-strict XPASS call can be passed=True and carry wasxfail="".
        report = SimpleNamespace(nodeid="synthetic-0", when="call", outcome="passed",
                                 passed=True, failed=False, skipped=False, wasxfail="")
        controls.pytest_runtest_logreport(report)
        self.assertEqual(len(controls.passed), 68)
        self.assertEqual(len(controls.problems), 1)
        with self.assertRaises(RuntimeError):
            controls.require_clean(0)

    def test_plain_pass_is_accepted_without_xfail_metadata(self):
        controls = QualificationControls()
        for i in range(68):
            controls.pytest_runtest_logreport(SimpleNamespace(
                nodeid="synthetic-" + str(i), when="call", outcome="passed",
                passed=True, failed=False, skipped=False))
        controls.require_clean(0)

if __name__ == "__main__":
    unittest.main()
