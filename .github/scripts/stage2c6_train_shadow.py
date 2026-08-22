from __future__ import annotations

"""Stage 2C.6: fresh TRAIN-only selective defense shadow gate.

The candidate is derived only from TRAIN evidence that predates the failed Stage
2C.5 validation. Validation is rejection evidence only and is never read here.
The Stage 2C.5 TRAIN shadow is used to choose a coarse deployment class (suit vs
NT defense). The Stage 2C.6 evaluation then runs on a new family-disjoint TRAIN
wave, excluding both model-fit families and all Stage 2C.5 shadow families.
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

SUITS = "SHDC"
RANKS = "AKQJT98765432"
RANK_VALUE = {rank: 14 - i for i, rank in enumerate(RANKS)}
CARD_MIN_SUPPORT = 8
PROTOCOL = "dds-stage2c6-train-shadow-v1"
SEED = 20260821


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def family_id(row: Mapping[str, object]) -> str:
    return str(row.get("root_deal_id") or row.get("deal_id"))


def parse_deal(pbn: str) -> dict[int, list[str]]:
    seats = "NESW"
    start = seats.index(pbn[0].upper())
    raw = pbn[2:].split()
    if len(raw) != 4:
        raise ValueError(f"bad PBN deal: {pbn!r}")
    out: dict[int, list[str]] = {}
    for i, hand in enumerate(raw):
        suits = hand.split(".")
        if len(suits) != 4:
            raise ValueError(f"bad PBN hand: {hand!r}")
        out[(start + i) % 4] = suits
    return out


def legal_cards(hand: Sequence[str], current: Sequence[Mapping[str, object]]) -> list[tuple[int, str]]:
    cards = [(suit, rank) for suit, holding in enumerate(hand) for rank in holding]
    if current:
        led = SUITS.index(str(current[0]["card"])[0].upper())
        follow = [card for card in cards if card[0] == led]
        if follow:
            return follow
    return cards


def token(card: tuple[int, str]) -> str:
    return f"{SUITS[card[0]]}{card[1]}"


def current_winner(current: Sequence[Mapping[str, object]], add: tuple[int, tuple[int, str]], trump: int) -> int:
    cards = [
        (int(x["seat"]), SUITS.index(str(x["card"])[0].upper()), str(x["card"])[1].upper())
        for x in current
    ]
    cards.append((int(add[0]), int(add[1][0]), str(add[1][1]).upper()))
    led = cards[0][1]
    trumps = [x for x in cards if trump != 4 and x[1] == trump]
    eligible = trumps if trumps else [x for x in cards if x[1] == led]
    return max(eligible, key=lambda x: RANK_VALUE[x[2]])[0]


def baseline_continuation_prediction(task: Mapping[str, object]) -> dict:
    hands = parse_deal(str(task["remaining_deal"]))
    seat = int(task["next_seat"])
    hand = hands[seat]
    current = list(task.get("current_trick") or [])
    legal = legal_cards(hand, current)
    trump = int(task["strain"])
    scored = []
    for card in legal:
        win = int(bool(current) and current_winner(current, (seat, card), trump) == seat)
        length = len(hand[card[0]])
        sequence = int(
            card[1] in "AKQJT9"
            and any(RANK_VALUE.get(other, 0) == RANK_VALUE[card[1]] - 1 for other in hand[card[0]])
        )
        score = (
            2.0 * win
            + 0.18 * length
            + 0.20 * sequence
            - 0.015 * RANK_VALUE[card[1]]
            - (0.35 if trump != 4 and card[0] == trump and not current else 0.0)
        )
        scored.append((score, card))
    if not scored:
        raise ValueError(f"no legal continuation cards: {task['task_id']}")
    scored.sort(key=lambda x: (-x[0], x[1][0], -RANK_VALUE[x[1][1]]))
    return {"task_id": task["task_id"], "card": token(scored[0][1]), "locked": True, "dds_called": False}


def card_feature(task: Mapping[str, object], card: tuple[int, str]) -> tuple:
    hand = parse_deal(str(task["remaining_deal"]))[int(task["next_seat"])]
    current = task.get("current_trick") or []
    strain = int(task["strain"])
    actor = str(task["actor"])
    follow = "follow" if current else "lead"
    suit, rank = card
    suit_len = min(5, len(hand[suit]))
    rank_bucket = "honor" if rank in "AKQJ" else "mid" if rank in "T987" else "low"
    trump_class = "nt" if strain == 4 else ("trump" if suit == strain else "side")
    return actor, "nt" if strain == 4 else "suit", follow, suit_len, rank_bucket, trump_class


def train_card_loss_model(
    train_tasks: Mapping[str, Mapping[str, object]],
    train_results: Sequence[Mapping[str, object]],
) -> tuple[dict, dict]:
    stat: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
    actor_stat: dict[str, list] = defaultdict(lambda: [0.0, 0])
    used = 0
    for row in train_results:
        task = train_tasks.get(str(row["task_id"]))
        if task is None:
            raise ValueError(f"TRAIN continuation task missing for result {row['task_id']}")
        result = row["dds_result"]
        scores = result.get("candidate_scores") or result.get("scores") or result.get("card_scores") or {}
        scores = {str(card).upper(): float(score) for card, score in scores.items()}
        if not scores:
            raise ValueError(f"TRAIN continuation result has no candidate scores: {row['task_id']}")
        best = max(scores.values())
        actor = str(task["actor"])
        for card_name, score in scores.items():
            card = (SUITS.index(card_name[0]), card_name[1])
            feature = card_feature(task, card)
            loss = best - score
            stat[feature][0] += loss
            stat[feature][1] += 1
            actor_stat[actor][0] += loss
            actor_stat[actor][1] += 1
        used += 1
    if used != 2000:
        raise ValueError(f"expected 2000 TRAIN continuation results, got {used}")
    return stat, actor_stat


def candidate_card(task: Mapping[str, object], stat: Mapping[tuple, Sequence], actor_stat: Mapping[str, Sequence]) -> str:
    hand = parse_deal(str(task["remaining_deal"]))[int(task["next_seat"])]
    current = task.get("current_trick") or []
    actor = str(task["actor"])
    actor_total, actor_n = actor_stat.get(actor, (0.0, 0))
    fallback = float(actor_total) / int(actor_n) if int(actor_n) else 1.0
    ranked = []
    for card in legal_cards(hand, current):
        total, n = stat.get(card_feature(task, card), (0.0, 0))
        estimate = float(total) / int(n) if int(n) >= CARD_MIN_SUPPORT else fallback
        tie = 0.001 * (RANK_VALUE[card[1]] / 14.0) + 0.0001 * card[0]
        ranked.append((estimate + tie, card))
    if not ranked:
        raise ValueError(f"no legal candidate cards: {task['task_id']}")
    ranked.sort(key=lambda x: (x[0], x[1][0], -RANK_VALUE[x[1][1]]))
    return token(ranked[0][1])


def metrics_values(vals: Sequence[float]) -> dict:
    if not vals:
        return {"n": 0, "optimal": 0, "optimal_rate": 0.0, "mean_regret": None, "regret_2plus": 0}
    return {
        "n": len(vals),
        "optimal": sum(value == 0 for value in vals),
        "optimal_rate": sum(value == 0 for value in vals) / len(vals),
        "mean_regret": statistics.fmean(vals),
        "regret_2plus": sum(value >= 2 for value in vals),
    }


def metrics(rows: Sequence[Mapping[str, object]], key: str) -> dict:
    return metrics_values([float(row[key]) for row in rows])


def verify_stage2c5_train_evidence(path: Path) -> dict:
    e = json.loads(path.read_text(encoding="utf-8"))
    if e.get("status") != "train_shadow_complete" or e.get("train_gate_pass") is not True:
        raise ValueError("Stage 2C.5 TRAIN shadow prerequisite did not PASS")
    if e.get("methodology_gate_pass") is not True:
        raise ValueError("Stage 2C.5 TRAIN methodology prerequisite did not PASS")
    if e.get("validation_accessed") is not False or e.get("sealed_accessed") is not False:
        raise ValueError("Stage 2C.5 policy basis is not TRAIN-only")
    if e.get("automatic_promotion") is not False:
        raise ValueError("Stage 2C.5 evidence unexpectedly allows automatic promotion")
    return e


def derive_train_only_deployment(
    prior_tasks: Sequence[Mapping[str, object]],
    prior_results: Sequence[Mapping[str, object]],
) -> tuple[dict[str, bool], dict]:
    task_by_id = {str(row["task_id"]): row for row in prior_tasks}
    if len(prior_tasks) != 2000 or len(prior_results) != 2000:
        raise ValueError("Stage 2C.5 TRAIN artifact must contain exactly 2000 tasks/results")
    if any(str(row.get("source_root_split")) != "train" for row in prior_tasks):
        raise ValueError("Stage 2C.5 prior tasks are not TRAIN-owned")

    details: dict[str, dict] = {}
    enabled: dict[str, bool] = {}
    for cls, predicate in {
        "nt": lambda t: int(t["strain"]) == 4,
        "suit": lambda t: int(t["strain"]) != 4,
    }.items():
        rows = []
        for row in prior_results:
            if str(row.get("actor")) != "defense":
                continue
            task = task_by_id.get(str(row["task_id"]))
            if task is None:
                raise ValueError(f"prior TRAIN task missing: {row['task_id']}")
            if predicate(task):
                rows.append(row)
        old = metrics(rows, "old_regret")
        learned = metrics(rows, "learned_regret")
        passes = (
            learned["mean_regret"] < old["mean_regret"]
            and learned["regret_2plus"] < old["regret_2plus"]
            and learned["optimal_rate"] >= old["optimal_rate"]
        )
        enabled[cls] = bool(passes)
        details[cls] = {"old": old, "learned": learned, "all_three_train_conditions": bool(passes)}

    if enabled != {"nt": False, "suit": True}:
        raise ValueError(f"unexpected TRAIN-only deployment classification: {enabled}")
    return enabled, details


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

    train_contracts.sort(key=lambda row: (
        hashlib.sha256(f"{PROTOCOL}:{family_id(row)}:{row['task_id']}".encode("utf-8")).hexdigest(),
        str(row["task_id"]),
    ))
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
        raise ValueError(f"insufficient fresh TRAIN contract families: {len(selected)} < {source_total}")
    if excluded_families & {family_id(row) for row in selected}:
        raise ValueError("fresh TRAIN wave overlaps excluded fitting/tuning families")
    return selected, {
        "split_counts": dict(split_counts),
        "train_families_available": len(families.get("train", set())),
        "validation_families_preserved": len(families.get("validation", set())),
        "sealed_families_preserved": len(families.get("sealed_test", set())),
        "excluded_families": len(excluded_families),
        "selected_source_families": len(seen),
        "pairwise_family_overlap": overlaps,
    }


def build_curriculum(sources: Sequence[Mapping[str, object]], per_actor: int, line_cards: int) -> tuple[list[dict], dict]:
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
        raise ValueError(f"Stage 2C.6 curriculum imbalance: {counts}")
    return curriculum, {
        "candidate_continuations": len(candidates),
        "candidate_by_actor": dict(Counter(str(row["actor"]) for row in candidates)),
        "selected_by_actor": dict(counts),
    }


def prepare(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prereq = verify_stage2c5_train_evidence(Path(args.stage2c5_train_evidence))

    fit_tasks_rows = read_jsonl(Path(args.stage2b) / "continuation_curriculum_balanced.jsonl")
    fit_tasks = {str(row["task_id"]): row for row in fit_tasks_rows}
    if len(fit_tasks) != 2000 or any(str(row.get("source_root_split")) != "train" for row in fit_tasks.values()):
        raise ValueError("candidate-fitting Stage 2B tasks are not exactly 2000 TRAIN-only rows")
    fit_results = read_jsonl(Path(args.stage2c2))
    if len(fit_results) != 2000:
        raise ValueError("candidate-fitting Stage 2C.2 results are not exactly 2000 rows")

    prior_tasks = read_jsonl(Path(args.stage2c5_prior) / "prepared/train_shadow_tasks.jsonl")
    prior_results = read_jsonl(Path(args.stage2c5_prior) / "result/stage2c5_train_shadow_results.jsonl")
    enabled, deployment_evidence = derive_train_only_deployment(prior_tasks, prior_results)

    fit_families = {family_id(row) for row in fit_tasks.values()}
    prior_shadow_families = {family_id(row) for row in prior_tasks}
    excluded_families = fit_families | prior_shadow_families
    sources, source_meta = deterministic_train_sources(Path(args.main_tasks), excluded_families, args.source_total)
    curriculum, curriculum_meta = build_curriculum(sources, args.per_actor, args.line_cards)
    selected_families = {family_id(row) for row in curriculum}
    if selected_families & excluded_families:
        raise ValueError("Stage 2C.6 fresh shadow overlaps fit/tuning families")

    card_stat, card_actor = train_card_loss_model(fit_tasks, fit_results)
    locked: list[dict] = []
    for task in curriculum:
        old = baseline_continuation_prediction(task)["card"]
        learned = candidate_card(task, card_stat, card_actor)
        actor = str(task["actor"])
        strain_class = "nt" if int(task["strain"]) == 4 else "suit"
        use_learned = actor == "defense" and enabled[strain_class]
        policy = learned if use_learned else old
        locked.append({
            "task_id": task["task_id"],
            "family_id": family_id(task),
            "actor": actor,
            "strain_class": strain_class,
            "old_card": old,
            "learned_card": learned,
            "policy_card": policy,
            "policy_use_learned": use_learned,
            "locked": True,
            "dds_called": False,
            "protocol": PROTOCOL,
        })

    tasks_path = out / "train_shadow_tasks.jsonl"
    locked_path = out / "locked_train_shadow_predictions.jsonl"
    write_jsonl(tasks_path, curriculum)
    write_jsonl(locked_path, locked)
    digest = sha256_file(locked_path)
    (out / "locked_train_shadow_predictions.sha256").write_text(f"{digest}  {locked_path.name}\n", encoding="utf-8")

    evidence = {
        "schema": f"{PROTOCOL}-preopen",
        "stage": "2C.6",
        "status": "train_shadow_predictions_locked",
        "authority": "EVIDENCE_ONLY",
        "candidate_policy": "defense_suit_learned__defense_nt_baseline__declarer_baseline",
        "policy_basis": {
            "source": "Stage 2C.5 TRAIN-only shadow artifact",
            "validation_used": False,
            "sealed_used": False,
            "deployment_enabled": enabled,
            "train_only_slice_evidence": deployment_evidence,
            "stage2c5_locked_prediction_sha256": prereq["locked_prediction_sha256"],
        },
        "candidate_fit_scope": "Stage 2B/2C.2 TRAIN-only",
        "candidate_fit_rows": len(fit_results),
        "candidate_fit_families": len(fit_families),
        "policy_tuning_scope": "Stage 2C.5 TRAIN shadow only",
        "prior_shadow_families": len(prior_shadow_families),
        "fresh_shadow_scope": "previously unused TRAIN families",
        "source_selection": {"source_total": args.source_total, "line_cards": args.line_cards, **source_meta},
        "curriculum": {"requested_per_actor": args.per_actor, "selected_total": len(curriculum), **curriculum_meta},
        "fresh_shadow_overlap_with_fit_or_prior_shadow": 0,
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
            "defense_nt_policy_identical_to_old": True,
        },
        "next_action": "evaluate locked predictions with DDS on fresh TRAIN shadow only",
    }
    write_json(out / "STAGE2C6_TRAIN_PREOPEN_EVIDENCE.json", evidence)
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
        raise ValueError("locked Stage 2C.6 predictions changed before DDS")
    if len(tasks) != 2000 or len(locked) != 2000:
        raise ValueError("Stage 2C.6 shadow scope must contain exactly 2000 positions")
    if any(str(task.get("source_root_split")) != "train" for task in tasks.values()):
        raise ValueError("non-TRAIN task entered Stage 2C.6 evaluation")
    if any(not row.get("locked") or row.get("dds_called") for row in locked):
        raise ValueError("Stage 2C.6 predictions were not locked before DDS")

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
            print(json.dumps({"stage2c6_train_positions_completed": index, "of": len(locked)}))

    write_jsonl(out / "stage2c6_train_shadow_results.jsonl", results)
    by_actor: dict[str, dict] = {}
    for actor in ("declarer", "defense"):
        actor_rows = [row for row in results if row["actor"] == actor]
        by_actor[actor] = {
            "old": metrics(actor_rows, "old_regret"),
            "learned": metrics(actor_rows, "learned_regret"),
            "policy": metrics(actor_rows, "policy_regret"),
            "policy_switches": sum(bool(row["policy_use_learned"]) for row in actor_rows),
        }

    defense_classes: dict[str, dict] = {}
    for cls in ("nt", "suit"):
        rows = [row for row in results if row["actor"] == "defense" and row["strain_class"] == cls]
        defense_classes[cls] = {
            "old": metrics(rows, "old_regret"),
            "learned": metrics(rows, "learned_regret"),
            "policy": metrics(rows, "policy_regret"),
            "policy_switches": sum(bool(row["policy_use_learned"]) for row in rows),
        }

    defense = by_actor["defense"]
    declarer_identical = all(
        row["policy_card"] == row["old_card"] and row["policy_regret"] == row["old_regret"]
        for row in results if row["actor"] == "declarer"
    )
    nt_identical = all(
        row["policy_card"] == row["old_card"] and row["policy_regret"] == row["old_regret"]
        for row in results if row["actor"] == "defense" and row["strain_class"] == "nt"
    )
    gate = (
        defense["policy"]["mean_regret"] < defense["old"]["mean_regret"]
        and defense["policy"]["regret_2plus"] < defense["old"]["regret_2plus"]
        and defense["policy"]["optimal_rate"] >= defense["old"]["optimal_rate"]
        and declarer_identical
        and nt_identical
    )

    evidence = {
        "schema": f"{PROTOCOL}-result",
        "stage": "2C.6",
        "status": "train_shadow_complete",
        "authority": "EVIDENCE_ONLY",
        "positions": len(results),
        "families": len({row["family_id"] for row in results}),
        "candidate_policy": "defense_suit_learned__defense_nt_baseline__declarer_baseline",
        "by_actor": by_actor,
        "defense_by_strain_class": defense_classes,
        "predictions_locked_before_dds": True,
        "locked_prediction_sha256": observed,
        "validation_accessed": False,
        "sealed_accessed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "methodology_checks": {
            "fit_prior_shadow_and_fresh_shadow_family_disjoint": True,
            "blind_predictions_locked_before_dds": True,
            "declarer_policy_identical_to_old": declarer_identical,
            "defense_nt_policy_identical_to_old": nt_identical,
            "validation_and_sealed_excluded": True,
        },
        "train_gate_pass": gate,
        "methodology_gate_pass": gate and declarer_identical and nt_identical,
        "next_gate": "owner_decision_on_validation" if gate else "return_to_train_new_candidate",
    }
    write_json(out / "STAGE2C6_TRAIN_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--main-tasks", required=True)
    p.add_argument("--stage2b", required=True)
    p.add_argument("--stage2c2", required=True)
    p.add_argument("--stage2c5-train-evidence", required=True)
    p.add_argument("--stage2c5-prior", required=True)
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
