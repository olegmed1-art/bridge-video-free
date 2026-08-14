#!/usr/bin/env python3
import os
import unittest
from unittest.mock import patch

import bridge_worker_3_1_free as core
from bridge_runtime_hardening_r25_1 import (
    REVISION,
    pathological_nonspeech_hallucination,
    strict_qc_pass,
)


class AsrQcR251Test(unittest.TestCase):
    def test_diana9_recorded_pattern_is_blocked(self):
        # Known real failure pattern: 26 checked blocks, 24 PASS + 2 FAIL.
        qc = [{"block": i, "ok": True} for i in range(26)]
        qc[21]["ok"] = False
        qc[25]["ok"] = False
        self.assertFalse(strict_qc_pass(qc, True))

    def test_single_remaining_failure_is_blocked(self):
        self.assertFalse(strict_qc_pass([{"ok": True}, {"ok": False}], True))

    def test_all_checked_blocks_may_pass(self):
        self.assertTrue(strict_qc_pass([{"ok": True} for _ in range(26)], True))

    def test_base_failure_cannot_be_overridden(self):
        self.assertFalse(strict_qc_pass([{"ok": True}], False))

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

    def test_user_facing_name_is_unchanged(self):
        self.assertEqual(core.ALGORITHM_VERSION, "3.1 FREE")
        self.assertEqual(REVISION, "3.1-free-r25.1")


if __name__ == "__main__":
    unittest.main()
