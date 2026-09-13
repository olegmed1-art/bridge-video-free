from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

import baseline_predictor as bp

MODEL_VERSION = "bridge-adaptive-v0.2"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_predictions(path: Path) -> dict[str, dict]:
    rows = _load_jsonl(path)
    out: dict[str, dict] = {}
    for row in rows:
        task_id = row.get("task_id")
        if not task_id or not row.get("locked"):
            raise ValueError(f"Prediction must be locked and have task_id: {row}")
        if task_id in out:
            raise ValueError(f"Duplicate prediction: {task_id}")
        out[task_id] = row
    return out


def _bin(value: int, width: int, cap: int | None = None) -> int:
    value = max(0, int(value))
    if cap is not None:
        value = min(value, cap)
    return (value // width) * width


def _side_stats(task: dict) -> dict:
    hands = bp.parse_deal(task["deal"])
    declarer = int(task["declarer"])
    partner = (declarer + 2) % 4
    strain = int(task["strain"])
    side = (declarer, partner)
    opp = ((declarer + 1) % 4, (declarer + 3) % 4)
    side_hcp = sum(bp.hcp(hands[s]) for s in side)
    opp_hcp = sum(bp.hcp(hands[s]) for s in opp)
    aces = sum(cards.count("A") for s in side for cards in hands[s])
    kings = sum(cards.count("K") for s in side for cards in hands[s])
    combined_lengths = [len(hands[declarer][s]) + len(hands[partner][s]) for s in range(4)]
    max_len = max(combined_lengths)
    shortness = sum(1 for seat in side for suit in range(4) if len(hands[seat][suit]) <= 1)
    if strain == 4:
        fit = max_len
        stoppers = 0
        for suit in range(4):
            joined = hands[declarer][suit] + hands[partner][suit]
            if "A" in joined or ("K" in joined and len(joined) >= 3) or ("Q" in joined and len(joined) >= 5):
                stoppers += 1
        trump_hcp = 0
        opp_trump_max = 0
    else:
        fit = combined_lengths[strain]
        trump_hcp = sum(bp.RANK_VALUE.get(r, 0) for seat in side for r in hands[seat][strain])
        opp_trump_max = max(len(hands[seat][strain]) for seat in opp)
        stoppers = 0
    return {
        "strain": strain,
        "side_hcp": side_hcp,
        "opp_hcp": opp_hcp,
        "fit": fit,
        "max_len": max_len,
        "shortness": shortness,
        "stoppers": stoppers,
        "aces": aces,
        "kings": kings,
        "trump_hcp": trump_hcp,
        "opp_trump_max": opp_trump_max,
    }


def _contract_keys(task: dict, baseline_tricks: int) -> list[tuple[str, str, int]]:
    s = _side_stats(task)
    strain = s["strain"]
    exact = (
        f"{strain}|{baseline_tricks}|{_bin(s['side_hcp'], 3, 40)}|{s['fit']}|"
        f"{s['max_len']}|{s['stoppers']}|{min(s['shortness'], 4)}|{s['aces']}|"
        f"{_bin(s['trump_hcp'], 2, 20)}|{s['opp_trump_max']}"
    )
    medium = f"{strain}|{baseline_tricks}|{_bin(s['side_hcp'], 3, 40)}|{s['fit']}|{s['stoppers']}"
    coarse = f"{strain}|{baseline_tricks}|{_bin(s['side_hcp'], 4, 40)}"
    strain_key = f"{strain}|{baseline_tricks}"
    return [
        ("exact", exact, 5),
        ("medium", medium, 10),
        ("coarse", coarse, 20),
        ("strain", strain_key, 30),
        ("global", "all", 1),
    ]


def _rank_group(rank: str) -> str:
    if rank == "A":
        return "A"
    if rank in "KQ":
        return "KQ"
    if rank in "JT9":
        return "JT9"
    return "low"


def _len_bucket(n: int) -> str:
    return str(n) if n <= 5 else "6+"


def _candidate_heuristic(task: dict, suit: int, rank: str, cards: str) -> float:
    strain = int(task["strain"])
    is_trump = strain != 4 and suit == strain
    seq = bp.sequence_lead(cards)
    length_score = len(cards) * (1.15 if strain == 4 else 0.95)
    honor_score = bp.top_honor_strength(cards) * 1.2
    seq_score = 1.4 if seq == rank else 0.0
    trump_penalty = 2.0 if is_trump else 0.0
    singleton_bonus = 0.55 if strain != 4 and len(cards) == 1 and not is_trump else 0.0
    unsupported_honor_penalty = 0.7 if len(cards) == 1 and rank in "KQJ" else 0.0
    fourth = cards[min(3, len(cards) - 1)] if cards else rank
    fourth_bonus = 0.35 if len(cards) >= 4 and rank == fourth and seq is None else 0.0
    short_ace_bonus = 0.35 if rank == "A" and len(cards) <= 2 else 0.0
    return length_score + honor_score + seq_score + singleton_bonus + fourth_bonus + short_ace_bonus - trump_penalty - unsupported_honor_penalty


def _lead_keys(task: dict, suit: int, rank: str, cards: str) -> list[tuple[str, str, int]]:
    strain = int(task["strain"])
    strain_type = "NT" if strain == 4 else "SUIT"
    is_trump = int(strain != 4 and suit == strain)
    seq_top = int(bp.sequence_lead(cards) == rank)
    singleton = int(len(cards) == 1)
    doubleton = int(len(cards) == 2)
    honor_bin = int(round(bp.top_honor_strength(cards) * 2))
    rg = _rank_group(rank)
    lb = _len_bucket(len(cards))
    exact = f"{strain_type}|{is_trump}|{lb}|{rg}|{seq_top}|{singleton}|{doubleton}|{honor_bin}"
    medium = f"{strain_type}|{is_trump}|{lb}|{rg}|{seq_top}|{singleton}"
    coarse = f"{strain_type}|{is_trump}|{lb}|{rg}"
    broad = f"{strain_type}|{is_trump}|{lb}"
    return [
        ("exact", exact, 15),
        ("medium", medium, 30),
        ("coarse", coarse, 60),
        ("broad", broad, 100),
        ("global", strain_type, 1),
    ]


def _finalize_buckets(raw: dict[str, dict[str, list[float]]]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for level, entries in raw.items():
        out[level] = {}
        for key, values in entries.items():
            if not values:
                continue
            med = float(statistics.median(values))
            mean = float(statistics.fmean(values))
            mad = float(statistics.median(abs(v - med) for v in values))
            out[level][key] = {"median": med, "mean": mean, "mad": mad, "count": len(values)}
    return out


def train_model(tasks_path: Path, predictions_path: Path, db_path: Path, out_path: Path) -> dict:
    tasks = {row["task_id"]: row for row in _load_jsonl(tasks_path) if row.get("split") == "train"}
    predictions = _load_predictions(predictions_path)
    con = sqlite3.connect(db_path)
    results = {
        task_id: json.loads(result_json)
        for task_id, result_json in con.execute("SELECT task_id,result_json FROM dds_results WHERE split='train'")
    }

    contract_raw: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    lead_raw: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    contract_samples = lead_tasks = lead_candidates = 0

    for task_id, task in tasks.items():
        pred = predictions.get(task_id)
        result = results.get(task_id)
        if pred is None or result is None:
            continue
        if task["task_type"] == "contract_tricks":
            baseline = int(pred["tricks"])
            residual = int(result["dds_tricks"]) - baseline
            for level, key, _ in _contract_keys(task, baseline):
                contract_raw[level][key].append(float(residual))
            contract_samples += 1
        elif task["task_type"] == "opening_lead":
            scores = {str(k).upper(): int(v) for k, v in result.get("scores", {}).items()}
            if not scores:
                continue
            best = max(scores.values())
            hands = bp.parse_deal(task["deal"])
            leader = int(task["leader"])
            for suit, cards in enumerate(hands[leader]):
                for rank in cards:
                    card = f"{bp.SUITS[suit]}{rank}"
                    if card not in scores:
                        continue
                    target = float(scores[card] - best)
                    for level, key, _ in _lead_keys(task, suit, rank, cards):
                        lead_raw[level][key].append(target)
                    lead_candidates += 1
            lead_tasks += 1

    model = {
        "model_version": MODEL_VERSION,
        "source_predictor_version": next((p.get("predictor_version") for p in predictions.values()), None),
        "training_split": "train",
        "contract": {
            "samples": contract_samples,
            "buckets": _finalize_buckets(contract_raw),
        },
        "opening_lead": {
            "tasks": lead_tasks,
            "candidate_samples": lead_candidates,
            "target": "candidate_defense_tricks_minus_best_defense_tricks",
            "buckets": _finalize_buckets(lead_raw),
        },
        "dds_used_during_model_training": True,
        "dds_used_during_prediction": False,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "model_version": MODEL_VERSION,
        "contract_samples": contract_samples,
        "opening_lead_tasks": lead_tasks,
        "opening_lead_candidate_samples": lead_candidates,
        "path": str(out_path),
    }


def _lookup(buckets: dict, keys: list[tuple[str, str, int]]) -> tuple[float, int, float, str]:
    for level, key, minimum in keys:
        rec = buckets.get(level, {}).get(key)
        if rec and int(rec["count"]) >= minimum:
            return float(rec["median"]), int(rec["count"]), float(rec.get("mad", 0.0)), level
    return 0.0, 0, 0.0, "none"


def _predict_contract(task: dict, model: dict) -> dict:
    base = bp.prediction_for(task, "bridge-baseline-v0.1")
    baseline = int(base["tricks"])
    correction, count, mad, level = _lookup(model["contract"]["buckets"], _contract_keys(task, baseline))
    tricks = max(0, min(13, int(round(baseline + correction))))
    confidence = "high" if count >= 100 and mad <= 0.5 else "medium" if count >= 20 else "low"
    return {
        "task_id": task["task_id"],
        "tricks": tricks,
        "confidence": confidence,
        "reason": (
            f"Adaptive blind estimate: baseline {baseline}, learned residual {correction:+.2f} "
            f"from {count} TRAIN observations at {level} backoff; no DDS call during prediction."
        ),
        "line": [],
        "predictor_version": model["model_version"],
        "model_evidence_count": count,
        "model_backoff_level": level,
        "locked": True,
    }


def _predict_lead(task: dict, model: dict) -> dict:
    hands = bp.parse_deal(task["deal"])
    leader = int(task["leader"])
    candidates: list[tuple[float, float, int, str, int, float, str]] = []
    for suit, cards in enumerate(hands[leader]):
        for rank in cards:
            card = f"{bp.SUITS[suit]}{rank}"
            learned, count, mad, level = _lookup(model["opening_lead"]["buckets"], _lead_keys(task, suit, rank, cards))
            heuristic = _candidate_heuristic(task, suit, rank, cards)
            combined = learned + 0.015 * heuristic
            candidates.append((combined, heuristic, -bp.RANK_ORDER.index(rank), card, count, mad, level))
    if not candidates:
        raise ValueError(f"No lead candidates for {task['task_id']}")
    candidates.sort(reverse=True)
    best = candidates[0]
    gap = best[0] - (candidates[1][0] if len(candidates) > 1 else best[0])
    confidence = "high" if best[4] >= 100 and gap >= 0.25 else "medium" if best[4] >= 30 and gap >= 0.08 else "low"
    return {
        "task_id": task["task_id"],
        "card": best[3],
        "expected_defense_tricks": None,
        "confidence": confidence,
        "reason": (
            f"Adaptive blind lead: learned relative DD value {best[0]:+.3f}, TRAIN evidence {best[4]}, "
            f"backoff {best[6]}, candidate gap {gap:.3f}; no DDS call during prediction."
        ),
        "line": [best[3]],
        "predictor_version": model["model_version"],
        "model_evidence_count": best[4],
        "model_backoff_level": best[6],
        "locked": True,
    }


def predict(tasks_path: Path, model_path: Path, out_path: Path, splits: set[str]) -> dict:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    counts: dict[str, int] = defaultdict(int)
    total = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tasks_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as out:
        for line in src:
            if not line.strip():
                continue
            task = json.loads(line)
            if task.get("split") not in splits:
                continue
            if task["task_type"] == "contract_tricks":
                pred = _predict_contract(task, model)
            elif task["task_type"] == "opening_lead":
                pred = _predict_lead(task, model)
            else:
                raise ValueError(f"Unsupported task type: {task['task_type']}")
            out.write(json.dumps(pred, ensure_ascii=False, sort_keys=True) + "\n")
            counts[task["task_type"]] += 1
            total += 1
    return {
        "model_version": model["model_version"],
        "predictions": total,
        "by_type": dict(counts),
        "splits": sorted(splits),
        "dds_called_during_prediction": False,
        "path": str(out_path),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Interpretable adaptive predictor trained on DDS TRAIN evidence")
    sp = p.add_subparsers(dest="command", required=True)

    q = sp.add_parser("train")
    q.add_argument("--tasks", required=True)
    q.add_argument("--predictions", required=True)
    q.add_argument("--db", required=True)
    q.add_argument("--out", required=True)

    q = sp.add_parser("predict")
    q.add_argument("--tasks", required=True)
    q.add_argument("--model", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--splits", nargs="+", required=True)

    args = p.parse_args()
    if args.command == "train":
        result = train_model(Path(args.tasks), Path(args.predictions), Path(args.db), Path(args.out))
    else:
        result = predict(Path(args.tasks), Path(args.model), Path(args.out), set(args.splits))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
