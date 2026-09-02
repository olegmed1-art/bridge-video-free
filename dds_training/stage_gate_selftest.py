from __future__ import annotations

import json
import tempfile
from pathlib import Path

from investigations import resolve_investigation, sync_required_investigations
from run_provenance import record_run_task
from stage_gate import assess_stage
from storage import connect, upsert_prediction, upsert_result


def _run_row(con, run_id: str, stage: str, split: str, sealed_opened: int = 0) -> None:
    con.execute(
        """
        INSERT INTO runs
          (run_id,stage,seed,corpus_sha256,solver_info_json,algorithm_version,
           requested_splits_json,task_file,predictions_sha256,sealed_opened,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (run_id, stage, 1, "x", "{}", "test", json.dumps([split]), "tasks", "pred", sealed_opened, "completed"),
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        tasks = [
            {"task_id": "T1", "deal_id": "D1", "task_type": "contract_tricks", "split": "train", "board": 1},
            {"task_id": "T2", "deal_id": "D2", "task_type": "contract_tricks", "split": "validation", "board": 2},
            {"task_id": "T3", "deal_id": "D3", "task_type": "contract_tricks", "split": "sealed_test", "board": 3},
        ]
        (work / "blind_tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks), encoding="utf-8")
        (work / "corpus_summary.json").write_text(json.dumps({"count": 10_000}), encoding="utf-8")
        con = connect(work / "training.sqlite3")

        initial = assess_stage(work, "pilot")
        assert initial["missing_by_split"] == {"train": 1, "validation": 1, "sealed_test": 1}
        assert not initial["ready_for_report"]

        prediction = {"tricks": 11, "confidence": "high", "locked": True}
        ordinary = {
            "dds_tricks": 11, "predicted_tricks": 11, "delta_pred_minus_dds": 0,
            "prediction_error": 0, "dd_regret": None, "investigation_required": False, "error_code": "OK",
        }
        over = {
            "dds_tricks": 10, "predicted_tricks": 11, "delta_pred_minus_dds": 1,
            "prediction_error": 1, "dd_regret": None, "investigation_required": True, "error_code": "D_OVER_DDS_CLAIM",
        }

        for task, split, result in ((tasks[0], "train", over), (tasks[1], "validation", ordinary), (tasks[2], "sealed_test", ordinary)):
            upsert_prediction(con, task, {**prediction, "task_id": task["task_id"]})
            upsert_result(con, task, result)
            run_id = f"R-{split}"
            _run_row(con, run_id, "pilot", split, sealed_opened=int(split == "sealed_test"))
            record_run_task(con, run_id=run_id, task=task, action="evaluated")
        con.commit()

        blocked = assess_stage(work, "pilot")
        assert blocked["missing_by_split"] == {"train": 0, "validation": 0, "sealed_test": 0}
        assert blocked["open_mandatory_investigations"] == 1
        assert not blocked["ready_for_report"]

        sync_required_investigations(con)
        resolve_investigation(
            con,
            task_id="T1",
            cause="Proposed line assumes an opponent error.",
            first_refutation="Optimal defense changes continuation.",
            lesson="Test optimal defense before overclaiming DDS.",
        )
        con.commit()
        ready = assess_stage(work, "pilot")
        assert ready["ready_for_report"], ready
        assert not ready["ready_for_next_stage"]

        (work / "report_pilot.md").write_text("pilot report\n", encoding="utf-8")
        finished = assess_stage(work, "pilot")
        assert finished["technical_stage_complete"] is True
        assert finished["report_exists"] is True
        assert finished["required_transition_gate"] == "main_train"
        assert finished["required_transition_gate_ready"] is False
        assert finished["ready_for_next_stage"] is False
        assert finished["explicit_user_approval_required"] is True
        assert any(
            finding["code"] == "MAIN_CORPUS_NOT_EXPANDED"
            for finding in finished["stage2_readiness"]["findings"]
        )

        print(json.dumps({
            "ok": True,
            "missing_work_blocked": True,
            "open_investigation_blocked": True,
            "report_required": True,
            "technical_completion_after_report": True,
            "stage2_preparation_still_required": True,
            "automatic_expansion_blocked": True,
        }, indent=2))


if __name__ == "__main__":
    main()
