from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def load_predictions(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("locked"):
            raise ValueError(f"Unlocked prediction in {path}: {row.get('task_id')}")
        out[row["task_id"]] = row
    return out


def load_tasks(path: Path, split: str) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("split") == split:
            out[row["task_id"]] = row
    return out


def evaluate(tasks: dict[str, dict], predictions: dict[str, dict], results: dict[str, dict]) -> dict:
    ct_total = ct_exact = ct_over = ct_under = 0
    ct_abs = 0.0
    ol_total = ol_opt = ol_illegal = ol_regret2 = 0
    ol_regret = 0.0
    missing = []

    for task_id, task in tasks.items():
        pred = predictions.get(task_id)
        result = results.get(task_id)
        if pred is None or result is None:
            missing.append(task_id)
            continue
        if task["task_type"] == "contract_tricks":
            actual = int(result["dds_tricks"])
            guessed = int(pred["tricks"])
            delta = guessed - actual
            ct_total += 1
            ct_exact += int(delta == 0)
            ct_abs += abs(delta)
            ct_over += int(delta > 0)
            ct_under += int(delta < 0)
        elif task["task_type"] == "opening_lead":
            scores = {str(k).upper(): int(v) for k, v in result.get("scores", {}).items()}
            card = str(pred["card"]).upper()
            ol_total += 1
            if card not in scores:
                ol_illegal += 1
                regret = 13.0
            else:
                best = max(scores.values())
                regret = float(best - scores[card])
                ol_opt += int(regret == 0)
                ol_regret2 += int(regret >= 2)
            ol_regret += regret

    return {
        "contract": {
            "total": ct_total,
            "exact": ct_exact,
            "exact_rate": None if not ct_total else ct_exact / ct_total,
            "mae": None if not ct_total else ct_abs / ct_total,
            "overclaims": ct_over,
            "underclaims": ct_under,
        },
        "opening_lead": {
            "total": ol_total,
            "equal_optimal": ol_opt,
            "equal_optimal_rate": None if not ol_total else ol_opt / ol_total,
            "mean_regret": None if not ol_total else ol_regret / ol_total,
            "regret_2plus": ol_regret2,
            "illegal": ol_illegal,
        },
        "missing": len(missing),
        "missing_first": missing[:5],
    }


def combined_loss(metrics: dict) -> float:
    ct = metrics["contract"]["mae"]
    ol = metrics["opening_lead"]["mean_regret"]
    if ct is None or ol is None:
        return float("inf")
    return float(ct) + float(ol)


def main() -> None:
    p = argparse.ArgumentParser(description="Compare locked blind predictors against already-opened holdout DDS facts")
    p.add_argument("--tasks", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--candidate", action="append", nargs=2, metavar=("NAME", "FILE"), required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    tasks = load_tasks(Path(args.tasks), args.split)
    con = sqlite3.connect(args.db)
    results = {
        task_id: json.loads(result_json)
        for task_id, result_json in con.execute("SELECT task_id,result_json FROM dds_results WHERE split=?", (args.split,))
    }

    models = {}
    for name, filename in args.candidate:
        preds = load_predictions(Path(filename))
        metrics = evaluate(tasks, preds, results)
        metrics["combined_loss"] = combined_loss(metrics)
        metrics["prediction_file"] = filename
        metrics["predictor_versions"] = sorted({str(x.get("predictor_version")) for x in preds.values()})
        models[name] = metrics

    ranked = sorted(models, key=lambda name: (models[name]["combined_loss"], name))
    selected = ranked[0]
    out = {
        "split": args.split,
        "task_count": len(tasks),
        "selection_rule": "lowest contract_MAE + opening_lead_mean_DD_regret; ties by name",
        "models": models,
        "selected": selected,
        "selected_prediction_file": models[selected]["prediction_file"],
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
