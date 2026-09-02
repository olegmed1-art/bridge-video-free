from __future__ import annotations

"""Create candidate/confirmed technical bridge rules from audited evidence.

Rule text is supplied by the analyst; this module never invents the school's
bidding system.  It evaluates whether the evidence supports a candidate or a
confirmed technical play/defense principle and stores a new immutable version.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from config import ALGORITHM_VERSION, SKILL_LIFECYCLE
from storage import add_rule_version, connect


def _recent_streak(rows: list[tuple[str]]) -> int:
    streak = 0
    for (outcome,) in reversed(rows):
        if outcome == "success":
            streak += 1
        else:
            break
    return streak


def evaluate_rule_support(con: sqlite3.Connection, skill_key: str) -> dict:
    independent = con.execute(
        """
        SELECT outcome,COALESCE(regret,0),evidence_type,task_id,deal_id
        FROM skill_evidence
        WHERE skill_key=? AND algorithm_version=?
          AND evidence_type IN ('transfer','real_world')
        ORDER BY id
        """,
        (skill_key, ALGORITHM_VERSION),
    ).fetchall()
    independent_success = sum(row[0] == "success" for row in independent)
    independent_rate = independent_success / len(independent) if independent else 0.0

    regression = con.execute(
        """
        SELECT outcome FROM skill_evidence
        WHERE skill_key=? AND algorithm_version=? AND evidence_type='regression'
        ORDER BY id
        """,
        (skill_key, ALGORITHM_VERSION),
    ).fetchall()
    counterexamples = con.execute(
        """
        SELECT outcome FROM skill_evidence
        WHERE skill_key=? AND algorithm_version=? AND evidence_type='counterexample'
        ORDER BY id
        """,
        (skill_key, ALGORITHM_VERSION),
    ).fetchall()
    counterexample_success = sum(row[0] == "success" for row in counterexamples)
    counterexample_fail = sum(row[0] != "success" for row in counterexamples)
    regression_streak = _recent_streak(regression)
    counterexample_streak = _recent_streak(counterexamples)

    confirmed = (
        len(independent) >= SKILL_LIFECYCLE["confirmed_transfer"]
        and independent_rate >= SKILL_LIFECYCLE["confirmed_rate"]
        and regression_streak >= SKILL_LIFECYCLE["stable_regression_passes"]
        and counterexample_streak >= SKILL_LIFECYCLE["stable_counterexamples"]
        and counterexample_fail == 0
    )
    status = "confirmed" if confirmed else "candidate"
    blockers = []
    if len(independent) < SKILL_LIFECYCLE["confirmed_transfer"]:
        blockers.append("insufficient_independent_transfer")
    if independent_rate < SKILL_LIFECYCLE["confirmed_rate"]:
        blockers.append("independent_transfer_rate_too_low")
    if regression_streak < SKILL_LIFECYCLE["stable_regression_passes"]:
        blockers.append("insufficient_clean_regression_streak")
    if counterexample_streak < SKILL_LIFECYCLE["stable_counterexamples"]:
        blockers.append("insufficient_successful_counterexamples")
    if counterexample_fail:
        blockers.append("counterexample_failure_present")

    return {
        "skill_key": skill_key,
        "algorithm_version": ALGORITHM_VERSION,
        "recommended_status": status,
        "independent_transfer": len(independent),
        "independent_success": independent_success,
        "independent_success_rate": independent_rate,
        "regression_checks": len(regression),
        "recent_regression_success_streak": regression_streak,
        "counterexample_checks": len(counterexamples),
        "counterexample_success": counterexample_success,
        "counterexample_fail": counterexample_fail,
        "recent_counterexample_success_streak": counterexample_streak,
        "blockers": blockers,
        "stable_not_granted_by_rule_alone": True,
    }


def propose_rule_version(
    con: sqlite3.Connection,
    *,
    rule_key: str,
    skill_key: str,
    rule_text: str,
    scope: str = "technical_play",
    force_candidate: bool = False,
) -> dict:
    text = rule_text.strip()
    if not text:
        raise ValueError("rule_text is required")
    support = evaluate_rule_support(con, skill_key)
    status = "candidate" if force_candidate else support["recommended_status"]
    version = add_rule_version(
        con,
        rule_key=rule_key,
        skill_key=skill_key,
        rule_text=text,
        status=status,
        evidence={
            "scope": scope,
            "support": support,
            "policy": "analyst_supplied_text_evidence_gated",
            "does_not_modify_bidding_system": True,
        },
    )
    return {
        "rule_key": rule_key,
        "skill_key": skill_key,
        "version": version,
        "status": status,
        "support": support,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an evidence-gated version of a technical bridge rule")
    parser.add_argument("--work", required=True)
    parser.add_argument("--rule-key", required=True)
    parser.add_argument("--skill-key", required=True)
    parser.add_argument("--rule-text", required=True)
    parser.add_argument("--scope", default="technical_play")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-candidate", action="store_true")
    args = parser.parse_args()
    con = connect(Path(args.work) / "training.sqlite3")
    support = evaluate_rule_support(con, args.skill_key)
    if not args.apply:
        result = {"applied": False, "support": support, "proposed_rule_text": args.rule_text}
    else:
        result = propose_rule_version(
            con,
            rule_key=args.rule_key,
            skill_key=args.skill_key,
            rule_text=args.rule_text,
            scope=args.scope,
            force_candidate=args.force_candidate,
        )
        con.commit()
        result["applied"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
