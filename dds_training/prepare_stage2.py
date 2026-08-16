from __future__ import annotations

"""Prepare Stage 2 safety artifacts without starting mass DDS training."""

import argparse
import json
from pathlib import Path

from continuation_tasks import continuation_tasks_from_line
from crossfit import annotate_file
from dds_play import analyse_line
from line_predictor import prediction_for
from shard_plan import build_shard_plan
from stage2_readiness import audit_stage2_readiness


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def prepare(work: Path, *, folds: int, shard_tasks: int, preflight_count: int, line_cards: int) -> dict:
    base_path = work / "blind_tasks.jsonl"
    if not base_path.exists():
        raise FileNotFoundError(base_path)

    crossfit_path = work / "blind_tasks_crossfit.jsonl"
    crossfit_summary = annotate_file(base_path, crossfit_path, folds=folds, seed=20260815)
    crossfit_tasks = _load_jsonl(crossfit_path)

    shard_plan = build_shard_plan(
        crossfit_tasks,
        stage="main",
        max_tasks=shard_tasks,
        selected_splits={"train", "validation", "sealed_test"},
    )
    (work / "shard_plan_main.json").write_text(
        json.dumps(shard_plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    candidates = [
        task for task in crossfit_tasks
        if task.get("split") == "train" and task.get("task_type") == "contract_tricks"
    ][:preflight_count]
    if len(candidates) < preflight_count:
        raise ValueError(f"Only {len(candidates)} contract TRAIN tasks available for preflight")

    task_path = work / "stage2_line_preflight_tasks.jsonl"
    prediction_path = work / "stage2_line_preflight_predictions.jsonl"
    predictions = []
    with task_path.open("w", encoding="utf-8") as task_out, prediction_path.open("w", encoding="utf-8") as pred_out:
        for task in candidates:
            task_out.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")
            prediction = prediction_for(
                task,
                cards_to_play=line_cards,
                predictor_version="bridge-line-baseline-v1",
            )
            predictions.append(prediction)
            pred_out.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")

    first_task = candidates[0]
    first_prediction = predictions[0]
    trajectory = analyse_line(
        deal=first_task["deal"],
        declarer=int(first_task["declarer"]),
        trump=int(first_task["strain"]),
        opening_leader=int(first_task.get("leader", (int(first_task["declarer"]) + 1) % 4)),
        cards=list(first_prediction["line"]),
    )
    trajectory_preflight = {
        "status": "ok" if not trajectory["trajectory"]["invariant_violations"] else "error",
        "task_id": first_task["task_id"],
        "cards": len(first_prediction["line"]),
        "positions": len(trajectory["projected_declarer_values"]),
        "start_value": trajectory["projected_declarer_values"][0],
        "end_value": trajectory["projected_declarer_values"][-1],
        "first_error": trajectory["trajectory"]["first_error"],
        "invariant_violations": len(trajectory["trajectory"]["invariant_violations"]),
        "line_sha256": trajectory["trajectory"]["line_sha256"],
    }
    (work / "stage2_dds_play_preflight.json").write_text(
        json.dumps(trajectory_preflight, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    continuation = []
    for task, prediction in zip(candidates, predictions):
        max_prefix = min(len(prediction["line"]), 8)
        prefixes = sorted({1, 4, 5, max_prefix})
        continuation.extend(
            continuation_tasks_from_line(
                task,
                list(prediction["line"]),
                prefix_indexes=prefixes,
                provenance="predicted_line",
            )
        )
    continuation_path = work / "stage2_continuation_preflight.jsonl"
    with continuation_path.open("w", encoding="utf-8") as handle:
        for task in continuation:
            handle.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")

    policy = {
        "schema": "dds-family-model-selection-v1",
        "separate_families": True,
        "families": ["contract_tricks", "opening_lead", "declarer_continuation", "defense_continuation"],
        "paired_bootstrap": True,
        "bootstrap_confidence": 0.95,
        "practical_minimums": {
            "contract_tricks_mae": 0.03,
            "opening_lead_regret": 0.01,
            "continuation_regret": 0.01,
        },
        "mixed_family_ensemble_allowed": True,
        "validation_only_for_selection": True,
        "sealed_test_never_selects_model": True,
    }
    (work / "family_model_selection_policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    readiness = audit_stage2_readiness(work)
    (work / "stage2_readiness.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "crossfit": crossfit_summary,
        "shards": shard_plan["shard_count"],
        "line_preflight_predictions": len(predictions),
        "continuation_preflight_tasks": len(continuation),
        "trajectory_preflight": trajectory_preflight,
        "readiness": readiness,
        "mass_training_started": False,
        "paid_api_used": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Stage 2 readiness artifacts without mass training")
    parser.add_argument("--work", required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--shard-tasks", type=int, default=2000)
    parser.add_argument("--preflight-count", type=int, default=4)
    parser.add_argument("--line-cards", type=int, default=16)
    args = parser.parse_args()
    result = prepare(
        Path(args.work),
        folds=args.folds,
        shard_tasks=args.shard_tasks,
        preflight_count=args.preflight_count,
        line_cards=args.line_cards,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["readiness"]["main_train"]["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
