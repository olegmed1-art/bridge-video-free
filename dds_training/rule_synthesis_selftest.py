from __future__ import annotations

import json
import tempfile
from pathlib import Path

from learning import record_skill_check
from rule_synthesis import evaluate_rule_support, propose_rule_version
from storage import connect


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        con = connect(Path(td) / "training.sqlite3")
        skill = "defense.opening_lead"

        initial = evaluate_rule_support(con, skill)
        assert initial["recommended_status"] == "candidate"
        assert initial["blockers"]

        for index in range(3):
            record_skill_check(
                con,
                skill_key=skill,
                task_id=f"T-{index}",
                deal_id=f"D-{index}",
                evidence_type="transfer",
                success=True,
                split="derived",
                details={"transfer_eligible": True, "source": "fresh_family"},
            )
        for index in range(3):
            record_skill_check(
                con,
                skill_key=skill,
                task_id=f"R-{index}",
                deal_id=f"RD-{index}",
                evidence_type="regression",
                success=True,
                split="derived",
            )
        for index in range(2):
            record_skill_check(
                con,
                skill_key=skill,
                task_id=f"C-{index}",
                deal_id=f"CD-{index}",
                evidence_type="counterexample",
                success=True,
                split="derived",
                details={"description": "same-looking position requiring a different lead"},
            )

        support = evaluate_rule_support(con, skill)
        assert support["recommended_status"] == "confirmed", support
        proposed = propose_rule_version(
            con,
            rule_key="opening-lead-compare-candidates",
            skill_key=skill,
            rule_text="Before choosing an opening lead, compare all legal candidates and preserve equal-optimal alternatives.",
        )
        con.commit()
        assert proposed["status"] == "confirmed"
        row = con.execute(
            "SELECT version,status,rule_text FROM rule_versions WHERE rule_key=?",
            ("opening-lead-compare-candidates",),
        ).fetchone()
        assert row[0] == 1 and row[1] == "confirmed"

        print(json.dumps({
            "ok": True,
            "unsupported_rule_remains_candidate": True,
            "independent_support_required": True,
            "counterexamples_required": True,
            "confirmed_version": row[0],
            "rule_status": row[1],
        }, indent=2))


if __name__ == "__main__":
    main()
