from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from config import ALGORITHM_VERSION, PROJECT_SEED
from report import generate_report
from run_provenance import ensure_run_task_table, record_run_task
from storage import connect, upsert_prediction, upsert_result

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "pilot"
        work.mkdir()
        corpus_hash = "a" * 64
        (work / "corpus_summary.json").write_text(
            json.dumps(
                {
                    "count": 10000,
                    "seed": PROJECT_SEED,
                    "raw_sha256": corpus_hash,
                    "splits": {"train": 7000, "validation": 1500, "sealed_test": 1500},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        con = connect(work / "training.sqlite3")
        ensure_run_task_table(con)
        run_id = "REPORT-SELFTEST"
        con.execute(
            """
            INSERT INTO runs(
              run_id,stage,seed,corpus_sha256,solver_info_json,algorithm_version,
              requested_splits_json,task_file,predictions_sha256,sealed_opened,status,completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """,
            (
                run_id,
                "pilot",
                PROJECT_SEED,
                corpus_hash,
                json.dumps({"engine": "fixture"}),
                ALGORITHM_VERSION,
                '["train"]',
                "fixture_tasks.jsonl",
                "b" * 64,
                0,
                "completed",
            ),
        )

        contract_task = {
            "task_id": "REPORT-CT",
            "deal_id": "REPORT-DEAL-1",
            "task_type": "contract_tricks",
            "split": "train",
            "deal": DEAL,
            "declarer": 2,
            "strain": 4,
        }
        lead_task = {
            "task_id": "REPORT-OL",
            "deal_id": "REPORT-DEAL-2",
            "task_type": "opening_lead",
            "split": "train",
            "deal": DEAL,
            "declarer": 2,
            "strain": 4,
            "leader": 3,
        }
        contract_prediction = {
            "task_id": contract_task["task_id"],
            "tricks": 8,
            "confidence": "medium",
            "reason": "fixture",
            "line": [],
            "predictor_version": "report-fixture",
            "locked": True,
        }
        lead_prediction = {
            "task_id": lead_task["task_id"],
            "card": "SA",
            "confidence": "medium",
            "reason": "fixture",
            "line": ["SA"],
            "predictor_version": "report-fixture",
            "locked": True,
        }
        contract_result = {
            "dds_tricks": 8,
            "predicted_tricks": 8,
            "delta_pred_minus_dds": 0,
            "prediction_error": 0,
            "dd_regret": None,
            "investigation_required": False,
            "error_code": "OK",
        }
        lead_result = {
            "scores": {"SA": 4, "S2": 3},
            "best_defense_tricks": 4,
            "optimal_cards": ["SA"],
            "chosen_card": "SA",
            "chosen_defense_tricks": 4,
            "legal_or_equivalent": True,
            "dd_regret": 0,
            "investigation_required": False,
            "error_code": "OK",
        }

        for task, prediction, result in (
            (contract_task, contract_prediction, contract_result),
            (lead_task, lead_prediction, lead_result),
        ):
            upsert_prediction(con, task, prediction)
            upsert_result(con, task, result)
            record_run_task(
                con,
                run_id=run_id,
                task=task,
                action="evaluated",
                details={"algorithm_version": ALGORITHM_VERSION},
            )
        con.commit()

        english_path = generate_report(work, "pilot")
        english = english_path.read_text(encoding="utf-8")
        assert "# DDS learning report — pilot" in english
        assert "Exact trick prediction: 100.00%" in english
        assert "Equal-optimal leads: 100.00%" in english
        assert "Status: **ok**" in english

        russian_path = work / "report_pilot_ru.md"
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("pilot_report_ru.py")),
                "--work",
                str(work),
                "--out",
                str(russian_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        russian = russian_path.read_text(encoding="utf-8")
        assert "# Итоговый отчёт DDS-обучения — пилот 10 000 сдач" in russian
        assert "точное совпадение 100.00%" in russian
        assert "равнооптимальных ходов 100.00%" in russian
        assert "Аудит базы: **ok**" in russian

        # Reports are derived views only. Immutable facts remain intact.
        db = sqlite3.connect(work / "training.sqlite3")
        assert db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM run_task_events").fetchone()[0] == 2

        print(
            json.dumps(
                {
                    "ok": True,
                    "english_report_rendered": True,
                    "russian_report_rendered": True,
                    "stage_scoped_metrics_verified": True,
                    "database_audit_ok": True,
                    "immutable_facts_preserved": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
