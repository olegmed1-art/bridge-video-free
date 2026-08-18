from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from audit import audit_database
from checkpointing import sha256_file, snapshot_database
from config import ALGORITHM_VERSION, FOLLOWUP_SOURCE_POLICY
from experience_events import record_reasoning_review, record_value_trajectory, summarize_value_trajectory
from learning import build_learning_plan, learning_allowed_for_task, record_skill_check, record_task_experience
from run_provenance import record_run_task
from storage import add_regression_case, connect, record_correction, upsert_prediction, upsert_result
from variants import create_error_followups, create_variants


def _insert_test_run(db: sqlite3.Connection, run_id: str, splits: list[str], sealed_opened: int = 0) -> None:
    db.execute(
        """
        INSERT INTO runs
          (run_id,stage,seed,corpus_sha256,solver_info_json,algorithm_version,
           requested_splits_json,task_file,predictions_sha256,sealed_opened,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, "pilot", 20260815, "selftest-sha", "{}", ALGORITHM_VERSION,
            json.dumps(sorted(splits), separators=(",", ":")), "selftest.jsonl", "pred-sha",
            sealed_opened, "completed",
        ),
    )


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

        # Holdout policy is fail-closed.
        assert learning_allowed_for_task(task)
        assert not learning_allowed_for_task({**task, "split": "validation"})
        assert not learning_allowed_for_task({**task, "split": "sealed_test"})
        assert learning_allowed_for_task({**task, "split": "derived", "source_root_split": "train"})
        assert not learning_allowed_for_task({**task, "split": "derived", "source_root_split": "validation"})
        assert not learning_allowed_for_task({**task, "split": "derived", "source_root_split": "sealed_test"})
        assert not learning_allowed_for_task({**task, "split": "derived"})

        upsert_prediction(db, task, prediction)
        upsert_result(db, task, result)
        _insert_test_run(db, "selftest-run", ["train"])
        record_run_task(
            db,
            run_id="selftest-run",
            task=task,
            action="evaluated",
            details={"purpose": "selftest"},
        )
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

        # Temporal recovery: declarer loses one, defense later gives it back.
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
        assert trajectory["squandered_defense_gift"] == 0
        assert trajectory["unrecovered_declarer_loss"] == 0

        # An earlier defensive gift is not allowed to retroactively 'restore' a
        # later declarer error; instead the declarer squanders the gift.
        prior_gift = summarize_value_trajectory([10, 11, 10], ["defense", "declarer"])
        assert prior_gift["recovered_declarer_loss"] == 0
        assert prior_gift["squandered_defense_gift"] == 1
        assert prior_gift["unrecovered_declarer_loss"] == 0
        assert prior_gift["unrecovered_defense_gift"] == 0

        bad_direction = summarize_value_trajectory([10, 11], ["declarer"])
        assert len(bad_direction["invariant_violations"]) == 1

        variants = create_variants(task)
        assert 5 <= len(variants) <= 6
        assert len({v["task_id"] for v in variants}) == len(variants)
        assert all(v["split"] == "derived" for v in variants)
        assert all(v["source_root_split"] == "train" for v in variants)
        assert all(v["transfer_eligible"] is False for v in variants)
        assert any(v["evidence_type"] == "perturbation" for v in variants)
        assert any(v["evidence_type"] == "symmetry" for v in variants)
        assert any(v["evidence_type"] == "regression" for v in variants)
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
        assert db.execute(
            "SELECT evidence_type FROM skill_evidence WHERE task_id=?",
            (variants[0]["task_id"],),
        ).fetchone()[0] == "reinforcement"

        # Stable skills can be weakened by a fresh regression/counterexample and
        # later recover only after fresh successful checks.
        recovery_skill = "selftest.recovery"
        for i in range(10):
            status = record_skill_check(
                db, skill_key=recovery_skill, task_id=f"REC-T{i}", deal_id=f"REC-D{i}",
                evidence_type="transfer", success=True, run_id="selftest",
            )
        for i in range(3):
            status = record_skill_check(
                db, skill_key=recovery_skill, task_id=f"REC-R{i}", deal_id=f"REC-RD{i}",
                evidence_type="regression", success=True, run_id="selftest",
            )
        for i in range(2):
            status = record_skill_check(
                db, skill_key=recovery_skill, task_id=f"REC-C{i}", deal_id=f"REC-CD{i}",
                evidence_type="counterexample", success=True, run_id="selftest",
            )
        assert status == "stable", status

        status = record_skill_check(
            db, skill_key=recovery_skill, task_id="REC-RFAIL", deal_id="REC-RFAIL-D",
            evidence_type="regression", success=False, run_id="selftest",
        )
        assert status == "weakened", status
        for i in range(3, 6):
            status = record_skill_check(
                db, skill_key=recovery_skill, task_id=f"REC-R{i}", deal_id=f"REC-RD{i}",
                evidence_type="regression", success=True, run_id="selftest",
            )
        assert status == "stable", status

        status = record_skill_check(
            db, skill_key=recovery_skill, task_id="REC-CFAIL", deal_id="REC-CFAIL-D",
            evidence_type="counterexample", success=False, run_id="selftest",
        )
        assert status == "weakened", status
        for i in range(2, 4):
            status = record_skill_check(
                db, skill_key=recovery_skill, task_id=f"REC-C{i}", deal_id=f"REC-CD{i}",
                evidence_type="counterexample", success=True, run_id="selftest",
            )
        assert status == "stable", status

        # Validation errors are stored as benchmark facts but do not become
        # learning evidence or follow-up sources.
        val_task = {**task, "task_id": "SELFTEST-VAL", "deal_id": "SELFTEST-VAL-DEAL", "split": "validation"}
        val_pred = {**prediction, "task_id": val_task["task_id"]}
        upsert_prediction(db, val_task, val_pred)
        upsert_result(db, val_task, result)
        db.execute(
            "INSERT INTO error_events(task_id,error_code,magnitude,details_json) VALUES(?,?,?,?)",
            (val_task["task_id"], result["error_code"], 1, json.dumps(result)),
        )
        assert db.execute("SELECT COUNT(*) FROM skill_evidence WHERE task_id=?", (val_task["task_id"],)).fetchone()[0] == 0

        base_tasks = root / "base_tasks.jsonl"
        base_tasks.write_text(
            json.dumps(task) + "\n" + json.dumps(val_task) + "\n",
            encoding="utf-8",
        )
        followup_path = root / "followups.jsonl"
        followup_summary = create_error_followups(base_tasks, db, followup_path, max_sources=10)
        followup_rows = [json.loads(x) for x in followup_path.read_text().splitlines() if x.strip()]
        assert followup_summary["source_policy"] == FOLLOWUP_SOURCE_POLICY
        assert followup_rows
        assert all(x["source_root_split"] == "train" for x in followup_rows)
        assert all(x["derived_from_task_id"] == task["task_id"] for x in followup_rows)

        db.commit()

        snapshot = snapshot_database(
            db,
            db_path=db_path,
            snapshot_dir=root / "checkpoints",
            run_id="selftest",
            completed_tasks=1000,
            errors=2,
            next_task_id="NEXT",
            keep_milestone_every=1000,
        )
        latest_snapshot = Path(snapshot["latest_snapshot"])
        milestone_snapshot = Path(snapshot["milestone_snapshot"])
        assert latest_snapshot.exists() and milestone_snapshot.exists()
        assert sha256_file(latest_snapshot) == snapshot["latest_sha256"]
        snap_db = sqlite3.connect(latest_snapshot)
        try:
            assert snap_db.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0] == 2
            assert snap_db.execute("SELECT COUNT(*) FROM correction_events").fetchone()[0] == 1
            assert snap_db.execute("SELECT COUNT(*) FROM run_task_events").fetchone()[0] == 1
        finally:
            snap_db.close()

        # Idempotent identical insert is allowed; fact, metadata, and provenance
        # mutation are not.
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
            bad_meta = dict(task)
            bad_meta["deal_id"] = "MUTATED-DEAL-ID"
            upsert_prediction(db, bad_meta, prediction)
        except ValueError:
            pass
        else:
            raise AssertionError("changed immutable task metadata was accepted")

        try:
            db.execute("UPDATE predictions SET locked=0 WHERE task_id=?", (task["task_id"],))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("immutability trigger did not block prediction update")

        try:
            db.execute("UPDATE run_task_events SET split='validation' WHERE run_id='selftest-run'")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("run-task provenance update was not blocked")

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
        assert audit["counts"]["run_task_events"] == 1
        assert correction_id == 1

        # Dedicated sealed provenance test: a sealed result without an original
        # authorized evaluated mapping is an audit error. A later `reused` event
        # does not fix it; only an authorized sealed-only `evaluated` event does.
        sealed_db = connect(root / "sealed.sqlite3")
        sealed_task = {**task, "task_id": "SELFTEST-SEALED", "deal_id": "SELFTEST-SEALED-DEAL", "split": "sealed_test"}
        sealed_pred = {**prediction, "task_id": sealed_task["task_id"]}
        upsert_prediction(sealed_db, sealed_task, sealed_pred)
        upsert_result(sealed_db, sealed_task, result)
        assert audit_database(sealed_db)["status"] == "error"

        _insert_test_run(sealed_db, "sealed-reuse", ["sealed_test"], sealed_opened=1)
        record_run_task(sealed_db, run_id="sealed-reuse", task=sealed_task, action="reused")
        assert audit_database(sealed_db)["status"] == "error"

        _insert_test_run(sealed_db, "sealed-eval", ["sealed_test"], sealed_opened=1)
        record_run_task(sealed_db, run_id="sealed-eval", task=sealed_task, action="evaluated")
        sealed_audit = audit_database(sealed_db)
        assert sealed_audit["status"] == "ok", sealed_audit

        print(json.dumps({
            "ok": True,
            "holdout_isolation": True,
            "train_only_followups": followup_summary,
            "immutable_predictions": True,
            "immutable_dds_results": True,
            "immutable_task_metadata": True,
            "immutable_run_task_provenance": True,
            "sealed_reuse_cannot_fake_origin": True,
            "append_only_corrections": True,
            "checkpoint_snapshot": snapshot,
            "skills_recorded": skills,
            "transfer_status": transfer_status,
            "recovery_skill_status": status,
            "derived_variants": [v["task_id"] for v in variants],
            "spaced_reviews": spaced,
            "trajectory": trajectory,
            "prior_gift_trajectory": prior_gift,
            "trajectory_invariant_test": bad_direction,
            "reasoning_reviews": reasoning,
            "learning_plan_top": plan[0],
            "audit": audit,
            "sealed_audit": sealed_audit,
        }, indent=2))


if __name__ == "__main__":
    main()
