from __future__ import annotations

"""Calibrate bridge-analysis confidence from out-of-fold DDS losses."""

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

CONFIDENCE_ORDER = ("low", "medium", "high")


def _success(task_type: str, prediction: dict, result: dict) -> bool:
    if task_type == "contract_tricks":
        return int(prediction["tricks"]) == int(result["dds_tricks"])
    regret = result.get("dd_regret")
    return regret is not None and float(regret) == 0.0


def _loss(task_type: str, prediction: dict, result: dict) -> float:
    if task_type == "contract_tricks":
        return float(abs(int(prediction["tricks"]) - int(result["dds_tricks"])))
    regret = result.get("dd_regret")
    return 13.0 if regret is None else float(regret)


def _pav_monotonic(counts: list[int], successes: list[int]) -> list[float]:
    """Pool-adjacent-violators for nondecreasing exact-probability estimates."""
    blocks = []
    for index, (count, success) in enumerate(zip(counts, successes)):
        blocks.append({
            "start": index,
            "end": index,
            "weight": int(count),
            "success": int(success),
            "mean": 0.0 if not count else success / count,
        })
        while len(blocks) >= 2 and blocks[-2]["mean"] > blocks[-1]["mean"]:
            right = blocks.pop()
            left = blocks.pop()
            weight = left["weight"] + right["weight"]
            success_sum = left["success"] + right["success"]
            blocks.append({
                "start": left["start"],
                "end": right["end"],
                "weight": weight,
                "success": success_sum,
                "mean": 0.0 if not weight else success_sum / weight,
            })
    values = [0.0] * len(counts)
    for block in blocks:
        for index in range(block["start"], block["end"] + 1):
            values[index] = float(block["mean"])
    return values


def fit_calibrator(rows: list[dict], *, minimum_count: int = 20) -> dict:
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        task_type = str(row["task_type"])
        confidence = str(row["prediction"].get("confidence", "unknown")).lower()
        if confidence not in CONFIDENCE_ORDER:
            confidence = "low"
        grouped[task_type][confidence].append(row)

    families = {}
    for task_type, levels in sorted(grouped.items()):
        counts = [len(levels.get(level, [])) for level in CONFIDENCE_ORDER]
        successes = [
            sum(_success(task_type, row["prediction"], row["result"]) for row in levels.get(level, []))
            for level in CONFIDENCE_ORDER
        ]
        calibrated = _pav_monotonic(counts, successes)
        mapping = {}
        all_losses = []
        brier_terms = []
        for index, level in enumerate(CONFIDENCE_ORDER):
            level_rows = levels.get(level, [])
            losses = [_loss(task_type, row["prediction"], row["result"]) for row in level_rows]
            all_losses.extend(losses)
            p = calibrated[index]
            for row in level_rows:
                y = 1.0 if _success(task_type, row["prediction"], row["result"]) else 0.0
                brier_terms.append((p - y) ** 2)
            mapping[level] = {
                "n": counts[index],
                "successes": successes[index],
                "raw_exact_rate": None if counts[index] == 0 else successes[index] / counts[index],
                "calibrated_exact_probability": p,
                "mean_loss": None if not losses else sum(losses) / len(losses),
                "supported": counts[index] >= minimum_count,
            }
        families[task_type] = {
            "mapping": mapping,
            "observations": sum(counts),
            "mean_loss": None if not all_losses else sum(all_losses) / len(all_losses),
            "brier": None if not brier_terms else sum(brier_terms) / len(brier_terms),
            "monotonic": all(
                calibrated[i] <= calibrated[i + 1] + 1e-12
                for i in range(len(calibrated) - 1)
            ),
        }
    return {
        "schema": "dds-confidence-calibration-v1",
        "source": "out_of_fold_train_only",
        "minimum_count": minimum_count,
        "families": families,
    }


def rows_from_db(
    db_path: Path,
    *,
    tasks_path: Path | None = None,
    require_oof: bool = True,
) -> list[dict]:
    task_meta = {}
    if tasks_path and tasks_path.exists():
        for line in tasks_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                task = json.loads(line)
                task_meta[str(task["task_id"])] = task

    con = sqlite3.connect(db_path)
    rows = []
    for task_id, task_type, split, pred_json, result_json in con.execute(
        """
        SELECT p.task_id,p.task_type,p.split,p.prediction_json,r.result_json
        FROM predictions p JOIN dds_results r ON r.task_id=p.task_id
        WHERE p.split='train'
        """
    ):
        prediction = json.loads(pred_json)
        meta = task_meta.get(str(task_id), {})
        is_oof = bool(
            prediction.get("out_of_fold")
            or meta.get("out_of_fold")
            or meta.get("crossfit_role") == "heldout"
        )
        if require_oof and not is_oof:
            continue
        rows.append({
            "task_id": task_id,
            "task_type": task_type,
            "split": split,
            "prediction": prediction,
            "result": json.loads(result_json),
            "out_of_fold": is_oof,
        })
    return rows


def apply_calibration(prediction: dict, task_type: str, calibrator: dict, *, review_threshold: float = 0.65) -> dict:
    out = dict(prediction)
    confidence = str(out.get("confidence", "low")).lower()
    if confidence not in CONFIDENCE_ORDER:
        confidence = "low"
    family = calibrator.get("families", {}).get(task_type)
    if not family:
        probability = 0.0
        supported = False
    else:
        record = family["mapping"].get(confidence, {})
        probability = float(record.get("calibrated_exact_probability", 0.0))
        supported = bool(record.get("supported", False))
    out["confidence_probability"] = probability
    out["confidence_calibration_schema"] = calibrator.get("schema")
    out["confidence_supported"] = supported
    out["requires_human_or_deeper_review"] = (not supported) or probability < review_threshold
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit monotonic confidence calibration from OOF TRAIN DDS facts")
    parser.add_argument("--db", required=True)
    parser.add_argument("--tasks")
    parser.add_argument("--out", required=True)
    parser.add_argument("--minimum-count", type=int, default=20)
    parser.add_argument("--allow-in-sample", action="store_true")
    args = parser.parse_args()
    rows = rows_from_db(
        Path(args.db),
        tasks_path=None if not args.tasks else Path(args.tasks),
        require_oof=not args.allow_in_sample,
    )
    if not rows:
        raise SystemExit("No eligible calibration rows; provide out-of-fold TRAIN predictions")
    calibration = fit_calibrator(rows, minimum_count=args.minimum_count)
    calibration["rows"] = len(rows)
    Path(args.out).write_text(json.dumps(calibration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(calibration, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
