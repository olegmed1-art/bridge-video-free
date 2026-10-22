from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import product
from unittest.mock import patch

from stage2b_prepare import compare_oof
from stage2b_v24 import row_loss, row_success

from stage2b_v25 import (
    CANDIDATE_ALGORITHM_VERSION,
    calibration_diagnostics,
    enrich_review_rows,
    exact_balanced_curriculum,
    paired_bootstrap_interval,
    recalibrate_oof_rows,
    rewrite_oof_raw_probabilities,
    stratified_oof_comparison,
    support_aware_contract_probability,
)


def make_row(
    *,
    index: int,
    task_type: str,
    strain: int,
    source_version: str,
    old_loss: int,
    new_loss: int,
    backoff: str,
    support: int,
) -> dict:
    if task_type == "contract_tricks":
        target = 8
        source_prediction = {
            "tricks": target + old_loss,
            "predictor_version": source_version,
        }
        prediction = {
            "tricks": target + new_loss,
            "predictor_version": "bridge-adaptive-v0.3-oof",
            "model_backoff_level": backoff,
            "model_evidence_count": support,
            "residual_variance": 0.8 if support else 0.0,
            "correction": -1 if new_loss < old_loss else 0,
            "confidence_probability": 0.9,
        }
        result = {"dds_tricks": target}
    else:
        scores = {"SA": 5, "SK": 5 - new_loss, "S2": 5 - old_loss}
        source_prediction = {
            "card": "S2",
            "predictor_version": source_version,
        }
        prediction = {
            "card": "SK",
            "predictor_version": "bridge-opening-lead-v0.3-oof",
            "model_backoff_level": backoff,
            "model_evidence_count": support,
            "raw_confidence_probability": 0.75,
        }
        result = {"scores": scores, "best_defense_tricks": 5, "dd_regret": float(old_loss)}
    return {
        "task_id": f"R-{task_type}-{index}",
        "task_type": task_type,
        "strain": strain,
        "family_id": f"F-{index}",
        "heldout_fold": index % 5,
        "split": "train",
        "source_prediction": source_prediction,
        "prediction": prediction,
        "result": result,
        "out_of_fold": True,
    }


def check_prediction_bound_lead_evidence() -> None:
    # The immutable result.dd_regret belongs to the source prediction. A new
    # candidate must be labelled from its own card, including equal optima.
    for old_loss, new_loss in ((2, 0), (0, 2)):
        rows = [
            make_row(
                index=2000 + index, task_type="opening_lead", strain=0,
                source_version="bridge-adaptive-v0.2", old_loss=old_loss,
                new_loss=new_loss, backoff="exact", support=250,
            )
            for index in range(120)
        ]
        before = deepcopy(rows)
        calibrated, calibrator = recalibrate_oof_rows(rows, minimum_support=5)
        assert calibrator["opening_lead_outcome_policy"] == "predicted-card-dds-score-v1"
        expected_success = new_loss == 0
        assert all(row_success(row) == expected_success for row in rows)
        assert all(row_loss(row) == new_loss for row in rows)
        assert all(row["prediction"]["calibrated_probability"] == int(expected_success) for row in calibrated)
        assert all(row["prediction"]["accept"] == expected_success for row in calibrated)
        metrics = stratified_oof_comparison(calibrated)["source_versions"]["bridge-adaptive-v0.2"]["opening_lead_suit"]
        assert metrics["candidate_zero_loss_rate"] == int(expected_success)
        assert compare_oof(rows)["families"]["opening_lead"]["candidate_mean_loss"] == new_loss
        diagnostic = calibration_diagnostics(calibrated)["groups"]["opening_lead_suit:exact"]
        assert diagnostic["accepted"] == (120 if expected_success else 0)
        assert diagnostic["accepted_actual_success_rate"] == (1.0 if expected_success else None)
        assert rows == before, "Calibration rewrote immutable source evidence"

    tied = deepcopy(rows[0])
    tied["prediction"]["card"] = "sk"
    tied["result"] = {"scores": {"sa": 5, "SK": 5, "S2": 3}, "best_defense_tricks": 5, "dd_regret": 2}
    assert row_success(tied) is True
    assert row_loss(tied) == 0

    malformed_results = [
        {"dd_regret": 0},
        {"scores": {}, "dd_regret": 0},
        {"scores": {"SA": 5, "S2": 3}, "dd_regret": 0},
        {"scores": {"SA": 5, "SK": 3, "sk": 4}, "dd_regret": 0},
        {"scores": {"SA": 5, "SK": None}, "dd_regret": 0},
        {"scores": {"SK": 3}, "best_defense_tricks": 5, "dd_regret": 2},
        {"scores": {"SA": 5, "SK": 3}, "best_defense_tricks": 4, "dd_regret": 0},
    ]
    for best in (None, "invalid", float("nan"), float("inf"), -1, 14):
        malformed_results.append({"scores": {"SA": 5, "SK": 3}, "best_defense_tricks": best})
    for score in (float("nan"), float("inf"), -1, 14):
        for card in ("SA", "SK"):
            result = {"scores": {"SA": 5, "SK": 3, "S2": 2}, "dd_regret": 0}
            result["scores"][card] = score
            malformed_results.append(result)
    for result in malformed_results:
        invalid = deepcopy(tied)
        invalid["result"] = result
        before = deepcopy(invalid)
        for check in (
            lambda: row_success(invalid),
            lambda: row_loss(invalid),
            lambda: recalibrate_oof_rows([invalid]),
            lambda: calibration_diagnostics([invalid]),
            lambda: compare_oof([invalid]),
            lambda: stratified_oof_comparison([invalid]),
        ):
            try:
                check()
            except ValueError:
                pass
            else:
                raise AssertionError("Incomplete/invalid lead evidence did not fail closed")
            assert invalid == before, "Evidence rejection rewrote immutable source data"

    # Independently derive loss by searching the smallest compensation that
    # dominates every alternative, rather than reusing max(scores)-chosen.
    # All 64 three-card tables in 0..3, each chosen card, both stale source
    # labels: 384 cases, no bridge solver or corpus access.
    for values in product(range(4), repeat=3):
        scores = dict(zip(("SA", "SK", "S2"), values))
        for chosen in scores:
            expected_loss = next(
                compensation for compensation in range(4)
                if all(scores[chosen] + compensation >= score for score in values)
            )
            expected_success = all(scores[chosen] >= score for score in values)
            for source_regret in (0, 2):
                row = deepcopy(tied)
                row["prediction"]["card"] = chosen
                row["result"] = {"scores": scores, "dd_regret": source_regret}
                assert row_success(row) == expected_success
                assert row_loss(row) == expected_loss
                assert compare_oof([row])["families"]["opening_lead"]["candidate_mean_loss"] == expected_loss
                metrics = stratified_oof_comparison([row])["source_versions"]["bridge-adaptive-v0.2"]["opening_lead_suit"]
                assert metrics["candidate_mean_loss"] == expected_loss
                calibrated, _ = recalibrate_oof_rows([row], minimum_support=1)
                assert calibrated[0]["prediction"]["calibrated_probability"] == int(expected_success)

    for task_type in ("declarer_continuation", "defense_continuation"):
        continuation = {"task_type": task_type, "prediction": {"card": "SA"}, "result": {"dd_regret": 1}}
        assert row_loss(continuation) == 1
        assert row_success(continuation) is False


def check_oof_input_admission() -> None:
    base = make_row(index=50, task_type="contract_tricks", strain=0,
                    source_version="baseline", old_loss=1, new_loss=0,
                    backoff="exact", support=250)
    base["fold"] = base["heldout_fold"]
    base["prediction"]["task_id"] = base["task_id"]
    base["prediction"]["out_of_fold"] = True
    base["result"].update({"task_id": base["task_id"], "split": "train",
                           "family_id": base["family_id"], "fold": base["heldout_fold"]})
    base["task"] = {
        "task_id": base["task_id"], "task_type": base["task_type"],
        "split": "train", "root_deal_id": base["family_id"],
        "crossfit_fold": base["heldout_fold"],
    }
    original = deepcopy(base)
    output, calibrator = recalibrate_oof_rows([base], minimum_support=1)
    assert calibrator["oof_input_policy"] == "explicit-train-oof-family-fold-v1"
    assert base == original
    assert output[0]["out_of_fold"] is True and output[0]["split"] == "train"
    assert output[0]["family_id"] == base["family_id"]

    invalid_batches = [[], [None]]
    for key in ("out_of_fold", "split", "task_id", "family_id", "heldout_fold"):
        row = deepcopy(base)
        del row[key]
        invalid_batches.append([row])
    for key, values in {
        "out_of_fold": (False, None, 0, 1, "false", "true"),
        "split": (None, "validation", "sealed_test", "TRAIN"),
        "task_id": (None, "", " ", " padded ", 1),
        "family_id": (None, "", " ", " padded ", 1),
        "heldout_fold": (None, -1, True, 0.0, "0"),
        "fold": (1, True, "0"),
        "prediction": (None, []),
        "result": (None, []),
        "task": (None, []),
    }.items():
        for value in values:
            row = deepcopy(base)
            row[key] = value
            invalid_batches.append([row])
    for container, key, value in (
        ("prediction", "out_of_fold", False),
        ("prediction", "out_of_fold", "true"),
        ("prediction", "task_id", "other"),
        ("prediction", "split", "validation"),
        ("prediction", "family_id", "other"),
        ("prediction", "fold", 1),
        ("prediction", "heldout_fold", False),
        ("prediction", "task_type", "opening_lead"),
        ("result", "split", "sealed_test"),
        ("result", "split", "validation"),
        ("result", "task_id", "other"),
        ("result", "family_id", "other"),
        ("result", "fold", 1),
        ("result", "heldout_fold", False),
        ("result", "task_type", "opening_lead"),
        ("task", "task_id", "other"),
        ("task", "task_type", "opening_lead"),
        ("task", "split", "sealed_test"),
        ("task", "crossfit_fold", 1),
        ("task", "crossfit_fold", False),
        ("task", "root_deal_id", "other"),
    ):
        row = deepcopy(base)
        row[container][key] = value
        invalid_batches.append([row])
    invalid_batches.append([base, deepcopy(base)])
    leaked = make_row(index=51, task_type="opening_lead", strain=0,
                      source_version="baseline", old_loss=1, new_loss=0,
                      backoff="exact", support=250)
    leaked["family_id"] = base["family_id"]
    invalid_batches.append([base, leaked])
    for rows in invalid_batches:
        before = deepcopy(rows)
        # Invalid provenance must stop before any calibration fit, including
        # a mixed batch where an otherwise-valid row precedes bad evidence.
        for batch in (rows, [deepcopy(base), *rows] if rows else rows):
            with patch("stage2b_v25.fit_segmented_oof_calibrator",
                       side_effect=AssertionError("Fitted unadmitted evidence")):
                for check in (rewrite_oof_raw_probabilities, recalibrate_oof_rows):
                    try:
                        check(batch)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Unadmitted OOF input did not fail closed")
        assert rows == before

    # Multiple task types for one family in the SAME fold are legitimate.
    sibling = deepcopy(leaked)
    sibling["heldout_fold"] = base["heldout_fold"]
    recalibrate_oof_rows([base, sibling], minimum_support=1)


def calibration_partition_rows() -> list[dict]:
    # Same raw probability, opposite outcomes in the two family partitions.
    # Each family has two tasks so task exclusion alone is insufficient.
    rows = []
    for index in range(8):
        row = make_row(
            index=index, task_type="opening_lead", strain=0,
            source_version="bridge-adaptive-v0.2", old_loss=1,
            new_loss=0 if index < 4 else 1, backoff="exact", support=250,
        )
        row["heldout_fold"] = index // 4
        row["family_id"] = f"CF-{index // 2}"
        rows.append(row)
    return rows


def check_fit_sample_diagnostics_label() -> None:
    calibrated, _ = recalibrate_oof_rows(calibration_partition_rows(), minimum_support=1)
    report = calibration_diagnostics(calibrated)
    assert report["evaluation_sample"] == "fit_sample"
    assert report["sampling"] == "in_sample"
    assert report["calibrator_fit_family_disjoint"] is False
    assert report["independent_evaluation"] is False
    assert report["full_pipeline_quality_evidence"] is False
    assert report["promotion"] is False


def check_crossfit_calibration_independence() -> None:
    from stage2b_v25 import crossfit_calibration_diagnostics

    rows = calibration_partition_rows()
    before = deepcopy(rows)
    final_rows, final_model = recalibrate_oof_rows(rows, minimum_support=1)
    final_before = deepcopy((final_rows, final_model))
    report = crossfit_calibration_diagnostics(rows, minimum_support=1)
    assert report["schema"] == "dds-stage2b-crossfit-calibration-diagnostics-v1"
    assert report["scope"] == "calibrator_fit_only"
    assert report["conditional_on_supplied_base_predictions"] is True
    assert report["calibrator_fit_family_disjoint"] is True
    assert report["upstream_model_training_lineage_verified"] is False
    assert report["full_pipeline_quality"] is False
    assert report["full_pipeline_quality_evidence"] is False
    assert report["promotion"] is False
    assert report["evaluation_sample"] == "heldout_train_families"
    assert report["rows"] == 8 and report["families"] == 4
    assert report["groups"]["opening_lead_suit:exact"]["calibrated_brier"] == 1.0
    assert report["groups"]["opening_lead_suit:exact"]["calibrated_ece"] == 1.0
    assert len(report["folds"]) == 2
    for fold_report in report["folds"]:
        fold = fold_report["heldout_fold"]
        assert fold_report["fit_folds"] == [1 - fold]
        assert fold_report["fit_rows"] == fold_report["heldout_rows"] == 4
        assert fold_report["fit_families"] == fold_report["heldout_families"] == 2
        assert fold_report["family_overlap"] == fold_report["task_overlap"] == 0
        metrics = fold_report["groups"]["opening_lead_suit:exact"]
        # An independent analytic oracle: fitting homogeneous outcomes gives
        # the opposite fold's constant label, hence every held-out error is 1.
        assert metrics["mean_calibrated_probability"] == fold
        assert metrics["calibrated_brier"] == 1.0
        for sample, selected in (
            ("fit", [row for row in rows if row["heldout_fold"] != fold]),
            ("heldout", [row for row in rows if row["heldout_fold"] == fold]),
        ):
            for kind in ("task", "family"):
                ids = sorted({row[f"{kind}_id"] for row in selected})
                expected_hash = hashlib.sha256(
                    json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                assert fold_report[f"{sample}_{kind}_ids_sha256"] == expected_hash
        assert len(fold_report["calibrator_sha256"]) == 64
    assert report == crossfit_calibration_diagnostics(list(reversed(rows)), minimum_support=1)
    assert report == crossfit_calibration_diagnostics(final_rows, minimum_support=1)
    assert rows == before and (final_rows, final_model) == final_before
    assert recalibrate_oof_rows(rows, minimum_support=1) == final_before

    # Inspect the actual fit/apply calls, not only the report's assertions.
    from stage2b_v24 import apply_segmented_calibration, fit_segmented_oof_calibrator
    current_fit = []
    fit_calls = []
    evaluated = []

    def observed_fit(fit_rows, **kwargs):
        current_fit[:] = fit_rows
        fit_calls.append({row["heldout_fold"] for row in fit_rows})
        return fit_segmented_oof_calibrator(fit_rows, **kwargs)

    def observed_apply(row, calibrator):
        assert row["task_id"] not in {item["task_id"] for item in current_fit}
        assert row["family_id"] not in {item["family_id"] for item in current_fit}
        evaluated.append(row["task_id"])
        return apply_segmented_calibration(row, calibrator)

    with patch("stage2b_v25.fit_segmented_oof_calibrator", side_effect=observed_fit), \
         patch("stage2b_v25.apply_segmented_calibration", side_effect=observed_apply):
        assert crossfit_calibration_diagnostics(rows, minimum_support=1) == report
    assert fit_calls == [{1}, {0}]
    assert sorted(evaluated) == sorted(row["task_id"] for row in rows)


def check_crossfit_calibration_rejections() -> None:
    from stage2b_v25 import crossfit_calibration_diagnostics

    rows = calibration_partition_rows()
    invalid_batches = [[], rows[:4], [*rows, deepcopy(rows[0])]]
    for key, value in (("split", "sealed_test"), ("out_of_fold", False),
                       ("heldout_fold", True), ("family_id", rows[0]["family_id"])):
        invalid = deepcopy(rows)
        invalid[-1][key] = value
        invalid_batches.append(invalid)
    missing = deepcopy(rows)
    missing[-1].pop("heldout_fold")
    invalid_batches.append(missing)
    for invalid in invalid_batches:
        before = deepcopy(invalid)
        with patch("stage2b_v25.fit_segmented_oof_calibrator",
                   side_effect=AssertionError("Fit ran before partition admission")):
            try:
                crossfit_calibration_diagnostics(invalid, minimum_support=1)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid crossfit partition admitted")
        assert invalid == before


def check_probability_numeric_admission() -> None:
    from stage2b_v25 import crossfit_calibration_diagnostics

    failures = []
    for key in ("raw_confidence_probability", "confidence_probability"):
        for value in (None, float("nan"), float("inf"), float("-inf"),
                      -1.0, 2.0, True, False, "0.5", "", [], {}):
            rows = calibration_partition_rows()
            # Check an invalid fallback field even when a valid raw field exists.
            rows[-1]["prediction"][key] = value
            before = json.dumps(rows, sort_keys=True)
            for entry in (rewrite_oof_raw_probabilities, recalibrate_oof_rows,
                          crossfit_calibration_diagnostics):
                with patch("stage2b_v25.fit_segmented_oof_calibrator",
                           side_effect=AssertionError("Fit received invalid numeric evidence")):
                    try:
                        entry(rows)
                    except ValueError:
                        pass
                    except Exception as exc:
                        failures.append((entry.__name__, key, repr(value), type(exc).__name__))
                    else:
                        failures.append((entry.__name__, key, repr(value), "admitted"))
                assert json.dumps(rows, sort_keys=True) == before
    assert not failures, f"Invalid probabilities not rejected before fit: {failures}"


def check_contract_numeric_admission() -> None:
    from stage2b_v25 import crossfit_calibration_diagnostics

    failures = []
    base = make_row(index=20, task_type="contract_tricks", strain=0,
                    source_version="baseline", old_loss=1, new_loss=0,
                    backoff="exact", support=250)
    for key in ("model_evidence_count", "residual_variance", "correction"):
        invalid = [None, float("nan"), float("inf"), float("-inf"), True, "1", "", [], {}]
        if key != "correction":
            invalid.append(-1)
        if key == "model_evidence_count":
            invalid.append(1.5)
        for value in invalid:
            row = deepcopy(base)
            row["prediction"][key] = value
            rows = [row, calibration_partition_rows()[-1]]
            before = json.dumps(rows, sort_keys=True)
            for entry in (rewrite_oof_raw_probabilities, recalibrate_oof_rows,
                          crossfit_calibration_diagnostics):
                with patch("stage2b_v25.fit_segmented_oof_calibrator",
                           side_effect=AssertionError("Fit received invalid contract evidence")):
                    try:
                        entry(rows)
                    except ValueError:
                        pass
                    except Exception as exc:
                        failures.append((entry.__name__, key, repr(value), type(exc).__name__))
                    else:
                        failures.append((entry.__name__, key, repr(value), "admitted"))
            # The public support-aware helper must reject malformed evidence
            # even for a zero-support/baseline fallback that ignores its value.
            row["prediction"]["model_backoff_level"] = "baseline"
            try:
                support_aware_contract_probability(row["prediction"])
            except ValueError:
                pass
            except Exception as exc:
                failures.append(("support_aware_contract_probability", key, repr(value), type(exc).__name__))
            else:
                failures.append(("support_aware_contract_probability", key, repr(value), "admitted"))
            row["prediction"]["model_backoff_level"] = base["prediction"]["model_backoff_level"]
            assert json.dumps(rows, sort_keys=True) == before
    assert not failures, f"Invalid contract evidence not rejected before fit: {failures}"


def check_missing_probability_policy() -> None:
    row = calibration_partition_rows()[0]
    row["prediction"].pop("raw_confidence_probability")
    row["prediction"].pop("confidence_probability", None)
    before = deepcopy(row)
    assert rewrite_oof_raw_probabilities([row])[0]["prediction"]["raw_confidence_probability"] == 0.25
    assert row == before
    row["prediction"]["confidence_probability"] = 0.6
    assert rewrite_oof_raw_probabilities([row])[0]["prediction"]["raw_confidence_probability"] == 0.6
    for provided, expected in ((0.0, 0.02), (1.0, 0.98)):
        row["prediction"]["raw_confidence_probability"] = provided
        assert rewrite_oof_raw_probabilities([row])[0]["prediction"]["raw_confidence_probability"] == expected
    row["prediction"].pop("model_evidence_count")
    assert rewrite_oof_raw_probabilities([row])[0]["prediction"]["raw_confidence_probability"] == 0.25
    assert support_aware_contract_probability({}) == 0.18
    assert support_aware_contract_probability({"model_evidence_count": 0}) == 0.18
    positive = {"model_backoff_level": "exact", "model_evidence_count": 1.0,
                "residual_variance": 0.0, "correction": 2.0}
    negative = {**positive, "correction": -2.0}
    assert support_aware_contract_probability(positive) == support_aware_contract_probability(negative)


def main() -> None:
    check_probability_numeric_admission()
    check_contract_numeric_admission()
    check_missing_probability_policy()
    check_fit_sample_diagnostics_label()
    check_crossfit_calibration_independence()
    check_crossfit_calibration_rejections()
    check_oof_input_admission()
    check_prediction_bound_lead_evidence()
    unsupported = {
        "model_backoff_level": "baseline",
        "model_evidence_count": 0,
        "residual_variance": 0.0,
        "correction": 0.0,
    }
    assert support_aware_contract_probability(unsupported) == 0.18
    supported = {
        "model_backoff_level": "exact",
        "model_evidence_count": 300,
        "residual_variance": 0.2,
        "correction": 0.5,
    }
    assert support_aware_contract_probability(supported) > 0.18

    rows = []
    for index in range(500):
        # Strong improvement in suit families, no real improvement in NT.
        rows.append(
            make_row(
                index=index,
                task_type="contract_tricks",
                strain=0 if index < 400 else 4,
                source_version="bridge-adaptive-v0.2",
                old_loss=1,
                new_loss=0 if index < 400 else 1,
                backoff="exact" if index % 3 else "baseline",
                support=200 if index % 3 else 0,
            )
        )
        rows.append(
            make_row(
                index=1000 + index,
                task_type="opening_lead",
                strain=1 if index < 400 else 4,
                source_version="bridge-adaptive-v0.2",
                old_loss=1,
                new_loss=0 if index < 400 else 1,
                backoff="exact",
                support=250,
            )
        )
    recalibrated, calibrator = recalibrate_oof_rows(rows, minimum_support=20)
    assert calibrator["raw_probability_policy"] == "support-aware-v2"
    baseline_rows = [
        row
        for row in recalibrated
        if row["task_type"] == "contract_tricks"
        and row["prediction"]["model_backoff_level"] == "baseline"
    ]
    assert baseline_rows
    assert max(row["prediction"]["raw_probability"] for row in baseline_rows) <= 0.18 + 1e-12

    comparison = stratified_oof_comparison(recalibrated)
    policy = comparison["family_policy"]["families"]
    assert policy["contract_suit"]["selected_for_future_validation"] == "candidate_v0.3"
    assert policy["opening_lead_suit"]["selected_for_future_validation"] == "candidate_v0.3"
    assert policy["contract_nt"]["selected_for_future_validation"] == "source_v0.2_fallback"
    assert policy["opening_lead_nt"]["selected_for_future_validation"] == "source_v0.2_fallback"
    assert comparison["source_versions"]["bridge-adaptive-v0.2"]["contract_suit"]["bootstrap_95_lower"] > 0

    diagnostics = calibration_diagnostics(recalibrated)
    assert diagnostics["schema"] == "dds-stage2b-calibration-diagnostics-v2"
    assert diagnostics["opening_lead_outcome_policy"] == "predicted-card-dds-score-v1"
    assert any(key.startswith("contract_suit:baseline") for key in diagnostics["groups"])

    interval = paired_bootstrap_interval([1.0] * 100, samples=200)
    assert interval == (1.0, 1.0)

    curriculum = exact_balanced_curriculum(
        [
            {"task_id": f"D-{i}", "position_id": f"DP-{i}", "actor": "declarer", "priority": i}
            for i in range(20)
        ]
        + [
            {"task_id": f"F-{i}", "position_id": f"FP-{i}", "actor": "defense", "priority": i}
            for i in range(20)
        ],
        per_actor=10,
    )
    assert sum(row["actor"] == "declarer" for row in curriculum) == 10
    assert sum(row["actor"] == "defense" for row in curriculum) == 10
    try:
        exact_balanced_curriculum(
            [{"task_id": "ONLY-D", "actor": "declarer", "position_id": "P"}],
            per_actor=1,
        )
    except ValueError as exc:
        assert "Insufficient defense" in str(exc)
    else:
        raise AssertionError("Missing defense side did not fail closed")

    enriched = enrich_review_rows(
        [{"task_id": "T1", "strain": "unknown", "deal_id": "D1"}],
        {"T1": {"strain": 4, "root_deal_id": "ROOT1"}},
    )
    assert enriched[0]["strain"] == "NT"
    assert enriched[0]["family_id"] == "ROOT1"

    print(
        json.dumps(
            {
                "ok": True,
                "candidate_algorithm": CANDIDATE_ALGORITHM_VERSION,
                "unsupported_probability_fixed": True,
                "stratified_source_version_comparison": True,
                "suit_candidate_nt_fallback_policy": True,
                "exact_50_50_curriculum": True,
                "queue_strain_enrichment": True,
                "candidate_card_calibration": True,
                "invalid_lead_score_evidence_rejected": True,
                "recorded_optimum_consistency_checked": True,
                "explicit_train_oof_admission_checked": True,
                "exhaustive_independent_score_oracle_cases": 384,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
