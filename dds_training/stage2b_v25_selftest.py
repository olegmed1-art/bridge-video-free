from __future__ import annotations

import json

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


def main() -> None:
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
    assert all(row["prediction"]["accept"] is False for row in baseline_rows)

    comparison = stratified_oof_comparison(recalibrated)
    policy = comparison["family_policy"]["families"]
    assert policy["contract_suit"]["selected_for_future_validation"] == "candidate_v0.3"
    assert policy["opening_lead_suit"]["selected_for_future_validation"] == "candidate_v0.3"
    assert policy["contract_nt"]["selected_for_future_validation"] == "source_v0.2_fallback"
    assert policy["opening_lead_nt"]["selected_for_future_validation"] == "source_v0.2_fallback"
    assert comparison["source_versions"]["bridge-adaptive-v0.2"]["contract_suit"]["bootstrap_95_lower"] > 0

    diagnostics = calibration_diagnostics(recalibrated)
    assert diagnostics["schema"] == "dds-stage2b-calibration-diagnostics-v2"
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
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
