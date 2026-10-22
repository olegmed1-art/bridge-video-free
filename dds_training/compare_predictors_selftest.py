from __future__ import annotations

"""Small synthetic regressions for predictor comparison; no database access."""

import json
import tempfile
import unittest
from pathlib import Path

from compare_predictors import (
    _select_family,
    evaluate,
    paired_bootstrap_difference,
    per_task_losses,
    write_selected_predictions,
)


class ComparisonCohortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = {
            "A": {"task_type": "contract_tricks"},
            "B": {"task_type": "contract_tricks"},
        }
        self.results = {"A": {"dds_tricks": 9}, "B": {"dds_tricks": 9}}
        self.baseline = {"A": {"tricks": 8}, "B": {"tricks": 9}}
        self.incomplete = {"A": {"tricks": 9}}

    def test_missing_candidate_prediction_cannot_enter_metrics(self) -> None:
        with self.assertRaisesRegex(ValueError, "[Cc]ohort"):
            evaluate(self.tasks, self.incomplete, self.results)

    def test_missing_candidate_prediction_cannot_enter_selection_losses(self) -> None:
        with self.assertRaisesRegex(ValueError, "[Cc]ohort"):
            per_task_losses(self.tasks, self.incomplete, self.results)

    def test_missing_dds_result_cannot_shrink_both_model_cohorts(self) -> None:
        for function in (evaluate, per_task_losses):
            with self.subTest(function=function.__name__):
                with self.assertRaisesRegex(ValueError, "[Cc]ohort"):
                    function(self.tasks, self.baseline, {"A": self.results["A"]})

    def test_missing_candidate_id_cannot_win_on_easy_intersection(self) -> None:
        losses = {
            "baseline": {"contract_tricks": {"A": 1.0, "B": 0.0}},
            "candidate": {"contract_tricks": {"A": 0.0}},
        }
        with self.assertRaisesRegex(ValueError, "[Cc]ohort"):
            _select_family("contract_tricks", losses, "baseline", 0.005, 20)

    def test_pairing_rejects_extra_or_missing_ids(self) -> None:
        for candidate, baseline in (({"A": 0.0}, {}), ({"A": 0.0}, {"B": 1.0})):
            with self.subTest(candidate=candidate, baseline=baseline):
                with self.assertRaisesRegex(ValueError, "[Cc]ohort"):
                    paired_bootstrap_difference(candidate, baseline, repetitions=20)

    def test_empty_evaluation_cohort_is_not_evidence(self) -> None:
        for function in (evaluate, per_task_losses):
            with self.subTest(function=function.__name__):
                with self.assertRaisesRegex(ValueError, "[Cc]ohort"):
                    function({}, {}, {})

    def test_complete_requested_cohort_ignores_out_of_scope_rows(self) -> None:
        predictions = dict(self.baseline, OUTSIDE={"tricks": 0})
        results = dict(self.results, OUTSIDE={"dds_tricks": 13})
        metrics = evaluate(self.tasks, predictions, results)
        losses = per_task_losses(self.tasks, predictions, results)
        self.assertEqual(metrics["contract"]["total"], 2)
        self.assertEqual(metrics["contract"]["mae"], 0.5)
        self.assertEqual(metrics["missing"], 0)
        self.assertEqual(set(losses["contract_tricks"]), {"A", "B"})

    def test_identical_complete_cohort_keeps_paired_selection(self) -> None:
        losses = {
            "baseline": {"contract_tricks": {"A": 2.0, "B": 2.0}},
            "candidate": {"contract_tricks": {"A": 1.0, "B": 1.0}},
        }
        selected, details = _select_family("contract_tricks", losses, "baseline", 0.005, 20)
        self.assertEqual(selected, "candidate")
        comparison = details["paired_comparisons"]["candidate"]
        self.assertEqual(comparison["n"], 2)
        self.assertEqual(comparison["ci95"], [-1.0, -1.0])
        tied = paired_bootstrap_difference({"A": 1.0, "B": 2.0}, {"A": 1.0, "B": 2.0}, repetitions=20)
        self.assertEqual(tied["mean_candidate_minus_baseline"], 0.0)
        self.assertEqual(tied["ci95"], [0.0, 0.0])

    def test_absent_family_has_no_spurious_winner(self) -> None:
        losses = {name: {"opening_lead": {}} for name in ("baseline", "candidate")}
        selected, details = _select_family("opening_lead", losses, "baseline", 0.005, 20)
        self.assertEqual(selected, "baseline")
        self.assertEqual(details["paired_comparisons"]["candidate"]["n"], 0)

    def test_missing_selected_prediction_does_not_create_or_truncate_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new" / "selected.jsonl"
            arguments = (self.tasks, {"candidate": self.incomplete}, {"contract_tricks": "candidate"}, output)
            with self.assertRaises(ValueError):
                write_selected_predictions(*arguments)
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())
            output.parent.mkdir()
            output.write_text("existing receipt\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                write_selected_predictions(*arguments)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing receipt\n")

    def test_complete_selected_predictions_keep_all_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "selected.jsonl"
            predictions = {task_id: dict(prediction, task_id=task_id) for task_id, prediction in self.baseline.items()}
            write_selected_predictions(self.tasks, {"baseline": predictions}, {"contract_tricks": "baseline"}, output)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["task_id"] for row in rows], ["A", "B"])
            self.assertTrue(all(row["selection_model"] == "baseline" for row in rows))

    def test_illegal_lead_loss_is_in_regret_tail(self) -> None:
        tasks = {"L": {"task_type": "opening_lead"}}
        predictions = {"L": {"card": "H2"}}
        results = {"L": {"scores": {"SA": 5, "S2": 3}}}
        metrics = evaluate(tasks, predictions, results)["opening_lead"]
        self.assertEqual(per_task_losses(tasks, predictions, results)["opening_lead"]["L"], 13.0)
        self.assertEqual(metrics["mean_regret"], 13.0)
        self.assertEqual(metrics["illegal"], 1)
        self.assertEqual(metrics["equal_optimal"], 0)
        self.assertEqual(metrics["regret_2plus"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
