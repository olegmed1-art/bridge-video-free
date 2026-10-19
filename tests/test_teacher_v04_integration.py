"""Native specifications for the v0.4 answer boundary; NOT_RUN."""
from copy import deepcopy
import unittest

from bridge_school_api import teacher_entry_blocking_reader_path as entry_path
from bridge_school_api.teacher_v04_integration import answer_entry_order


def observation(entries="NONE", concrete=True):
    return {
        "schema": "teacher-symbolic-entry-v1", "profile": "SCHOOL_L1_DB_V1",
        "role": "declarer", "phase": "suit_timing", "trump": "NT",
        "declarer": "N", "dummy": "S", "dummy_visible": True,
        "target_suit": "S",
        "hand_a": {"seat": "N", "honors": ["A"], "length": 3,
                   "low_cards": ["3", "2"]},
        "hand_b": {"seat": "S", "honors": ["Q", "J"], "length": 4,
                   "low_cards": ["5", "4"]},
        "side_entries_to_a": entries, "leader": "N", "current_trick": [],
        "request": "CONCRETE_CARD" if concrete else "SYMBOLIC_PLAN",
    }


class TeacherV04IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.reader = entry_path.load_prepared_test_reader(test_only=True)
        self.requests = []

    def fetch(self, request):
        self.requests.append(deepcopy(request))
        return self.reader.read_eligible_skills(request)

    def answer(self, o=None, reader=None):
        return answer_entry_order(
            observation() if o is None else o,
            self.fetch if reader is None else reader, test_only=True)

    def assert_withheld(self, result):
        for field in ("answer", "decision", "proposal", "binding"):
            self.assertIsNone(result[field])
        self.assertEqual(result["knowledge_versions"], [])
        self.assertEqual(result["trace"], [])

    def test_real_reader_l1_decision_trace_explanation_and_fresh_card(self):
        result = self.answer()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result["status"], "CONDITIONAL_RECOMMENDATION")
        self.assertEqual(result["decision"]["selected_plan"], "LOW_TO_QJ")
        self.assertEqual(result["proposal"]["card"], "S2")
        self.assertEqual(result["proposal"]["choice_basis"],
                         "REPRESENTATIVE_LOW_NOT_PROVEN_BEST_SPOT")
        self.assertEqual(set(result["proposal"]["allowed_low_cards"]), {"S2", "S3"})
        nodes = {n["node_id"]: n for n in result["trace"]}
        self.assertEqual(nodes[result["answer"]["selection_node"]]["selected_plan"],
                         result["decision"]["selected_plan"])
        self.assertEqual(nodes[result["answer"]["comparison_node"]]["preference"],
                         result["decision"]["selected_plan"])
        self.assertEqual({x["skill_id"] for x in result["knowledge_versions"]},
                         {"SKILL-0003", "SKILL-0004"})
        for citation in result["knowledge_versions"]:
            node = nodes[citation["skill_id"]]
            self.assertEqual(citation["canonical_refs"], node["canonical_refs"])
            self.assertEqual(citation["provenance"], node["provenance"])
            self.assertEqual(node["rule_evaluation"]["status"], "MATCH")
            self.assertEqual(node["version_no"], 1)
        self.assertIsNone(result["decision"]["success_probability"])
        self.assertFalse(result["production_ready"])
        self.assertTrue(result["test_only"])

    def test_sufficient_communications_remain_unranked_after_fresh_read(self):
        result = self.answer(observation("SUFFICIENT"))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result["status"], "ADMISSIBLE_ALTERNATIVES")
        self.assertIsNone(result["proposal"])
        self.assertIsNone(result["decision"]["selected_plan"])
        self.assertEqual(len(result["decision"]["alternatives"]), 2)
        self.assertIsNotNone(result["answer"])

    def test_ordinary_unknown_entries_keep_symbolic_conditional_answer(self):
        result = self.answer(observation("UNKNOWN", concrete=False))
        self.assertEqual(result["status"], "CONDITIONAL_RECOMMENDATION")
        self.assertIn("SIDE_ENTRIES_TO_A_NONE", result["decision"]["conditional_premises"])
        self.assertIsNotNone(result["answer"])
        self.assertIsNone(result["proposal"])
        self.assertEqual(len(self.requests), 2)

    def test_missing_concrete_context_names_gap_without_invented_card(self):
        o = observation()
        o["target_suit"] = None
        result = self.answer(o)
        self.assertEqual(result["status"], "NEEDS_CONTEXT")
        self.assertEqual(result["missing_context"], ["target_suit"])
        self.assertIsNone(result["proposal"])
        self.assertIsNotNone(result["answer"])

    def test_hidden_and_answer_payloads_are_refused_before_reader(self):
        for key in ("full_deal", "opponent_hands", "dds_answer", "exercise_answer"):
            with self.subTest(key=key):
                o = observation()
                o[key] = "synthetic forbidden field"
                result = self.answer(o)
                self.assertEqual(result["status"], "ABSTAIN")
                self.assert_withheld(result)
        self.assertEqual(self.requests, [])

    def test_tournament_profile_cannot_activate_l1_adapter(self):
        o = observation()
        o["profile"] = "SCHOOL_TOURNAMENT_CURRENT_V1"
        result = self.answer(o)
        self.assertEqual(result["reason"], "PROFILE_SCOPE")
        self.assertEqual(self.requests, [])
        self.assert_withheld(result)

    def test_default_mode_never_calls_reader(self):
        result = answer_entry_order(observation(), self.fetch)
        self.assertEqual(result["reason"], "TEST_ONLY_OPT_IN_REQUIRED")
        self.assertEqual(self.requests, [])
        self.assert_withheld(result)

    def test_revoke_either_skill_before_disclosure_withholds_text_and_card(self):
        for skill in ("SKILL-0003", "SKILL-0004"):
            with self.subTest(skill=skill):
                reader = entry_path.load_prepared_test_reader(test_only=True)
                calls = []
                def fetch(request):
                    calls.append(deepcopy(request))
                    if len(calls) == 2:
                        reader.revoke(skill)
                    return reader.read_eligible_skills(request)
                result = self.answer(reader=fetch)
                self.assertEqual(len(calls), 2)
                self.assertEqual(result["reason"], "MISSING_ELIGIBLE_SKILL")
                self.assert_withheld(result)

    def test_second_read_failure_cannot_disclose_old_explanation(self):
        def fetch(request):
            if self.requests:
                raise OSError("SYNTHETIC_PRIVATE_SERVICE_DETAIL")
            return self.fetch(request)
        result = self.answer(reader=fetch)
        self.assertEqual(result["reason"], "READER_FETCH_FAILED")
        self.assertNotIn("SYNTHETIC_PRIVATE_SERVICE_DETAIL", str(result))
        self.assert_withheld(result)

    def test_valid_observation_drift_on_second_read_withholds_both(self):
        o = observation()
        def fetch(request):
            if self.requests:
                o["side_entries_to_a"] = "SUFFICIENT"
            return self.fetch(request)
        result = self.answer(o, fetch)
        self.assertEqual(result["reason"], "OBSERVATION_CHANGED_DURING_READ")
        self.assert_withheld(result)

    def test_version_generation_drift_withholds_symbolic_and_card_answers(self):
        for field in ("version_no", "dependency_generation"):
            for concrete in (False, True):
                with self.subTest(field=field, concrete=concrete):
                    calls = []
                    def fetch(request):
                        calls.append(request)
                        rows = self.reader.read_eligible_skills(request)
                        if len(calls) == 2:
                            for row in rows:
                                row[field] += 1
                        return rows
                    result = self.answer(observation(concrete=concrete), fetch)
                    self.assertEqual(result["reason"], "STALE_DECISION_RECOMPUTE")
                    self.assert_withheld(result)

    def test_reader_receives_no_hands_or_source_answer_and_input_is_unchanged(self):
        o = observation()
        before = deepcopy(o)
        self.answer(o)
        self.assertEqual(o, before)
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertEqual(set(self.requests[0]), {
            "system_profile", "skill_ids", "role", "phase", "trump",
            "task_class", "adapter_ref"})
        self.assertEqual(self.requests[0]["skill_ids"], ["SKILL-0003", "SKILL-0004"])

    def test_later_revocation_cannot_reuse_an_earlier_successful_response(self):
        previous = self.answer()
        self.assertIsNotNone(previous["proposal"])
        self.reader.revoke("SKILL-0004")
        current = self.answer()
        self.assertEqual(current["reason"], "MISSING_ELIGIBLE_SKILL")
        self.assert_withheld(current)


if __name__ == "__main__":
    unittest.main()
