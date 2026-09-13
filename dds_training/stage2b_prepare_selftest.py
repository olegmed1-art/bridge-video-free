from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from stage2b_prepare import (
    compare_oof,
    contract_feature_levels,
    lead_candidates,
    load_train_facts,
    prepare_oof_candidate,
    project_review_queue,
    select_line_source_tasks,
    write_jsonl,
)

DEALS = [
    "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3",
    "N:AK7.QT3.9742.K85 QJ84.K865.A3.Q92 T52.AJ92.KQ85.74 963.74.JT6.AJT63",
    "N:T74.AQJ.985.KQJ4 K965.KT8.AQJ.T92 AQJ2.9652.K74.85 83.743.T632.A763",
    "N:KQ5.T87.AJ96.Q72 A83.KQJ4.753.K84 JT96.A52.KQ2.J93 742.963.T84.AT65",
    "N:AQ4.753.KQ5.QT74 9762.AKJ8.92.K83 KJT5.QT4.AJT7.52 83.962.8643.AJ96",
]


def make_task(index: int, task_type: str) -> dict:
    return {
        "task_id": f"T-{task_type}-{index}",
        "deal_id": f"D-{index}",
        "root_deal_id": f"F-{index}",
        "split": "train",
        "task_type": task_type,
        "deal": DEALS[index % len(DEALS)],
        "declarer": index % 4,
        "leader": (index % 4 + 1) % 4,
        "strain": 4 if index % 3 == 0 else index % 4,
        "crossfit_fold": index % 5,
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks = []
        for index in range(100):
            tasks.append(make_task(index, "contract_tricks"))
            tasks.append(make_task(index, "opening_lead"))
        task_path = root / "tasks.jsonl"
        write_jsonl(task_path, tasks)
        task_map = {task["task_id"]: task for task in tasks}

        db = root / "training.sqlite3"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE predictions(task_id TEXT PRIMARY KEY,deal_id TEXT,task_type TEXT,split TEXT,prediction_json TEXT);
            CREATE TABLE dds_results(task_id TEXT PRIMARY KEY,deal_id TEXT,task_type TEXT,split TEXT,result_json TEXT);
            CREATE TABLE skill_evidence(id INTEGER PRIMARY KEY,skill_key TEXT,task_id TEXT,deal_id TEXT,regret REAL,confidence TEXT,evidence_json TEXT,outcome TEXT);
            """
        )
        for task in tasks:
            if task["task_type"] == "contract_tricks":
                baseline = 7 + (int(task["deal_id"].split("-")[1]) % 3)
                target = max(0, min(13, baseline + (1 if task["strain"] != 4 and int(task["deal_id"].split("-")[1]) % 4 else 0)))
                prediction = {
                    "task_id": task["task_id"],
                    "tricks": baseline,
                    "confidence": "medium",
                    "confidence_probability": 0.55,
                    "model_backoff_level": "medium",
                    "locked": True,
                }
                result = {"dds_tricks": target, "error_code": "OK" if target == baseline else "TRICK_ERROR"}
            else:
                candidates = lead_candidates(task)
                scores = {candidate["card"]: 5 - (i % 3) for i, candidate in enumerate(candidates)}
                best_card = max(scores, key=scores.get)
                prediction = {
                    "task_id": task["task_id"],
                    "card": candidates[-1]["card"],
                    "confidence": "low",
                    "confidence_probability": 0.35,
                    "model_backoff_level": "coarse",
                    "locked": True,
                }
                result = {
                    "scores": scores,
                    "dd_regret": max(scores.values()) - scores[prediction["card"]],
                    "optimal_cards": [best_card],
                    "error_code": "OK",
                }
            con.execute(
                "INSERT INTO predictions VALUES(?,?,?,?,?)",
                (task["task_id"], task["deal_id"], task["task_type"], "train", json.dumps(prediction)),
            )
            con.execute(
                "INSERT INTO dds_results VALUES(?,?,?,?,?)",
                (task["task_id"], task["deal_id"], task["task_type"], "train", json.dumps(result)),
            )
        for index in range(30):
            payload = {
                "prediction": {"confidence_probability": 0.7},
                "result": {"error_code": "REGRET", "mechanism": "tempo"},
                "family_id": f"F-{index % 5}",
                "strain": "NT",
            }
            con.execute(
                "INSERT INTO skill_evidence VALUES(?,?,?,?,?,?,?,?)",
                (index + 1, "defense.opening_lead", f"E-{index}", f"D-{index}", float(index % 3), "medium", json.dumps(payload), "error"),
            )
        con.commit()

        rows = load_train_facts(db, task_map)
        assert len(rows) == 200
        sample_prediction = json.loads(
            con.execute(
                "SELECT prediction_json FROM predictions WHERE task_id=?",
                (tasks[0]["task_id"],),
            ).fetchone()[0]
        )
        assert contract_feature_levels(tasks[0], sample_prediction)
        assert lead_candidates(tasks[1])

        prepared = prepare_oof_candidate(rows)
        assert len(prepared["oof_rows"]) == 200
        assert len(prepared["folds"]) == 5
        assert prepared["calibrator"]["source"] == "family_safe_out_of_fold_train_only"
        assert set(prepared["comparison"]["families"]) == {"contract_tricks", "opening_lead"}

        line_sources = select_line_source_tasks(prepared["oof_rows"], source_total=20)
        assert len(line_sources) == 20
        assert all(row["task"]["task_type"] == "contract_tricks" for row in line_sources)

        projection = project_review_queue(con)
        assert projection
        assert projection[0]["requested_tasks"] <= 250

        comparison = compare_oof(prepared["oof_rows"])
        assert comparison["families"]["contract_tricks"]["n"] == 100
        assert comparison["families"]["opening_lead"]["n"] == 100

        print(
            json.dumps(
                {
                    "ok": True,
                    "train_rows": len(rows),
                    "oof_rows": len(prepared["oof_rows"]),
                    "folds": len(prepared["folds"]),
                    "line_sources": len(line_sources),
                    "queue_groups": len(projection),
                    "dds_called": False,
                    "validation_opened": False,
                    "sealed_opened": False,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
