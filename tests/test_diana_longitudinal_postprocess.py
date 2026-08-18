from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import diana_longitudinal_postprocess as postprocess
from diana_longitudinal_quality_v2 import build_quality_layer


def technical_master() -> dict:
    return {
        "job_id": "a" * 32,
        "algorithmRevision": "test-r25.7",
        "source": {"immutable": True},
        "content_quality": {
            "semantic_qc_status": "PASS",
            "semantic_critical_unresolved": 0,
        },
        "technical_qc": {
            "visual": {
                "pass1": {"status": "VISUAL_PASS_1_COMPLETE"},
                "pass2": {"status": "VISUAL_PASS_2_COMPLETE"},
            }
        },
        "transcript": [],
        "episodes": [],
        "learning_interactions": [],
        "canon_links": [],
        "deals": [],
    }


class DianaLongitudinalPostprocessTests(unittest.TestCase):
    def test_date_parsing_and_independent_evidence(self):
        self.assertIn(("2021-02-22", "22.02.2021"), postprocess._date_from_text("Диана 1. 22.02.2021.mp4"))
        self.assertIn(("2021-02-22", "2021-02-22"), postprocess._date_from_text("2021-02-22"))
        self.assertIn(("2021-02-22", "22 февраля 2021"), postprocess._date_from_text("22 февраля 2021"))

        master = technical_master()
        master["transcript"] = [{
            "segment_id": "s1", "start": 0, "end": 3,
            "text": "Сегодня 22 февраля 2021 года.",
        }]
        source = {"id": "master", "name": "Диана 1 22.02.2021.mp4", "createdTime": "2026-08-17T00:00:00Z"}
        original = {"id": "original", "createdTime": "2021-02-22T10:00:00Z", "modifiedTime": "2021-02-22T12:00:00Z"}
        with patch.dict(os.environ, {"BRIDGE_LESSON_NUMBER": "1"}, clear=False):
            lesson = postprocess._lesson_identity(master, source, original)
        self.assertEqual(lesson["lesson_date"], "2021-02-22")
        self.assertEqual(lesson["lesson_date_status"], "CONFIRMED")
        self.assertGreaterEqual(len(lesson["date_evidence"]), 3)

    def test_missing_speakers_cannot_be_methodology_ready(self):
        master = technical_master()
        master["transcript"] = [
            {"segment_id": "s1", "start": 0, "end": 5, "text": "Почему нужно считать взятки?"},
            {"segment_id": "s2", "start": 5, "end": 10, "text": "Я думаю, что их шесть."},
        ]
        master["episodes"] = [
            {
                "episode_id": "e1", "ordinal": 1, "start": 0, "end": 5,
                "type": "объяснение", "summary_text": "Почему нужно считать взятки?",
                "terms": ["взятка"], "evidence": ["s1"],
            },
            {
                "episode_id": "e2", "ordinal": 2, "start": 5, "end": 10,
                "type": "розыгрыш", "summary_text": "Я думаю, что их шесть.",
                "terms": ["взятка"], "evidence": ["s2"],
            },
        ]
        quality = build_quality_layer(master)
        self.assertEqual(quality["readiness"]["technical_status"], "TECHNICAL_READY")
        self.assertEqual(quality["readiness"]["content_status"], "CONTENT_EXTRACTED")
        self.assertEqual(quality["readiness"]["methodology_status"], "METHODOLOGY_PARTIAL")
        self.assertIn("TEACHER_STUDENT_ROLES_NOT_RELIABLY_MAPPED", quality["readiness"]["methodology_issues"])

    def test_complete_role_sequence_can_pass_methodology_gate(self):
        master = technical_master()
        master["transcript"] = [
            {"segment_id": "s1", "start": 0, "end": 4, "text": "Диана, сколько верхних взяток?", "speaker": "A", "speaker_role": "teacher", "speaker_role_confidence": 0.9},
            {"segment_id": "s2", "start": 4, "end": 8, "text": "Я считаю шесть верхних взяток.", "speaker": "B", "speaker_role": "student", "speaker_role_confidence": 0.9},
            {"segment_id": "s3", "start": 8, "end": 13, "text": "Смотри, нужно отдельно посчитать каждую масть.", "speaker": "A", "speaker_role": "teacher", "speaker_role_confidence": 0.9},
            {"segment_id": "s4", "start": 13, "end": 18, "text": "Поняла, тогда в трефе пять, всего семь.", "speaker": "B", "speaker_role": "student", "speaker_role_confidence": 0.9},
        ]
        master["episodes"] = [
            {"episode_id": "e1", "ordinal": 1, "start": 0, "end": 4, "type": "объяснение", "summary_text": "Диана, сколько верхних взяток?", "terms": ["взятка"], "evidence": ["s1"]},
            {"episode_id": "e2", "ordinal": 2, "start": 4, "end": 8, "type": "розыгрыш", "summary_text": "Я считаю шесть верхних взяток.", "terms": ["взятка"], "evidence": ["s2"]},
            {"episode_id": "e3", "ordinal": 3, "start": 8, "end": 13, "type": "объяснение", "summary_text": "Смотри, нужно отдельно посчитать каждую масть.", "terms": ["взятка", "масть"], "evidence": ["s3"]},
            {"episode_id": "e4", "ordinal": 4, "start": 13, "end": 18, "type": "розыгрыш", "summary_text": "Поняла, тогда в трефе пять, всего семь.", "terms": ["взятка", "трефа"], "evidence": ["s4"]},
        ]
        quality = build_quality_layer(master)
        self.assertEqual(quality["readiness"]["methodology_status"], "METHODOLOGY_READY")
        self.assertGreaterEqual(quality["counts"]["complete_learning_interactions"], 1)
        complete = [x for x in quality["learning_interactions"] if x["status"] == "COMPLETE_EVIDENCE_CANDIDATE"]
        self.assertTrue(complete[0]["student_action"])
        self.assertTrue(complete[0]["teacher_intervention"])
        self.assertTrue(complete[0]["student_followup"])

    def test_intro_is_not_student_opportunity(self):
        master = technical_master()
        master["transcript"] = [
            {"segment_id": "s1", "start": 0, "end": 4, "text": "Сегодня мы поговорим про игру без козыря.", "speaker": "A", "speaker_role": "teacher", "speaker_role_confidence": 0.9},
            {"segment_id": "s2", "start": 4, "end": 7, "text": "Хорошо.", "speaker": "B", "speaker_role": "student", "speaker_role_confidence": 0.9},
        ]
        master["episodes"] = [
            {"episode_id": "e1", "ordinal": 1, "start": 0, "end": 4, "type": "методический эпизод", "summary_text": "Сегодня мы поговорим про игру без козыря.", "terms": ["без козыря"], "evidence": ["s1"]},
            {"episode_id": "e2", "ordinal": 2, "start": 4, "end": 7, "type": "методический эпизод", "summary_text": "Хорошо.", "terms": [], "evidence": ["s2"]},
        ]
        quality = build_quality_layer(master)
        self.assertEqual(quality["counts"]["complete_learning_interactions"], 0)

    def test_weak_canon_match_is_not_canon_evidence(self):
        master = technical_master()
        master["transcript"] = [{"segment_id": "s1", "start": 0, "end": 5, "text": "Сегодня поговорим про торговлю."}]
        master["episodes"] = [{
            "episode_id": "e1", "ordinal": 1, "start": 0, "end": 5,
            "type": "торговля", "summary_text": "Сегодня поговорим про торговлю.",
            "terms": ["торговля"], "evidence": ["s1"],
        }]
        master["canon_links"] = [{
            "episode_id": "e1", "score": 0.062,
            "status": "слабое тематическое совпадение",
            "canonical_excerpt": "Открытие один без козыря показывает 15–17 очков.",
        }]
        quality = build_quality_layer(master)
        item = quality["canon_candidates"][0]
        self.assertIn(item["classification"], {"TOPIC_MENTION", "CANON_RETRIEVAL_CANDIDATE"})
        self.assertFalse(item["counts_as_canon_evidence"])

    def test_fragment_fails_knowledge_value_gate(self):
        master = technical_master()
        master["transcript"] = [{"segment_id": "s1", "start": 0, "end": 2, "text": "И торговля дальше."}]
        master["episodes"] = [{
            "episode_id": "e1", "ordinal": 1, "start": 0, "end": 2,
            "type": "торговля", "summary_text": "И торговля дальше.",
            "terms": ["торговля"], "evidence": ["s1"],
        }]
        quality = build_quality_layer(master)
        self.assertEqual(quality["knowledge_candidates"][0]["status"], "STAGING_REJECTED")
        self.assertIn("CONTENT_TOO_FRAGMENTARY", quality["knowledge_candidates"][0]["rejection_reasons"])

    def test_full_board_only_is_dds_eligible(self):
        master = technical_master()
        ranks = "AKQJT98765432"
        suits = "SHDC"
        deck = [rank + suit for suit in suits for rank in ranks]
        master["deals"] = [{
            "deal_id": "d1",
            "hands": {
                "N": deck[0:13], "E": deck[13:26],
                "S": deck[26:39], "W": deck[39:52],
            },
            "contract": "3NT", "declarer": "S", "opening_lead": "2S",
        }, {"deal_id": "d2", "hands": {"N": None, "E": None, "S": None, "W": None}}]
        quality = build_quality_layer(master)
        self.assertEqual(quality["deal_reconstructions"][0]["board_status"], "VERIFIED_FULL_BOARD")
        self.assertTrue(quality["deal_reconstructions"][0]["dds_eligible"])
        self.assertEqual(quality["deal_reconstructions"][1]["board_status"], "BOARD_UNKNOWN")
        self.assertFalse(quality["deal_reconstructions"][1]["dds_eligible"])

    def test_curriculum_remains_candidate(self):
        master = technical_master()
        master["session_summary"] = {"top_topic_counts": [["открытие", 1]]}
        lesson = {"lesson_number": 1, "lesson_date": "2021-02-22"}
        quality = build_quality_layer(master, lesson)
        curriculum = postprocess._curriculum(master, lesson, quality)
        candidate = curriculum["candidate_school_curriculum"]
        self.assertFalse(candidate["activation_allowed"])
        self.assertIsNone(candidate["modules"][0]["proposed_school_stage"])


if __name__ == "__main__":
    unittest.main()
