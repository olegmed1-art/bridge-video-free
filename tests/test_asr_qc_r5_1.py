#!/usr/bin/env python3
import unittest

from bridge_runtime_hardening_r5_1 import REVISION, strict_qc_pass
import bridge_worker_3_1_free as core


class AsrQcR51Test(unittest.TestCase):
    def test_diana9_recorded_pattern_is_blocked(self):
        # Production run #19 recorded 26 checked blocks: 24 PASS and 2 FAIL
        # (block 21 similarity 0.745; final block 25 similarity 0.0).
        qc = [{"block": i, "ok": True} for i in range(26)]
        qc[21]["ok"] = False
        qc[25]["ok"] = False
        self.assertFalse(strict_qc_pass(qc, True))

    def test_single_remaining_failure_is_blocked(self):
        qc = [{"block": 0, "ok": True}, {"block": 1, "ok": False}]
        self.assertFalse(strict_qc_pass(qc, True))

    def test_all_checked_blocks_may_pass(self):
        qc = [{"block": i, "ok": True} for i in range(26)]
        self.assertTrue(strict_qc_pass(qc, True))

    def test_base_failure_cannot_be_overridden(self):
        qc = [{"block": 0, "ok": True}]
        self.assertFalse(strict_qc_pass(qc, False))

    def test_user_facing_name_is_unchanged(self):
        self.assertEqual(core.ALGORITHM_VERSION, "3.1 FREE")
        self.assertEqual(REVISION, "3.1-free-master-analysis-r5.1")


if __name__ == "__main__":
    unittest.main()
