from __future__ import annotations

import json
from copy import deepcopy
from itertools import product

from stage2b_prepare import compare_oof
from stage2b_v24 import row_loss, row_success

from stage2b_v25 import (
    CANDIDATE_ALGORITHM_VERSION,
    calibration_diagnostics,
    enrich_review_rows,
    exact_balanced_curriculum,
    paired_bootstrap_interval,
    recalibrate_oof_rows,
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
        result = {"scores": scores, "dd_regret": float(old_loss)}
    return {
        "task_id": f"R-{task_type}-{index}",
        "task_type": task_type,
        "strain": strain,
        "family_id": f"F-{index}",
        "heldout_fold": index % 5,
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
    tied["result"] = {"scores": {"sa": 5, "SK": 5, "S2": 3}, "dd_regret": 2}
    assert row_success(tied) is True
    assert row_loss(tied) == 0

    malformed_results = [
        {"dd_regret": 0},
        {"scores": {}, "dd_regret": 0},
        {"scores": {"SA": 5, "S2": 3}, "dd_regret": 0},
        {"scores": {"SA": 5, "SK": 3, "sk": 4}, "dd_regret": 0},
        {"scores": {"SA": 5, "SK": None}, "dd_regret": 0},
    ]
    for score in (float("nan"), float("inf"), -1, 14):
        for card in ("SA", "SK"):
            result = {"scores": {"SA": 5, "SK": 3, "S2": 2}, "dd_regret": 0}
            result["scores"][card] = score
            malformed_results.append(result)
    for result in malformed_results:
        invalid = deepcopy(tied)
        invalid["result"] = result
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


def main() -> None:
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
                "exhaustive_independent_score_oracle_cases": 384,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
