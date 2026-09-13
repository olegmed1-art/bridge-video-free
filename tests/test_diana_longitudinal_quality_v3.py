from __future__ import annotations

import unittest

from diana_longitudinal_quality_v3 import build_quality_layer


def base_master() -> dict:
    return {
        "job_id": "b" * 32,
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
                "segment_id": "s1",
                "start": 0,
                "end": 4,
                "text": "Диана, сколько верхних взяток?",
                "speaker": "A",
                "speaker_role": "teacher",
                "speaker_role_confidence": 0.95,
            },
            {
                "segment_id": "s2",
                "start": 4,
                "end": 8,
                "text": "Я считаю семь верхних взяток.",
                "speaker": "B",
                "speaker_role": "student",
                "speaker_role_confidence": 0.95,
            },
            {
                "segment_id": "s3",
                "start": 8,
                "end": 13,
                "text": "Смотри, нужно отдельно посчитать каждую масть.",
                "speaker": "A",
                "speaker_role": "teacher",
                "speaker_role_confidence": 0.95,
            },
            {
                "segment_id": "s4",
                "start": 13,
                "end": 18,
                "text": "Поняла, тогда сохраняю семь.",
                "speaker": "B",
                "speaker_role": "student",
                "speaker_role_confidence": 0.95,
            },
        ],
        "episodes": [
            {
                "episode_id": "e1",
                "ordinal": 1,
                "start": 0,
                "end": 4,
                "type": "объяснение",
                "summary_text": "Диана, сколько верхних взяток?",
                "terms": ["взятка"],
                "evidence": ["s1"],
            },
            {
                "episode_id": "e2",
                "ordinal": 2,
                "start": 4,
                "end": 8,
                "type": "розыгрыш",
                "summary_text": "Я считаю семь верхних взяток.",
                "terms": ["взятка"],
                "evidence": ["s2"],
            },
            {
                "episode_id": "e3",
                "ordinal": 3,
                "start": 8,
                "end": 13,
                "type": "объяснение",
                "summary_text": "Смотри, нужно отдельно посчитать каждую масть.",
                "terms": ["взятка", "масть"],
                "evidence": ["s3"],
            },
            {
                "episode_id": "e4",
                "ordinal": 4,
                "start": 13,
                "end": 18,
                "type": "розыгрыш",
                "summary_text": "Поняла, тогда сохраняю семь.",
                "terms": ["взятка"],
                "evidence": ["s4"],
            },
        ],
        "learning_interactions": [],
        "canon_links": [],
        "deals": [],
    }


class DianaLongitudinalQualityV3Tests(unittest.TestCase):
    def test_dynamic_outputs_exist_and_remain_staging_only(self):
        master = base_master()
        quality = build_quality_layer(
            master,
            {"lesson_id": "lesson-current", "lesson_number": 3},
        )
        self.assertEqual(quality["schema_version"], 3)
        self.assertEqual(quality["method_version"], "diana-quality-v3.0")
        for key in (
            "skill_state_changes",
            "hypotheses_requiring_confirmation",
            "evidence_against_existing_hypotheses",
            "recommended_next_probes",
        ):
            self.assertIn(key, quality)
            self.assertIsInstance(quality[key], list)

        states = {
            item["topic_candidate"]: item["current_state_candidate"]
            for item in quality["skill_state_changes"]
        }
        self.assertNotEqual(states.get("взятка"), "STABLE_INDEPENDENT")
        self.assertEqual(
            quality["authority"]["student_profile_production_write"],
            "DENY",
        )
        self.assertEqual(
            quality["authority"]["person_specific_learning_conclusion"],
            "DENY",
        )
        self.assertTrue(quality["candidate_staging_records"])
        self.assertTrue(all(
            item["promotion_status"] == "STAGING_ONLY"
            and item["promotion_allowed"] is False
            for item in quality["candidate_staging_records"]
        ))

    def test_prior_gap_hypothesis_collects_independent_counterevidence(self):
        master = base_master()
        master["learning_interactions"] = [{
            "cycle_id": "cycle-independent",
            "focus_episode_id": "e1",
            "task_or_trigger": "Сколько верхних взяток?",
            "student_action": "Самостоятельно считаю семь верхних взяток.",
            "teacher_intervention": "Преподаватель просит обосновать ответ без подсказки.",
            "student_response": "Объясняю подсчет по каждой масти и ответ не меняю.",
            "outcome": "Самостоятельное решение правильное и подтверждено.",
            "autonomy": "independent",
            "attribution_status": "supported",
            "evidence": ["s1", "s2", "s3", "s4"],
        }]
        master["prior_learning_state"] = {
            "skill_states": [{
                "topic_candidate": "взятка",
                "current_state_candidate": "INDEPENDENT_SUCCESS_ONCE",
                "history_support": {
                    "independent_lesson_ids": ["lesson-old-1"],
                },
            }],
            "hypotheses_requiring_confirmation": [{
                "hypothesis_id": "prior-gap-1",
                "topic_candidate": "взятка",
                "hypothesis_type": "POSSIBLE_SKILL_GAP",
            }],
        }
        quality = build_quality_layer(
            master,
            {"lesson_id": "lesson-current", "lesson_number": 3},
        )
        skill = next(
            item for item in quality["skill_state_changes"]
            if item["topic_candidate"] == "взятка"
        )
        self.assertEqual(
            skill["current_state_candidate"],
            "INDEPENDENT_SUCCESS_ONCE",
        )
        self.assertTrue(any(
            item["hypothesis_id"] == "prior-gap-1"
            and item["counterevidence_type"] == "INDEPENDENT_SUCCESS_OBSERVED"
            for item in quality["evidence_against_existing_hypotheses"]
        ))

    def test_stable_state_requires_two_prior_independent_lessons(self):
        master = base_master()
        master["learning_interactions"] = [{
            "cycle_id": "cycle-independent",
            "focus_episode_id": "e1",
            "task_or_trigger": "Сколько верхних взяток?",
            "student_action": "Самостоятельно считаю семь верхних взяток.",
            "teacher_intervention": "Преподаватель просит обосновать ответ без подсказки.",
            "student_response": "Объясняю подсчет по каждой масти и ответ не меняю.",
            "outcome": "Самостоятельное решение правильное и подтверждено.",
            "autonomy": "independent",
            "attribution_status": "supported",
            "evidence": ["s1", "s2", "s3", "s4"],
        }]
        master["prior_learning_state"] = {
            "skill_states": [{
                "topic_candidate": "взятка",
                "current_state_candidate": "INDEPENDENT_SUCCESS_ONCE",
                "history_support": {
                    "independent_lesson_ids": [
                        "lesson-old-1",
                        "lesson-old-2",
                    ],
                },
            }],
        }
        quality = build_quality_layer(
            master,
            {"lesson_id": "lesson-current", "lesson_number": 3},
        )
        skill = next(
            item for item in quality["skill_state_changes"]
            if item["topic_candidate"] == "взятка"
        )
        self.assertEqual(skill["current_state_candidate"], "STABLE_INDEPENDENT")
        self.assertFalse(skill["production_profile_write_allowed"])
        self.assertFalse(skill["person_specific_write_allowed"])


if __name__ == "__main__":
    unittest.main()
