from __future__ import annotations

"""Stage 2C.5: independent TRAIN-only shadow gate after sealed rejection.

This cycle is intentionally blind to validation and sealed outcomes. It fits the
existing card-loss candidate only from immutable Stage 2C.2 TRAIN results, narrows
its deployment policy to defense only based on pre-validation Stage 2C.4 TRAIN
regression evidence, then evaluates locked predictions on previously unused TRAIN
families. No result is written back into the historical database and no automatic
promotion is allowed.
"""

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from continuation_eval import evaluate_continuation
from continuation_tasks import continuation_tasks_from_line
from line_predictor import generate_line
from stage2b_v25 import exact_balanced_curriculum
from stage2c4_train_helpers import (
    baseline_continuation_prediction,
    candidate_card,
    read_jsonl,
    sha256_file,
    train_card_loss_model,
    write_json,
    write_jsonl,
)

PROTOCOL = "dds-stage2c5-train-shadow-v1"
SEED = 20260821


def family_id(row: Mapping[str, object]) -> str:
    return str(row.get("root_deal_id") or row.get("deal_id"))


def verify_train_policy_prerequisite(path: Path) -> dict:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("status") != "blind_regression_complete":
        raise ValueError("Stage 2C.4 TRAIN regression prerequisite is not complete")
    if evidence.get("new_dds_calls") is not False:
        raise ValueError("unexpected Stage 2C.4 DDS mutation")
    if evidence.get("validation_opened") is not False or evidence.get("sealed_opened") is not False:
        raise ValueError("policy prerequisite must precede validation/sealed")
    defense = evidence["continuations"]["defense"]
    if not (defense["candidate"]["mean_regret"] < defense["old"]["mean_regret"]):
        raise ValueError("TRAIN evidence does not support defense candidate mean-regret improvement")
    if not (defense["candidate"]["regret_2plus"] < defense["old"]["regret_2plus"]):
        raise ValueError("TRAIN evidence does not support defense severe-error improvement")
    return evidence


def deterministic_train_sources(
    main_tasks_path: Path,
    excluded_families: set[str],
    source_total: int,
) -> tuple[list[dict], dict]:
    train_contracts: list[dict] = []
    split_counts = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    for raw in read_jsonl(main_tasks_path):
        split = str(raw.get("split"))
        split_counts[split] += 1
        fam = family_id(raw)
        families[split].add(fam)
        if split == "train" and raw.get("task_type") == "contract_tricks" and fam not in excluded_families:
            train_contracts.append(raw)

    overlaps = {
        "train_validation": len(families.get("train", set()) & families.get("validation", set())),
        "train_sealed": len(families.get("train", set()) & families.get("sealed_test", set())),
        "validation_sealed": len(families.get("validation", set()) & families.get("sealed_test", set())),
    }
    if any(overlaps.values()):
        raise ValueError(f"main corpus family leakage across splits: {overlaps}")

    train_contracts.sort(
        key=lambda row: (
            hashlib.sha256(f"{PROTOCOL}:{family_id(row)}:{row['task_id']}".encode("utf-8")).hexdigest(),
            str(row["task_id"]),
        )
    )
    selected: list[dict] = []
    seen: set[str] = set()
    for row in train_contracts:
        fam = family_id(row)
        if fam in seen:
            continue
        seen.add(fam)
        selected.append(row)
        if len(selected) == source_total:
            break
    if len(selected) != source_total:
        raise ValueError(f"insufficient unused TRAIN contract families: {len(selected)} < {source_total}")
    if excluded_families & {family_id(row) for row in selected}:
        raise ValueError("new TRAIN wave overlaps candidate-fitting families")

    return selected, {
        "split_counts": dict(split_counts),
        "train_families_available": len(families.get("train", set())),
        "validation_families_preserved": len(families.get("validation", set())),
        "sealed_families_preserved": len(families.get("sealed_test", set())),
        "excluded_fit_families": len(excluded_families),
        "selected_source_families": len(seen),
        "pairwise_family_overlap": overlaps,
    }


def build_train_curriculum(
    sources: Sequence[Mapping[str, object]],
    per_actor: int,
    line_cards: int,
) -> tuple[list[dict], dict]:
    candidates: list[dict] = []
    for source in sources:
        line = generate_line(dict(source), cards_to_play=line_cards)
        for item in continuation_tasks_from_line(dict(source), line, provenance="predicted_line"):
            if str(item.get("source_root_split")) != "train":
                raise ValueError("TRAIN continuation lost source split ownership")
            item["priority"] = 0.0
            item["severity"] = 0.0
            candidates.append(item)
    curriculum = exact_balanced_curriculum(candidates, per_actor=per_actor, seed=SEED)
    counts = Counter(str(row["actor"]) for row in curriculum)
    if counts != {"declarer": per_actor, "defense": per_actor}:
        raise ValueError(f"TRAIN shadow curriculum imbalance: {counts}")
    return curriculum, {
        "candidate_continuations": len(candidates),
        "candidate_by_actor": dict(Counter(str(row["actor"]) for row in candidates)),
        "selected_by_actor": dict(counts),
    }


def metrics(rows: Sequence[Mapping[str, object]], key: str) -> dict:
    vals = [float(row[key]) for row in rows]
    return {
        "n": len(vals),
        "optimal": sum(value == 0 for value in vals),
        "optimal_rate": sum(value == 0 for value in vals) / len(vals),
        "mean_regret": statistics.fmean(vals),
        "regret_2plus": sum(value >= 2 for value in vals),
    }


def prepare(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    prereq = verify_train_policy_prerequisite(Path(args.train_policy_evidence))
    fit_tasks_rows = read_jsonl(Path(args.stage2b) / "continuation_curriculum_balanced.jsonl")
    fit_tasks = {str(row["task_id"]): row for row in fit_tasks_rows}
    if len(fit_tasks) != 2000:
        raise ValueError(f"expected 2000 Stage 2B TRAIN tasks, got {len(fit_tasks)}")
    if any(str(row.get("source_root_split")) != "train" for row in fit_tasks.values()):
        raise ValueError("candidate-fitting tasks are not TRAIN-only")
    fit_results = read_jsonl(Path(args.stage2c2))
    if len(fit_results) != 2000:
        raise ValueError(f"expected 2000 Stage 2C.2 TRAIN results, got {len(fit_results)}")

    excluded_families = {family_id(row) for row in fit_tasks.values()}
    sources, source_meta = deterministic_train_sources(Path(args.main_tasks), excluded_families, args.source_total)
    curriculum, curriculum_meta = build_train_curriculum(sources, args.per_actor, args.line_cards)
    selected_families = {family_id(row) for row in curriculum}
    overlap = excluded_families & selected_families
    if overlap:
        raise ValueError(f"candidate-fit/TRAIN-shadow family overlap: {sorted(overlap)[:10]}")

    card_stat, card_actor = train_card_loss_model(fit_tasks, fit_results)
    locked: list[dict] = []
    for task in curriculum:
        old = baseline_continuation_prediction(task)["card"]
        learned = candidate_card(task, card_stat, card_actor)
        actor = str(task["actor"])
        use_learned = actor == "defense"
        policy = learned if use_learned else old
        locked.append(
            {
                "task_id": task["task_id"],
                "family_id": family_id(task),
                "actor": actor,
                "old_card": old,
                "learned_card": learned,
                "policy_card": policy,
                "policy_use_learned": use_learned,
                "locked": True,
                "dds_called": False,
                "protocol": PROTOCOL,
            }
        )

    tasks_path = out / "train_shadow_tasks.jsonl"
    locked_path = out / "locked_train_shadow_predictions.jsonl"
    write_jsonl(tasks_path, curriculum)
    write_jsonl(locked_path, locked)
    digest = sha256_file(locked_path)
    (out / "locked_train_shadow_predictions.sha256").write_text(
        f"{digest}  {locked_path.name}\n", encoding="utf-8"
    )

    evidence = {
        "schema": f"{PROTOCOL}-preopen",
        "stage": "2C.5",
        "status": "train_shadow_predictions_locked",
        "authority": "EVIDENCE_ONLY",
        "candidate_policy": "defense_learned__declarer_baseline",
        "policy_basis": {
            "source": "Stage 2C.4 blind regression TRAIN-only evidence",
            "validation_used": False,
            "sealed_used": False,
            "defense_old_mean_regret": prereq["continuations"]["defense"]["old"]["mean_regret"],
            "defense_candidate_mean_regret": prereq["continuations"]["defense"]["candidate"]["mean_regret"],
            "defense_old_regret_2plus": prereq["continuations"]["defense"]["old"]["regret_2plus"],
            "defense_candidate_regret_2plus": prereq["continuations"]["defense"]["candidate"]["regret_2plus"],
        },
        "candidate_fit_scope": "TRAIN-only",
        "candidate_fit_rows": len(fit_results),
        "candidate_fit_families": len(excluded_families),
        "shadow_scope": "previously unused TRAIN families",
        "source_selection": {"source_total": args.source_total, "line_cards": args.line_cards, **source_meta},
        "curriculum": {"requested_per_actor": args.per_actor, "selected_total": len(curriculum), **curriculum_meta},
        "fit_shadow_family_overlap": len(overlap),
        "locked_prediction_sha256": digest,
        "predictions_locked_before_dds": True,
        "dds_called": False,
        "validation_accessed": False,
        "sealed_accessed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "predeclared_train_gate": {
            "defense_policy_mean_regret_lt_old": True,
            "defense_policy_regret_2plus_lt_old": True,
            "defense_policy_optimal_rate_ge_old": True,
            "declarer_policy_identical_to_old": True,
        },
        "next_action": "evaluate locked predictions with DDS on TRAIN shadow only",
    }
    write_json(out / "STAGE2C5_TRAIN_PREOPEN_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def evaluate(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = {str(row["task_id"]): row for row in read_jsonl(prepared / "train_shadow_tasks.jsonl")}
    locked_path = prepared / "locked_train_shadow_predictions.jsonl"
    locked = read_jsonl(locked_path)
    expected = (prepared / "locked_train_shadow_predictions.sha256").read_text(encoding="utf-8").split()[0]
    observed = sha256_file(locked_path)
    if expected != observed:
        raise ValueError("locked TRAIN shadow predictions changed before DDS")
    if len(tasks) != 2000 or len(locked) != 2000:
        raise ValueError("TRAIN shadow scope must contain exactly 2000 positions")
    if any(str(task.get("source_root_split")) != "train" for task in tasks.values()):
        raise ValueError("non-TRAIN task entered TRAIN shadow evaluation")
    if any(not row.get("locked") or row.get("dds_called") for row in locked):
        raise ValueError("TRAIN shadow predictions were not locked before DDS")

    results: list[dict] = []
    for index, prediction in enumerate(locked, 1):
        task = tasks[str(prediction["task_id"])]
        probe = evaluate_continuation(task, {"card": prediction["old_card"]})
        scores = probe.get("candidate_scores") or probe.get("scores") or probe.get("card_scores") or {}
        scores = {str(card).upper(): float(score) for card, score in scores.items()}
        if not scores:
            raise ValueError(f"DDS candidate scores missing for {prediction['task_id']}")
        best = max(scores.values())
        old = str(prediction["old_card"]).upper()
        learned = str(prediction["learned_card"]).upper()
        policy = str(prediction["policy_card"]).upper()
        for card in (old, learned, policy):
            if card not in scores:
                raise ValueError(f"locked legal card {card} missing from DDS score table")
        results.append(
            {
                **prediction,
                "old_regret": best - scores[old],
                "learned_regret": best - scores[learned],
                "policy_regret": best - scores[policy],
                "optimal_cards": sorted(card for card, score in scores.items() if score == best),
                "dds_called": True,
            }
        )
        if index % 250 == 0:
            print(json.dumps({"train_shadow_positions_completed": index, "of": len(locked)}))

    write_jsonl(out / "stage2c5_train_shadow_results.jsonl", results)
    by_actor: dict[str, dict] = {}
    for actor in ("declarer", "defense"):
        actor_rows = [row for row in results if row["actor"] == actor]
        by_actor[actor] = {
            "old": metrics(actor_rows, "old_regret"),
            "learned": metrics(actor_rows, "learned_regret"),
            "policy": metrics(actor_rows, "policy_regret"),
            "policy_switches": sum(bool(row["policy_use_learned"]) for row in actor_rows),
        }

    defense = by_actor["defense"]
    declarer = by_actor["declarer"]
    declarer_identical = all(
        row["policy_card"] == row["old_card"] and row["policy_regret"] == row["old_regret"]
        for row in results if row["actor"] == "declarer"
    )
    gate = (
        defense["policy"]["mean_regret"] < defense["old"]["mean_regret"]
        and defense["policy"]["regret_2plus"] < defense["old"]["regret_2plus"]
        and defense["policy"]["optimal_rate"] >= defense["old"]["optimal_rate"]
        and declarer_identical
    )

    evidence = {
        "schema": f"{PROTOCOL}-result",
        "stage": "2C.5",
        "status": "train_shadow_complete",
        "authority": "EVIDENCE_ONLY",
        "positions": len(results),
        "families": len({row["family_id"] for row in results}),
        "by_actor": by_actor,
        "predictions_locked_before_dds": True,
        "locked_prediction_sha256": observed,
        "validation_accessed": False,
        "sealed_accessed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "methodology_checks": {
            "candidate_fit_and_shadow_family_disjoint": True,
            "blind_predictions_locked_before_dds": True,
            "declarer_policy_identical_to_old": declarer_identical,
            "validation_and_sealed_excluded": True,
        },
        "train_gate_pass": bool(gate),
        "methodology_gate_pass": bool(gate),
        "next_gate": "owner_decision_on_validation" if gate else "return_to_train_new_candidate",
    }
    write_json(out / "STAGE2C5_TRAIN_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--main-tasks", required=True)
    prep.add_argument("--stage2b", required=True)
    prep.add_argument("--stage2c2", required=True)
    prep.add_argument("--train-policy-evidence", required=True)
    prep.add_argument("--out", required=True)
    prep.add_argument("--source-total", type=int, default=650)
    prep.add_argument("--per-actor", type=int, default=1000)
    prep.add_argument("--line-cards", type=int, default=16)
    prep.set_defaults(func=prepare)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--prepared", required=True)
    ev.add_argument("--out", required=True)
    ev.set_defaults(func=evaluate)
    return p


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
