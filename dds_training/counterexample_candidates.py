from __future__ import annotations

"""Find nearby positions whose DDS target/action changes.

Candidates are not automatically promoted to verified counterexamples.  They
must be presented as a fresh blind discrimination task first.  This prevents the
system from calling every changed perturbation a learned bridge rule.
"""

import argparse
import json
import sqlite3
from pathlib import Path


def _optimal_cards(result: dict) -> set[str]:
    if "optimal_cards" in result:
        return {str(x).upper() for x in result.get("optimal_cards", [])}
    scores = {str(k).upper(): int(v) for k, v in result.get("scores", {}).items()}
    if not scores:
        return set()
    best = max(scores.values())
    return {card for card, value in scores.items() if value == best}


def candidate_from_pair(
    source_task: dict,
    variant_task: dict,
    source_result: dict,
    variant_result: dict,
    source_prediction: dict | None = None,
) -> dict | None:
    task_type = str(source_task["task_type"])
    if task_type != str(variant_task["task_type"]):
        raise ValueError("Source and variant task types differ")
    if str(variant_task.get("evidence_type")) not in {"perturbation", "reinforcement"}:
        return None

    base = {
        "source_task_id": source_task["task_id"],
        "variant_task_id": variant_task["task_id"],
        "source_deal_id": source_task["deal_id"],
        "variant_deal_id": variant_task["deal_id"],
        "root_deal_id": variant_task.get("root_deal_id") or source_task.get("root_deal_id") or source_task["deal_id"],
        "task_type": task_type,
        "variant_kind": variant_task.get("variant_kind"),
        "perturbation": variant_task.get("perturbation"),
        "requires_blind_discrimination": True,
        "verified": False,
    }

    if task_type == "contract_tricks":
        before = int(source_result["dds_tricks"])
        after = int(variant_result["dds_tricks"])
        if before == after:
            return None
        return {
            **base,
            "skill_key": "declarer.trick_estimation",
            "change_kind": "contract_value_changed",
            "source_target": before,
            "variant_target": after,
            "magnitude": abs(after - before),
            "discrimination_prompt": (
                "A nearby legal card swap changes the double-dummy trick ceiling. "
                "Identify which changed card alters entries, tempo, control or suit development before seeing DDS."
            ),
        }

    if task_type == "opening_lead":
        source_optimal = _optimal_cards(source_result)
        variant_optimal = _optimal_cards(variant_result)
        if not source_optimal or not variant_optimal or source_optimal == variant_optimal:
            return None
        chosen = None if source_prediction is None else str(source_prediction.get("card", "")).upper()
        chosen_flip = bool(chosen and chosen in source_optimal and chosen not in variant_optimal)
        jaccard = len(source_optimal & variant_optimal) / len(source_optimal | variant_optimal)
        return {
            **base,
            "skill_key": "defense.opening_lead",
            "change_kind": "optimal_lead_set_changed",
            "source_optimal_cards": sorted(source_optimal),
            "variant_optimal_cards": sorted(variant_optimal),
            "chosen_source_card": chosen,
            "chosen_card_flipped_from_optimal": chosen_flip,
            "optimal_set_jaccard": jaccard,
            "magnitude": 1.0 - jaccard,
            "discrimination_prompt": (
                "A nearby legal card swap changes the equal-optimal opening-lead set. "
                "Explain which entry, sequence, tempo, ruff or communication feature changed."
            ),
        }
    return None


def _load_tasks(paths: list[Path]) -> dict[str, dict]:
    tasks = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                task = json.loads(line)
                tasks[str(task["task_id"])] = task
    return tasks


def extract_candidates(db_path: Path, task_paths: list[Path]) -> list[dict]:
    tasks = _load_tasks(task_paths)
    con = sqlite3.connect(db_path)
    results = {
        str(task_id): json.loads(payload)
        for task_id, payload in con.execute("SELECT task_id,result_json FROM dds_results")
    }
    predictions = {
        str(task_id): json.loads(payload)
        for task_id, payload in con.execute("SELECT task_id,prediction_json FROM predictions")
    }
    candidates = []
    for variant_id, variant in sorted(tasks.items()):
        source_id = variant.get("derived_from_task_id")
        if not source_id or str(variant.get("evidence_type")) not in {"perturbation", "reinforcement"}:
            continue
        source = tasks.get(str(source_id))
        if source is None or str(source_id) not in results or variant_id not in results:
            continue
        candidate = candidate_from_pair(
            source,
            variant,
            results[str(source_id)],
            results[variant_id],
            predictions.get(str(source_id)),
        )
        if candidate:
            candidate["candidate_id"] = f"CE-{source_id}-{variant_id}"
            candidates.append(candidate)
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract unverified counterexample candidates from DDS perturbations")
    parser.add_argument("--db", required=True)
    parser.add_argument("--tasks", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    candidates = extract_candidates(Path(args.db), [Path(x) for x in args.tasks])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in candidates:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "schema": "dds-counterexample-candidates-v1",
        "candidates": len(candidates),
        "verified": 0,
        "policy": "candidate_only_until_fresh_blind_discrimination",
        "path": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
