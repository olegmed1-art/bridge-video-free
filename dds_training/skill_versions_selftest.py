from __future__ import annotations

import json
import tempfile
from pathlib import Path

from config import ALGORITHM_VERSION
from learning import record_skill_check
from storage import connect


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        con = connect(Path(td) / "training.sqlite3")
        skill_key = "selftest.versioned_skill"
        con.execute(
            """
            INSERT INTO skill_profiles
              (skill_key,side,family,title,status,trigger_text,rule_text,
               algorithm_version,evidence_count,transfer_count,regression_passes,
               regression_failures,counterexample_count)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                skill_key,
                "declarer",
                "calculation",
                "Legacy versioned skill",
                "confirmed",
                "legacy trigger",
                "legacy rule",
                "dds-learning-v2.1",
                25,
                8,
                2,
                1,
                1,
            ),
        )
        con.commit()

        status = record_skill_check(
            con,
            skill_key=skill_key,
            task_id="V22-TRANSFER-1",
            deal_id="V22-DEAL-1",
            evidence_type="transfer",
            success=True,
            split="derived",
            details={"source": "fresh_corpus"},
        )
        con.commit()

        legacy = con.execute(
            """
            SELECT status,evidence_count,transfer_count,regression_passes,
                   regression_failures,counterexample_count
            FROM skill_profile_versions
            WHERE skill_key=? AND algorithm_version='dds-learning-v2.1'
            """,
            (skill_key,),
        ).fetchone()
        current = con.execute(
            """
            SELECT status,evidence_count,transfer_count,reinforcement_count
            FROM skill_profile_versions
            WHERE skill_key=? AND algorithm_version=?
            """,
            (skill_key, ALGORITHM_VERSION),
        ).fetchone()
        compatibility = con.execute(
            "SELECT algorithm_version,status,evidence_count,transfer_count FROM skill_profiles WHERE skill_key=?",
            (skill_key,),
        ).fetchone()

        assert legacy == ("confirmed", 25, 8, 2, 1, 1), legacy
        assert current is not None and current[1:] == (1, 1, 0), current
        assert compatibility[0] == ALGORITHM_VERSION, compatibility
        assert compatibility[2:] == (1, 1), compatibility
        assert status == "candidate", status

        print(json.dumps({
            "ok": True,
            "legacy_version_preserved": {
                "algorithm_version": "dds-learning-v2.1",
                "status": legacy[0],
                "evidence_count": legacy[1],
                "transfer_count": legacy[2],
            },
            "current_version": {
                "algorithm_version": ALGORITHM_VERSION,
                "status": current[0],
                "evidence_count": current[1],
                "transfer_count": current[2],
                "reinforcement_count": current[3],
            },
            "compatibility_profile_points_to_current": True,
        }, indent=2))


if __name__ == "__main__":
    main()
