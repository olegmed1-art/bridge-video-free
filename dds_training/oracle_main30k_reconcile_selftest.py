from __future__ import annotations

import json
import tempfile
from pathlib import Path

from config import ALGORITHM_VERSION
from oracle_main30k_reconcile import reconcile
from run_provenance import record_run_task
from storage import connect, upsert_prediction, upsert_result


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def task(task_id: str, split: str) -> dict:
    return {"task_id": task_id, "deal_id": f"DEAL-{task_id}", "task_type": "contract_tricks",
            "split": split, "deal": DEAL, "declarer": 2, "strain": 4}


def add_run(con, run_id: str, splits: list[str], sealed_opened: bool) -> None:
    con.execute(
        """INSERT INTO runs
        (run_id,stage,seed,corpus_sha256,solver_info_json,algorithm_version,
         requested_splits_json,task_file,predictions_sha256,sealed_opened,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, "main", 20260830, "fixture-corpus", '{"engine":"DDS3","fallback_used":false}',
         ALGORITHM_VERSION, json.dumps(splits), "fixture.jsonl", "fixture-predictions",
         int(sealed_opened), "completed"),
    )


def add_result(con, item: dict, result: dict) -> None:
    upsert_prediction(con, item, {"task_id": item["task_id"],
        "tricks": result.get("predicted_tricks", result.get("dds_tricks", 9)),
        "confidence": "medium", "reason": "reconcile selftest", "locked": True})
    upsert_result(con, item, result)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dds-main30k-reconcile-") as td:
        work = Path(td)
        con = connect(work / "training.sqlite3")
        over = task("TRAIN-OVER", "train")
        unsupported = task("TRAIN-UNSUPPORTED", "train")
        sealed = task("SEALED-FACT", "sealed_test")
        add_result(con, over, {"dds_tricks": 9, "predicted_tricks": 10,
            "delta_pred_minus_dds": 1, "prediction_error": 1, "dd_regret": None,
            "investigation_required": True, "error_code": "D_OVER_DDS_CLAIM"})
        add_result(con, unsupported, {"dds_tricks": 9, "predicted_tricks": 8,
            "delta_pred_minus_dds": -1, "prediction_error": -1, "dd_regret": None,
            "investigation_required": True, "error_code": "D_MISSED_TRICKS"})
        add_result(con, sealed, {"dds_tricks": 9, "predicted_tricks": 9,
            "delta_pred_minus_dds": 0, "prediction_error": 0, "dd_regret": None,
            "investigation_required": False, "error_code": "OK"})
        add_run(con, "fixture-train", ["train"], False)
        add_run(con, "fixture-sealed", ["sealed_test"], True)
        record_run_task(con, run_id="fixture-train", task=over, action="evaluated", details={"fixture": True})
        record_run_task(con, run_id="fixture-train", task=unsupported, action="evaluated", details={"fixture": True})
        record_run_task(con, run_id="fixture-sealed", task=sealed, action="evaluated", details={"fixture": True})
        con.commit()
        con.close()

        first = reconcile(work, "reconcile-fixture", plan_limit=8)
        assert first["dds_called"] is False
        assert first["validation_opened"] is False and first["sealed_test_opened"] is False
        assert first["deterministic_resolution"]["resolved_now"] == 1
        assert first["deterministic_resolution"]["unsupported_open"] == 1
        assert first["investigations"]["open_total"] == 1
        assert first["sealed_results_present"] == 1 and first["sealed_learning_leaks"] == 0
        assert first["sealed_provenance"] == [{"run_id": "fixture-sealed", "stage": "main",
            "requested_splits": ["sealed_test"], "sealed_opened": True, "status": "completed",
            "evaluated_sealed_tasks": 1}]

        con = connect(work / "training.sqlite3")
        before = (con.execute("SELECT COUNT(*) FROM learning_queue WHERE source_run_id='reconcile-fixture'").fetchone()[0],
                  con.execute("SELECT COUNT(*) FROM investigation_events WHERE event_type='resolved'").fetchone()[0])
        con.close()
        second = reconcile(work, "reconcile-fixture", plan_limit=8)
        assert second["deterministic_resolution"]["resolved_now"] == 0
        assert second["deterministic_resolution"]["unsupported_open"] == 1
        assert second["investigations"]["opened_now"] == 0
        assert second["priority_plan_rows_persisted"] == first["priority_plan_rows_persisted"]
        con = connect(work / "training.sqlite3")
        after = (con.execute("SELECT COUNT(*) FROM learning_queue WHERE source_run_id='reconcile-fixture'").fetchone()[0],
                 con.execute("SELECT COUNT(*) FROM investigation_events WHERE event_type='resolved'").fetchone()[0])
        con.close()
        assert after == before
        print(json.dumps({"ok": True, "idempotent": True, "unsupported_fail_closed": True,
            "sealed_provenance_preserved": True, "sealed_learning_leaks": 0, "dds_called": False}, sort_keys=True))


if __name__ == "__main__":
    main()
