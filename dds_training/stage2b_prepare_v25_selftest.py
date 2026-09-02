from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from stage2b_prepare import lead_candidates, write_jsonl
from stage2b_prepare_v25 import prepare_stage2b_v25

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
        "board": 10001 + index,
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
        work = root / "work"
        out = root / "out"
        work.mkdir()
        tasks = []
        for index in range(100):
            tasks.append(make_task(index, "contract_tricks"))
            tasks.append(make_task(index, "opening_lead"))
        task_path = work / "tasks.jsonl"
        write_jsonl(task_path, tasks)

        db = work / "training.sqlite3"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE predictions(
              task_id TEXT PRIMARY KEY,deal_id TEXT,task_type TEXT,split TEXT,prediction_json TEXT
            );
            CREATE TABLE dds_results(
              task_id TEXT PRIMARY KEY,deal_id TEXT,task_type TEXT,split TEXT,result_json TEXT
            );
            CREATE TABLE skill_evidence(
              id INTEGER PRIMARY KEY,skill_key TEXT,task_id TEXT,deal_id TEXT,regret REAL,
              confidence TEXT,evidence_json TEXT,outcome TEXT
            );
            CREATE TABLE investigation_events(
              id INTEGER PRIMARY KEY,task_id TEXT,event_type TEXT,details_json TEXT
            );
            CREATE TABLE experience_events(
              id INTEGER PRIMARY KEY,event_type TEXT,task_id TEXT,payload_json TEXT
            );
            """
        )
        for task in tasks:
            index = int(task["deal_id"].split("-")[1])
            if task["task_type"] == "contract_tricks":
                baseline = 7 + index % 3
                target = max(0, min(13, baseline + (1 if task["strain"] != 4 and index % 4 else 0)))
                prediction = {
                    "task_id": task["task_id"],
                    "tricks": baseline,
                    "confidence": "medium",
                    "confidence_probability": 0.55,
                    "model_backoff_level": "medium",
                    "predictor_version": "bridge-adaptive-v0.2",
                    "locked": True,
                }
                result = {
                    "dds_tricks": target,
                    "error_code": "OK" if target == baseline else "TRICK_ERROR",
                }
            else:
                candidates = lead_candidates(task)
                scores = {candidate["card"]: 6 - (i % 3) for i, candidate in enumerate(candidates)}
                prediction = {
                    "task_id": task["task_id"],
                    "card": candidates[-1]["card"],
                    "confidence": "low",
                    "confidence_probability": 0.35,
                    "model_backoff_level": "coarse",
                    "predictor_version": "bridge-adaptive-v0.2",
                    "locked": True,
                }
                result = {
                    "scores": scores,
                    "dd_regret": max(scores.values()) - scores[prediction["card"]],
                    "optimal_cards": [card for card, value in scores.items() if value == max(scores.values())],
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
            task = tasks[index * 2 + 1]
            payload = {
                "prediction": {"confidence_probability": 0.7},
                "result": {"error_code": "REGRET", "mechanism": "tempo"},
            }
            con.execute(
                "INSERT INTO skill_evidence VALUES(?,?,?,?,?,?,?,?)",
                (
                    index + 1,
                    "defense.opening_lead",
                    task["task_id"],
                    task["deal_id"],
                    float(index % 3),
                    "medium",
                    json.dumps(payload),
                    "error",
                ),
            )
        con.commit()
        con.close()

        summary = prepare_stage2b_v25(
            work=work,
            task_paths=[task_path],
            out_dir=out,
            main_tasks_path=task_path,
            line_source_total=80,
            continuations_per_actor=5,
            line_cards=16,
            blueprint_families=5,
        )
        ready = summary["readiness"]
        assert ready["train_rows"] == 200
        assert ready["oof_rows"] == 200
        assert ready["continuation_tasks"] == 10
        assert ready["continuation_by_actor"] == {"declarer": 5, "defense": 5}
        assert ready["multi_contract_blueprint"]["tasks"] == 100
        assert ready["multi_contract_blueprint"]["dds_called"] is False
        assert "NT" in ready["review_queue_strains"]
        assert ready["validation_opened"] is False
        assert ready["sealed_opened"] is False
        assert ready["dds_called"] is False

        manifest = json.loads((out / "CURRENT_STAGE_MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["current_algorithm"] == "dds-learning-v2.5-stage2b-candidate"
        assert manifest["holdout_status"] == "closed"
        assert manifest["sealed_status"] == "closed"
        assert manifest["metadata"]["multi_contract_blueprint_tasks"] == 100
        assert (out / "dds-stage2b-v25-prepared-compact.tgz").is_file()
        assert (out / "dds-stage2b-v25-prepared-compact.tgz.sha256").is_file()

        policy = json.loads((out / "family_selection_policy.json").read_text(encoding="utf-8"))
        assert policy["automatic_promotion"] is False
        assert set(policy["families"]) == {
            "contract_nt",
            "contract_suit",
            "opening_lead_nt",
            "opening_lead_suit",
        }

        print(
            json.dumps(
                {
                    "ok": True,
                    "train_rows": ready["train_rows"],
                    "oof_rows": ready["oof_rows"],
                    "continuation_by_actor": ready["continuation_by_actor"],
                    "blueprint_tasks": ready["multi_contract_blueprint"]["tasks"],
                    "queue_strains": ready["review_queue_strains"],
                    "validation_opened": False,
                    "sealed_opened": False,
                    "dds_called": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
