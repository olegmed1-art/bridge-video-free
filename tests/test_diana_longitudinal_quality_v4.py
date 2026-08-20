from __future__ import annotations

import unittest

from diana_longitudinal_quality_v4 import (
    build_quality_layer,
    deal_reconstruction_gate_v4,
    speaker_summary_v4,
)


def base_master() -> dict:
    return {
        "job_id": "4" * 32,
        "algorithmRevision": "test-r25.15",
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
        "transcript": [
            {
                "segment_id": "s1", "start": 0, "end": 4,
                "text": "Диана, сколько верхних взяток?",
                "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher",
                "speaker_role_confidence": 0.95,
            },
            {
                "segment_id": "s2", "start": 5, "end": 8,
                "text": "Я считаю семь верхних взяток.",
                "speaker": "SPEAKER_B", "speaker_role_candidate": "student",
                "speaker_role_confidence": 0.95,
            },
            {
                "segment_id": "s3", "start": 9, "end": 13,
                "text": "Смотри, нужно отдельно посчитать каждую масть.",
                "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher",
                "speaker_role_confidence": 0.95,
            },
            {
                "segment_id": "s4", "start": 14, "end": 18,
                "text": "Поняла, тогда сохраняю семь.",
                "speaker": "SPEAKER_B", "speaker_role_candidate": "student",
                "speaker_role_confidence": 0.95,
            },
        ],
        "episodes": [
            {
                "episode_id": "e1", "ordinal": 1, "start": 0, "end": 4,
                "type": "planning", "summary_text": "Диана, сколько верхних взяток?",
                "terms": ["взятка"], "evidence": ["s1"],
            },
            {
                "episode_id": "e2", "ordinal": 2, "start": 5, "end": 8,
                "type": "planning", "summary_text": "Я считаю семь верхних взяток.",
                "terms": ["взятка"], "evidence": ["s2"],
            },
            {
                "episode_id": "e3", "ordinal": 3, "start": 9, "end": 13,
                "type": "explanation", "summary_text": "Смотри, нужно отдельно посчитать каждую масть.",
                "terms": ["взятка", "масть"], "evidence": ["s3"],
            },
            {
                "episode_id": "e4", "ordinal": 4, "start": 14, "end": 18,
                "type": "planning", "summary_text": "Поняла, тогда сохраняю семь.",
                "terms": ["взятка"], "evidence": ["s4"],
            },
        ],
        "learning_interactions": [],
        "canon_links": [],
        "deals": [],
    }


class DianaLongitudinalQualityV4Tests(unittest.TestCase):
    def test_explicit_turn_window_builds_complete_interaction_without_claiming_correctness(self):
        quality = build_quality_layer(base_master(), {"lesson_id": "lesson-3", "lesson_number": 3})
        complete = [
            item for item in quality["learning_interactions"]
            if item.get("source") == "transcript_decision_window_v4"
            and item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"
        ]
        self.assertEqual(len(complete), 1)
        item = complete[0]
        self.assertEqual(item["actor_attribution_status"], "EXPLICIT_ACOUSTIC_ROLE_SUPPORTED")
        self.assertFalse(item["outcome_correctness_verified"])
        self.assertIn("правильность решения этим этапом не установлена", item["observed_outcome"])
        self.assertGreaterEqual(len(item["evidence_refs"]), 4)
        self.assertEqual(quality["readiness"]["methodology_status"], "METHODOLOGY_READY")
        self.assertEqual(quality["authority"]["student_profile_production_write"], "DENY")
        self.assertEqual(quality["authority"]["person_specific_learning_conclusion"], "DENY")

    def test_organizational_question_cannot_create_learning_interaction(self):
        master = base_master()
        master["transcript"][0]["text"] = "Диана, видишь экран? Карты сейчас видно?"
        master["episodes"][0]["summary_text"] = master["transcript"][0]["text"]
        quality = build_quality_layer(master, {"lesson_id": "lesson-3", "lesson_number": 3})
        self.assertFalse(any(
            item.get("source") == "transcript_decision_window_v4"
            for item in quality["learning_interactions"]
        ))

    def test_overlapping_teacher_tasks_are_deduplicated_by_downstream_chain(self):
        master = base_master()
        extra = {
            "segment_id": "s0", "start": -4, "end": -1,
            "text": "Посмотри на контракт. Сколько здесь верхних взяток?",
            "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher",
            "speaker_role_confidence": 0.95,
        }
        master["transcript"].insert(0, extra)
        master["episodes"].insert(0, {
            "episode_id": "e0", "ordinal": 0, "start": -4, "end": -1,
            "type": "planning", "summary_text": extra["text"],
            "terms": ["контракт", "взятка"], "evidence": ["s0"],
        })
        quality = build_quality_layer(master, {"lesson_id": "lesson-3", "lesson_number": 3})
        complete = [
            item for item in quality["learning_interactions"]
            if item.get("source") == "transcript_decision_window_v4"
        ]
        self.assertEqual(len(complete), 1)
        self.assertGreaterEqual(float(complete[0]["start"]), 0.0)

    def test_speaker_metrics_do_not_count_semantic_fallback_as_acoustic_role(self):
        master = base_master()
        master["transcript"].append({
            "segment_id": "s5", "start": 20, "end": 22,
            "text": "Диана, почему ты так решила?",
            "speaker": "SPEAKER_A",
        })
        master["transcript"].append({
            "segment_id": "s6", "start": 23, "end": 25,
            "text": "Я думаю иначе.",
            "speaker_role_candidate": "student", "speaker_role_confidence": 0.9,
        })
        summary = speaker_summary_v4(master)
        self.assertEqual(summary["speaker_labeled_segments"], 5)
        self.assertEqual(summary["explicit_role_labeled_segments"], 5)
        self.assertEqual(summary["acoustic_role_mapped_segments"], 4)
        self.assertEqual(summary["role_labeled_segments"], 4)
        self.assertEqual(summary["semantic_fallback_role_segments"], 1)
        self.assertEqual(summary["role_without_acoustic_speaker_segments"], 1)
        self.assertFalse(summary["semantic_fallback_counts_as_role_coverage"])

    def test_exact_board_identity_can_merge_structured_fragments(self):
        master = {
            "job_id": "4" * 32,
            "deals": [
                {
                    "deal_id": "d1", "board_id": "stable-board-key",
                    "hands": {"N": ["AS", "KS"], "E": None, "S": None, "W": None},
                    "evidence": ["frame-1"],
                },
                {
                    "deal_id": "d2", "board_id": "stable-board-key",
                    "hands": {"N": ["QS"], "E": ["AH"], "S": None, "W": None},
                    "evidence": ["frame-2"],
                },
                {
                    "deal_id": "d3", "board_id": "another-board",
                    "hands": {"S": ["AD"]}, "evidence": ["frame-3"],
                },
            ],
        }
        results = deal_reconstruction_gate_v4(master)
        merged = [item for item in results if item.get("fragment_merge_status")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["unique_card_count"], 4)
        self.assertEqual(set(merged[0]["source_fragment_deal_ids"]), {"d1", "d2"})
        self.assertEqual(merged[0]["fragment_identity_type"], "board_id")

    def test_board_number_or_time_alone_never_merges(self):
        master = {
            "job_id": "4" * 32,
            "deals": [
                {"deal_id": "d1", "board_number": 7, "start": 10, "hands": {"N": ["AS"]}},
                {"deal_id": "d2", "board_number": 7, "start": 20, "hands": {"E": ["AH"]}},
            ],
        }
        results = deal_reconstruction_gate_v4(master)
        self.assertFalse(any(item.get("fragment_merge_status") for item in results))

    def test_review_terminology_and_semantic_only_guards_are_explicit(self):
        quality = build_quality_layer(base_master(), {"lesson_id": "lesson-3", "lesson_number": 3})
        self.assertEqual(quality["schema_version"], 4)
        self.assertEqual(quality["method_version"], "diana-quality-v4.0")
        self.assertIn("knowledge_candidates_for_review", quality["counts"])
        self.assertIn("promotable_knowledge_candidates", quality["count_semantics"])
        self.assertIn("DEPRECATED", quality["count_semantics"]["promotable_knowledge_candidates"])
        self.assertTrue(quality["incremental_processing"]["semantic_only_rebuild_supported"])
        self.assertFalse(quality["incremental_processing"]["heavy_video_reprocessing_required"])
        self.assertFalse(quality["incremental_processing"]["raw_asr_mutated"])
        self.assertEqual(quality["authority"]["database_destination"], "STAGING_ONLY")


if __name__ == "__main__":
    unittest.main()
