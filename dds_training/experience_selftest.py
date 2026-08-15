from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from audit import audit_database
from checkpointing import sha256_file, snapshot_database
from experience_events import record_reasoning_review, record_value_trajectory
from learning import build_learning_plan, record_skill_check, record_task_experience
from storage import add_regression_case, connect, record_correction, upsert_prediction, upsert_result
from variants import create_variants


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db_path = root / "training.sqlite3"
        db = connect(db_path)
        task = {
            "task_id": "SELFTEST-CT",
            "deal_id": "SELFTEST-DEAL",
            "task_type": "contract_tricks",
            "split": "train",
            "deal": "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3",
            "declarer": 2,
            "strain": 4,
            "strain_name": "NT",
        }
        prediction = {
            "task_id": task["task_id"],
            "tricks": 11,
            "confidence": "high",
            "reason": "self-test prediction",
            "locked": True,
        }
        result = {
            "dds_tricks": 10,
            "predicted_tricks": 11,
            "delta_pred_minus_dds": 1,
            "prediction_error": 1,
            "dd_regret": None,
            "investigation_required": True,
            "error_code": "D_OVER_DDS_CLAIM",
        }

        upsert_prediction(db, task, prediction)
        upsert_result(db, task, result)
        skills = record_task_experience(db, task, prediction, result, "selftest")
        db.execute(
            "INSERT INTO error_events(task_id,error_code,magnitude,details_json) VALUES(?,?,?,?)",
            (task["task_id"], result["error_code"], 1, json.dumps(result)),
        )
        add_regression_case(db, task, result, skills[0])
        correction_id = record_correction(
            db,
            target_table="skill_evidence",
            target_key=task["task_id"],
            correction_type="classification_note",
            reason="Self-test correction proves append-only correction path.",
            replacement={"note": "original fact remains untouched"},
        )

        record_reasoning_review(
            db,
            task_id=task["task_id"],
            deal_id=task["deal_id"],
            verdict="correct_result_wrong_reasoning",
            details={"why": "self-test separates result from explanation"},
            run_id="selftest",
        )

        trajectory = record_value_trajectory(
            db,
            task_id=task["task_id"],
            deal_id=task["deal_id"],
            values=[10, 9, 9, 10],
            actors=["declarer", "defense", "defense"],
            run_id="selftest",
        )
        assert trajectory["first_error"]["actor"] == "declarer"
        assert trajectory["declarer_gross_loss"] == 1
        assert trajectory["defense_gross_gift"] == 1
        assert trajectory["recovered_declarer_loss"] == 1
        assert trajectory["unrecovered_declarer_loss"] == 0

        variants = create_variants(task)
        assert len(variants) == 5
        assert len({v["task_id"] for v in variants}) == 5
        assert all(v["split"] == "derived" for v in variants)
        assert any(v["evidence_type"] == "perturbation" for v in variants)
        assert any(v["evidence_type"] == "symmetry" for v in variants)
        transfer_status = record_skill_check(
            db,
            skill_key="declarer.overclaim_detection",
            task_id=variants[0]["task_id"],
            deal_id=variants[0]["deal_id"],
            evidence_type="symmetry",
            success=True,
            confidence="high",
            run_id="selftest",
            details={"source": task["task_id"]},
        )
        db.commit()

        snapshot = snapshot_database(
            db,
            db_path=db_path,
            snapshot_dir=root / "checkpoints",
            run_id="selftest",
            completed_tasks=1000,
            errors=1,
            next_task_id="NEXT",
            keep_milestone_every=1000,
        )
        latest_snapshot = Path(snapshot["latest_snapshot"])
        milestone_snapshot = Path(snapshot["milestone_snapshot"])
        assert latest_snapshot.exists() and milestone_snapshot.exists()
        assert sha256_file(latest_snapshot) == snapshot["latest_sha256"]
        snap_db = sqlite3.connect(latest_snapshot)
        try:
            assert snap_db.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0] == 1
            assert snap_db.execute("SELECT COUNT(*) FROM correction_events").fetchone()[0] == 1
        finally:
            snap_db.close()

        upsert_prediction(db, task, prediction)
        try:
            changed = dict(result)
            changed["dds_tricks"] = 9
            upsert_result(db, task, changed)
        except ValueError:
            pass
        else:
            raise AssertionError("changed immutable DDS fact was accepted")

        try:
            db.execute("UPDATE predictions SET locked=0 WHERE task_id=?", (task["task_id"],))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("immutability trigger did not block prediction update")

        plan = build_learning_plan(db)
        assert plan and plan[0]["high_confidence_errors"] >= 1, plan
        spaced = db.execute("SELECT COUNT(*) FROM learning_queue WHERE purpose='spaced_review'").fetchone()[0]
        assert spaced == 6, spaced
        reasoning = db.execute("SELECT COUNT(*) FROM experience_events WHERE event_type='reasoning_review'").fetchone()[0]
        trajectories = db.execute("SELECT COUNT(*) FROM experience_events WHERE event_type='value_trajectory'").fetchone()[0]
        assert reasoning == 1 and trajectories == 1

        audit = audit_database(db)
        assert audit["status"] == "ok", audit
        assert audit["counts"]["correction_events"] == 1
        assert audit["counts"]["regression_cases"] == 1
        assert correction_id == 1
        print(json.dumps({
            "ok": True,
            "immutable_predictions": True,
            "immutable_dds_results": True,
            "append_only_corrections": True,
            "checkpoint_snapshot": snapshot,
            "skills_recorded": skills,
            "transfer_status": transfer_status,
            "derived_variants": [v["task_id"] for v in variants],
            "spaced_reviews": spaced,
            "trajectory": trajectory,
            "reasoning_reviews": reasoning,
            "learning_plan_top": plan[0],
            "audit": audit,
        }, indent=2))


if __name__ == "__main__":
    main()
