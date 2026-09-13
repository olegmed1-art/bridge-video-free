#!/usr/bin/env python3
import copy
import unittest

import bridge_worker_3_1_free as core
from bridge_runtime_hardening_r25_1 import strict_qc_pass
from bridge_runtime_hardening_r25_2 import REVISION, sanitize_actor_specific_semantics


class SemanticGateR252Test(unittest.TestCase):
    def _master(self, *, speaker=None, unreliable=False):
        transcript = [
            {
                "segment_id": "seg1",
                "speaker": speaker,
                "text": "Смотрите план игры с козырем",
                "unreliable": unreliable,
            },
            {
                "segment_id": "seg2",
                "speaker": speaker,
                "text": "Я не понимаю, что делать",
                "unreliable": False,
            },
        ]
        cycle = {
            "cycle_id": "cycle1",
            "focus_episode_id": "ep1",
            "task_or_trigger": "ситуация",
            "student_action": "Смотрите план игры с козырем",
            "teacher_intervention": "объяснение",
            "student_response": "поняла",
            "intervention_type": "объяснение",
            "outcome": "есть непосредственный признак понимания",
            "autonomy": "после вмешательства преподавателя",
            "confidence": "medium",
            "evidence": ["seg1", "seg2"],
        }
        return {
            "transcript": transcript,
            "learning_interactions": [copy.deepcopy(cycle)],
            "student_analysis": {
                "observations": [
                    {
                        "observation_id": "student1",
                        "student_action": cycle["student_action"],
                        "evidence": ["seg1"],
                    }
                ]
            },
            "teacher_analysis": [
                {"observation_id": "teacher1", "method": "объяснение", "evidence": ["seg1"]}
            ],
            "content_quality": {},
            "warnings": [],
        }

    def test_local_asr_does_not_invent_student_or_teacher_identity(self):
        master = sanitize_actor_specific_semantics(self._master(speaker=None))
        self.assertEqual(master["student_analysis"]["observations"], [])
        self.assertEqual(master["teacher_analysis"], [])
        self.assertEqual(
            master["content_quality"]["actor_attribution_status"],
            "unavailable_without_speaker_labels",
        )
        cycle = master["learning_interactions"][0]
        self.assertIsNone(cycle["student_action"])
        self.assertIsNone(cycle["teacher_intervention"])
        self.assertIsNone(cycle["student_response"])
        self.assertEqual(cycle["confidence"], "low")
        self.assertGreater(master["content_quality"]["actor_specific_claims_excluded"], 0)

    def test_diarized_reliable_claims_survive(self):
        master = sanitize_actor_specific_semantics(self._master(speaker="Student"))
        self.assertEqual(len(master["student_analysis"]["observations"]), 1)
        self.assertEqual(len(master["teacher_analysis"]), 1)
        self.assertEqual(len(master["learning_interactions"]), 1)
        self.assertEqual(
            master["content_quality"]["actor_attribution_status"], "speaker_labels_available"
        )

    def test_diarized_claims_using_unreliable_asr_are_withheld(self):
        master = sanitize_actor_specific_semantics(
            self._master(speaker="Student", unreliable=True)
        )
        self.assertEqual(master["student_analysis"]["observations"], [])
        self.assertEqual(master["teacher_analysis"], [])
        self.assertEqual(master["learning_interactions"], [])
        self.assertGreater(master["content_quality"]["actor_specific_claims_excluded"], 0)

    def test_r25_1_diana_hard_stop_is_preserved(self):
        qc = [{"block": i, "ok": True, "similarity": 0.90} for i in range(26)]
        qc[21].update(ok=False, similarity=0.745)
        qc[25].update(ok=False, similarity=0.0)
        self.assertFalse(strict_qc_pass(qc, True))

    def test_r25_1_sunday_moderate_drift_can_remain_warning_only(self):
        qc = [{"block": i, "ok": True, "similarity": 0.90} for i in range(27)]
        for block, similarity in {15: 0.761, 16: 0.846, 17: 0.667, 18: 0.761}.items():
            qc[block].update(ok=False, similarity=similarity)
        self.assertTrue(strict_qc_pass(qc, True))

    def test_public_name_unchanged(self):
        self.assertEqual(core.ALGORITHM_VERSION, "3.1 FREE")
        self.assertEqual(REVISION, "3.1-free-r25.2")


if __name__ == "__main__":
    unittest.main()
