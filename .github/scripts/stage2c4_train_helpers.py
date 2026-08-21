from __future__ import annotations

"""Final blind sealed gate for DDS Stage 2C.4.

Fail-closed protocol:
- candidate card-loss model is fit only from immutable TRAIN Stage 2C.2 evidence;
- selective gate is fit only from immutable family-disjoint OOF TRAIN Stage 2C.4 evidence;
- validation is used only as a prerequisite PASS record, never for fitting or tuning;
- sealed positions are selected deterministically without DDS outcomes;
- old/candidate/hybrid predictions are locked and hashed before the first sealed DDS call;
- sealed results never update learning or historical databases;
- passing sealed does not automatically promote the model.
"""

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from continuation_tasks import continuation_tasks_from_line
from line_predictor import generate_line
from stage2b_v25 import exact_balanced_curriculum

SUITS = "SHDC"
RANKS = "AKQJT98765432"
RANK_VALUE = {rank: 14 - i for i, rank in enumerate(RANKS)}
PROTOCOL = "dds-stage2c4-sealed-v1"
GATE_MARGIN = 0.08
GATE_MIN_SUPPORT = 20
CARD_MIN_SUPPORT = 8
SEED = 20260818


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
    return {
        "task_id": task["task_id"],
        "card": token(scored[0][1]),
        "alternatives": [token(card) for _, card in scored[1:4]],
        "predictor_version": "bridge-stage2c1-continuation-blind-v1",
        "locked": True,
        "dds_called": False,
    }


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


def position_feature(task: Mapping[str, object]) -> tuple:
    hand = parse_deal(str(task["remaining_deal"]))[int(task["next_seat"])]
    current = task.get("current_trick") or []
    lengths = sorted((len(holding) for holding in hand), reverse=True)
    shape = (min(5, lengths[0]), min(5, lengths[1]))
    return (
        str(task["actor"]),
        "nt" if int(task["strain"]) == 4 else "suit",
        "follow" if current else "lead",
        shape,
    )


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


def candidate_card(
    task: Mapping[str, object],
    stat: Mapping[tuple, Sequence],
    actor_stat: Mapping[str, Sequence],
) -> str:
    hand = parse_deal(str(task["remaining_deal"]))[int(task["next_seat"])]
    current = task.get("current_trick") or []
    ranked = []
    actor = str(task["actor"])
    actor_total, actor_n = actor_stat.get(actor, (0.0, 0))
    fallback = float(actor_total) / int(actor_n) if int(actor_n) else 1.0
    for card in legal_cards(hand, current):
        total, n = stat.get(card_feature(task, card), (0.0, 0))
        estimate = float(total) / int(n) if int(n) >= CARD_MIN_SUPPORT else fallback
        tie = 0.001 * (RANK_VALUE[card[1]] / 14.0) + 0.0001 * card[0]
        ranked.append((estimate + tie, card))
    if not ranked:
        raise ValueError(f"no legal candidate cards: {task['task_id']}")
    ranked.sort(key=lambda x: (x[0], x[1][0], -RANK_VALUE[x[1][1]]))
    return token(ranked[0][1])


def train_selective_gate(
    train_tasks: Mapping[str, Mapping[str, object]],
    stage2c4_predictions: Sequence[Mapping[str, object]],
) -> tuple[dict, dict]:
    stat: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
    actor_stat: dict[str, list] = defaultdict(lambda: [0.0, 0])
    used = 0
    for row in stage2c4_predictions:
        old = row.get("old_regret")
        new = row.get("new_regret")
        if old is None or new is None:
            continue
        task = train_tasks.get(str(row["task_id"]))
        if task is None:
            raise ValueError(f"TRAIN continuation task missing for gate row {row['task_id']}")
        delta = float(old) - float(new)
        feature = position_feature(task)
        stat[feature][0] += delta
        stat[feature][1] += 1
        actor = str(task["actor"])
        actor_stat[actor][0] += delta
        actor_stat[actor][1] += 1
        used += 1
    if used < 1900:
        raise ValueError(f"insufficient OOF gate evidence: {used}")
    return stat, actor_stat


def gate_decision(
    task: Mapping[str, object],
    stat: Mapping[tuple, Sequence],
    actor_stat: Mapping[str, Sequence],
) -> tuple[float, bool, int]:
    total, n = stat.get(position_feature(task), (0.0, 0))
    actor = str(task["actor"])
    actor_total, actor_n = actor_stat.get(actor, (0.0, 0))
    if int(n) >= GATE_MIN_SUPPORT:
        expected = float(total) / int(n)
        support = int(n)
    else:
        expected = float(actor_total) / int(actor_n) if int(actor_n) else 0.0
        support = int(actor_n)
    return expected, expected > GATE_MARGIN, support


def verify_validation_prerequisite(path: Path) -> dict:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("status") != "validation_complete":
        raise ValueError("validation prerequisite is not complete")
    if evidence.get("validation_gate_pass") is not True:
        raise ValueError("validation prerequisite did not PASS")
    if evidence.get("automatic_promotion") is not False:
        raise ValueError("validation evidence unexpectedly allows automatic promotion")
    sealed = evidence.get("sealed") or {}
    if sealed.get("opened") is not False or int(sealed.get("evaluated", -1)) != 0:
        raise ValueError("sealed was already opened in validation evidence")
    if evidence.get("next_gate") != "owner_decision_on_sealed":
        raise ValueError("validation evidence does not point to owner_decision_on_sealed")
    return evidence


def deterministic_sealed_sources(main_tasks_path: Path, source_total: int) -> tuple[list[dict], dict]:
    sealed_contracts = []
    split_counts = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    sealed_task_ids = []
    for raw in read_jsonl(main_tasks_path):
        split = str(raw.get("split"))
        split_counts[split] += 1
        family = str(raw.get("root_deal_id") or raw.get("deal_id"))
        families[split].add(family)
        if split == "sealed_test":
            sealed_task_ids.append(str(raw["task_id"]))
            if raw.get("task_type") == "contract_tricks":
                sealed_contracts.append(raw)

    train_families = families.get("train", set())
    validation_families = families.get("validation", set())
    sealed_families = families.get("sealed_test", set())
    pairwise = {
        "train_validation": len(train_families & validation_families),
        "train_sealed": len(train_families & sealed_families),
        "validation_sealed": len(validation_families & sealed_families),
    }
    if any(pairwise.values()):
        raise ValueError(f"main corpus family leakage across splits: {pairwise}")

    sealed_contracts.sort(
        key=lambda row: (
            hashlib.sha256(
                f"{PROTOCOL}:{row.get('root_deal_id') or row.get('deal_id')}:{row['task_id']}".encode("utf-8")
            ).hexdigest(),
            str(row["task_id"]),
        )
    )
    selected = []
    seen_families = set()
    for row in sealed_contracts:
        family = str(row.get("root_deal_id") or row.get("deal_id"))
        if family in seen_families:
            continue
        seen_families.add(family)
        selected.append(row)
        if len(selected) == source_total:
            break
    if len(selected) != source_total:
        raise ValueError(f"insufficient unique sealed contract families: {len(selected)} < {source_total}")

    return selected, {
        "split_counts": dict(split_counts),
        "train_families": len(train_families),
        "validation_families": len(validation_families),
        "sealed_families": len(sealed_families),
        "selected_source_families": len(seen_families),
        "pairwise_family_overlap": pairwise,
        "sealed_task_count": len(sealed_task_ids),
        "sealed_task_id_digest": hashlib.sha256("\n".join(sorted(sealed_task_ids)).encode("utf-8")).hexdigest(),
    }


def build_sealed_curriculum(
    sources: Sequence[Mapping[str, object]],
    per_actor: int,
    line_cards: int,
) -> tuple[list[dict], dict]:
    candidates = []
    for source in sources:
        line = generate_line(dict(source), cards_to_play=line_cards)
        for item in continuation_tasks_from_line(dict(source), line, provenance="predicted_line"):
            if str(item.get("source_root_split")) != "sealed_test":
                raise ValueError("sealed continuation lost source split ownership")
            item["priority"] = 0.0
            item["severity"] = 0.0
            candidates.append(item)
    curriculum = exact_balanced_curriculum(candidates, per_actor=per_actor, seed=SEED)
    counts = Counter(str(x["actor"]) for x in curriculum)
    if counts != {"declarer": per_actor, "defense": per_actor}:
        raise ValueError(f"sealed curriculum imbalance: {counts}")
    return curriculum, {
        "candidate_continuations": len(candidates),
        "candidate_by_actor": dict(Counter(str(x["actor"]) for x in candidates)),
        "selected_by_actor": dict(counts),
    }


def prepare(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    validation = verify_validation_prerequisite(Path(args.validation_evidence))

    train_tasks_rows = read_jsonl(Path(args.stage2b) / "continuation_curriculum_balanced.jsonl")
    train_tasks = {str(row["task_id"]): row for row in train_tasks_rows}
    if len(train_tasks) != 2000:
        raise ValueError(f"expected 2000 Stage 2B continuation tasks, got {len(train_tasks)}")
    if any(str(row.get("source_root_split")) != "train" for row in train_tasks.values()):
        raise ValueError("Stage 2B continuation evidence is not TRAIN-only")

    train_results = read_jsonl(Path(args.stage2c2))
    stage2c4_predictions = read_jsonl(Path(args.stage2c4))
    if len(train_results) != 2000 or len(stage2c4_predictions) != 2000:
        raise ValueError("Stage 2C.2/2C.4 TRAIN evidence must each contain exactly 2000 rows")

    train_selected_families = {
        str(row.get("root_deal_id") or row.get("deal_id")) for row in train_tasks.values()
    }
    sources, source_meta = deterministic_sealed_sources(Path(args.main_tasks), args.source_total)
    sealed_source_families = {str(row.get("root_deal_id") or row.get("deal_id")) for row in sources}
    overlap = sorted(train_selected_families & sealed_source_families)
    if overlap:
        raise ValueError(f"TRAIN/sealed selected-family leakage: {overlap[:10]}")

    curriculum, curriculum_meta = build_sealed_curriculum(sources, args.per_actor, args.line_cards)
    card_stat, card_actor = train_card_loss_model(train_tasks, train_results)
    gate_stat, gate_actor = train_selective_gate(train_tasks, stage2c4_predictions)

    locked = []
    for task in curriculum:
        old = baseline_continuation_prediction(task)["card"]
        new = candidate_card(task, card_stat, card_actor)
        expected, use_new, support = gate_decision(task, gate_stat, gate_actor)
        locked.append(
            {
                "task_id": task["task_id"],
                "family_id": str(task.get("root_deal_id") or task.get("deal_id")),
                "actor": str(task["actor"]),
                "old_card": old,
                "candidate_card": new,
                "hybrid_card": new if use_new else old,
                "gate_expected_gain": expected,
                "gate_use_new": use_new,
                "gate_support": support,
                "locked": True,
                "dds_called": False,
                "protocol": PROTOCOL,
            }
        )

    tasks_path = out / "sealed_continuation_tasks.jsonl"
    locked_path = out / "locked_sealed_predictions.jsonl"
    write_jsonl(tasks_path, curriculum)
    write_jsonl(locked_path, locked)
    locked_digest = sha256_file(locked_path)
    (out / "locked_sealed_predictions.sha256").write_text(
        f"{locked_digest}  {locked_path.name}\n", encoding="utf-8"
    )

    evidence = {
        "schema": f"{PROTOCOL}-preopen",
        "stage": "2C.4",
        "status": "sealed_predictions_locked",
        "owner_sealed_authorized": True,
        "validation_prerequisite": {
            "status": validation["status"],
            "validation_gate_pass": validation["validation_gate_pass"],
            "next_gate": validation["next_gate"],
            "locked_validation_prediction_sha256": validation["validation"]["locked_prediction_sha256"],
        },
        "validation_used_for_learning": False,
        "validation_used_for_gate_tuning": False,
        "sealed_opened": True,
        "sealed_completed": False,
        "dds_called": False,
        "learning_allowed": False,
        "learning_mutated": False,
        "automatic_promotion": False,
        "candidate_training_scope": "TRAIN-only",
        "candidate_training_rows": len(train_results),
        "gate_training_scope": "5-fold family-disjoint OOF TRAIN evidence",
        "gate_margin": GATE_MARGIN,
        "gate_min_support": GATE_MIN_SUPPORT,
        "card_min_support": CARD_MIN_SUPPORT,
        "source_selection": {
            "policy": "deterministic SHA-256 order, one sealed_test contract source per family, no DDS outcome",
            "source_total": args.source_total,
            "line_cards": args.line_cards,
            **source_meta,
        },
        "curriculum": {
            "requested_per_actor": args.per_actor,
            "selected_total": len(curriculum),
            **curriculum_meta,
        },
        "family_overlap_train_selected_sealed": len(overlap),
        "locked_prediction_sha256": locked_digest,
        "switches_to_candidate": sum(bool(row["gate_use_new"]) for row in locked),
        "predeclared_sealed_gate": {
            "defense_hybrid_mean_regret_lt_old": True,
            "defense_hybrid_regret_2plus_lt_old": True,
            "declarer_hybrid_mean_regret_le_old_plus": 0.01,
        },
        "next_action": "DDS sealed evaluation only; no learning and no automatic promotion",
    }
    write_json(out / "STAGE2C4_SEALED_PREOPEN_EVIDENCE.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def metrics(rows: Sequence[Mapping[str, object]], key: str) -> dict:
    vals = [float(row[key]) for row in rows if row.get(key) is not None]
    if not vals:
        raise ValueError(f"no values for {key}")
    return {
        "n": len(vals),
        "optimal": sum(value == 0 for value in vals),
        "optimal_rate": sum(value == 0 for value in vals) / len(vals),
        "mean_regret": statistics.fmean(vals),
        "regret_2plus": sum(value >= 2 for value in vals),
    }


def evaluate(args: argparse.Namespace) -> None:
    from continuation_eval import evaluate_continuation

    root = Path(args.prepared)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = {str(row["task_id"]): row for row in read_jsonl(root / "sealed_continuation_tasks.jsonl")}
    locked_path = root / "locked_sealed_predictions.jsonl"
    locked = read_jsonl(locked_path)
    expected_digest = (root / "locked_sealed_predictions.sha256").read_text(encoding="utf-8").strip().split()[0]
    observed_digest = sha256_file(locked_path)
    if expected_digest != observed_digest:
        raise ValueError("locked sealed prediction digest changed before DDS evaluation")
    if len(tasks) != 2000 or len(locked) != 2000:
        raise ValueError("sealed scope must be exactly 2000 positions")
    if any(str(task.get("source_root_split")) != "sealed_test" for task in tasks.values()):
        raise ValueError("non-sealed continuation entered sealed scope")
    if any(not row.get("locked") or row.get("dds_called") for row in locked):
        raise ValueError("sealed predictions were not locked before DDS")

    result_rows = []
    for index, prediction in enumerate(locked, 1):
        task = tasks[str(prediction["task_id"])]
        probe = evaluate_continuation(task, {"card": prediction["old_card"]})
        scores = probe.get("candidate_scores") or probe.get("scores") or probe.get("card_scores") or {}
        scores = {str(card).upper(): float(score) for card, score in scores.items()}
        if not scores:
            raise ValueError(f"DDS candidate scores missing for sealed task {prediction['task_id']}")
        best = max(scores.values())
        old = str(prediction["old_card"]).upper()
        new = str(prediction["candidate_card"]).upper()
        hybrid = str(prediction["hybrid_card"]).upper()
        for card in (old, new, hybrid):
            if card not in scores:
                raise ValueError(f"locked legal card {card} missing from DDS score table for {prediction['task_id']}")
        optimal = sorted(card for card, score in scores.items() if score == best)
        result_rows.append(
            {
                **prediction,
                "old_regret": best - scores[old],
                "candidate_regret": best - scores[new],
                "hybrid_regret": best - scores[hybrid],
                "optimal_cards": optimal,
                "candidate_scores": scores,
                "dds_called": True,
            }
        )
        if index % 250 == 0:
            print(json.dumps({"sealed_positions_completed": index, "of": len(locked)}))

    write_jsonl(out / "stage2c4_sealed_results.jsonl", result_rows)
    by_actor = {}
    for actor in ("declarer", "defense"):
        actor_rows = [row for row in result_rows if row["actor"] == actor]
        by_actor[actor] = {
            "old": metrics(actor_rows, "old_regret"),
            "candidate": metrics(actor_rows, "candidate_regret"),
            "hybrid": metrics(actor_rows, "hybrid_regret"),
            "switches": sum(bool(row["gate_use_new"]) for row in actor_rows),
        }

    defense = by_actor["defense"]
    declarer = by_actor["declarer"]
    gate = (
        defense["hybrid"]["mean_regret"] < defense["old"]["mean_regret"]
        and defense["hybrid"]["regret_2plus"] < defense["old"]["regret_2plus"]
        and declarer["hybrid"]["mean_regret"] <= declarer["old"]["mean_regret"] + 0.01
    )

    evidence = {
        "schema": f"{PROTOCOL}-result",
        "stage": "2C.4",
        "status": "sealed_complete",
        "owner_sealed_authorized": True,
        "validation_prerequisite_passed": True,
        "validation_used_for_learning": False,
        "validation_used_for_gate_tuning": False,
        "sealed_opened": True,
        "sealed_completed": True,
        "dds_called": True,
        "learning_allowed": False,
        "learning_mutated": False,
        "historical_database_mutated": False,
        "automatic_promotion": False,
        "locked_prediction_sha256": observed_digest,
        "positions": len(result_rows),
        "families": len({row["family_id"] for row in result_rows}),
        "by_actor": by_actor,
        "sealed_gate_pass": bool(gate),
        "sealed_gate_definition": {
            "defense_hybrid_mean_regret_lt_old": True,
            "defense_hybrid_regret_2plus_lt_old": True,
            "declarer_hybrid_mean_regret_le_old_plus": 0.01,
        },
        "next_gate": "owner_decision_on_promotion" if gate else "return_to_train_new_candidate",
    }
    write_json(out / "STAGE2C4_SEALED_EVIDENCE.json", evidence)
    report = (
        "# DDS Stage 2C.4 — Sealed final blind gate\n\n"
        "Sealed-test открыт по отдельному явному решению владельца после PASS validation. "
        "Validation не использовалась для обучения или настройки candidate/gate. Все sealed-предсказания "
        "зафиксированы и хэшированы до первого DDS-вызова.\n\n"
        f"Защита: old optimal {defense['old']['optimal_rate']:.2%}, hybrid {defense['hybrid']['optimal_rate']:.2%}; "
        f"mean regret {defense['old']['mean_regret']:.3f} → {defense['hybrid']['mean_regret']:.3f}; "
        f"regret>=2 {defense['old']['regret_2plus']} → {defense['hybrid']['regret_2plus']}.\n\n"
        f"Разыгрывающий: old optimal {declarer['old']['optimal_rate']:.2%}, hybrid {declarer['hybrid']['optimal_rate']:.2%}; "
        f"mean regret {declarer['old']['mean_regret']:.3f} → {declarer['hybrid']['mean_regret']:.3f}; "
        f"regret>=2 {declarer['old']['regret_2plus']} → {declarer['hybrid']['regret_2plus']}.\n\n"
        f"Sealed gate: {'PASS' if gate else 'FAIL'}.\n\n"
        "Обучение на sealed запрещено, historical database не изменялась, автоматическое продвижение модели запрещено.\n\n"
        f"Следующий gate: `{evidence['next_gate']}`.\n"
    )
    (out / "STAGE2C4_SEALED_REPORT_RU.md").write_text(report, encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--main-tasks", required=True)
    prep.add_argument("--stage2b", required=True)
    prep.add_argument("--stage2c2", required=True)
    prep.add_argument("--stage2c4", required=True)
    prep.add_argument("--validation-evidence", required=True)
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
