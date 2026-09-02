from __future__ import annotations

"""Deterministic post-stage review for DDS learning methodology v2.3.

The module proposes algorithm changes from measured evidence but never edits the
analyzer automatically. Every proposal must pass a regression/holdout test and
an explicit approval gate before it can become an active rule.
"""

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from v23_runtime import ALGORITHM_VERSION


@dataclass(frozen=True)
class ReviewThresholds:
    minimum_family_share: float = 0.10
    maximum_calibration_ece: float = 0.08
    minimum_independent_transfer: int = 30
    minimum_counterexamples: int = 5
    maximum_structural_investigation_fraction: float = 0.20
    maximum_family_regression: float = 0.02
    minimum_full_play_trajectories: int = 100
    maximum_shard_failure_rate: float = 0.05


def _finding(code: str, severity: str, evidence: dict, proposed_change: str, validation: str) -> dict:
    return {
        "code": code,
        "severity": severity,
        "evidence": evidence,
        "proposed_change": proposed_change,
        "required_validation": validation,
        "status": "candidate_change",
    }


def review_stage(summary: dict, thresholds: ReviewThresholds | None = None) -> dict:
    thresholds = thresholds or ReviewThresholds()
    findings: list[dict] = []

    family_counts = {str(k): int(v) for k, v in summary.get("task_family_counts", {}).items()}
    total_families = sum(family_counts.values())
    if total_families:
        shares = {name: count / total_families for name, count in family_counts.items()}
        weak = {name: share for name, share in shares.items() if share < thresholds.minimum_family_share}
        if weak:
            findings.append(
                _finding(
                    "TASK_FAMILY_IMBALANCE",
                    "high",
                    {"shares": shares, "below_threshold": weak, "threshold": thresholds.minimum_family_share},
                    "Reallocate follow-up and Stage-2 shard quotas by task family before generating more random positions.",
                    "Run a quota dry-run and prove every required family reaches the minimum share without holdout leakage.",
                )
            )

    leakage = int(summary.get("holdout_learning_leakage", 0))
    if leakage:
        findings.append(
            _finding(
                "HOLDOUT_LEAKAGE",
                "blocker",
                {"records": leakage},
                "Quarantine affected runs, trace lineage, and rebuild learning state from immutable TRAIN facts only.",
                "Database and lineage audits must return zero validation/sealed learning records.",
            )
        )

    calibration = summary.get("calibration", {})
    for family, report in calibration.items():
        ece = float(report.get("ece", 1.0))
        if ece > thresholds.maximum_calibration_ece:
            findings.append(
                _finding(
                    "CONFIDENCE_MISCALIBRATION",
                    "high",
                    {"family": family, "ece": ece, "maximum": thresholds.maximum_calibration_ece},
                    "Fit or refresh the out-of-fold calibrator and reduce confidence on unsupported regions.",
                    "Brier/ECE must improve on a fresh calibration fold without worsening the family's bridge loss.",
                )
            )

    skill_rows = summary.get("skills", [])
    for row in skill_rows:
        transfer = int(row.get("independent_transfer", 0))
        counterexamples = int(row.get("counterexamples", 0))
        status = str(row.get("status", "candidate"))
        if status in {"confirmed", "stable"} and transfer < thresholds.minimum_independent_transfer:
            findings.append(
                _finding(
                    "SKILL_PROMOTED_WITHOUT_TRANSFER",
                    "blocker",
                    {"skill": row.get("skill_key"), "status": status, "independent_transfer": transfer},
                    "Downgrade the current-version skill profile and require independent-family transfer evidence.",
                    "Promotion test must use unseen families and cross-fitting.",
                )
            )
        if status in {"confirmed", "stable"} and counterexamples < thresholds.minimum_counterexamples:
            findings.append(
                _finding(
                    "SKILL_WITHOUT_COUNTEREXAMPLES",
                    "blocker",
                    {"skill": row.get("skill_key"), "counterexamples": counterexamples},
                    "Generate minimal action-flip counterexamples and keep the skill at testing until discrimination succeeds.",
                    "At least the configured number of independent counterexamples must be solved correctly.",
                )
            )

    investigations = summary.get("investigations", {})
    total_investigations = int(investigations.get("total", 0))
    structural = int(investigations.get("structural_without_line", 0))
    structural_fraction = structural / total_investigations if total_investigations else 0.0
    if structural_fraction > thresholds.maximum_structural_investigation_fraction:
        findings.append(
            _finding(
                "TOO_MANY_LINELESS_INVESTIGATIONS",
                "high",
                {
                    "total": total_investigations,
                    "structural_without_line": structural,
                    "fraction": structural_fraction,
                    "maximum": thresholds.maximum_structural_investigation_fraction,
                },
                "Increase the share of line-bearing predictions and reject unsupported overclaims before DDS evaluation.",
                "A fresh shard must reduce the structural-investigation fraction below the threshold.",
            )
        )

    trajectories = int(summary.get("full_play_trajectories", 0))
    if summary.get("claims_full_play_skill") and trajectories < thresholds.minimum_full_play_trajectories:
        findings.append(
            _finding(
                "FULL_PLAY_CLAIM_WITHOUT_TRAJECTORIES",
                "blocker",
                {"trajectories": trajectories, "minimum": thresholds.minimum_full_play_trajectories},
                "Withdraw the full-play skill claim and add verified DDS value trajectories before promotion.",
                "The fixed partial-position regression corpus and trajectory invariants must pass.",
            )
        )

    family_regressions = summary.get("family_regressions", {})
    for family, change in family_regressions.items():
        change = float(change)
        if change > thresholds.maximum_family_regression:
            findings.append(
                _finding(
                    "MODEL_FAMILY_REGRESSION",
                    "high",
                    {"family": family, "loss_increase": change, "maximum": thresholds.maximum_family_regression},
                    "Keep the previous model for this family or use a family ensemble; do not hide regression in a combined score.",
                    "Paired bootstrap upper bound must satisfy the non-regression guard.",
                )
            )

    shards = summary.get("shards", {})
    attempted = int(shards.get("attempted", 0))
    failed = int(shards.get("failed", 0))
    failure_rate = failed / attempted if attempted else 0.0
    if failure_rate > thresholds.maximum_shard_failure_rate:
        findings.append(
            _finding(
                "SHARD_RELIABILITY_LOW",
                "high",
                {"attempted": attempted, "failed": failed, "failure_rate": failure_rate},
                "Reduce shard size, persist earlier snapshots, and investigate the dominant failure signature.",
                "A restart drill must resume from the first unfinished shard without duplicate DDS facts.",
            )
        )

    negative = summary.get("negative_controls", {})
    if negative and str(negative.get("status")) != "ok":
        findings.append(
            _finding(
                "NEGATIVE_CONTROL_SUSPICIOUS",
                "blocker",
                negative,
                "Stop model promotion and audit label/DealID/family leakage.",
                "Real-label performance must beat shuffled controls by the configured practical margin.",
            )
        )

    severity_order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda row: (severity_order.get(row["severity"], 9), row["code"]))
    blockers = [row for row in findings if row["severity"] == "blocker"]
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "review_status": "blocked" if blockers else "reviewed",
        "thresholds": asdict(thresholds),
        "findings": findings,
        "blockers": blockers,
        "automatic_code_change_allowed": False,
        "change_policy": (
            "A proposed algorithm change becomes active only after a dedicated test, paired fresh-data comparison, "
            "non-regression audit, versioned record, and explicit approval when it affects a mass stage."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Review a completed DDS stage and propose versioned algorithm changes")
    p.add_argument("--summary", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    report = review_stage(summary)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
