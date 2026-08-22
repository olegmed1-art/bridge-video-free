from __future__ import annotations

"""Fresh Stage 2C.6 validation for the fixed TRAIN-only selective-defense policy.

Both earlier validation source-family waves are deterministically reconstructed
and excluded. Validation outcomes are never used for fitting/tuning. Sealed is
inaccessible. Predictions are locked before DDS and the result is evidence only.
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence

import stage2c6_train_shadow as s6
from continuation_eval import evaluate_continuation
from continuation_tasks import continuation_tasks_from_line
from line_predictor import generate_line
from stage2b_v25 import exact_balanced_curriculum

PROTOCOL = "dds-stage2c6-validation-v1"
PRIOR_PROTOCOLS = ("dds-stage2c4-validation-v1", "dds-stage2c5-validation-v1")
PRIOR_SOURCE_TOTAL = 650
SEED = 20260822
POLICY = "defense_suit_learned__defense_nt_baseline__declarer_baseline"


def verify_authorization(auth_path: Path, train_path: Path) -> tuple[dict, dict]:
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    train = json.loads(train_path.read_text(encoding="utf-8"))
    if auth.get("schema") != "dds-stage2c6-validation-authorization-v1" or auth.get("status") != "OWNER_AUTHORIZED":
        raise ValueError("Stage 2C.6 validation is not owner-authorized")
    if auth.get("authority") != "EVIDENCE_ONLY" or auth.get("candidate_policy") != POLICY:
        raise ValueError("Stage 2C.6 authorization scope mismatch")
    for key in (
        "validation_learning_allowed",
        "validation_gate_tuning_allowed",
        "historical_database_mutation_allowed",
        "automatic_promotion_allowed",
        "sealed_access_allowed",
    ):
        if auth.get(key) is not False:
            raise ValueError(f"authorization unexpectedly permits {key}")
    if train.get("schema") != "dds-stage2c6-train-shadow-v1-result" or train.get("status") != "train_shadow_complete":
        raise ValueError("Stage 2C.6 TRAIN evidence incomplete")
    if train.get("train_gate_pass") is not True or train.get("methodology_gate_pass") is not True:
        raise ValueError("Stage 2C.6 TRAIN gate did not PASS")
    if train.get("candidate_policy") != POLICY or train.get("next_gate") != "owner_decision_on_validation":
        raise ValueError("Stage 2C.6 TRAIN authority boundary mismatch")
    if train.get("validation_accessed") is not False or train.get("sealed_accessed") is not False:
        raise ValueError("Stage 2C.6 TRAIN evidence crossed protected split")
    if train.get("automatic_promotion") is not False:
        raise ValueError("Stage 2C.6 TRAIN evidence unexpectedly allows promotion")
    if train.get("locked_prediction_sha256") != auth.get("locked_train_prediction_sha256"):
        raise ValueError("Stage 2C.6 TRAIN evidence digest mismatch")
    checks = train.get("methodology_checks") or {}
    required = (
        "blind_predictions_locked_before_dds",
        "declarer_policy_identical_to_old",
        "defense_nt_policy_identical_to_old",
        "fit_prior_shadow_and_fresh_shadow_family_disjoint",
        "validation_and_sealed_excluded",
    )
    if not all(checks.get(key) is True for key in required):
        raise ValueError("Stage 2C.6 TRAIN methodology checks incomplete")
    return auth, train


def source_order(row: Mapping[str, object], protocol: str) -> tuple[str, str]:
    key = f"{protocol}:{s6.family_id(row)}:{row['task_id']}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest(), str(row["task_id"])


def first_unique(rows: Sequence[dict], total: int, protocol: str, excluded: set[str] | None = None) -> list[dict]:
    excluded = excluded or set()
    selected: list[dict] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: source_order(r, protocol)):
        fam = s6.family_id(row)
        if fam in excluded or fam in seen:
            continue
        seen.add(fam)
        selected.append(row)
        if len(selected) == total:
            break
    if len(selected) != total:
        raise ValueError(f"insufficient validation families for {protocol}: {len(selected)} < {total}")
    return selected


def fresh_validation_sources(main_tasks_path: Path, source_total: int) -> tuple[list[dict], dict]:
    contracts: list[dict] = []
    split_counts = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    sealed_task_ids: list[str] = []
    for row in s6.read_jsonl(main_tasks_path):
        split = str(row.get("split"))
        split_counts[split] += 1
        families[split].add(s6.family_id(row))
        if split == "validation" and row.get("task_type") == "contract_tricks":
            contracts.append(row)
        elif split == "sealed_test":
            sealed_task_ids.append(str(row["task_id"]))
    pairwise = {
        "train_validation": len(families.get("train", set()) & families.get("validation", set())),
        "train_sealed": len(families.get("train", set()) & families.get("sealed_test", set())),
        "validation_sealed": len(families.get("validation", set()) & families.get("sealed_test", set())),
    }
    if any(pairwise.values()):
        raise ValueError(f"main corpus family leakage: {pairwise}")
    prior4 = first_unique(contracts, PRIOR_SOURCE_TOTAL, PRIOR_PROTOCOLS[0])
    prior4_fams = {s6.family_id(row) for row in prior4}
    prior5 = first_unique(contracts, PRIOR_SOURCE_TOTAL, PRIOR_PROTOCOLS[1], prior4_fams)
    prior5_fams = {s6.family_id(row) for row in prior5}
    excluded = prior4_fams | prior5_fams
    selected = first_unique(contracts, source_total, PROTOCOL, excluded)
    selected_fams = {s6.family_id(row) for row in selected}
    if selected_fams & excluded:
        raise ValueError("fresh validation overlaps earlier validation families")
    return selected, {
        "split_counts": dict(split_counts),
        "pairwise_family_overlap": pairwise,
        "validation_families_available": len(families.get("validation", set())),
        "stage2c4_validation_source_families_excluded": len(prior4_fams),
        "stage2c5_validation_source_families_excluded": len(prior5_fams),
        "prior_validation_union_excluded": len(excluded),
        "fresh_validation_source_families": len(selected_fams),
        "prior_fresh_validation_family_overlap": len(excluded & selected_fams),
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


def prepare(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    auth, train = verify_authorization(Path(args.authorization), Path(args.train_evidence))
    fit_rows = s6.read_jsonl(Path(args.stage2b) / "continuation_curriculum_balanced.jsonl")
    fit_tasks = {str(row["task_id"]): row for row in fit_rows}
    fit_results = s6.read_jsonl(Path(args.stage2c2))
    if len(fit_tasks) != 2000 or len(fit_results) != 2000:
        raise ValueError("candidate fit requires exact 2000 TRAIN tasks/results")
    if any(str(row.get("source_root_split")) != "train" for row in fit_tasks.values()):
        raise ValueError("candidate fit is not TRAIN-only")

    sources, source_meta = fresh_validation_sources(Path(args.main_tasks), args.source_total)
    fit_families = {s6.family_id(row) for row in fit_tasks.values()}
    if fit_families & {s6.family_id(row) for row in sources}:
        raise ValueError("TRAIN fit overlaps validation families")
    curriculum, curriculum_meta = build_curriculum(sources, args.per_actor, args.line_cards)
    card_stat, card_actor = s6.train_card_loss_model(fit_tasks, fit_results)

    locked: list[dict] = []
    for task in curriculum:
        old = s6.baseline_continuation_prediction(task)["card"]
        learned = s6.candidate_card(task, card_stat, card_actor)
        actor = str(task["actor"])
        strain_class = "nt" if int(task["strain"]) == 4 else "suit"
        use_learned = actor == "defense" and strain_class == "suit"
        locked.append({
            "task_id": task["task_id"],
            "family_id": s6.family_id(task),
            "actor": actor,
            "strain_class": strain_class,
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
    s6.write_jsonl(tasks_path, curriculum)
    s6.write_jsonl(locked_path, locked)
    digest = s6.sha256_file(locked_path)
    (out / "locked_validation_predictions.sha256").write_text(f"{digest}  {locked_path.name}\n", encoding="utf-8")
    evidence = {
        "schema": f"{PROTOCOL}-preopen",
        "stage": "2C.6",
        "status": "validation_predictions_locked",
        "authority": auth["authority"],
        "owner_validation_authorized": True,
        "candidate_policy": POLICY,
        "candidate_fit_scope": "Stage 2B/2C.2 TRAIN-only",
        "candidate_fit_rows": len(fit_results),
        "policy_frozen_from_train_evidence_sha256": train["locked_prediction_sha256"],
        "prior_validation_outcomes_used_for_fitting": False,
        "prior_validation_outcomes_used_for_tuning": False,
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
        "predeclared_validation_gate": {
            "defense_policy_mean_regret_lt_old": True,
            "defense_policy_regret_2plus_lt_old": True,
            "defense_policy_optimal_rate_ge_old": True,
            "defense_suit_policy_mean_regret_lt_old": True,
            "defense_suit_policy_regret_2plus_lt_old": True,
            "defense_suit_policy_optimal_rate_ge_old": True,
            "declarer_policy_identical_to_old": True,
            "defense_nt_policy_identical_to_old": True,
        },
        "next_action": "DDS fresh validation only; sealed remains inaccessible",
    }
    s6.write_json(out / "STAGE2C6_VALIDATION_PREOPEN_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def evaluate(args: argparse.Namespace) -> None:
    prepared = Path(args.prepared)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = {str(row["task_id"]): row for row in s6.read_jsonl(prepared / "validation_tasks.jsonl")}
    locked_path = prepared / "locked_validation_predictions.jsonl"
    locked = s6.read_jsonl(locked_path)
    expected = (prepared / "locked_validation_predictions.sha256").read_text(encoding="utf-8").split()[0]
    observed = s6.sha256_file(locked_path)
    if observed != expected or len(tasks) != 2000 or len(locked) != 2000:
        raise ValueError("locked validation scope/digest mismatch")
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
            print(json.dumps({"stage2c6_validation_positions_completed": index, "of": len(locked)}))

    s6.write_jsonl(out / "stage2c6_validation_results.jsonl", results)
    by_actor = {}
    for actor in ("declarer", "defense"):
        rows = [row for row in results if row["actor"] == actor]
        by_actor[actor] = {
            "old": s6.metrics(rows, "old_regret"),
            "learned": s6.metrics(rows, "learned_regret"),
            "policy": s6.metrics(rows, "policy_regret"),
            "policy_switches": sum(bool(row["policy_use_learned"]) for row in rows),
        }
    defense_classes = {}
    for cls in ("nt", "suit"):
        rows = [row for row in results if row["actor"] == "defense" and row["strain_class"] == cls]
        defense_classes[cls] = {
            "old": s6.metrics(rows, "old_regret"),
            "learned": s6.metrics(rows, "learned_regret"),
            "policy": s6.metrics(rows, "policy_regret"),
            "policy_switches": sum(bool(row["policy_use_learned"]) for row in rows),
        }
    defense = by_actor["defense"]
    suit = defense_classes["suit"]
    declarer_identical = all(
        row["policy_card"] == row["old_card"] and row["policy_regret"] == row["old_regret"]
        for row in results if row["actor"] == "declarer"
    )
    nt_identical = all(
        row["policy_card"] == row["old_card"] and row["policy_regret"] == row["old_regret"]
        for row in results if row["actor"] == "defense" and row["strain_class"] == "nt"
    )
    outcomes = {
        "defense_policy_mean_regret_lt_old": defense["policy"]["mean_regret"] < defense["old"]["mean_regret"],
        "defense_policy_regret_2plus_lt_old": defense["policy"]["regret_2plus"] < defense["old"]["regret_2plus"],
        "defense_policy_optimal_rate_ge_old": defense["policy"]["optimal_rate"] >= defense["old"]["optimal_rate"],
        "defense_suit_policy_mean_regret_lt_old": suit["policy"]["mean_regret"] < suit["old"]["mean_regret"],
        "defense_suit_policy_regret_2plus_lt_old": suit["policy"]["regret_2plus"] < suit["old"]["regret_2plus"],
        "defense_suit_policy_optimal_rate_ge_old": suit["policy"]["optimal_rate"] >= suit["old"]["optimal_rate"],
        "declarer_policy_identical_to_old": declarer_identical,
        "defense_nt_policy_identical_to_old": nt_identical,
    }
    gate = all(outcomes.values())
    evidence = {
        "schema": f"{PROTOCOL}-result",
        "stage": "2C.6",
        "status": "validation_complete",
        "authority": "EVIDENCE_ONLY",
        "owner_validation_authorized": True,
        "candidate_policy": POLICY,
        "positions": len(results),
        "families": len({row["family_id"] for row in results}),
        "by_actor": by_actor,
        "defense_by_strain_class": defense_classes,
        "gate_outcomes": outcomes,
        "validation_gate_pass": gate,
        "predictions_locked_before_dds": True,
        "locked_prediction_sha256": observed,
        "prior_validation_outcomes_used_for_fitting": False,
        "prior_validation_outcomes_used_for_tuning": False,
        "validation_used_for_learning": False,
        "validation_used_for_gate_tuning": False,
        "sealed_accessed": False,
        "learning_allowed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "next_gate": "owner_decision_on_new_sealed" if gate else "return_to_train_new_candidate",
    }
    s6.write_json(out / "STAGE2C6_VALIDATION_EVIDENCE.json", evidence)
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
