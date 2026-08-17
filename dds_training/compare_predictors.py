from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

BOOTSTRAP_SEED = 20260815


def load_predictions(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("locked"):
            raise ValueError(f"Unlocked prediction in {path}: {row.get('task_id')}")
        task_id = row.get("task_id")
        if not task_id:
            raise ValueError(f"Prediction without task_id in {path}")
        if task_id in out:
            raise ValueError(f"Duplicate prediction {task_id} in {path}")
        out[task_id] = row
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


def _task_loss(task: dict, prediction: dict, result: dict) -> float:
    if task["task_type"] == "contract_tricks":
        return float(abs(int(prediction["tricks"]) - int(result["dds_tricks"])))
    if task["task_type"] == "opening_lead":
        scores = {str(k).upper(): int(v) for k, v in result.get("scores", {}).items()}
        card = str(prediction["card"]).upper()
        if card not in scores:
            return 13.0
        return float(max(scores.values()) - scores[card])
    raise ValueError(f"Unsupported task type: {task['task_type']}")


def per_task_losses(
    tasks: dict[str, dict],
    predictions: dict[str, dict],
    results: dict[str, dict],
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {"contract_tricks": {}, "opening_lead": {}}
    for task_id, task in tasks.items():
        pred = predictions.get(task_id)
        result = results.get(task_id)
        if pred is None or result is None:
            continue
        out[task["task_type"]][task_id] = _task_loss(task, pred, result)
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


def paired_bootstrap_difference(
    candidate: dict[str, float],
    baseline: dict[str, float],
    *,
    repetitions: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    ids = sorted(set(candidate) & set(baseline))
    if not ids:
        return {"n": 0, "mean_candidate_minus_baseline": None, "ci95": [None, None]}
    diffs = [candidate[x] - baseline[x] for x in ids]
    mean = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    samples = []
    n = len(diffs)
    for _ in range(repetitions):
        samples.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    low = samples[max(0, int(0.025 * repetitions) - 1)]
    high = samples[min(repetitions - 1, int(0.975 * repetitions))]
    return {
        "n": n,
        "mean_candidate_minus_baseline": mean,
        "ci95": [low, high],
        "bootstrap_repetitions": repetitions,
        "seed": seed,
    }


def _select_family(
    family: str,
    model_losses: dict[str, dict[str, dict[str, float]]],
    baseline_name: str,
    min_improvement: float,
    bootstrap_repetitions: int,
) -> tuple[str, dict]:
    baseline = model_losses[baseline_name][family]
    comparisons = {}
    winner = baseline_name
    winner_mean = 0.0
    for name in sorted(model_losses):
        if name == baseline_name:
            continue
        comparison = paired_bootstrap_difference(
            model_losses[name][family],
            baseline,
            repetitions=bootstrap_repetitions,
            seed=BOOTSTRAP_SEED + (0 if family == "contract_tricks" else 1),
        )
        comparisons[name] = comparison
        mean = comparison["mean_candidate_minus_baseline"]
        high = comparison["ci95"][1]
        # Lower loss is better. Switch only when the improvement is both
        # practically non-trivial and statistically paired below zero.
        if mean is not None and high is not None and mean <= -min_improvement and high < 0:
            if winner == baseline_name or mean < winner_mean:
                winner = name
                winner_mean = mean
    return winner, {
        "baseline": baseline_name,
        "selected": winner,
        "min_required_improvement": min_improvement,
        "paired_comparisons": comparisons,
    }


def write_selected_predictions(
    tasks: dict[str, dict],
    prediction_sets: dict[str, dict[str, dict]],
    selected_by_task_type: dict[str, str],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for task_id in sorted(tasks):
            task = tasks[task_id]
            selected_name = selected_by_task_type[task["task_type"]]
            pred = prediction_sets[selected_name].get(task_id)
            if pred is None:
                raise ValueError(f"Selected model {selected_name} lacks prediction {task_id}")
            row = dict(pred)
            row["selection_model"] = selected_name
            row["selection_task_type"] = task["task_type"]
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Compare locked blind predictors against opened validation DDS facts")
    p.add_argument("--tasks", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--candidate", action="append", nargs=2, metavar=("NAME", "FILE"), required=True)
    p.add_argument("--baseline-name", default="baseline")
    p.add_argument("--min-improvement", type=float, default=0.005)
    p.add_argument("--bootstrap-repetitions", type=int, default=2000)
    p.add_argument("--selected-out")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    tasks = load_tasks(Path(args.tasks), args.split)
    con = sqlite3.connect(args.db)
    results = {
        task_id: json.loads(result_json)
        for task_id, result_json in con.execute("SELECT task_id,result_json FROM dds_results WHERE split=?", (args.split,))
    }

    prediction_sets: dict[str, dict[str, dict]] = {}
    models = {}
    model_losses = {}
    for name, filename in args.candidate:
        preds = load_predictions(Path(filename))
        prediction_sets[name] = preds
        metrics = evaluate(tasks, preds, results)
        metrics["combined_loss"] = combined_loss(metrics)
        metrics["prediction_file"] = filename
        metrics["predictor_versions"] = sorted({str(x.get("predictor_version")) for x in preds.values()})
        models[name] = metrics
        model_losses[name] = per_task_losses(tasks, preds, results)

    if args.baseline_name not in models:
        raise ValueError(f"Baseline model {args.baseline_name!r} was not supplied")

    selected_by_task_type = {}
    family_selection = {}
    for family in ("contract_tricks", "opening_lead"):
        selected, details = _select_family(
            family,
            model_losses,
            args.baseline_name,
            args.min_improvement,
            args.bootstrap_repetitions,
        )
        selected_by_task_type[family] = selected
        family_selection[family] = details

    unique_selected = sorted(set(selected_by_task_type.values()))
    selected = unique_selected[0] if len(unique_selected) == 1 else "ensemble"
    selected_prediction_file = None
    if args.selected_out:
        selected_path = Path(args.selected_out)
        write_selected_predictions(tasks, prediction_sets, selected_by_task_type, selected_path)
        selected_prediction_file = str(selected_path)
    elif selected != "ensemble":
        selected_prediction_file = models[selected]["prediction_file"]

    out = {
        "split": args.split,
        "task_count": len(tasks),
        "selection_rule": (
            "paired per-task loss by family; candidate replaces baseline only when mean improvement reaches the configured "
            "minimum and the paired bootstrap 95% upper bound is below zero"
        ),
        "models": models,
        "family_selection": family_selection,
        "selected_by_task_type": selected_by_task_type,
        "selected": selected,
        "selected_prediction_file": selected_prediction_file,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
