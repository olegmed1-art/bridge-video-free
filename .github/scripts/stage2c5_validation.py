from __future__ import annotations

"""Stage 2C.5 validation for the fixed TRAIN-only policy.

Fail-closed rules:
- fit the card-loss candidate only from immutable Stage 2B/2C.2 TRAIN evidence;
- policy is fixed before validation: learned on defense, baseline on declarer;
- exclude every source family used by the earlier Stage 2C.4 validation wave;
- lock all new validation predictions before DDS;
- never learn/tune from validation and never access sealed;
- never auto-promote.
"""

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence

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

PROTOCOL = "dds-stage2c5-validation-v1"
PRIOR_VALIDATION_PROTOCOL = "dds-stage2c4-validation-v1"
SEED = 20260821
PRIOR_SOURCE_TOTAL = 650


def family_id(row: Mapping[str, object]) -> str:
    return str(row.get("root_deal_id") or row.get("deal_id"))


def verify_authority(path: Path, train_evidence_path: Path) -> tuple[dict, dict]:
    auth = json.loads(path.read_text(encoding="utf-8"))
    train = json.loads(train_evidence_path.read_text(encoding="utf-8"))
    if auth.get("schema") != "dds-stage2c5-validation-authorization-v1" or auth.get("status") != "OWNER_AUTHORIZED":
        raise ValueError("Stage 2C.5 validation is not owner-authorized")
    if auth.get("authority") != "EVIDENCE_ONLY":
        raise ValueError("unexpected validation authority")
    scope = auth.get("validation_scope") or {}
    if scope.get("candidate_policy") != "defense_learned__declarer_baseline":
        raise ValueError("unexpected candidate policy")
    if scope.get("candidate_fit_scope") != "TRAIN-only":
        raise ValueError("candidate is not TRAIN-only")
    if any(scope.get(k) is not False for k in (
        "validation_learning_allowed",
        "validation_gate_tuning_allowed",
        "historical_database_mutation_allowed",
        "automatic_promotion_allowed",
    )):
        raise ValueError("validation mutation/promotion unexpectedly allowed")
    if (auth.get("sealed") or {}).get("access_allowed") is not False:
        raise ValueError("sealed access unexpectedly allowed")

    if train.get("status") != "train_shadow_complete":
        raise ValueError("TRAIN shadow prerequisite incomplete")
    if train.get("train_gate_pass") is not True or train.get("methodology_gate_pass") is not True:
        raise ValueError("TRAIN gate prerequisite did not PASS")
    if train.get("validation_accessed") is not False or train.get("sealed_accessed") is not False:
        raise ValueError("TRAIN evidence crossed protected split")
    if train.get("automatic_promotion") is not False or train.get("next_gate") != "owner_decision_on_validation":
        raise ValueError("TRAIN evidence authority boundary mismatch")
    if train.get("locked_prediction_sha256") != auth["prerequisite"]["locked_train_shadow_prediction_sha256"]:
        raise ValueError("TRAIN evidence digest mismatch")
    by_actor = train["by_actor"]
    if by_actor["declarer"]["policy_switches"] != 0 or by_actor["defense"]["policy_switches"] != 1000:
        raise ValueError("fixed Stage 2C.5 policy mismatch")
    return auth, train


def source_order(row: Mapping[str, object], protocol: str) -> tuple[str, str]:
    key = f"{protocol}:{family_id(row)}:{row['task_id']}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest(), str(row["task_id"])


def first_unique_families(rows: Sequence[dict], total: int, protocol: str, excluded: set[str] | None = None) -> list[dict]:
    excluded = excluded or set()
    selected: list[dict] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: source_order(r, protocol)):
        fam = family_id(row)
        if fam in excluded or fam in seen:
            continue
        seen.add(fam)
        selected.append(row)
        if len(selected) == total:
            break
    if len(selected) != total:
        raise ValueError(f"insufficient unique validation families: {len(selected)} < {total}")
    return selected


def deterministic_validation_sources(main_tasks_path: Path, source_total: int) -> tuple[list[dict], dict]:
    validation_contracts: list[dict] = []
    split_counts = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    sealed_task_ids: list[str] = []
    for raw in read_jsonl(main_tasks_path):
        split = str(raw.get("split"))
        split_counts[split] += 1
        fam = family_id(raw)
        families[split].add(fam)
        if split == "validation" and raw.get("task_type") == "contract_tricks":
            validation_contracts.append(raw)
        elif split == "sealed_test":
            sealed_task_ids.append(str(raw["task_id"]))

    pairwise = {
        "train_validation": len(families.get("train", set()) & families.get("validation", set())),
        "train_sealed": len(families.get("train", set()) & families.get("sealed_test", set())),
        "validation_sealed": len(families.get("validation", set()) & families.get("sealed_test", set())),
    }
    if any(pairwise.values()):
        raise ValueError(f"main corpus family leakage across splits: {pairwise}")

    prior_sources = first_unique_families(validation_contracts, PRIOR_SOURCE_TOTAL, PRIOR_VALIDATION_PROTOCOL)
    prior_families = {family_id(row) for row in prior_sources}
    selected = first_unique_families(validation_contracts, source_total, PROTOCOL, excluded=prior_families)
    selected_families = {family_id(row) for row in selected}
    if selected_families & prior_families:
        raise ValueError("new validation wave overlaps prior validation source families")

    return selected, {
        "split_counts": dict(split_counts),
        "pairwise_family_overlap": pairwise,
        "validation_families_available": len(families.get("validation", set())),
        "prior_validation_source_families_excluded": len(prior_families),
        "new_validation_source_families": len(selected_families),
        "prior_new_validation_family_overlap": len(prior_families & selected_families),
        "sealed_task_count": len(sealed_task_ids),
        "sealed_task_id_digest": hashlib.sha256("\n".join(sorted(sealed_task_ids)).encode("utf-8")).hexdigest(),
    }


def build_curriculum(sources: Sequence[Mapping[str, object]], per_actor: int, line_cards: int) -> tuple[list[dict], dict]:
    candidates: list[dict] = []
    for source in sources:
        line = generate_line(dict(source), cards_to_play=line_cards)
        for item in continuation_tasks_from_line(dict(source), line, provenance="predicted_line"):
            if str(item.get("source_root_split")) != "validation":
                raise ValueError("validation continuation lost source ownership")
            item["priority"] = 0.0
            item["severity"] = 0.0
            candidates.append(item)
    curriculum = exact_balanced_curriculum(candidates, per_actor=per_actor, seed=SEED)
    counts = Counter(str(row["actor"]) for row in curriculum)
    if counts != {"declarer": per_actor, "defense": per_actor}:
        raise ValueError(f"validation curriculum imbalance: {counts}")
    return curriculum, {
        "candidate_continuations": len(candidates),
        "candidate_by_actor": dict(Counter(str(row["actor"]) for row in candidates)),
        "selected_by_actor": dict(counts),
    }


def metrics(rows: Sequence[Mapping[str, object]], key: str) -> dict:
    vals = [float(row[key]) for row in rows]
    return {
        "n": len(vals),
        "optimal": sum(v == 0 for v in vals),
        "optimal_rate": sum(v == 0 for v in vals) / len(vals),
        "mean_regret": statistics.fmean(vals),
        "regret_2plus": sum(v >= 2 for v in vals),
    }


def prepare(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    auth, train_evidence = verify_authority(Path(args.authorization), Path(args.train_evidence))

    fit_rows = read_jsonl(Path(args.stage2b) / "continuation_curriculum_balanced.jsonl")
    fit_tasks = {str(row["task_id"]): row for row in fit_rows}
    fit_results = read_jsonl(Path(args.stage2c2))
    if len(fit_tasks) != 2000 or len(fit_results) != 2000:
        raise ValueError("candidate fit requires exact 2000 TRAIN tasks/results")
    if any(str(row.get("source_root_split")) != "train" for row in fit_tasks.values()):
        raise ValueError("candidate fit is not TRAIN-only")

    sources, source_meta = deterministic_validation_sources(Path(args.main_tasks), args.source_total)
    fit_families = {family_id(row) for row in fit_tasks.values()}
    source_families = {family_id(row) for row in sources}
    if fit_families & source_families:
        raise ValueError("candidate fit overlaps validation families")

    curriculum, curriculum_meta = build_curriculum(sources, args.per_actor, args.line_cards)
    card_stat, card_actor = train_card_loss_model(fit_tasks, fit_results)

    locked: list[dict] = []
    for task in curriculum:
        old = baseline_continuation_prediction(task)["card"]
        learned = candidate_card(task, card_stat, card_actor)
        actor = str(task["actor"])
        use_learned = actor == "defense"
        locked.append({
            "task_id": task["task_id"],
            "family_id": family_id(task),
            "actor": actor,
            "old_card": old,
            "learned_card": learned,
            "policy_card": learned if use_learned else old,
            "policy_use_learned": use_learned,
            "locked": True,
            "dds_called": False,
            "protocol": PROTOCOL,
        })

    tasks_path = out / "validation_tasks.jsonl"
    locked_path = out / "locked_validation_predictions.jsonl"
    write_jsonl(tasks_path, curriculum)
    write_jsonl(locked_path, locked)
    digest = sha256_file(locked_path)
    (out / "locked_validation_predictions.sha256").write_text(f"{digest}  {locked_path.name}\n", encoding="utf-8")

    evidence = {
        "schema": f"{PROTOCOL}-preopen",
        "stage": "2C.5",
        "status": "validation_predictions_locked",
        "authority": auth["authority"],
        "owner_validation_authorized": True,
        "candidate_policy": "defense_learned__declarer_baseline",
        "candidate_fit_scope": "TRAIN-only",
        "candidate_fit_rows": len(fit_results),
        "validation_opened": True,
        "validation_completed": False,
        "sealed_accessed": False,
        "learning_allowed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "source_selection": {"source_total": args.source_total, "line_cards": args.line_cards, **source_meta},
        "curriculum": {"requested_per_actor": args.per_actor, "selected_total": len(curriculum), **curriculum_meta},
        "locked_prediction_sha256": digest,
        "predictions_locked_before_dds": True,
        "dds_called": False,
        "train_shadow_locked_sha256": train_evidence["locked_prediction_sha256"],
        "predeclared_validation_gate": {
            "defense_policy_mean_regret_lt_old": True,
            "defense_policy_regret_2plus_lt_old": True,
            "defense_policy_optimal_rate_ge_old": True,
            "declarer_policy_identical_to_old": True,
        },
        "next_action": "evaluate locked validation predictions with DDS",
    }
    write_json(out / "STAGE2C5_VALIDATION_PREOPEN_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def evaluate(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = {str(row["task_id"]): row for row in read_jsonl(prepared / "validation_tasks.jsonl")}
    locked_path = prepared / "locked_validation_predictions.jsonl"
    locked = read_jsonl(locked_path)
    expected = (prepared / "locked_validation_predictions.sha256").read_text(encoding="utf-8").split()[0]
    observed = sha256_file(locked_path)
    if observed != expected:
        raise ValueError("locked validation predictions changed before DDS")
    if len(tasks) != 2000 or len(locked) != 2000:
        raise ValueError("validation scope must contain exactly 2000 positions")
    if any(str(task.get("source_root_split")) != "validation" for task in tasks.values()):
        raise ValueError("non-validation task entered validation")
    if any(not row.get("locked") or row.get("dds_called") for row in locked):
        raise ValueError("validation predictions were not locked before DDS")

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
        results.append({
            **prediction,
            "old_regret": best - scores[old],
            "learned_regret": best - scores[learned],
            "policy_regret": best - scores[policy],
            "optimal_cards": sorted(card for card, score in scores.items() if score == best),
            "dds_called": True,
        })
        if index % 250 == 0:
            print(json.dumps({"validation_positions_completed": index, "of": len(locked)}))

    write_jsonl(out / "stage2c5_validation_results.jsonl", results)
    by_actor: dict[str, dict] = {}
    for actor in ("declarer", "defense"):
        rows = [row for row in results if row["actor"] == actor]
        by_actor[actor] = {
            "old": metrics(rows, "old_regret"),
            "learned": metrics(rows, "learned_regret"),
            "policy": metrics(rows, "policy_regret"),
            "policy_switches": sum(bool(row["policy_use_learned"]) for row in rows),
        }

    defense = by_actor["defense"]
    declarer_rows = [row for row in results if row["actor"] == "declarer"]
    declarer_identical = all(
        row["policy_card"] == row["old_card"] and row["policy_regret"] == row["old_regret"]
        for row in declarer_rows
    )
    outcomes = {
        "defense_mean_regret_condition": defense["policy"]["mean_regret"] < defense["old"]["mean_regret"],
        "defense_regret_2plus_condition": defense["policy"]["regret_2plus"] < defense["old"]["regret_2plus"],
        "defense_optimal_rate_condition": defense["policy"]["optimal_rate"] >= defense["old"]["optimal_rate"],
        "declarer_identical_condition": declarer_identical,
    }
    gate = all(outcomes.values())
    evidence = {
        "schema": f"{PROTOCOL}-result",
        "stage": "2C.5",
        "status": "validation_complete",
        "authority": "EVIDENCE_ONLY",
        "owner_validation_authorized": True,
        "positions": len(results),
        "families": len({row["family_id"] for row in results}),
        "by_actor": by_actor,
        "gate_outcomes": outcomes,
        "validation_gate_pass": gate,
        "predictions_locked_before_dds": True,
        "locked_prediction_sha256": observed,
        "validation_used_for_learning": False,
        "validation_used_for_gate_tuning": False,
        "sealed_accessed": False,
        "learning_allowed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "next_gate": "owner_decision_on_new_sealed" if gate else "return_to_train_new_candidate",
    }
    write_json(out / "STAGE2C5_VALIDATION_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--main-tasks", required=True)
    p.add_argument("--stage2b", required=True)
    p.add_argument("--stage2c2", required=True)
    p.add_argument("--train-evidence", required=True)
    p.add_argument("--authorization", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--source-total", type=int, default=650)
    p.add_argument("--per-actor", type=int, default=1000)
    p.add_argument("--line-cards", type=int, default=16)
    p.set_defaults(func=prepare)
    e = sub.add_parser("evaluate")
    e.add_argument("--prepared", required=True)
    e.add_argument("--out", required=True)
    e.set_defaults(func=evaluate)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
