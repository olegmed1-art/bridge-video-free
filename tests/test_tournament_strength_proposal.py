"""Synthetic/offline reasoning regressions; no canon or live activation."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from bridge_school_api import tournament_strength_proposal as proposal
from bridge_school_api.tournament_shape_core import evaluate, digest, PAYLOAD_HASHES
from test_tournament_shape_exercises import catalog, context, SCHOOL


def opening(**changes):
    policy = proposal.load_policy()
    return {"school_id": str(SCHOOL), "system_profile": policy["system_profile"],
            "rule_key": policy["opening_rule_key"], "hcp_min": 15, "hcp_max": 17,
            "point_method": "HCP", "source_kind": "school_canon_snapshot",
            "source_url": policy["opening_source_url"], "source_version": policy["opening_source_version"],
            "evidence_scope": "REVIEWED_SNAPSHOT_NOT_RUNTIME_ACTIVATION", **changes}


def assess(hand="A73.3.J942.Q7654", call="3H", **changes):
    args = dict(context=context(), hand=hand, call=call, catalog=catalog(call),
                opening_evidence=opening(), policy_version=proposal.POLICY_VERSION)
    args.update(changes)
    return proposal.evaluate_proposal(**args)


def generate(**changes):
    args = dict(context=context(), call="3H", catalog=catalog(), opening_evidence=opening(),
                policy_version=proposal.POLICY_VERSION, seed=20261011, count=2, positive_count=2)
    args.update(changes)
    return proposal.generate_proposal_exercises(**args)


class DerivedReasoningTests(unittest.TestCase):
    def test_policy_contains_premises_not_a_looked_up_answer(self):
        policy = proposal.load_policy()
        self.assertNotIn("responder_hcp_min", policy)
        self.assertEqual(policy["status"], "PROPOSAL")
        self.assertEqual(policy["source_kind"], "inference")
        self.assertFalse(policy["canon_activation"])

    def test_requested_hands_and_9_10_11_boundary_in_both_calls(self):
        cases = [("A73.3.J942.Q7654", 7), ("AKQ.2.5432.65432", 9),
                 ("AKQ.J.5432.65432", 10), ("AKQ.Q.5432.65432", 11)]
        for original, hcp in cases:
            for call in ("3H", "3S"):
                parts = original.split(".")
                hand = original if call == "3H" else ".".join([parts[1], parts[0], *parts[2:]])
                result = assess(hand, call)
                self.assertEqual(result["observed_hcp"], hcp)
                self.assertEqual(result["status"], "PROPOSAL_PASS" if hcp >= 10 else "PROPOSAL_FAIL")
                self.assertEqual(result["responder_hcp_min"], 25 - 15)
                self.assertIsNone(result["call_approved"])
                self.assertIsNone(result["learner_error"])
                self.assertIsNone(result["game_guaranteed"])
                self.assertFalse(result["best_bid_assessed"])
                self.assertFalse(result["assessment_complete"])
        self.assertEqual(assess("J.AQ8.J632.K9865", "3S")["status"], "PROPOSAL_PASS")

    def test_shape_is_mandatory_even_with_enough_strength(self):
        result = assess("AKQJ.AKQ.AKQ.AKQ")
        self.assertEqual(result["status"], "PROPOSAL_FAIL")
        self.assertEqual(result["required_conditions"]["shape"], "MISMATCH")
        self.assertEqual(result["required_conditions"]["responder_hcp_min"], "PASS")

    def test_proof_has_sourced_premises_and_subtraction_before_shape_gate(self):
        result = assess("AKQ.Q.5432.65432")
        proof = result["derivation"]
        self.assertEqual([s["id"] for s in proof["steps"]],
            ["agreement", "game_force", "game_goal", "partner_min", "responder_min", "shape_required"])
        step = proof["steps"][4]
        self.assertEqual((step["operation"], step["operands"], step["result"]), ("subtract", [25, 15], 10))
        self.assertEqual(proof["premises"]["game_goal"]["source_kind"], "heuristic")
        self.assertEqual(proof["premises"]["agreement"]["source"]["payload_sha256"], PAYLOAD_HASHES["3H"])
        self.assertIn("25 HCP", result["explanation"])
        self.assertIn("15 HCP", result["explanation"])
        self.assertIn("10 HCP", result["explanation"])

    def test_synthetic_parameterized_reasoning_recalculates_threshold(self):
        base = assess()["derivation"]["premises"]
        for target, low, high, expected in ((25,15,17,10), (25,12,14,13), (26,14,16,12), (24,16,18,8)):
            premises = deepcopy(base)
            for item in premises.values():
                item["source"] = {"kind": "SYNTHETIC_TEST_ONLY"}
                item["version"] = "synthetic-premise-v1"
            premises["game_goal"]["hcp"] = target
            premises["opening"].update(hcp_min=low, hcp_max=high)
            proof = proposal.derive_strength_requirement(premises)
            self.assertEqual(proof["responder_hcp_min"], expected)
            self.assertEqual(proof["steps"][4]["operands"], [target, low])
        # Generic arithmetic does not change the pinned school wrapper.
        self.assertEqual(assess(opening_evidence=opening(hcp_min=12,hcp_max=14))["status"], "ABSTAIN")

    def test_missing_conflicting_or_unknown_reasoning_premises_abstain(self):
        base = assess()["derivation"]["premises"]
        self.assertEqual(proposal.derive_strength_requirement({})["status"], "ABSTAIN")
        for premise in base:
            for field, value in (("status", "UNKNOWN"), ("conflicts", ["other-source"]),
                                 ("source", {}), ("version", "")):
                changed = deepcopy(base)
                changed[premise][field] = value
                self.assertEqual(proposal.derive_strength_requirement(changed)["status"], "ABSTAIN")
        for field, value in (("hcp_min", True), ("hcp_min", 18), ("hcp_max", None)):
            changed = deepcopy(base)
            changed["opening"][field] = value
            self.assertEqual(proposal.derive_strength_requirement(changed)["status"], "ABSTAIN")
        base["agreement"]["forcing_status"] = "UNKNOWN"
        self.assertEqual(proposal.derive_strength_requirement(base)["status"], "ABSTAIN")

    def test_opening_range_source_school_and_version_drift_abstain(self):
        cases = [None, {}, opening(hcp_min=14), opening(hcp_max=18), opening(hcp_min=True),
                 opening(source_url="unknown"), opening(source_version="stale"),
                 opening(source_kind="world_reference"), opening(school_id=str(UUID(int=900))),
                 opening(system_profile="SCHOOL_L1_DB_V1"), opening(point_method="distribution"),
                 opening(conflicts=["another opening range"])]
        for source in cases:
            answer = assess(opening_evidence=source)
            self.assertEqual(answer["status"], "ABSTAIN")
            self.assertIsNone(answer["policy_conditions_met"])
            batch = generate(opening_evidence=source)
            self.assertEqual(batch.status, "ABSTAIN")
            self.assertEqual(batch.exercises, ())

    def test_catalog_unknown_or_altered_never_becomes_negative_example(self):
        for rows in ([], catalog()):
            if rows:
                rows[0]["method_version"] = "stale"
            self.assertEqual(assess(catalog=rows)["status"], "ABSTAIN")
            self.assertEqual(generate(catalog=rows).exercises, ())

    def test_policy_hash_and_version_drift_abstain(self):
        self.assertEqual(assess(policy_version="stale")["status"], "ABSTAIN")
        altered = proposal.load_policy()
        altered["partnership_target_hcp"] = 26
        args = dict(context=context(), call="3H", catalog=catalog(),
                    opening_evidence=opening(), policy_version=proposal.POLICY_VERSION)
        with patch.object(proposal.Path, "read_text", return_value=json.dumps(altered)):
            self.assertEqual(proposal.evaluate_proposal(hand="A73.3.J942.Q7654", **args)["status"], "ABSTAIN")
            self.assertEqual(proposal.generate_proposal_exercises(seed=7, **args).exercises, ())

    def test_default_canonical_evaluator_stays_abstain(self):
        answer = evaluate(context(hand_pbn="AKQ.Q.5432.65432"), "3H", catalog())
        self.assertEqual(answer["status"], "ABSTAIN")
        self.assertNotIn("derivation", answer)
        self.assertNotIn("policy_conditions_met", answer)
        self.assertEqual(digest(catalog()[0]["compiled_payload"]), PAYLOAD_HASHES["3H"])

    def test_reproducible_positive_examples_and_private_projection(self):
        for call, seed in (("3H", 20261011), ("3S", 20261012)):
            batch = generate(call=call, catalog=catalog(call), seed=seed, count=2)
            self.assertEqual(batch, generate(call=call, catalog=catalog(call), seed=seed, count=2))
            self.assertEqual(batch.status, "PROPOSAL_GENERATED")
            self.assertEqual(len({x.hand_pbn for x in batch.exercises}), 2)
            for exercise in batch.exercises:
                result = assess(exercise.hand_pbn, call)
                self.assertEqual(result["status"], "PROPOSAL_PASS")
                view = exercise.student_view()
                self.assertEqual(view["policy_status"], "PROPOSAL")
                for hidden in ("expected", "seed", "source_bindings", "opening_binding", "derivation"):
                    self.assertNotIn(hidden, view)

    def test_feedback_rederives_all_conditions_and_no_canonical_learner_error(self):
        batch = generate(count=8, positive_count=4)
        self.assertEqual(batch.status, "PROPOSAL_GENERATED")
        for exercise in batch.exercises:
            truth = assess(exercise.hand_pbn)["policy_conditions_met"]
            answer = exercise.assess_response(not truth, catalog=catalog(), opening_evidence=opening(),
                                               policy_version=proposal.POLICY_VERSION)
            self.assertEqual(answer["status"], "PROPOSAL_INCORRECT")
            self.assertTrue(answer["proposal_response_error"])
            self.assertIsNone(answer["learner_error"])
            self.assertIsNone(answer["call_approved"])
            self.assertIn("derivation", answer)

    def test_replay_rechecks_policy_and_both_source_bindings(self):
        task = generate(count=1, positive_count=1).exercises[0]
        rows = catalog()
        rows[0]["runtime_activation_id"] = UUID(int=901)
        for exercise, source, version, data in (
            (task, opening(hcp_min=14), proposal.POLICY_VERSION, catalog()),
            (task, opening(), "stale", catalog()),
            (replace(task, _policy_sha256="old"), opening(), proposal.POLICY_VERSION, catalog()),
            (replace(task, _expected=False), opening(), proposal.POLICY_VERSION, catalog()),
            (task, opening(), proposal.POLICY_VERSION, rows)):
            answer = exercise.assess_response(False, catalog=data, opening_evidence=source, policy_version=version)
            self.assertEqual(answer["status"], "ABSTAIN")
            self.assertIsNone(answer["learner_error"])

    def test_mutated_stored_proof_cannot_change_delivery_or_silently_grade_other_premises(self):
        task = generate(count=1, positive_count=1).exercises[0]
        for field, value in (("game_goal", {"hcp": 40}), ("opening", {"hcp_min": 12, "hcp_max": 14})):
            changed = deepcopy(task._derivation)
            changed["premises"][field].update(value)
            stale = replace(task, _derivation=changed)
            self.assertEqual(stale.student_view()["status"], "ABSTAIN")
            feedback = stale.assess_response(True, catalog=catalog(), opening_evidence=opening(),
                                            policy_version=proposal.POLICY_VERSION)
            self.assertEqual(feedback["reason"], "EXERCISE_PROOF_CHANGED")
        for stale in (replace(task, _policy_version="old"), replace(task, _policy_sha256="old")):
            self.assertEqual(stale.student_view()["status"], "ABSTAIN")

    def test_consistently_forged_non_uuid_school_is_rejected(self):
        rows = catalog()
        rows[0]["school_id"] = "forged"
        answer = assess(context=context(school_id="forged"), catalog=rows,
                        opening_evidence=opening(school_id="forged"))
        self.assertEqual(answer["status"], "ABSTAIN")


if __name__ == "__main__":
    unittest.main()
