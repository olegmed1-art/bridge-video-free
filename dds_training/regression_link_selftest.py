from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from learning import record_task_experience
from regression_links import sync_regression_skill_links
from storage import add_regression_case, connect, upsert_prediction, upsert_result


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        con = connect(Path(td) / "training.sqlite3")
        task = {
            "task_id": "MULTI-ERR",
            "deal_id": "MULTI-DEAL",
            "task_type": "contract_tricks",
            "split": "train",
        }
        pred = {"task_id": task["task_id"], "tricks": 11, "confidence": "high", "locked": True}
        result = {
            "dds_tricks": 10,
            "predicted_tricks": 11,
            "delta_pred_minus_dds": 1,
            "prediction_error": 1,
            "dd_regret": None,
            "investigation_required": True,
            "error_code": "D_OVER_DDS_CLAIM",
        }
        upsert_prediction(con, task, pred)
        upsert_result(con, task, result)
        skills = record_task_experience(con, task, pred, result, "selftest")
        assert set(skills) == {"declarer.trick_estimation", "declarer.overclaim_detection"}, skills
        add_regression_case(con, task, result, skills[0])
        synced = sync_regression_skill_links(con)
        links = {r[0] for r in con.execute("SELECT skill_key FROM regression_skill_links WHERE task_id=?", (task["task_id"],))}
        assert links == set(skills), (links, skills)
        assert synced["multi_skill_cases"] == 1

        try:
            con.execute("UPDATE regression_skill_links SET skill_key='x' WHERE task_id=?", (task["task_id"],))
        except sqlite3.IntegrityError:
            immutable = True
        else:
            immutable = False
        assert immutable

        print(json.dumps({
            "ok": True,
            "skills": sorted(skills),
            "regression_links": sorted(links),
            "multi_skill_case_preserved": True,
            "append_only": True,
            "sync": synced,
        }, indent=2))


if __name__ == "__main__":
    main()
