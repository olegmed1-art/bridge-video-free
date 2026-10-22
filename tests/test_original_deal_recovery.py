"""Independent enumeration oracle and bounded original-deal recovery cases."""
from itertools import product
import unittest

from bridge_contracts.video_deal import FULL_DECK, SEATS
from bridge_vision.original_deal_solver import (
    RecoveryInputError, feasible_owner_domains, recover_original_deal,
)


def brute_force(deck, capacities, domains):
    """Exhaustive assignments, deliberately no matching/augmenting paths."""
    solutions = []
    for owners in product(*[domains[c] for c in deck]):
        if all(owners.count(s) == n for s, n in capacities.items()):
            solutions.append(owners)
    return {c: sorted({sol[i] for sol in solutions}) for i, c in enumerate(deck)} if solutions else {}


class SolverTests(unittest.TestCase):
    def test_exhaustive_small_models_against_brute_force(self):
        deck = list("abcd")
        caps = {"N": 2, "E": 1, "S": 1}
        choices = [[s for i, s in enumerate(caps) if mask & (1 << i)] for mask in range(1, 8)]
        for values in product(choices, repeat=4):
            domains = dict(zip(deck, values))
            expected = brute_force(deck, caps, domains)
            actual = feasible_owner_domains(deck, caps, domains)
            self.assertEqual(actual["domains"], expected, domains)
            status = "CONFLICT" if not expected else "UNIQUE" if all(len(v) == 1 for v in expected.values()) else "MULTIPLE"
            self.assertEqual(actual["status"], status)

    def test_hall_deficit_no_empty_domain_or_individual_overfull(self):
        result = feasible_owner_domains(list("abcd"), dict.fromkeys("NESW", 1),
                                        {"a": ["N", "E"], "b": ["N", "E"], "c": ["N", "E"], "d": ["S", "W"]})
        self.assertEqual(result["status"], "CONFLICT")

    def test_multiple_solutions_can_have_forced_cards(self):
        result = feasible_owner_domains(list("abc"), dict.fromkeys("NES", 1),
                                        {"a": ["N"], "b": ["E", "S"], "c": ["E", "S"]})
        self.assertEqual(result["status"], "MULTIPLE")
        self.assertEqual(result["domains"], {"a": ["N"], "b": ["E", "S"], "c": ["E", "S"]})

    def facts(self, count):
        return [{"card": c, "seat": SEATS[i // 13], "evidence_id": f"proof-{i}"}
                for i, c in enumerate(sorted(FULL_DECK)[:count])]

    def test_unique_deck_complement_provenance_is_not_observation(self):
        result = recover_original_deal(self.facts(39))
        self.assertEqual(result["status"], "VERIFIED_FULL")
        self.assertEqual(len(result["inferred"]), 13)
        self.assertEqual(result["deal"]["hands"]["W"]["observed_cards"], [])
        self.assertEqual(len(result["deal"]["hands"]["W"]["inferred_cards"]), 13)
        for proof in result["inferred"].values():
            self.assertEqual(len(proof["premise_evidence_ids"]), 39)
            self.assertTrue(proof["conditional_on_premises"])
        self.assertLessEqual(result["matching_calls"], 209)
        self.assertFalse(result["canonical_promotion_allowed"])

    def test_partial_zero_facts_full_and_duplicate(self):
        for count, expected in [(0, "VERIFIED_PARTIAL"), (38, "VERIFIED_PARTIAL"), (52, "VERIFIED_FULL")]:
            result = recover_original_deal(self.facts(count))
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["inferred"], {})
        facts = self.facts(39)
        self.assertEqual(recover_original_deal(facts), recover_original_deal(facts + facts))

    def test_wrong_but_consistent_observations_are_still_conditional(self):
        facts = self.facts(52)
        facts[0]["seat"], facts[13]["seat"] = facts[13]["seat"], facts[0]["seat"]
        result = recover_original_deal(facts)
        self.assertEqual(result["solution_status"], "UNIQUE")
        self.assertEqual(result["verification"], "CONDITIONAL_ON_REVIEWED_EVIDENCE")
        self.assertFalse(result["real_video_accuracy_evaluated"])

    def test_conflicting_and_overfull_observations(self):
        facts = self.facts(13)
        for extra in [dict(facts[0], seat="E", evidence_id="conflict"),
                      {"card": sorted(FULL_DECK)[13], "seat": "N", "evidence_id": "overfull"},
                      dict(facts[0], card=sorted(FULL_DECK)[13])]:
            result = recover_original_deal(facts + [extra])
            self.assertEqual(result["status"], "CONFLICT")
            self.assertEqual(result["inferred"], {})
            self.assertIsNone(result["deal"])

    def test_malformed_inputs(self):
        for facts in [None, "AS", [{}], [{"card": [], "seat": "N", "evidence_id": "x"}],
                      [{"card": "AS", "seat": ["N"], "evidence_id": "x"}]]:
            with self.assertRaises(RecoveryInputError):
                recover_original_deal(facts)
        for deck, caps, domains in [(list("aa"), {"N": 2}, {"a": ["N"]}),
                                    (["a"], {"N": True}, {"a": ["N"]}),
                                    (["a"], {"N": 1}, {"a": "N"}),
                                    (["a"], {"N": 1}, {"a": [["N"]]})]:
            with self.assertRaises(RecoveryInputError):
                feasible_owner_domains(deck, caps, domains)


if __name__ == "__main__":
    unittest.main()
