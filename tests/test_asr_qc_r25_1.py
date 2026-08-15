#!/usr/bin/env python3
import unittest

import bridge_worker_3_1_free as core
from bridge_runtime_hardening_r25_1 import (
    REVISION,
    critical_qc_failures,
    pathological_nonspeech_hallucination,
    strict_qc_pass,
)


class AsrQcR251Test(unittest.TestCase):
    def test_diana9_recorded_profile_is_blocked(self):
        # Real Diana 9 profile: ordinary drift plus a terminal zero-overlap block.
        qc = [{"block": i, "ok": True, "similarity": 0.90} for i in range(26)]
        qc[21].update(ok=False, similarity=0.743, retry=True)
        qc[25].update(ok=False, similarity=0.0, retry=True)
        self.assertEqual(critical_qc_failures(qc), 1)
        self.assertFalse(strict_qc_pass(qc, True))

    def test_sunday_known_good_profile_is_not_overblocked(self):
        # Real Sunday E2E profile: base QC passed with four moderate disagreements.
        qc = [{"block": i, "ok": True, "similarity": 0.90} for i in range(27)]
        for block, similarity in {10: 0.767, 17: 0.725, 18: 0.786, 22: 0.818}.items():
            qc[block].update(ok=False, similarity=similarity, retry=True)
        self.assertEqual(critical_qc_failures(qc), 0)
        self.assertTrue(strict_qc_pass(qc, True))

    def test_hallucination_evidence_is_always_blocked(self):
        qc = [{"ok": True, "similarity": 0.91}]
        self.assertFalse(strict_qc_pass(qc, True, hallucination_blocks=1))

    def test_missing_similarity_on_failure_is_blocked(self):
        qc = [{"ok": True, "similarity": 0.90}, {"ok": False, "retry": True}]
        self.assertEqual(critical_qc_failures(qc), 1)
        self.assertFalse(strict_qc_pass(qc, True))

    def test_all_checked_blocks_may_pass(self):
        self.assertTrue(
            strict_qc_pass([{"ok": True, "similarity": 0.90} for _ in range(26)], True)
        )

    def test_base_failure_cannot_be_overridden(self):
        self.assertFalse(strict_qc_pass([{"ok": True, "similarity": 0.90}], False))

    def test_dense_repeated_applause_is_blocked(self):
        text = ("[Аплодисменты] " * 12) + "пас один без козыря"
        self.assertTrue(pathological_nonspeech_hallucination(text))

    def test_occasional_real_marker_is_allowed(self):
        text = (
            "Мы открываем один без козыря, дальше Стейман. [Аплодисменты] "
            "После этого обсуждаем ответ два черва и дальнейший ребид."
        )
        self.assertFalse(pathological_nonspeech_hallucination(text))

    def test_many_markers_do_not_block_when_speech_dominates(self):
        speech = " ".join(["обсуждаем торговлю контракт ответ партнера взятка"] * 80)
        text = speech + " " + ("[Аплодисменты] " * 8)
        self.assertFalse(pathological_nonspeech_hallucination(text))

    def test_reason_counts_survive_public_log_sanitizing(self):
        logged = core.sanitize_public_log(
            {"qc_hallucination_blocks": 2, "qc_critical_failed": 1}
        )
        self.assertEqual(
            logged, {"qc_hallucination_blocks": 2, "qc_critical_failed": 1}
        )

    def test_user_facing_name_is_unchanged(self):
        self.assertEqual(core.ALGORITHM_VERSION, "3.1 FREE")
        self.assertEqual(REVISION, "3.1-free-r25.1")


if __name__ == "__main__":
    unittest.main()
