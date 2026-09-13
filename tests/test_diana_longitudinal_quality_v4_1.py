from __future__ import annotations

import unittest

import diana_longitudinal_quality_v2 as v2
from diana_longitudinal_quality_v4_1 import _task_action_aligned, _task_excerpts_v41, _transcript_decision_interactions_v41, build_quality_layer


def base_master() -> dict:
    return {
        "job_id": "5" * 32,
        "algorithmRevision": "test-r25.15",
        "content_quality": {"semantic_qc_status": "PASS", "semantic_critical_unresolved": 0},
        "technical_qc": {"visual": {"pass1": {"status": "VISUAL_PASS_1_COMPLETE"}, "pass2": {"status": "VISUAL_PASS_2_COMPLETE"}}},
        "transcript": [
            {"segment_id": "s1", "start": 0, "end": 3, "text": "Сколько здесь верхних взяток?", "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher", "speaker_role_confidence": 0.95},
            {"segment_id": "s2", "start": 4, "end": 6, "text": "Семь.", "speaker": "SPEAKER_B", "speaker_role_candidate": "student", "speaker_role_confidence": 0.95},
            {"segment_id": "s3", "start": 7, "end": 11, "text": "Смотри, нужно отдельно посчитать каждую масть.", "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher", "speaker_role_confidence": 0.95},
            {"segment_id": "s4", "start": 12, "end": 16, "text": "Тогда считаю по мастям и оставляю семь.", "speaker": "SPEAKER_B", "speaker_role_candidate": "student", "speaker_role_confidence": 0.95},
        ],
        "episodes": [{"episode_id": "e1", "ordinal": 1, "start": 0, "end": 3, "type": "planning", "summary_text": "Сколько здесь верхних взяток?", "terms": ["взятка"], "evidence": ["s1"]}],
        "learning_interactions": [], "canon_links": [], "deals": [],
    }


class DianaLongitudinalQualityV41Tests(unittest.TestCase):
    def test_question_and_alignment_guards(self):
        self.assertEqual(_task_excerpts_v41("Сколько здесь верхних взяток"), [])
        self.assertTrue(_task_excerpts_v41("Сколько здесь верхних взяток?"))
        self.assertTrue(_task_excerpts_v41("Как сыграешь этот контракт"))
        self.assertTrue(_task_action_aligned("Сколько верхних взяток?", "Семь"))
        self.assertFalse(_task_action_aligned("Как сыграешь этот контракт?", "Семь"))
        self.assertFalse(_task_action_aligned("Как сыграешь этот контракт?", "Я бы открыла один без козыря."))

    def test_complete_chain_does_not_claim_correctness(self):
        master = base_master()
        events = v2.build_atomic_events(master)
        sections = v2.build_sections(events, master["job_id"])
        interactions = _transcript_decision_interactions_v41(master, events, sections)
        self.assertEqual(len(interactions), 1, interactions)
        item = interactions[0]
        self.assertEqual(item["status"], "COMPLETE_EVIDENCE_CANDIDATE")
        self.assertFalse(item["outcome_correctness_verified"])
        self.assertEqual(item["actor_attribution_status"], "EXPLICIT_ACOUSTIC_ROLE_SUPPORTED")
        self.assertGreaterEqual(len(item["evidence_refs"]), 4)

    def test_nested_prompt_keeps_closest_task(self):
        master = base_master()
        master["transcript"].insert(0, {"segment_id": "s0", "start": -4, "end": -1, "text": "Сколько здесь верхних взяток?", "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher", "speaker_role_confidence": 0.95})
        events = v2.build_atomic_events(master)
        sections = v2.build_sections(events, master["job_id"])
        interactions = _transcript_decision_interactions_v41(master, events, sections)
        self.assertEqual(len(interactions), 1, interactions)
        self.assertGreaterEqual(float(interactions[0]["start"]), 0.0)

    def test_authority_and_cost_guards(self):
        quality = build_quality_layer(base_master(), {"lesson_id": "lesson-3", "lesson_number": 3})
        self.assertEqual(quality["method_version"], "diana-quality-v4.1")
        self.assertFalse(quality["incremental_processing"]["heavy_video_reprocessing_required"])
        self.assertFalse(quality["incremental_processing"]["raw_asr_mutated"])
        for key in ("canon_activation", "curriculum_activation", "student_profile_production_write", "methodology_activation"):
            self.assertEqual(quality["authority"][key], "DENY")
        self.assertEqual(quality["authority"]["database_destination"], "STAGING_ONLY")
        self.assertFalse(quality["cost_gate"]["paid_ai_api_required"])
        self.assertFalse(quality["cost_gate"]["paid_cloud_required"])


if __name__ == "__main__":
    unittest.main()
