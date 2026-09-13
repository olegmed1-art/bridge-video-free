from __future__ import annotations

import json

from algorithm_review_v23 import review_stage


def main() -> None:
    problematic = {
        "task_family_counts": {
            "contract_tricks": 900,
            "opening_lead": 900,
            "defense_continuation": 40,
            "declarer_continuation": 20,
        },
        "holdout_learning_leakage": 0,
        "calibration": {
            "contract_tricks": {"ece": 0.12},
            "opening_lead": {"ece": 0.04},
        },
        "skills": [
            {
                "skill_key": "defense.switch",
                "status": "confirmed",
                "independent_transfer": 8,
                "counterexamples": 0,
            }
        ],
        "investigations": {"total": 100, "structural_without_line": 45},
        "claims_full_play_skill": True,
        "full_play_trajectories": 12,
        "family_regressions": {"opening_lead": 0.03, "contract_tricks": -0.10},
        "shards": {"attempted": 20, "failed": 2},
        "negative_controls": {"status": "suspicious", "gap": 0.01},
    }
    report = review_stage(problematic)
    codes = {row["code"] for row in report["findings"]}
    assert report["review_status"] == "blocked"
    assert {
        "TASK_FAMILY_IMBALANCE",
        "CONFIDENCE_MISCALIBRATION",
        "SKILL_PROMOTED_WITHOUT_TRANSFER",
        "SKILL_WITHOUT_COUNTEREXAMPLES",
        "TOO_MANY_LINELESS_INVESTIGATIONS",
        "FULL_PLAY_CLAIM_WITHOUT_TRAJECTORIES",
        "MODEL_FAMILY_REGRESSION",
        "SHARD_RELIABILITY_LOW",
        "NEGATIVE_CONTROL_SUSPICIOUS",
    }.issubset(codes), codes
    assert report["automatic_code_change_allowed"] is False

    healthy = {
        "task_family_counts": {
            "contract_tricks": 250,
            "opening_lead": 250,
            "defense_continuation": 250,
            "declarer_continuation": 250,
        },
        "holdout_learning_leakage": 0,
        "calibration": {
            "contract_tricks": {"ece": 0.04},
            "opening_lead": {"ece": 0.05},
        },
        "skills": [
            {
                "skill_key": "defense.switch",
                "status": "testing",
                "independent_transfer": 20,
                "counterexamples": 3,
            }
        ],
        "investigations": {"total": 100, "structural_without_line": 10},
        "claims_full_play_skill": False,
        "full_play_trajectories": 0,
        "family_regressions": {"opening_lead": 0.0, "contract_tricks": -0.10},
        "shards": {"attempted": 20, "failed": 0},
        "negative_controls": {"status": "ok", "gap": 0.20},
    }
    clean = review_stage(healthy)
    assert clean["review_status"] == "reviewed"
    assert clean["blockers"] == []

    print(
        json.dumps(
            {
                "ok": True,
                "problematic_finding_count": len(report["findings"]),
                "problematic_blockers": [row["code"] for row in report["blockers"]],
                "healthy_review_status": clean["review_status"],
                "automatic_code_change_allowed": clean["automatic_code_change_allowed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
