from __future__ import annotations

"""Prepare DDS Learning Stage 2B without opening holdouts or calling DDS.

The command consumes immutable TRAIN predictions/results and creates:

* family-safe out-of-fold candidate-v0.3 predictions;
* segmented numeric confidence calibration with Wilson lower bounds;
* separate NT/suit opening-lead models and ranked alternatives;
* a targeted legal-line source wave;
* a balanced declarer/defense continuation curriculum;
* unverified counterexample candidates when derived perturbations exist;
* structural-vs-card-level investigation classification;
* a bounded operational review projection;
* a content-addressed CURRENT_STAGE_MANIFEST and compact archive.

No historical database row is rewritten. No validation or sealed-test row is
read for model fitting. This module never imports or calls DDS.
"""

import argparse
import json
import sqlite3
import tarfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from stage2b_v24 import (
    CANDIDATE_ALGORITHM_VERSION,
    aggregate_review_queue,
    apply_segmented_calibration,
    balanced_curriculum,
    build_current_stage_manifest,
    fit_hierarchical_residual_model,
    fit_opening_lead_family_model,
    fit_segmented_oof_calibrator,
    investigation_resolution_status,
    predict_hierarchical_residual,
    rank_opening_leads,
    sha256_file,
    task_family,
    write_json,
)

SEATS = "NESW"
SUITS = "SHDC"
RANK_ORDER = "AKQJT98765432"
HCP = {"A": 4, "K": 3, "Q": 2, "J": 1}


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def load_tasks(paths: Sequence[Path]) -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    for path in paths:
        for row in read_jsonl(path):
            task_id = str(row["task_id"])
            existing = tasks.get(task_id)
            if existing is not None and existing != row:
                raise ValueError(f"Conflicting task definition: {task_id}")
            tasks[task_id] = row
    return tasks


def parse_deal(pbn: str) -> dict[int, list[str]]:
    pbn = pbn.strip()
    if len(pbn) < 3 or pbn[1] != ":":
        raise ValueError(f"Bad PBN deal: {pbn!r}")
    start = SEATS.index(pbn[0].upper())
    raw_hands = pbn[2:].split()
    if len(raw_hands) != 4:
        raise ValueError("Expected four PBN hands")
    hands: dict[int, list[str]] = {}
    for offset, raw in enumerate(raw_hands):
        suits = raw.split(".")
        if len(suits) != 4:
            raise ValueError(f"Bad PBN hand: {raw!r}")
        hands[(start + offset) % 4] = suits
    return hands


def hand_hcp(hand: Sequence[str]) -> int:
    return sum(HCP.get(rank, 0) for holding in hand for rank in holding)


def _bucket(value: int, width: int, cap: int | None = None) -> int:
    value = max(0, int(value))
    if cap is not None:
        value = min(value, cap)
    return (value // width) * width


def contract_feature_levels(task: Mapping[str, object], prediction: Mapping[str, object]) -> list[tuple[str, str]]:
    hands = parse_deal(str(task["deal"]))
    declarer = int(task["declarer"])
    partner = (declarer + 2) % 4
    strain = int(task["strain"])
    baseline = int(prediction["tricks"])
    side_hcp = hand_hcp(hands[declarer]) + hand_hcp(hands[partner])
    combined = [len(hands[declarer][s]) + len(hands[partner][s]) for s in range(4)]
    fit = max(combined) if strain == 4 else combined[strain]
    max_len = max(combined)
    shortness = sum(
        len(hands[seat][suit]) <= 1
        for seat in (declarer, partner)
        for suit in range(4)
        if strain == 4 or suit != strain
    )
    family = task_family("contract_tricks", strain)
    return [
        ("family", family),
        ("broad", f"{strain}|{baseline}"),
        ("coarse", f"{strain}|{baseline}|{_bucket(side_hcp, 4, 40)}|{fit}"),
        ("exact", f"{strain}|{baseline}|{_bucket(side_hcp, 3, 40)}|{fit}|{max_len}|{min(shortness, 6)}"),
    ]


def _sequence_top(cards: str, rank: str) -> bool:
    for sequence in ("AK", "KQ", "QJ", "JT", "T9", "98", "87"):
        if all(token in cards for token in sequence) and rank == sequence[0]:
            return True
    return False


def _rank_group(rank: str) -> str:
    if rank == "A":
        return "A"
    if rank in "KQ":
        return "KQ"
    if rank in "JT9":
        return "JT9"
    return "low"


def _lead_heuristic(*, holding: str, rank: str, is_trump: bool, nt: bool) -> float:
    sequence = _sequence_top(holding, rank)
    score = len(holding) * (1.15 if nt else 0.95)
    score += 1.6 if sequence else 0.0
    score += 0.6 if len(holding) == 1 and not nt and not is_trump else 0.0
    score -= 2.0 if is_trump else 0.0
    score -= 0.8 if len(holding) == 1 and rank in "KQJ" else 0.0
    score -= 0.02 * RANK_ORDER.index(rank)
    return score


def lead_candidates(task: Mapping[str, object]) -> list[dict]:
    hands = parse_deal(str(task["deal"]))
    leader = int(task["leader"])
    strain = int(task["strain"])
    nt = strain == 4
    family = task_family("opening_lead", strain)
    rows = []
    for suit, holding in enumerate(hands[leader]):
        for rank in holding:
            is_trump = (not nt) and suit == strain
            length = len(holding)
            rank_group = _rank_group(rank)
            sequence = int(_sequence_top(holding, rank))
            singleton = int(length == 1)
            doubleton = int(length == 2)
            rows.append(
                {
                    "card": f"{SUITS[suit]}{rank}",
                    "heuristic": _lead_heuristic(holding=holding, rank=rank, is_trump=is_trump, nt=nt),
                    "levels": [
                        ("family", family),
                        ("broad", f"{family}|{int(is_trump)}|{min(length, 6)}"),
                        ("coarse", f"{family}|{int(is_trump)}|{min(length, 6)}|{rank_group}"),
                        ("exact", f"{family}|{int(is_trump)}|{min(length, 6)}|{rank_group}|{sequence}|{singleton}|{doubleton}"),
                    ],
                }
            )
    return rows


def load_train_facts(db_path: Path, tasks: Mapping[str, dict]) -> list[dict]:
    con = sqlite3.connect(db_path)
    rows = []
    query = """
        SELECT p.task_id,p.task_type,p.split,p.prediction_json,r.result_json
        FROM predictions p JOIN dds_results r ON r.task_id=p.task_id
        WHERE p.split='train' AND r.split='train'
        ORDER BY p.task_id
    """
    for task_id, task_type, split, prediction_json, result_json in con.execute(query):
        task = tasks.get(str(task_id))
        if task is None or str(task.get("split")) != "train":
            continue
        rows.append(
            {
                "task_id": str(task_id),
                "task_type": str(task_type),
                "split": str(split),
                "strain": int(task["strain"]),
                "fold": int(task.get("crossfit_fold", -1)),
                "family_id": str(task.get("root_deal_id") or task.get("deal_id")),
                "task": task,
                "prediction": json.loads(prediction_json),
                "result": json.loads(result_json),
            }
        )
    con.close()
    if not rows:
        raise ValueError("No joined TRAIN predictions/results were found")
    missing_folds = [row["task_id"] for row in rows if row["fold"] < 0]
    if missing_folds:
        raise ValueError(f"TRAIN rows lack crossfit_fold metadata: {missing_folds[:10]}")
    return rows


def _fit_models(rows: Sequence[Mapping[str, object]]) -> dict:
    contract_samples = []
    lead_samples = []
    for row in rows:
        task = row["task"]
        prediction = row["prediction"]
        result = row["result"]
        if row["task_type"] == "contract_tricks":
            contract_samples.append(
                {
                    "family": task_family("contract_tricks", task["strain"]),
                    "baseline": int(prediction["tricks"]),
                    "target": int(result["dds_tricks"]),
                    "levels": contract_feature_levels(task, prediction),
                }
            )
        elif row["task_type"] == "opening_lead":
            scores = {str(card).upper(): float(value) for card, value in result.get("scores", {}).items()}
            if not scores:
                continue
            best = max(scores.values())
            family = task_family("opening_lead", task["strain"])
            for candidate in lead_candidates(task):
                card = candidate["card"]
                if card not in scores:
                    continue
                lead_samples.append(
                    {
                        "family": family,
                        "regret": best - scores[card],
                        "levels": candidate["levels"],
                    }
                )
    if not contract_samples:
        raise ValueError("No contract samples available for candidate v0.3")
    if not lead_samples:
        raise ValueError("No opening-lead candidate samples available for candidate v0.3")
    return {
        "contract": fit_hierarchical_residual_model(
            contract_samples,
            prior_strength=25.0,
            minimum_support=8,
            minimum_gain=0.005,
        ),
        "opening_lead": fit_opening_lead_family_model(
            lead_samples,
            prior_strength=30.0,
            minimum_support=12,
        ),
        "training_rows": len(rows),
        "contract_samples": len(contract_samples),
        "lead_candidate_samples": len(lead_samples),
    }


def _predict_row(row: Mapping[str, object], models: Mapping[str, object]) -> dict:
    task = row["task"]
    source_prediction = row["prediction"]
    if row["task_type"] == "contract_tricks":
        family = task_family("contract_tricks", task["strain"])
        estimate = predict_hierarchical_residual(
            baseline=int(source_prediction["tricks"]),
            family=family,
            levels=contract_feature_levels(task, source_prediction),
            model=models["contract"],
        )
        return {
            "task_id": row["task_id"],
            "tricks": estimate["prediction"],
            "baseline_tricks": int(source_prediction["tricks"]),
            "confidence_probability": estimate["raw_confidence_probability"],
            "model_backoff_level": estimate["backoff_level"],
            "model_evidence_count": estimate["support_count"],
            "residual_variance": estimate["residual_variance"],
            "estimated_gain": estimate["estimated_gain"],
            "line": [],
            "predictor_version": "bridge-adaptive-v0.3-oof",
            "locked": True,
            "out_of_fold": True,
        }
    family = task_family("opening_lead", task["strain"])
    ranked = rank_opening_leads(lead_candidates(task), family=family, model=models["opening_lead"], alternatives=3)
    return {
        "task_id": row["task_id"],
        "card": ranked["card"],
        "alternatives": ranked["alternatives"],
        "expected_regret": ranked["expected_regret"],
        "risk_2plus": ranked["risk_2plus"],
        "confidence_probability": ranked["raw_confidence_probability"],
        "model_backoff_level": ranked["model_backoff_level"],
        "model_evidence_count": ranked["support_count"],
        "line": [ranked["card"]],
        "predictor_version": "bridge-opening-lead-v0.3-oof",
        "model_family": family,
        "locked": True,
        "out_of_fold": True,
    }


def prepare_oof_candidate(rows: Sequence[Mapping[str, object]]) -> dict:
    folds = sorted({int(row["fold"]) for row in rows})
    if len(folds) < 2:
        raise ValueError("At least two cross-fit folds are required")
    oof_rows = []
    fold_summaries = []
    for fold in folds:
        training = [row for row in rows if int(row["fold"]) != fold]
        heldout = [row for row in rows if int(row["fold"]) == fold]
        training_families = {row["family_id"] for row in training}
        heldout_families = {row["family_id"] for row in heldout}
        overlap = sorted(training_families & heldout_families)
        if overlap:
            raise ValueError(f"Family leakage in fold {fold}: {overlap[:10]}")
        models = _fit_models(training)
        for row in heldout:
            prediction = _predict_row(row, models)
            oof_rows.append(
                {
                    **row,
                    "prediction": prediction,
                    "source_prediction": row["prediction"],
                    "out_of_fold": True,
                    "heldout_fold": fold,
                }
            )
        fold_summaries.append(
            {
                "fold": fold,
                "training_rows": len(training),
                "heldout_rows": len(heldout),
                "training_families": len(training_families),
                "heldout_families": len(heldout_families),
                "family_overlap": 0,
            }
        )
    calibrator_rows = [
        {
            "task_type": row["task_type"],
            "strain": row["strain"],
            "prediction": row["prediction"],
            "result": row["result"],
            "out_of_fold": True,
        }
        for row in oof_rows
    ]
    calibrator = fit_segmented_oof_calibrator(
        calibrator_rows,
        minimum_support=50,
        maximum_bins=12,
        review_threshold=0.65,
    )
    for row in oof_rows:
        calibrated = apply_segmented_calibration(
            {
                "task_type": row["task_type"],
                "strain": row["strain"],
                "prediction": row["prediction"],
            },
            calibrator,
        )
        row["prediction"] = {**row["prediction"], **calibrated}
    full_models = _fit_models(rows)
    return {
        "oof_rows": oof_rows,
        "folds": fold_summaries,
        "calibrator": calibrator,
        "full_candidate_model": {
            "schema": "dds-stage2b-candidate-model-v1",
            "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
            **full_models,
        },
        "comparison": compare_oof(oof_rows),
    }


def _task_loss(task_type: str, prediction: Mapping[str, object], result: Mapping[str, object]) -> float:
    if task_type == "contract_tricks":
        return abs(int(prediction["tricks"]) - int(result["dds_tricks"]))
    scores = {str(card).upper(): float(value) for card, value in result.get("scores", {}).items()}
    card = str(prediction.get("card", "")).upper()
    if scores and card in scores:
        return max(scores.values()) - scores[card]
    regret = result.get("dd_regret")
    return 13.0 if regret is None else float(regret)


def compare_oof(rows: Sequence[Mapping[str, object]]) -> dict:
    by_type: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        task_type = str(row["task_type"])
        old_loss = _task_loss(task_type, row["source_prediction"], row["result"])
        new_loss = _task_loss(task_type, row["prediction"], row["result"])
        by_type[task_type].append((old_loss, new_loss))
    families = {}
    for task_type, losses in sorted(by_type.items()):
        old_mean = sum(old for old, _ in losses) / len(losses)
        new_mean = sum(new for _, new in losses) / len(losses)
        families[task_type] = {
            "n": len(losses),
            "source_mean_loss": old_mean,
            "candidate_mean_loss": new_mean,
            "mean_improvement": old_mean - new_mean,
            "candidate_better": sum(new < old for old, new in losses),
            "candidate_worse": sum(new > old for old, new in losses),
            "ties": sum(new == old for old, new in losses),
            "promotion_eligible": False,
            "reason": "OOF comparison is evidence for validation readiness, not automatic promotion.",
        }
    return {"schema": "dds-stage2b-oof-comparison-v1", "families": families}


def select_line_source_tasks(
    rows: Sequence[Mapping[str, object]],
    *,
    source_total: int = 500,
) -> list[dict]:
    contract = []
    for row in rows:
        if row["task_type"] != "contract_tricks":
            continue
        old_prediction = row["source_prediction"]
        candidate = row["prediction"]
        result = row["result"]
        error = abs(int(candidate["tricks"]) - int(result["dds_tricks"]))
        source_error = abs(int(old_prediction["tricks"]) - int(result["dds_tricks"]))
        lower = float(candidate.get("lower_confidence_bound", 0.0))
        backoff_penalty = 1.0 if candidate.get("model_backoff_level") in {"baseline", "family", "broad"} else 0.0
        severity = max(error, source_error)
        priority = 10.0 * severity + 4.0 * lower + 2.0 * backoff_penalty
        control = severity == 0
        contract.append(
            {
                "task_id": row["task_id"],
                "task": row["task"],
                "candidate_prediction": candidate,
                "source_prediction": old_prediction,
                "result": result,
                "severity": severity,
                "priority": priority if not control else 0.25 + lower,
                "control": control,
                "family_id": row["family_id"],
            }
        )
    errors = sorted((row for row in contract if not row["control"]), key=lambda row: (-row["priority"], row["task_id"]))
    controls = sorted((row for row in contract if row["control"]), key=lambda row: (-row["priority"], row["task_id"]))
    control_budget = min(source_total // 5, len(controls))
    selected = errors[: max(0, source_total - control_budget)] + controls[:control_budget]
    return selected[:source_total]


def create_line_curriculum(
    selected_sources: Sequence[Mapping[str, object]],
    *,
    continuation_total: int = 2000,
    line_cards: int = 16,
) -> tuple[list[dict], list[dict]]:
    from continuation_tasks import continuation_tasks_from_line
    from line_predictor import generate_line

    line_rows = []
    continuation_candidates = []
    for source in selected_sources:
        task = source["task"]
        line = generate_line(task, cards_to_play=line_cards)
        line_row = {
            "task_id": source["task_id"],
            "task": task,
            "line": line,
            "line_policy": "greedy-cheapest-winner-v1",
            "line_cards": len(line),
            "locked": True,
            "blind": True,
            "priority": source["priority"],
            "severity": source["severity"],
            "control": source["control"],
        }
        line_rows.append(line_row)
        for continuation in continuation_tasks_from_line(task, line, provenance="predicted_line"):
            continuation["priority"] = float(source["priority"])
            continuation["severity"] = float(source["severity"])
            continuation["source_control"] = bool(source["control"])
            continuation_candidates.append(continuation)
    curriculum = balanced_curriculum(continuation_candidates, total=continuation_total, seed=20260818)
    return line_rows, curriculum


def classify_investigations(con: sqlite3.Connection) -> list[dict]:
    table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_events'"
    ).fetchone()
    if table is None:
        return []
    latest = con.execute(
        """
        SELECT e.task_id,e.event_type,e.details_json,p.prediction_json
        FROM investigation_events e
        JOIN (SELECT task_id,MAX(id) max_id FROM investigation_events GROUP BY task_id) x ON x.max_id=e.id
        JOIN predictions p ON p.task_id=e.task_id
        ORDER BY e.task_id
        """
    ).fetchall()
    trajectory_by_task = {}
    for task_id, payload in con.execute(
        "SELECT task_id,payload_json FROM experience_events WHERE event_type='value_trajectory' ORDER BY id"
    ):
        trajectory_by_task[str(task_id)] = json.loads(payload)
    out = []
    for task_id, event_type, details_json, prediction_json in latest:
        details = json.loads(details_json)
        prediction = json.loads(prediction_json)
        status = investigation_resolution_status(
            prediction,
            trajectory=trajectory_by_task.get(str(task_id)),
        )
        out.append(
            {
                "task_id": str(task_id),
                "ledger_event_type": str(event_type),
                "existing_details": details,
                **status,
            }
        )
    return out


def project_review_queue(con: sqlite3.Connection) -> list[dict]:
    evidence = []
    for skill_key, task_id, deal_id, regret, confidence, evidence_json in con.execute(
        """
        SELECT skill_key,task_id,deal_id,COALESCE(regret,0),confidence,evidence_json
        FROM skill_evidence WHERE outcome!='success' ORDER BY id
        """
    ):
        payload = json.loads(evidence_json)
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        prediction = payload.get("prediction", {}) if isinstance(payload, dict) else {}
        evidence.append(
            {
                "skill_key": skill_key,
                "task_id": task_id,
                "deal_id": deal_id,
                "family_id": payload.get("family_id") or payload.get("root_deal_id") or deal_id,
                "error_code": result.get("error_code", "unknown"),
                "strain": payload.get("strain", "unknown"),
                "mechanism": result.get("mechanism", result.get("error_code", "unknown")),
                "due_window": "stage2b",
                "severity": float(regret or 0.0),
                "confidence_probability": float(prediction.get("confidence_probability", 0.75 if confidence == "high" else 0.5 if confidence == "medium" else 0.25)),
                "requested_tasks": 1,
            }
        )
    return aggregate_review_queue(evidence, max_tasks_per_group=250, representative_limit=10)


def extract_counterexample_candidates(db_path: Path, task_paths: Sequence[Path]) -> list[dict]:
    try:
        from counterexample_candidates import extract_candidates
    except ImportError:
        return []
    return extract_candidates(db_path, list(task_paths))


def prepare_stage2b(
    *,
    work: Path,
    task_paths: Sequence[Path],
    out_dir: Path,
    line_source_total: int = 500,
    continuation_total: int = 2000,
    line_cards: int = 16,
) -> dict:
    db_path = work / "training.sqlite3"
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    tasks = load_tasks(task_paths)
    rows = load_train_facts(db_path, tasks)
    oof = prepare_oof_candidate(rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    oof_path = out_dir / "oof_predictions_v03.jsonl"
    write_jsonl(
        oof_path,
        [
            {
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "strain": row["strain"],
                "family_id": row["family_id"],
                "heldout_fold": row["heldout_fold"],
                "prediction": row["prediction"],
                "source_prediction": row["source_prediction"],
                "result": row["result"],
            }
            for row in oof["oof_rows"]
        ],
    )
    model_path = out_dir / "candidate_model_v03.json"
    calibration_path = out_dir / "oof_calibration_v2.json"
    comparison_path = out_dir / "oof_comparison_v03_vs_v02.json"
    write_json(model_path, oof["full_candidate_model"])
    write_json(calibration_path, oof["calibrator"])
    write_json(comparison_path, oof["comparison"])

    sources = select_line_source_tasks(oof["oof_rows"], source_total=line_source_total)
    line_rows, curriculum = create_line_curriculum(
        sources,
        continuation_total=continuation_total,
        line_cards=line_cards,
    )
    lines_path = out_dir / "line_wave_sources.jsonl"
    curriculum_path = out_dir / "continuation_curriculum.jsonl"
    write_jsonl(lines_path, line_rows)
    write_jsonl(curriculum_path, curriculum)

    counterexamples = extract_counterexample_candidates(db_path, task_paths)
    counterexample_path = out_dir / "counterexample_candidates.jsonl"
    write_jsonl(counterexample_path, counterexamples)

    con = sqlite3.connect(db_path)
    investigations = classify_investigations(con)
    queue = project_review_queue(con)
    dds_results_count = con.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0]
    con.close()
    investigations_path = out_dir / "investigation_resolution_classes.json"
    queue_path = out_dir / "review_queue_projection.json"
    write_json(investigations_path, investigations)
    write_json(queue_path, queue)

    readiness = {
        "schema": "dds-stage2b-readiness-v1",
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "train_rows": len(rows),
        "oof_rows": len(oof["oof_rows"]),
        "folds": oof["folds"],
        "line_sources": len(line_rows),
        "continuation_tasks": len(curriculum),
        "continuation_by_actor": dict(Counter(row["actor"] for row in curriculum)),
        "counterexample_candidates": len(counterexamples),
        "investigations": {
            "total": len(investigations),
            "structural": sum(row["resolution_status"] == "resolved_structurally" for row in investigations),
            "card_level": sum(row["resolution_status"] == "resolved_at_card_level" for row in investigations),
        },
        "review_queue_groups": len(queue),
        "validation_opened": False,
        "sealed_opened": False,
        "dds_called": False,
        "mass_training_started": False,
        "next_gate": "blind_continuation_and_counterexample_train",
    }
    readiness_path = out_dir / "stage2b_readiness.json"
    write_json(readiness_path, readiness)

    manifest_path = out_dir / "CURRENT_STAGE_MANIFEST.json"
    manifest = build_current_stage_manifest(
        current_stage="stage2b_prepared",
        current_algorithm=CANDIDATE_ALGORITHM_VERSION,
        canonical_files={
            "database": db_path,
            "candidate_model": model_path,
            "oof_calibration": calibration_path,
            "oof_comparison": comparison_path,
            "line_wave": lines_path,
            "continuation_curriculum": curriculum_path,
            "counterexample_candidates": counterexample_path,
            "investigation_classes": investigations_path,
            "review_queue_projection": queue_path,
            "readiness": readiness_path,
        },
        holdout_status="closed",
        sealed_status="closed",
        next_gate="blind_continuation_and_counterexample_train",
        metadata={
            "dds_results": dds_results_count,
            "oof_rows": len(oof["oof_rows"]),
            "continuation_tasks": len(curriculum),
            "counterexample_candidates": len(counterexamples),
        },
    )
    write_json(manifest_path, manifest)

    archive_path = out_dir / "dds-stage2b-prepared-compact.tgz"
    canonical_paths = [
        db_path,
        model_path,
        calibration_path,
        comparison_path,
        oof_path,
        lines_path,
        curriculum_path,
        counterexample_path,
        investigations_path,
        queue_path,
        readiness_path,
        manifest_path,
    ]
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in canonical_paths:
            archive.add(path, arcname=f"stage2b/{path.name}", recursive=False)
    archive_sha_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    archive_sha_path.write_text(f"{sha256_file(archive_path)}  {archive_path.name}\n", encoding="utf-8")

    summary = {
        "schema": "dds-stage2b-preparation-summary-v1",
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "readiness": readiness,
        "comparison": oof["comparison"],
        "artifacts": {
            "manifest": str(manifest_path),
            "archive": str(archive_path),
            "archive_sha256": sha256_file(archive_path),
        },
    }
    write_json(out_dir / "stage2b_summary.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prepare Stage 2B from immutable TRAIN facts without DDS/holdout exposure")
    p.add_argument("--work", required=True)
    p.add_argument("--tasks", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--line-sources", type=int, default=500)
    p.add_argument("--continuations", type=int, default=2000)
    p.add_argument("--line-cards", type=int, default=16)
    return p


def main() -> None:
    args = parser().parse_args()
    summary = prepare_stage2b(
        work=Path(args.work),
        task_paths=[Path(path) for path in args.tasks],
        out_dir=Path(args.out),
        line_source_total=args.line_sources,
        continuation_total=args.continuations,
        line_cards=args.line_cards,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
