from __future__ import annotations

"""Synthetic legacy outcome regressions; no fitting, producer or SQL calls."""

import unittest

from confidence_calibration import _loss, _success


class ConfidenceOutcomeTests(unittest.TestCase):
    def test_source_card_success_cannot_label_a_different_chosen_card(self) -> None:
        result = {"scores": {"SA": 5, "S2": 3}, "dd_regret": 0}
        self.assertFalse(_success("opening_lead", {"card": "S2"}, result))
        self.assertEqual(_loss("opening_lead", {"card": "S2"}, result), 2.0)

    def test_source_card_failure_cannot_label_an_optimal_chosen_card(self) -> None:
        result = {"scores": {"SA": 5, "S2": 3}, "dd_regret": 2}
        self.assertTrue(_success("opening_lead", {"card": "SA"}, result))
        self.assertEqual(_loss("opening_lead", {"card": "SA"}, result), 0.0)

    def test_tied_optimal_chosen_card_is_success(self) -> None:
        result = {"scores": {"SA": 5, "SK": 5, "S2": 3}, "dd_regret": 2}
        self.assertTrue(_success("opening_lead", {"card": "sk"}, result))
        self.assertEqual(_loss("opening_lead", {"card": "sk"}, result), 0.0)

    def test_card_scores_suffice_without_source_card_regret(self) -> None:
        result = {"scores": {"SA": 5, "S2": 3}}
        self.assertTrue(_success("opening_lead", {"card": "SA"}, result))
        self.assertEqual(_loss("opening_lead", {"card": "S2"}, result), 2.0)

    def test_missing_card_specific_evidence_fails_closed(self) -> None:
        for result in ({"dd_regret": 0}, {"scores": {}, "dd_regret": 0}, {"scores": {"SA": 5}, "dd_regret": 0}):
            for function in (_success, _loss):
                with self.subTest(result=result, function=function.__name__):
                    with self.assertRaises(ValueError):
                        function("opening_lead", {"card": "S2"}, result)

    def test_nonfinite_scores_and_contradictory_optimum_fail_closed(self) -> None:
        for result in (
            {"scores": {"SA": float("nan")}, "dd_regret": 0},
            {"scores": {"SA": float("inf")}, "dd_regret": 0},
            {"scores": {"SA": 14}, "dd_regret": 0},
            {"scores": {"SA": 5}, "best_defense_tricks": 6, "dd_regret": 0},
            {"scores": {"SA": 5, "sa": 4}, "dd_regret": 0},
        ):
            for function in (_success, _loss):
                with self.subTest(result=result, function=function.__name__):
                    with self.assertRaises(ValueError):
                        function("opening_lead", {"card": "SA"}, result)

    def test_contract_outcomes_preserve_trick_error_semantics(self) -> None:
        self.assertTrue(_success("contract_tricks", {"tricks": 9}, {"dds_tricks": 9, "dd_regret": 2}))
        self.assertFalse(_success("contract_tricks", {"tricks": 8}, {"dds_tricks": 9, "dd_regret": 0}))
        self.assertEqual(_loss("contract_tricks", {"tricks": 7}, {"dds_tricks": 9}), 2.0)

    def test_continuation_outcomes_preserve_existing_regret_semantics(self) -> None:
        for task_type in ("declarer_continuation", "defense_continuation"):
            with self.subTest(task_type=task_type):
                self.assertTrue(_success(task_type, {}, {"dd_regret": 0}))
                self.assertFalse(_success(task_type, {}, {"dd_regret": 2}))
                self.assertEqual(_loss(task_type, {}, {"dd_regret": 2}), 2.0)
                self.assertFalse(_success(task_type, {}, {}))
                self.assertEqual(_loss(task_type, {}, {}), 13.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
