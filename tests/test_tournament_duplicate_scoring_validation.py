import copy
from decimal import Decimal
from fractions import Fraction
import unittest

from bridge_school_api.tournament_duplicate_scoring_v3 import (
    DuplicateScoringError,
    duplicate_score_declarer,
    score_for_target_pair,
    validate_published_score,
    validate_tournament_fact_scores,
)


def _facts(contract, delta, score):
    return {
        "schema": "bridge-tournament-facts-v1",
        "columns": [
            "board", "vulnerability", "pair_direction", "status", "contract",
            "declarer", "result_delta", "pair_score",
        ],
        "rows": [f"1|None|N-S|played|{contract}|N|{delta}|{score}"],
    }


def _published(contract, delta, score=70):
    return validate_published_score(
        board_number=1, contract=contract, declarer="N", result_delta=delta,
        vulnerability="None", target_side="NS", published_score=score,
    )


class ResultDeltaValidationTests(unittest.TestCase):
    def test_reported_impossible_results_are_rejected(self):
        for contract, delta in (("7NT", 1), ("1C", -8)):
            with self.subTest(contract=contract, delta=delta):
                with self.assertRaises(DuplicateScoringError):
                    duplicate_score_declarer(contract, result_delta=delta, vulnerable=False)

    def test_fractional_results_cannot_be_truncated(self):
        for delta in (0.9, -0.9, 1.5, -1.5, Decimal("0.9"), Fraction(-1, 2)):
            with self.subTest(delta=delta):
                with self.assertRaises(DuplicateScoringError):
                    duplicate_score_declarer("1C", result_delta=delta, vulnerable=False)

    def test_boolean_and_nonnumeric_results_raise_domain_error(self):
        for delta in (True, False, None, "", " ", "bad", "0.9", "-0.9", "0.0", [], {}, b"0", 1j):
            with self.subTest(delta=delta):
                with self.assertRaises(DuplicateScoringError):
                    duplicate_score_declarer("1C", result_delta=delta, vulnerable=False)

    def test_nonfinite_results_raise_domain_error(self):
        for delta in (float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(delta=delta):
                with self.assertRaises(DuplicateScoringError):
                    duplicate_score_declarer("1C", result_delta=delta, vulnerable=False)

    def test_integer_strings_and_exact_integral_values_remain_compatible(self):
        for delta in (0, 0.0, -0.0, "0", " +0 ", Decimal("0"), Fraction(0, 1)):
            with self.subTest(delta=delta):
                self.assertEqual(duplicate_score_declarer("1C", result_delta=delta, vulnerable=False), 70)
        for delta in (-1, -1.0, "-1", " -1 ", Decimal("-1"), Fraction(-1, 1)):
            with self.subTest(delta=delta):
                self.assertEqual(duplicate_score_declarer("1C", result_delta=delta, vulnerable=False), -50)
        self.assertEqual(duplicate_score_declarer("1C", result_delta="+6", vulnerable=False), 190)

    def test_zero_and_thirteen_tricks_are_valid_for_every_contract(self):
        for level in range(1, 8):
            for strain in ("C", "D", "H", "S", "NT"):
                for doubled in ("", "X", "XX"):
                    contract = f"{level}{strain}{doubled}"
                    for vulnerable in (False, True):
                        for tricks in (0, 13):
                            with self.subTest(contract=contract, vulnerable=vulnerable, tricks=tricks):
                                score = duplicate_score_declarer(
                                    contract, result_delta=tricks - (6 + level), vulnerable=vulnerable,
                                )
                                self.assertIsInstance(score, int)
                        for tricks in (-1, 14):
                            with self.subTest(contract=contract, vulnerable=vulnerable, tricks=tricks):
                                with self.assertRaises(DuplicateScoringError):
                                    duplicate_score_declarer(
                                        contract, result_delta=tricks - (6 + level), vulnerable=vulnerable,
                                    )

    def test_boundary_scores_are_unchanged(self):
        cases = [
            ("1C", -7, False, -350), ("1C", 6, False, 190),
            ("7NT", -13, False, -650), ("7NT", 0, False, 1520),
            ("7NTX", -13, False, -3500), ("7NTXX", -13, True, -7600),
            ("7NTXX", 0, True, 2980), ("6HX", 1, True, 1860),
        ]
        for contract, delta, vulnerable, expected in cases:
            with self.subTest(contract=contract, delta=delta, vulnerable=vulnerable):
                self.assertEqual(duplicate_score_declarer(contract, result_delta=delta, vulnerable=vulnerable), expected)

    def test_target_pair_wrapper_rejects_invalid_results(self):
        for contract, delta in (("7NT", 1), ("1C", -8), ("1C", -0.9), ("1C", True)):
            with self.subTest(contract=contract, delta=delta):
                with self.assertRaises(DuplicateScoringError):
                    score_for_target_pair(
                        contract, declarer="S", result_delta=delta, vulnerability="None", target_side="EW",
                    )

    def test_published_score_wrapper_rejects_invalid_results(self):
        for contract, delta in (("7NT", 1), ("1C", -8), ("1C", 0.9), ("1C", float("inf")), ("1C", False)):
            with self.subTest(contract=contract, delta=delta):
                with self.assertRaises(DuplicateScoringError):
                    _published(contract, delta)

    def test_published_score_wrapper_preserves_valid_values(self):
        for contract, delta, score in (("1C", "-7", -350), ("1C", "+6", 190), ("7NT", "0", 1520)):
            with self.subTest(contract=contract, delta=delta):
                validation = _published(contract, delta, score)
                self.assertTrue(validation.matches)
                self.assertEqual(validation.result_delta, int(delta))
                self.assertIsInstance(validation.result_delta, int)

    def test_facts_wrapper_rejects_impossible_or_malformed_results(self):
        for contract, delta, score in (
            ("7NT", "1", 1550), ("1C", "-8", -400), ("1C", "0.9", 70),
            ("1C", "-0.9", 70), ("1C", "True", 70), ("1C", "inf", 70),
            ("1C", "bad", 70), ("1C", "", 70),
        ):
            with self.subTest(contract=contract, delta=delta):
                source = _facts(contract, delta, score)
                before = copy.deepcopy(source)
                with self.assertRaises(DuplicateScoringError):
                    validate_tournament_fact_scores(source)
                self.assertEqual(source, before)

    def test_facts_wrapper_preserves_valid_bounds_and_source(self):
        for contract, delta, score in (("1C", "-7", -350), ("1C", "+6", 190), ("7NT", "0", 1520)):
            with self.subTest(contract=contract, delta=delta):
                source = _facts(contract, delta, score)
                before = copy.deepcopy(source)
                report = validate_tournament_fact_scores(source)
                self.assertTrue(report["all_published_scores_match"])
                self.assertEqual(report["played_scores_checked"], 1)
                self.assertEqual(report["checks"][0]["result_delta"], int(delta))
                self.assertEqual(source, before)


if __name__ == "__main__":
    unittest.main()
