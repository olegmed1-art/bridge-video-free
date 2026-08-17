from __future__ import annotations

import json
import tempfile
from pathlib import Path

import baseline_predictor as baseline
from adaptive_predictor import predict, train_model
from corpus import generate_corpus
from storage import connect, upsert_result
from tasks import create_blind_tasks


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def opening_scores(task: dict, chosen_card: str) -> dict[str, int]:
    hands = baseline.parse_deal(task["deal"])
    leader = int(task["leader"])
    scores: dict[str, int] = {}
    for suit, cards in enumerate(hands[leader]):
        for rank in cards:
            scores[f"{baseline.SUITS[suit]}{rank}"] = 4
    if chosen_card in scores:
        scores[chosen_card] = 5
    return scores


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dds-predictor-selftest-") as td:
        root = Path(td)
        generate_corpus(20, root, seed=20260815)
        tasks_path = root / "blind_tasks.jsonl"
        create_blind_tasks(root / "raw.pbn", root / "manifest.jsonl", tasks_path)
        tasks = load_jsonl(tasks_path)
        assert tasks and {row["split"] for row in tasks} >= {"train", "validation"}

        predictions_path = root / "locked_train_predictions.jsonl"
        con = connect(root / "training.sqlite3")
        train_count = 0
        contract_count = 0
        lead_count = 0
        with predictions_path.open("w", encoding="utf-8") as handle:
            for index, task in enumerate(tasks):
                if task["split"] != "train":
                    continue
                prediction = baseline.prediction_for(task, "bridge-baseline-selftest-v1")
                assert prediction["locked"] is True
                assert prediction["task_id"] == task["task_id"]
                handle.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
                train_count += 1

                if task["task_type"] == "contract_tricks":
                    guessed = int(prediction["tricks"])
                    dds = max(0, min(13, guessed + (1 if index % 3 == 0 else 0)))
                    result = {
                        "dds_tricks": dds,
                        "predicted_tricks": guessed,
                        "delta_pred_minus_dds": guessed - dds,
                        "prediction_error": abs(guessed - dds),
                        "dd_regret": None,
                        "investigation_required": guessed > dds,
                        "error_code": "OK" if guessed == dds else ("D_OVER_DDS_CLAIM" if guessed > dds else "D_MISSED_TRICKS"),
                    }
                    contract_count += 1
                elif task["task_type"] == "opening_lead":
                    chosen = str(prediction["card"]).upper()
                    scores = opening_scores(task, chosen)
                    best = max(scores.values())
                    result = {
                        "scores": scores,
                        "best_defense_tricks": best,
                        "optimal_cards": sorted(card for card, score in scores.items() if score == best),
                        "chosen_card": chosen,
                        "chosen_defense_tricks": scores.get(chosen),
                        "legal_or_equivalent": chosen in scores,
                        "dd_regret": None if chosen not in scores else best - scores[chosen],
                        "investigation_required": False,
                        "error_code": "OK" if scores.get(chosen) == best else "F_OPENING_LEAD_REGRET",
                    }
                    lead_count += 1
                else:
                    raise AssertionError(task["task_type"])
                upsert_result(con, task, result)
        con.commit()
        assert train_count == contract_count + lead_count
        assert contract_count > 0 and lead_count > 0

        model_path = root / "adaptive_model.json"
        trained = train_model(tasks_path, predictions_path, root / "training.sqlite3", model_path)
        assert trained["contract_samples"] == contract_count, trained
        assert trained["opening_lead_tasks"] == lead_count, trained
        model = json.loads(model_path.read_text(encoding="utf-8"))
        assert model["dds_used_during_model_training"] is True
        assert model["dds_used_during_prediction"] is False

        adaptive_out = root / "adaptive_validation.jsonl"
        summary = predict(tasks_path, model_path, adaptive_out, {"validation"})
        validation_tasks = [row for row in tasks if row["split"] == "validation"]
        adaptive_rows = load_jsonl(adaptive_out)
        assert summary["predictions"] == len(validation_tasks)
        assert summary["dds_called_during_prediction"] is False
        assert len(adaptive_rows) == len(validation_tasks)
        assert all(row["locked"] is True for row in adaptive_rows)
        assert all(row["predictor_version"] == model["model_version"] for row in adaptive_rows)

        baseline_validation = [
            baseline.prediction_for(task, "bridge-baseline-selftest-v1")
            for task in validation_tasks
        ]
        assert len(baseline_validation) == len(adaptive_rows)
        assert all(row["locked"] is True for row in baseline_validation)

        print(json.dumps({
            "ok": True,
            "train_tasks": train_count,
            "contract_samples": contract_count,
            "opening_lead_samples": lead_count,
            "validation_predictions": len(adaptive_rows),
            "baseline_directly_exercised": True,
            "adaptive_train_and_predict_exercised": True,
            "dds_called_during_prediction": False,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
