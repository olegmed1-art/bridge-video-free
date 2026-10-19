"""Native regression specifications. NOT_RUN in the blocked Windows environment.

These invoke the new decision seam and the repository's existing L1 evaluator.
Exercise labels belong to test names/documentation, never to runtime observations.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from bridge_school_api import teacher_entry_blocking_adapter as adapter
from bridge_school_api.l1_canonical_registry import ACTIVE_DOMAIN_RULE_IDS, SYSTEM_VERSION
from bridge_school_api.l1_canonical_runtime_v4 import evaluate

FIXTURE = Path(__file__).resolve().parents[1] / "knowledge" / "teacher_entry_blocking_prepared_v1.json"


def observation(*, entries="NONE", concrete=False, known=False):
    return {
        "schema": "teacher-symbolic-entry-v1", "profile": SYSTEM_VERSION,
        "role": "declarer", "phase": "suit_timing", "trump": "NT",
        "declarer": "N", "dummy": "S", "dummy_visible": True,
        "target_suit": "S" if known else None,
        "hand_a": {"seat": "N", "honors": ["A"], "length": 3,
                   "low_cards": ["3", "2"] if known else None},
        "hand_b": {"seat": "S", "honors": ["Q", "J"], "length": 4,
                   "low_cards": ["5", "4"] if known else None},
        "side_entries_to_a": entries, "leader": "N" if known else None,
        "current_trick": [] if known else None,
        "request": "CONCRETE_CARD" if concrete else "SYMBOLIC_PLAN",
    }


class TeacherEntryBlockingDecisionTests(unittest.TestCase):
    def setUp(self):
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]

    def decide(self, o=None, records=None):
        return adapter.decide(
            observation() if o is None else o,
            self.records if records is None else records, test_only=True,
        )

    def node(self, result, node_id):
        return next(n for n in result["trace"] if n["node_id"] == node_id)

    def test_ex0002_joint_symbolic_preference_and_two_alternatives(self):
        result = self.decide()
        self.assertEqual(result["status"], "CONDITIONAL_RECOMMENDATION")
        self.assertEqual(result["selected_plan"], "LOW_TO_QJ")
        self.assertIsNone(result["immediate_action"])
        self.assertEqual({p["plan_id"] for p in result["alternatives"]},
                         {"LOW_TO_QJ", "ACE_FIRST"})
        self.assertEqual(self.node(result, "SKILL-0003")["rule_evaluation"]["status"], "MATCH")
        self.assertEqual(self.node(result, "SKILL-0004")["rule_evaluation"]["status"], "MATCH")
        self.assertIn("SIDE_ENTRIES_TO_A_NONE", result["conditional_premises"])

    def test_ex0002_entry_preserved_in_forecast_never_spent_in_actual_ledger(self):
        result = self.decide()
        low, ace = result["alternatives"]
        self.assertEqual(low["resources"]["reserved"], ["ACE_ENTRY_A"])
        self.assertNotIn("ACE_ENTRY_A", low["resources"]["planned_consumed_first"])
        self.assertIn("ACE_ENTRY_A", ace["resources"]["planned_consumed_first"])
        self.assertEqual(self.node(result, "resources")["actually_consumed"], [])
        self.assertTrue(all(p["resources"]["actually_consumed"] == []
                            for p in result["alternatives"]))

    def test_ex0001_sufficient_communications_pair_does_not_rank(self):
        result = self.decide(observation(entries="SUFFICIENT"))
        self.assertEqual(result["status"], "ADMISSIBLE_ALTERNATIVES")
        self.assertIsNone(result["selected_plan"])
        self.assertIsNone(result["immediate_action"])
        self.assertEqual(len(result["alternatives"]), 2)
        self.assertEqual(self.node(result, "SKILL-0004")["rule_evaluation"]["status"], "NO_MATCH")
        self.assertEqual(self.node(result, "resources")["reserved_until_return"], [])

    def test_unknown_entries_gives_guarded_symbolic_plan_without_fabricated_fact(self):
        result = self.decide(observation(entries="UNKNOWN"))
        self.assertEqual(result["status"], "CONDITIONAL_RECOMMENDATION")
        self.assertEqual(self.node(result, "comparison")["side_entries_to_a"], "UNKNOWN")
        self.assertIn("SIDE_ENTRIES_TO_A_NONE", result["conditional_premises"])
        self.assertIsNone(result["immediate_action"])
        self.assertEqual(self.node(result, "comparison")["switches"],
                         {"SIDE_ENTRIES_SUFFICIENT": "UNRANKED_BY_THIS_SKILL_PAIR"})

    def test_concrete_without_suit_spots_turn_reports_exact_missing_fields(self):
        result = self.decide(observation(concrete=True))
        self.assertEqual(result["status"], "NEEDS_CONTEXT")
        self.assertEqual(set(result["missing_context"]),
                         {"target_suit", "hand_a.low_cards", "hand_b.low_cards",
                          "leader", "current_trick"})
        self.assertIsNone(result["immediate_action"])
        self.assertEqual(result["selected_plan"], "LOW_TO_QJ")

    def test_known_concrete_first_card_uses_only_supplied_visible_low_rank(self):
        result = self.decide(observation(concrete=True, known=True))
        self.assertEqual(result["status"], "CONDITIONAL_RECOMMENDATION")
        action = result["immediate_action"]
        self.assertEqual((action["seat"], action["card"]), ("N", "S2"))
        self.assertEqual(set(action["allowed_low_cards"]), {"S2", "S3"})
        self.assertEqual(action["choice_basis"], "REPRESENTATIVE_LOW_NOT_PROVEN_BEST_SPOT")

    def test_symbolic_request_never_issues_card_even_with_full_first_step_context(self):
        result = self.decide(observation(known=True))
        self.assertIsNone(result["immediate_action"])

    def test_concrete_unknown_entries_abstains_from_card_and_names_context(self):
        result = self.decide(observation(entries="UNKNOWN", concrete=True, known=True))
        self.assertEqual(result["status"], "NEEDS_CONTEXT")
        self.assertEqual(result["missing_context"], ["side_entries_to_a"])
        self.assertIsNone(result["immediate_action"])

    def test_concrete_partial_spots_cannot_guess_remaining_rank(self):
        o = observation(concrete=True, known=True)
        o["hand_a"]["low_cards"] = ["3"]
        o["hand_b"]["low_cards"] = []
        result = self.decide(o)
        self.assertEqual(result["missing_context"], ["hand_a.low_cards", "hand_b.low_cards"])
        self.assertIsNone(result["immediate_action"])

    def test_sufficient_concrete_does_not_import_ace_first_answer(self):
        result = self.decide(observation(entries="SUFFICIENT", concrete=True, known=True))
        self.assertEqual(result["status"], "ADMISSIBLE_ALTERNATIVES")
        self.assertIsNone(result["selected_plan"])
        self.assertIsNone(result["immediate_action"])

    def test_relabel_friendly_seats_and_suit_does_not_depend_on_exercise(self):
        o = observation(concrete=True, known=True)
        o["target_suit"] = "H"
        o["hand_a"]["seat"], o["hand_b"]["seat"], o["leader"] = "S", "N", "S"
        result = self.decide(o)
        self.assertEqual(result["immediate_action"]["card"], "H2")
        self.assertEqual(result["immediate_action"]["seat"], "S")

    def test_high_spot_selection_is_representative_and_not_a_probability_claim(self):
        o = observation(concrete=True, known=True)
        o["hand_a"]["low_cards"], o["hand_b"]["low_cards"] = ["9", "8"], ["7", "6"]
        result = self.decide(o)
        self.assertEqual(result["immediate_action"]["card"], "S8")
        self.assertIsNone(result["success_probability"])

    def test_q_wins_is_condition_not_known_defender_location(self):
        result = self.decide()
        branches = result["alternatives"][0]["continuations"]
        self.assertIn("Q_WINS_TRICK", [b["condition"] for b in branches])
        self.assertIn("K_CAPTURES_Q", [b["condition"] for b in branches])
        self.assertIn("OTHER_PUBLIC_RESPONSE_INCLUDING_EARLY_K",
                      [b["condition"] for b in branches])
        self.assertIn("NO_QUEUED_PLAY_FROM_HAND_B", [b["effect"] for b in branches])
        self.assertIn("NO_PROBABILITY_OR_THREE_TRICK_GUARANTEE", result["limits"])
        self.assertIsNone(result["success_probability"])
        self.assertIsNone(self.node(result, "comparison")["success_probability"])

    def test_own_and_dummy_visibility_gate_before_l1_evaluation(self):
        o = observation()
        o["dummy_visible"] = False
        with patch.object(adapter, "evaluate_l1") as evaluator:
            result = self.decide(o)
        self.assertEqual(result["reason"], "DUMMY_NOT_VISIBLE")
        evaluator.assert_not_called()

    def test_other_leader_cannot_activate_current_hand_a_first_step(self):
        for leader in ("S", "E", "W"):
            with self.subTest(leader=leader):
                o = observation(known=True)
                o["leader"] = leader
                result = self.decide(o)
                self.assertEqual(result["status"], "ABSTAIN")
                self.assertEqual(result["reason"], "SUPPORTED_FIRST_STEP_REQUIRES_LEAD_FROM_A")
                self.assertIsNone(result["immediate_action"])

    def test_nonempty_public_trick_requires_new_adapter_or_replanning(self):
        o = observation(known=True)
        o["current_trick"] = [{"seat": "N", "card": "S2"}]
        result = self.decide(o)
        self.assertEqual(result["status"], "ABSTAIN")
        self.assertEqual(result["reason"], "FIRST_STEP_ONLY_REPLAN_AFTER_PUBLIC_PLAY")

    def test_role_phase_contract_and_tournament_profile_remain_outside_scope(self):
        for key, value in (("role", "defender"), ("phase", "before_play"),
                           ("trump", "H"), ("profile", "SCHOOL_TOURNAMENT_CURRENT_V1")):
            with self.subTest(key=key):
                o = observation()
                o[key] = value
                result = self.decide(o)
                self.assertEqual(result["status"], "ABSTAIN")
                self.assertIsNone(result["selected_plan"])

    def test_strict_input_allowlist_rejects_answer_and_hidden_fields_without_echo(self):
        for key in ("exercise_id", "answer_key", "opponent_cards", "full_deal",
                    "dds_result", "source_answer", "hcp"):
            with self.subTest(key=key):
                o = observation()
                o[key] = "private-sentinel"
                result = self.decide(o)
                self.assertEqual(result["reason"], "OBSERVATION_FIELDS")
                self.assertNotIn("private-sentinel", json.dumps(result))
                self.assertEqual(result["trace"], [])

    def test_nested_hand_allowlist_and_friendly_seats_reject_hidden_payload(self):
        o = observation()
        o["hand_a"]["hidden_cards"] = "private-sentinel"
        self.assertEqual(self.decide(o)["reason"], "HAND_FIELDS")
        o = observation()
        o["hand_b"]["seat"] = "E"
        self.assertEqual(self.decide(o)["reason"], "HAND_VISIBILITY")

    def test_other_holdings_and_ten_are_not_treated_as_unknown_small_card(self):
        for name, field, value in (("hand_a", "honors", ["A", "K"]),
                                   ("hand_b", "honors", ["Q", "T"]),
                                   ("hand_a", "length", 4),
                                   ("hand_a", "low_cards", ["T", "2"])):
            with self.subTest(name=name, field=field):
                o = observation()
                o[name][field] = value
                result = self.decide(o)
                self.assertEqual(result["status"], "ABSTAIN")

    def test_duplicate_known_card_and_bool_length_are_rejected(self):
        o = observation(known=True)
        o["hand_b"]["low_cards"] = ["2", "4"]
        self.assertEqual(self.decide(o)["reason"], "DUPLICATE_CARD")
        o = observation()
        o["hand_a"]["length"] = True
        self.assertEqual(self.decide(o)["reason"], "HOLDING_PATTERN_SCOPE")

    def test_missing_top_fields_are_precise_context_request(self):
        o = observation()
        del o["leader"]
        result = self.decide(o)
        self.assertEqual(result["status"], "NEEDS_CONTEXT")
        self.assertEqual(result["missing_context"], ["leader"])

    def test_record_revoke_of_either_skill_excludes_joint_plan(self):
        for skill in ("SKILL-0003", "SKILL-0004"):
            with self.subTest(revoked=skill):
                surviving = [r for r in self.records
                             if r["content"]["skill_contract"]["skill_id"] != skill]
                result = self.decide(records=surviving)
                self.assertEqual(result["reason"], "MISSING_ELIGIBLE_SKILL")
                self.assertIsNone(result["selected_plan"])
                self.assertEqual(result["trace"], [])

    def test_revoked_skill_prevents_old_decision_action_on_fresh_shortlist(self):
        o = observation(concrete=True, known=True)
        old = self.decide(o)
        fresh_records = deepcopy(self.records[1:])
        fresh_records[0]["dependency_generation"] = 2
        action = adapter.next_action(old, o, fresh_records, test_only=True)
        self.assertEqual(action["status"], "ABSTAIN")
        self.assertEqual(action["reason"], "MISSING_ELIGIBLE_SKILL")
        self.assertIsNone(action["action"])

    def test_retired_unapproved_or_wrong_profile_record_cannot_rank(self):
        for key, value in (("review_status", "pending"), ("version_status", "retired"),
                           ("system_profile", "SCHOOL_TOURNAMENT_CURRENT_V1")):
            with self.subTest(key=key):
                records = deepcopy(self.records)
                records[0][key] = value
                self.assertEqual(self.decide(records=records)["reason"], "SKILL_NOT_ELIGIBLE")

    def test_contract_payload_change_and_mixed_generation_fail_closed(self):
        records = deepcopy(self.records)
        records[0]["content"]["skill_contract"]["actions"] = ["CASH_A"]
        self.assertEqual(self.decide(records=records)["reason"], "UNSUPPORTED_SKILL_CONTRACT")
        records = deepcopy(self.records)
        records[0]["dependency_generation"] = 2
        self.assertEqual(self.decide(records=records)["reason"], "MIXED_DEPENDENCY_GENERATIONS")

    def test_generation_change_recomputes_and_refuses_cached_action(self):
        o = observation(concrete=True, known=True)
        old = self.decide(o)
        records = deepcopy(self.records)
        for r in records:
            r["dependency_generation"] = 2
        result = self.decide(o, records)
        self.assertEqual(result["binding"]["dependency_generation"], 2)
        self.assertNotEqual(result["binding"], old["binding"])
        action = adapter.next_action(old, o, records, test_only=True)
        self.assertEqual(action["reason"], "STALE_DECISION_RECOMPUTE")
        self.assertIsNone(action["action"])

    def test_version_change_without_generation_change_still_refuses_cached_action(self):
        o = observation(concrete=True, known=True)
        old = self.decide(o)
        records = deepcopy(self.records)
        records[0]["version_no"] = 2
        action = adapter.next_action(old, o, records, test_only=True)
        self.assertEqual(action["reason"], "STALE_DECISION_RECOMPUTE")

    def test_observation_change_refuses_old_binding_without_replay(self):
        o = observation(concrete=True, known=True)
        old = self.decide(o)
        changed = deepcopy(o)
        changed["target_suit"] = "D"
        action = adapter.next_action(old, changed, self.records, test_only=True)
        self.assertEqual(action["reason"], "STALE_DECISION_RECOMPUTE")
        self.assertIsNone(action["action"])

    def test_tampered_cached_card_is_ignored_and_real_decision_recomputed(self):
        o = observation(concrete=True, known=True)
        old = self.decide(o)
        old["immediate_action"]["card"] = "SA"
        action = adapter.next_action(old, o, self.records, test_only=True)
        self.assertEqual(action["status"], "ACTION")
        self.assertEqual(action["action"]["card"], "S2")

    def test_duplicate_versions_and_invalid_generation_are_rejected(self):
        records = [deepcopy(self.records[0]), deepcopy(self.records[0])]
        self.assertEqual(self.decide(records=records)["reason"], "DUPLICATE_SKILL_VERSION")
        for value in (0, True, "2", None):
            with self.subTest(value=value):
                records = deepcopy(self.records)
                records[0]["dependency_generation"] = value
                self.assertEqual(self.decide(records=records)["reason"],
                                 "DEPENDENCY_GENERATION_REQUIRED")

    def test_unapproved_or_answer_provenance_cannot_enter_runtime(self):
        records = deepcopy(self.records)
        records[0]["provenance"][0]["review_status"] = "DRAFT"
        self.assertEqual(self.decide(records=records)["reason"], "APPROVED_PROVENANCE_REQUIRED")
        records = deepcopy(self.records)
        records[0]["provenance"][0]["answer_key"] = "private-sentinel"
        self.assertEqual(self.decide(records=records)["reason"], "APPROVED_PROVENANCE_REQUIRED")
        records = deepcopy(self.records)
        records[0]["content"]["answer_key"] = "private-sentinel"
        result = self.decide(records=records)
        self.assertEqual(result["reason"], "CONTENT_FIELDS")
        self.assertNotIn("private-sentinel", json.dumps(result))

    def test_real_l1_evaluator_is_called_after_validation_with_derived_flags(self):
        with patch.object(adapter, "evaluate_l1", wraps=evaluate) as evaluator:
            result = self.decide()
        self.assertEqual(evaluator.call_count, 2)
        self.assertEqual(evaluator.call_args_list[0].args,
                         ("RULE-L1-PLAY-PRESERVE-ENTRIES",
                          {"planned_target_hand_known": True}))
        self.assertEqual(evaluator.call_args_list[1].args,
                         ("RULE-L1-PLAY-AVOID-BLOCKING",
                          {"entry_or_access_risk": True}))
        self.assertEqual(result["selected_plan"], "LOW_TO_QJ")

    def test_changed_l1_handler_does_not_keep_old_rank(self):
        original = evaluate("RULE-L1-PLAY-PRESERVE-ENTRIES",
                            {"planned_target_hand_known": False})
        with patch.object(adapter, "evaluate_l1", return_value=original):
            result = self.decide()
        self.assertEqual(result["reason"], "L1_RULE_EVALUATION_CHANGED")
        self.assertIsNone(result["selected_plan"])

    def test_explanation_and_citations_are_rendered_from_same_selection_trace(self):
        result = self.decide(observation(concrete=True, known=True))
        self.assertEqual(result["explanation"], adapter.render_explanation(result["trace"]))
        selection = self.node(result, "selection")
        self.assertEqual(selection["selected_plan"], result["selected_plan"])
        self.assertEqual(selection["immediate_action"], result["immediate_action"])
        citations = result["explanation"]["citations"]
        self.assertEqual({c["skill_id"] for c in citations}, {"SKILL-0003", "SKILL-0004"})
        for citation in citations:
            n = self.node(result, citation["skill_id"])
            self.assertIn(citation["canonical_refs"]["fact_id"],
                          n["rule_evaluation"]["evidence"])
        self.assertIn("S2", " ".join(result["explanation"]["paragraphs_ru"]))

    def test_runtime_result_has_no_exercise_key_hidden_hand_or_probability(self):
        result = self.decide()
        output = json.dumps(result, ensure_ascii=False)
        for forbidden in ("EX0001", "EX0002", "answer_key", "opponent_cards",
                          "full_deal", "DDS", "HCP", "%"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, output)

    def test_no_mutation_or_dependency_order_sensitivity(self):
        o, records = observation(), deepcopy(self.records)
        before = deepcopy((o, records))
        result = self.decide(o, records)
        self.assertEqual((o, records), before)
        reversed_result = self.decide(o, list(reversed(records)))
        self.assertEqual(result, reversed_result)

    def test_existing_l1_registry_and_direct_evaluations_remain_unchanged(self):
        ids = tuple(ACTIVE_DOMAIN_RULE_IDS)
        before = evaluate("RULE-L1-PLAY-AVOID-BLOCKING", {"entry_or_access_risk": True})
        negative = evaluate("RULE-L1-PLAY-AVOID-BLOCKING", {"entry_or_access_risk": False})
        self.decide()
        self.assertEqual(tuple(ACTIVE_DOMAIN_RULE_IDS), ids)
        self.assertEqual(evaluate("RULE-L1-PLAY-AVOID-BLOCKING",
                                  {"entry_or_access_risk": True}), before)
        self.assertEqual(evaluate("RULE-L1-PLAY-AVOID-BLOCKING",
                                  {"entry_or_access_risk": False}), negative)
        self.assertEqual(before.status, "MATCH")
        self.assertEqual(negative.status, "NO_MATCH")

    def test_default_requires_explicit_test_only_opt_in_for_decision_and_card(self):
        o = observation(concrete=True, known=True)
        result = adapter.decide(o, self.records)
        self.assertEqual(result["reason"], "TEST_ONLY_OPT_IN_REQUIRED")
        action = adapter.next_action({}, o, self.records)
        self.assertEqual(action["reason"], "TEST_ONLY_OPT_IN_REQUIRED")
        self.assertIsNone(action["action"])

    def test_bool_contract_version_does_not_match_numeric_schema_version(self):
        records = deepcopy(self.records)
        records[0]["content"]["skill_contract"]["contract_version"] = True
        self.assertEqual(self.decide(records=records)["reason"], "UNSUPPORTED_SKILL_CONTRACT")


if __name__ == "__main__":
    unittest.main()
