from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

from continuation_eval import evaluate_continuation
from dds_engine import contract_tricks_batch, evaluate_opening_lead


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_taskmap(state_root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in state_root.rglob("*.jsonl"):
        if "task" not in path.name.lower():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                task = json.loads(line)
                task_id = task.get("task_id")
                if task_id:
                    out.setdefault(str(task_id), task)
        except Exception:
            continue
    return out


def regret_stats(rows: list[dict]) -> dict:
    vals = [x["dds_result"].get("dd_regret") for x in rows if x["dds_result"].get("dd_regret") is not None]
    return {
        "n": len(vals),
        "optimal": sum(v == 0 for v in vals),
        "optimal_rate": (sum(v == 0 for v in vals) / len(vals)) if vals else None,
        "mean_regret": statistics.fmean(vals) if vals else None,
        "regret_2plus": sum(v >= 2 for v in vals),
    }


def run(stage2b: Path, stage2c1: Path, train_state: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)

    cont_tasks = {str(x["task_id"]): x for x in read_jsonl(stage2b / "continuation_curriculum_balanced.jsonl")}
    cont_preds = read_jsonl(stage2c1 / "locked_predictions_continuations.jsonl")
    if len(cont_tasks) != 2000 or len(cont_preds) != 2000:
        raise ValueError("Stage 2C continuation scope must contain exactly 2000 tasks/predictions")
    cont_results: list[dict] = []
    for index, prediction in enumerate(cont_preds, 1):
        task = cont_tasks[str(prediction["task_id"])]
        if str(task.get("source_root_split")) != "train":
            raise ValueError("Continuation is not TRAIN-owned")
        result = evaluate_continuation(task, prediction)
        cont_results.append({
            "task_id": prediction["task_id"],
            "family_id": prediction.get("family_id"),
            "actor": task["actor"],
            "prediction": prediction,
            "dds_result": result,
        })
        if index % 250 == 0:
            print(json.dumps({"continuations_completed": index}))
    write_jsonl(out / "stage2c2_continuation_results.jsonl", cont_results)

    taskmap = load_taskmap(train_state)
    ce_preds = read_jsonl(stage2c1 / "locked_predictions_counterexamples.jsonl")
    if len(ce_preds) != 882:
        raise ValueError("Counterexample locked decision scope must be exactly 882")
    ce_results: list[dict] = []
    for index, prediction in enumerate(ce_preds, 1):
        task = taskmap.get(str(prediction["task_id"]))
        if task is None:
            raise ValueError(f"Missing counterexample task {prediction['task_id']}")
        root_split = str(task.get("source_root_split", task.get("split")))
        if root_split not in {"train", "derived"}:
            raise ValueError(f"Counterexample task is not TRAIN-derived: {root_split}")
        if task["task_type"] == "contract_tricks":
            table = contract_tricks_batch([task["deal"]])[0]
            actual = int(table[int(task["strain"])][int(task["declarer"])])
            predicted = int(prediction["tricks"])
            result = {
                "dds_tricks": actual,
                "prediction_error": predicted - actual,
                "absolute_error": abs(predicted - actual),
                "exact": predicted == actual,
            }
        elif task["task_type"] == "opening_lead":
            result = evaluate_opening_lead(
                task["deal"], int(task["strain"]), int(task["declarer"]), str(prediction["card"])
            )
        else:
            raise ValueError(f"Unsupported counterexample task type: {task['task_type']}")
        ce_results.append({
            "task_id": prediction["task_id"],
            "pair_id": prediction["counterexample_pair_id"],
            "role": prediction["counterexample_role"],
            "task_type": task["task_type"],
            "prediction": prediction,
            "dds_result": result,
        })
        if index % 200 == 0:
            print(json.dumps({"counterexample_decisions_completed": index}))
    write_jsonl(out / "stage2c2_counterexample_results.jsonl", ce_results)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in ce_results:
        grouped[str(row["pair_id"])].append(row)
    pair_rows: list[dict] = []
    for pair_id, pair in sorted(grouped.items()):
        if len(pair) != 2:
            raise ValueError(f"Counterexample pair {pair_id} does not have two decisions")
        source = next(x for x in pair if x["role"] == "source")
        variant = next(x for x in pair if x["role"] == "variant")
        if source["task_type"] == "contract_tricks":
            sdds = int(source["dds_result"]["dds_tricks"])
            vdds = int(variant["dds_result"]["dds_tricks"])
            spred = int(source["prediction"]["tricks"])
            vpred = int(variant["prediction"]["tricks"])
            truth_changed = sdds != vdds
            prediction_changed = spred != vpred
            direction_ok = ((spred - vpred) * (sdds - vdds) > 0) if truth_changed and prediction_changed else False
            passed = bool(truth_changed and prediction_changed and direction_ok)
            detail = {
                "source_dds": sdds,
                "variant_dds": vdds,
                "source_prediction": spred,
                "variant_prediction": vpred,
                "truth_changed": truth_changed,
                "prediction_changed": prediction_changed,
                "direction_ok": direction_ok,
            }
        else:
            sregret = source["dds_result"].get("dd_regret")
            vregret = variant["dds_result"].get("dd_regret")
            scard = str(source["prediction"]["card"])
            vcard = str(variant["prediction"]["card"])
            passed = sregret == 0 and vregret == 0
            detail = {
                "source_card": scard,
                "variant_card": vcard,
                "prediction_changed": scard != vcard,
                "source_regret": sregret,
                "variant_regret": vregret,
                "both_equal_optimal": passed,
            }
        pair_rows.append({"pair_id": pair_id, "task_type": source["task_type"], "blind_pair_pass": bool(passed), **detail})
    write_jsonl(out / "stage2c2_counterexample_pair_audit.jsonl", pair_rows)

    multi_tasks = read_jsonl(stage2b / "multi_contract_blueprint.jsonl")
    multi_preds = read_jsonl(stage2c1 / "locked_predictions_multicontract.jsonl")
    if len(multi_tasks) != 10000 or len(multi_preds) != 10000:
        raise ValueError("Multicontract scope must contain exactly 10000 tasks/predictions")
    pred_map = {str(x["task_id"]): x for x in multi_preds}
    families: dict[str, dict] = {}
    for task in multi_tasks:
        if str(task.get("source_root_split")) != "train":
            raise ValueError("Multicontract task is not TRAIN-owned")
        family = str(task.get("root_deal_id") or task["deal_id"])
        families.setdefault(family, {"deal": task["deal"], "tasks": []})["tasks"].append(task)
    family_items = list(families.items())
    tables: dict[str, list] = {}
    for start in range(0, len(family_items), 40):
        chunk = family_items[start:start + 40]
        matrices = contract_tricks_batch([item[1]["deal"] for item in chunk])
        for (family, _), matrix in zip(chunk, matrices):
            tables[family] = matrix
        print(json.dumps({"multicontract_families_completed": min(start + len(chunk), len(family_items)), "of": len(family_items)}))
    multi_results: list[dict] = []
    for task in multi_tasks:
        prediction = pred_map[str(task["task_id"])]
        family = str(task.get("root_deal_id") or task["deal_id"])
        actual = int(tables[family][int(task["strain"])][int(task["declarer"])])
        predicted = int(prediction["tricks"])
        multi_results.append({
            "task_id": task["task_id"],
            "family_id": family,
            "strain": int(task["strain"]),
            "declarer": int(task["declarer"]),
            "prediction": predicted,
            "dds_tricks": actual,
            "error": predicted - actual,
            "absolute_error": abs(predicted - actual),
            "exact": predicted == actual,
        })
    write_jsonl(out / "stage2c2_multicontract_results.jsonl", multi_results)

    cont_by_actor = {
        actor: regret_stats([x for x in cont_results if x["actor"] == actor])
        for actor in ("declarer", "defense")
    }
    ce_contract = [x for x in ce_results if x["task_type"] == "contract_tricks"]
    ce_lead = [x for x in ce_results if x["task_type"] == "opening_lead"]
    ce_abs = [x["dds_result"]["absolute_error"] for x in ce_contract]
    ce_summary = {
        "decisions": len(ce_results),
        "pairs": len(pair_rows),
        "pair_passes": sum(x["blind_pair_pass"] for x in pair_rows),
        "pair_pass_rate": sum(x["blind_pair_pass"] for x in pair_rows) / len(pair_rows),
        "contract_decisions": len(ce_contract),
        "contract_exact": sum(x["dds_result"]["exact"] for x in ce_contract),
        "contract_mae": statistics.fmean(ce_abs) if ce_abs else None,
        "opening_lead": regret_stats(ce_lead),
    }
    multi_abs = [x["absolute_error"] for x in multi_results]
    multi_summary = {
        "tasks": len(multi_results),
        "families": len(families),
        "exact": sum(x["exact"] for x in multi_results),
        "exact_rate": sum(x["exact"] for x in multi_results) / len(multi_results),
        "mae": statistics.fmean(multi_abs),
        "errors_2plus": sum(x >= 2 for x in multi_abs),
    }
    card_errors = [x for x in cont_results if (x["dds_result"].get("dd_regret") or 0) > 0]
    card_errors += [x for x in ce_lead if (x["dds_result"].get("dd_regret") or 0) > 0]
    summary = {
        "schema": "dds-stage2c2-train-results-v1",
        "stage": "2C.2",
        "status": "dds_train_complete",
        "locked_records_evaluated": 12882,
        "dds_called": True,
        "scope": "TRAIN-owned only",
        "validation_opened": False,
        "sealed_opened": False,
        "historical_database_mutated": False,
        "continuations": {"total": len(cont_results), "by_actor": cont_by_actor},
        "counterexamples": ce_summary,
        "multicontract": multi_summary,
        "card_level_error_positions": len(card_errors),
        "next_gate": "card_level_investigation_regression_methodology_audit",
    }
    (out / "stage2c2_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*")) if p.is_file()}
    (out / "stage2c2_digests.json").write_text(json.dumps(digests, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage2b", required=True)
    parser.add_argument("--stage2c1", required=True)
    parser.add_argument("--train-state", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    summary = run(Path(args.stage2b), Path(args.stage2c1), Path(args.train_state), Path(args.out))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
