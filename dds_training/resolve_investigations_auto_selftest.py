from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import resolve_investigations_auto as target
from investigations import open_investigations
from storage import connect, upsert_prediction, upsert_result


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def _contract_task() -> dict:
    return {
        "task_id": "AUTO-CT-1",
        "deal_id": "AUTO-DEAL-CT",
        "task_type": "contract_tricks",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
    }


def _lead_task() -> dict:
    return {
        "task_id": "AUTO-OL-1",
        "deal_id": "AUTO-DEAL-OL",
        "task_type": "opening_lead",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dds-auto-investigation-selftest-") as td:
        work = Path(td)
        task_path = work / "tasks.jsonl"
        out_path = work / "resolution.json"
        tasks = [_contract_task(), _lead_task()]
        task_path.write_text(
            "".join(json.dumps(task, sort_keys=True) + "\n" for task in tasks),
            encoding="utf-8",
        )

        con = connect(work / "training.sqlite3")
        upsert_prediction(
            con,
            tasks[0],
            {
                "task_id": tasks[0]["task_id"],
                "tricks": 11,
                "confidence": "medium",
                "reason": "fixture",
                "line": [],
                "locked": True,
            },
        )
        upsert_result(
            con,
            tasks[0],
            {
                "dds_tricks": 10,
                "predicted_tricks": 11,
                "delta_pred_minus_dds": 1,
                "prediction_error": 1,
                "dd_regret": None,
                "investigation_required": True,
                "error_code": "D_OVER_DDS_CLAIM",
            },
        )
        upsert_prediction(
            con,
            tasks[1],
            {
                "task_id": tasks[1]["task_id"],
                "expected_defense_tricks": 6,
                "confidence": "medium",
                "reason": "fixture",
                "line": [],
                "locked": True,
            },
        )
        upsert_result(
            con,
            tasks[1],
            {
                "best_defense_tricks": 5,
                "dd_regret": 1,
                "investigation_required": True,
                "error_code": "D_OVER_DDS_CLAIM",
            },
        )
        con.commit()
        con.close()

        loaded = target.load_tasks([task_path, work / "missing.jsonl"])
        assert set(loaded) == {"AUTO-CT-1", "AUTO-OL-1"}

        contract = target.contract_diagnostic(
            tasks[0],
            {"tricks": 11, "reason": "fixture", "line": []},
            {"dds_tricks": 10},
        )
        assert contract[3]["resolution_quality"] == "structural_estimate_without_verified_line"
        defense = target.defense_diagnostic(
            tasks[1],
            {"expected_defense_tricks": 6, "reason": "fixture", "line": []},
            {"best_defense_tricks": 5},
        )
        assert defense[3]["resolution_quality"] == "structural_defense_estimate_without_verified_line"

        old_argv = sys.argv
        sys.argv = [
            "resolve_investigations_auto.py",
            "--work", str(work),
            "--tasks", str(task_path),
            "--run-id", "auto-selftest",
            "--out", str(out_path),
        ]
        try:
            target.main()
        finally:
            sys.argv = old_argv

        summary = json.loads(out_path.read_text(encoding="utf-8"))
        assert summary["sync"]["required_results"] == 2
        assert summary["resolved"] == 2
        assert summary["skipped"] == []
        assert summary["remaining_open"] == 0
        assert {row["quality"] for row in summary["resolved_items"]} == {
            "structural_estimate_without_verified_line",
            "structural_defense_estimate_without_verified_line",
        }

        verify = connect(work / "training.sqlite3")
        assert open_investigations(verify) == []
        assert verify.execute(
            "SELECT COUNT(*) FROM investigation_events WHERE event_type='resolved'"
        ).fetchone()[0] == 2
        verify.close()

        print(json.dumps({
            "ok": True,
            "production_resolver_main_executed": True,
            "contract_and_defense_paths_executed": True,
            "append_only_resolutions": 2,
            "remaining_open": 0,
            "new_bridge_methodology_added": False,
        }, indent=2))


if __name__ == "__main__":
    main()
