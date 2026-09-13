from __future__ import annotations

import json
import tempfile
from pathlib import Path

from stage2b_v24 import (
    CANDIDATE_ALGORITHM_VERSION,
    aggregate_review_queue,
    apply_segmented_calibration,
    balanced_curriculum,
    build_current_stage_manifest,
    fit_hierarchical_residual_model,
    fit_opening_lead_family_model,
    fit_segmented_oof_calibrator,
    investigation_resolution_status,
    predict_hierarchical_residual,
    rank_opening_leads,
    wilson_lower_bound,
)


def main() -> None:
    assert 0.0 < wilson_lower_bound(80, 100) < 0.8
    assert wilson_lower_bound(0, 0) == 0.0

    calibration_rows = []
    for index in range(240):
        family_nt = index % 2 == 0
        raw = 0.2 + 0.6 * (index % 20) / 19
        success = (index % 20) < int(raw * 20)
        calibration_rows.append(
            {
                "task_type": "contract_tricks",
                "strain": 4 if family_nt else 0,
                "prediction": {
                    "tricks": 8,
                    "confidence_probability": raw,
                    "model_backoff_level": "medium" if index % 3 else "coarse",
                },
                "result": {"dds_tricks": 8 if success else 7},
                "out_of_fold": True,
            }
        )
    calibrator = fit_segmented_oof_calibrator(calibration_rows, minimum_support=20, maximum_bins=6)
    applied = apply_segmented_calibration(calibration_rows[-1], calibrator)
    assert 0 <= applied["lower_confidence_bound"] <= applied["calibrated_probability"] <= 1
    assert applied["support_count"] > 0

    residual_samples = []
    for index in range(120):
        family = "contract_nt" if index < 60 else "contract_suit"
        baseline = 7 + (index % 3)
        target = baseline + (1 if family == "contract_suit" and index % 4 != 0 else 0)
        residual_samples.append(
            {
                "family": family,
                "baseline": baseline,
                "target": target,
                "levels": [
                    ("family", family),
                    ("coarse", f"{family}:{baseline}"),
                    ("exact", f"{family}:{baseline}:{index % 5}"),
                ],
            }
        )
    residual_model = fit_hierarchical_residual_model(residual_samples, minimum_support=5, minimum_gain=0.0)
    prediction = predict_hierarchical_residual(
        baseline=8,
        family="contract_suit",
        levels=[("family", "contract_suit"), ("coarse", "contract_suit:8"), ("exact", "contract_suit:8:1")],
        model=residual_model,
    )
    assert 0 <= prediction["prediction"] <= 13
    assert prediction["support_count"] >= 0

    lead_samples = []
    for index in range(200):
        family = "opening_lead_nt" if index < 100 else "opening_lead_suit"
        card_type = "sequence" if index % 2 == 0 else "unsupported"
        regret = 0.0 if card_type == "sequence" else (2.0 if index % 5 == 1 else 1.0)
        lead_samples.append(
            {
                "family": family,
                "regret": regret,
                "levels": [("family", family), ("card_type", f"{family}:{card_type}")],
            }
        )
    lead_model = fit_opening_lead_family_model(lead_samples, minimum_support=10)
    ranked = rank_opening_leads(
        [
            {"card": "SA", "heuristic": 1.0, "levels": [("family", "opening_lead_nt"), ("card_type", "opening_lead_nt:unsupported")]},
            {"card": "SK", "heuristic": 0.8, "levels": [("family", "opening_lead_nt"), ("card_type", "opening_lead_nt:sequence")]},
        ],
        family="opening_lead_nt",
        model=lead_model,
    )
    assert ranked["card"] == "SK", ranked
    assert len(ranked["alternatives"]) == 2
    assert ranked["alternatives"][0]["risk_2plus"] <= ranked["alternatives"][1]["risk_2plus"]

    curriculum = balanced_curriculum(
        [
            {"task_id": f"D-{i}", "actor": "declarer", "priority": i}
            for i in range(20)
        ]
        + [
            {"task_id": f"F-{i}", "actor": "defense", "priority": i}
            for i in range(20)
        ],
        total=20,
    )
    assert sum(row["actor"] == "declarer" for row in curriculum) == 10
    assert sum(row["actor"] == "defense" for row in curriculum) == 10

    aggregated = aggregate_review_queue(
        [
            {
                "skill_key": "defense.opening_lead",
                "error_code": "REGRET",
                "strain": "NT",
                "mechanism": "unsupported_honor",
                "due_window": "1000",
                "task_id": f"T-{i}",
                "family_id": f"F-{i % 7}",
                "severity": i % 4,
                "requested_tasks": 2,
            }
            for i in range(100)
        ],
        max_tasks_per_group=50,
    )
    assert len(aggregated) == 1
    assert aggregated[0]["requested_tasks"] == 50
    assert aggregated[0]["distinct_families"] == 7

    assert investigation_resolution_status({"line": []})["resolution_status"] == "resolved_structurally"
    card_level = investigation_resolution_status(
        {"line": ["SA", "S2"]},
        trajectory={"first_error": {"decision_index": 1}, "decision_errors": []},
    )
    assert card_level["resolution_status"] == "resolved_at_card_level"
    assert card_level["promotion_eligible"] is True

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "training.sqlite3"
        audit = root / "audit.json"
        db.write_bytes(b"sqlite-fixture")
        audit.write_text("{\"status\":\"ok\"}\n", encoding="utf-8")
        manifest = build_current_stage_manifest(
            current_stage="stage2b_preparation",
            current_algorithm=CANDIDATE_ALGORITHM_VERSION,
            canonical_files={"database": db, "audit": audit},
            holdout_status="closed",
            sealed_status="closed",
            next_gate="blind_continuation_and_counterexample_train",
            metadata={"dds_results": 50497},
        )
        assert manifest["canonical_files"]["database"]["sha256"]
        assert manifest["metadata"]["dds_results"] == 50497

    print(
        json.dumps(
            {
                "ok": True,
                "candidate_algorithm": CANDIDATE_ALGORITHM_VERSION,
                "segmented_oof_calibration": True,
                "wilson_lower_bound": True,
                "hierarchical_shrinkage": True,
                "separate_lead_family_model": True,
                "balanced_continuations": True,
                "bounded_queue_aggregation": True,
                "card_level_resolution_status": True,
                "current_stage_manifest": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
