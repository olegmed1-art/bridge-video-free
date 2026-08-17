from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import adaptive_predictor as ap
import baseline_predictor as bp

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    contract = {
        "task_id": "ADAPT-CT-TRAIN",
        "task_type": "contract_tricks",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
    }
    lead = {
        "task_id": "ADAPT-OL-TRAIN",
        "task_type": "opening_lead",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }
    base_contract = bp.prediction_for(contract, "baseline-selftest")
    base_lead = bp.prediction_for(lead, "baseline-selftest")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks_path = root / "tasks.jsonl"
        predictions_path = root / "predictions.jsonl"
        db_path = root / "training.sqlite3"
        model_path = root / "adaptive.json"
        output_path = root / "adaptive_predictions.jsonl"
        validation_contract = {**contract, "task_id": "ADAPT-CT-VALID", "split": "validation"}
        validation_lead = {**lead, "task_id": "ADAPT-OL-VALID", "split": "validation"}
        write_jsonl(tasks_path, [contract, lead, validation_contract, validation_lead])
        write_jsonl(predictions_path, [base_contract, base_lead])

        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE dds_results(task_id TEXT PRIMARY KEY, split TEXT, result_json TEXT)")
        con.execute(
            "INSERT INTO dds_results VALUES(?,?,?)",
            (
                contract["task_id"],
                "train",
                json.dumps({"dds_tricks": min(13, int(base_contract["tricks"]) + 1)}),
            ),
        )
        con.execute(
            "INSERT INTO dds_results VALUES(?,?,?)",
            (
                lead["task_id"],
                "train",
                json.dumps(
                    {
                        "scores": {
                            base_lead["card"]: 5,
                            "S2": 4,
                        }
                    }
                ),
            ),
        )
        con.commit()
        con.close()

        trained = ap.train_model(tasks_path, predictions_path, db_path, model_path)
        model = json.loads(model_path.read_text(encoding="utf-8"))
        assert trained["contract_samples"] == 1, trained
        assert trained["opening_lead_tasks"] == 1, trained
        assert trained["opening_lead_candidate_samples"] >= 1, trained
        assert model["training_split"] == "train"
        assert model["dds_used_during_prediction"] is False

        summary = ap.predict(tasks_path, model_path, output_path, {"validation"})
        predictions = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        assert summary["predictions"] == 2, summary
        assert summary["dds_called_during_prediction"] is False
        assert {row["task_id"] for row in predictions} == {"ADAPT-CT-VALID", "ADAPT-OL-VALID"}
        assert all(row["locked"] is True for row in predictions)
        contract_prediction = next(row for row in predictions if "tricks" in row)
        assert 0 <= int(contract_prediction["tricks"]) <= 13
        lead_prediction = next(row for row in predictions if "card" in row)
        hands = bp.parse_deal(DEAL)
        suit = bp.SUITS.index(lead_prediction["card"][0])
        assert lead_prediction["card"][1:] in hands[lead["leader"]][suit]

        duplicate_path = root / "duplicates.jsonl"
        write_jsonl(duplicate_path, [base_contract, base_contract])
        try:
            ap._load_predictions(duplicate_path)
        except ValueError as exc:
            assert "Duplicate" in str(exc)
        else:
            raise AssertionError("Duplicate prediction was accepted")

        unlocked_path = root / "unlocked.jsonl"
        write_jsonl(unlocked_path, [{**base_contract, "locked": False}])
        try:
            ap._load_predictions(unlocked_path)
        except ValueError as exc:
            assert "locked" in str(exc)
        else:
            raise AssertionError("Unlocked prediction was accepted")

    print(
        json.dumps(
            {
                "ok": True,
                "train_split_only": True,
                "model_written": True,
                "validation_inference_locked": True,
                "duplicate_and_unlocked_predictions_blocked": True,
                "dds_called_during_prediction": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
