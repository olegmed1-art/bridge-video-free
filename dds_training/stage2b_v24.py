from __future__ import annotations

"""Core algorithms for DDS Learning Stage 2B (candidate revision v2.4).

This module is deliberately dependency-free.  It provides:

* family/segment aware out-of-fold confidence calibration;
* Wilson lower confidence bounds and mandatory abstention;
* hierarchical residual shrinkage for contract-trick estimates;
* separate NT/suit opening-lead value models with alternatives and risk;
* balanced declarer/defense continuation curriculum selection;
* non-destructive aggregation of the operational review queue;
* explicit structural-vs-card-level investigation classification;
* content-addressed CURRENT_STAGE_MANIFEST generation.

It does not call DDS, mutate historical facts, open validation/sealed data, or
change the school's bidding system.
"""

import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

CANDIDATE_ALGORITHM_VERSION = "dds-learning-v2.4-stage2b-candidate"
CALIBRATION_SCHEMA = "dds-oof-calibration-v2"
RESIDUAL_MODEL_SCHEMA = "dds-hierarchical-residual-v1"
LEAD_MODEL_SCHEMA = "dds-opening-lead-family-model-v1"
CURRENT_STAGE_SCHEMA = "dds-current-stage-manifest-v1"

_LABEL_PRIOR = {"low": 0.25, "medium": 0.50, "high": 0.75}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wilson_lower_bound(successes: int, total: int, *, z: float = 1.959963984540054) -> float:
    """Return a two-sided 95% Wilson lower confidence bound by default."""
    n = int(total)
    s = int(successes)
    if n <= 0:
        return 0.0
    if s < 0 or s > n:
        raise ValueError("successes must be in 0..total")
    p = s / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return _clamp((centre - margin) / denominator, 0.0, 1.0)


def prediction_raw_probability(prediction: Mapping[str, object]) -> float:
    for key in ("raw_confidence_probability", "confidence_probability"):
        value = prediction.get(key)
        if value is not None:
            return _clamp(float(value), 0.0, 1.0)
    return _LABEL_PRIOR.get(str(prediction.get("confidence", "low")).lower(), 0.25)


def task_family(task_type: str, strain: int | str | None) -> str:
    task_type = str(task_type)
    if task_type == "contract_tricks":
        return "contract_nt" if str(strain).upper() in {"4", "NT"} else "contract_suit"
    if task_type == "opening_lead":
        return "opening_lead_nt" if str(strain).upper() in {"4", "NT"} else "opening_lead_suit"
    if task_type in {"declarer_continuation", "defense_continuation"}:
        suffix = "nt" if str(strain).upper() in {"4", "NT"} else "suit"
        return f"{task_type}_{suffix}"
    return task_type


def row_success(row: Mapping[str, object]) -> bool:
    task_type = str(row["task_type"])
    prediction = row["prediction"]
    result = row["result"]
    if not isinstance(prediction, Mapping) or not isinstance(result, Mapping):
        raise TypeError("prediction and result must be mappings")
    if task_type == "contract_tricks":
        return int(prediction["tricks"]) == int(result["dds_tricks"])
    regret = result.get("dd_regret")
    return regret is not None and float(regret) == 0.0


def row_loss(row: Mapping[str, object]) -> float:
    task_type = str(row["task_type"])
    prediction = row["prediction"]
    result = row["result"]
    if not isinstance(prediction, Mapping) or not isinstance(result, Mapping):
        raise TypeError("prediction and result must be mappings")
    if task_type == "contract_tricks":
        return abs(int(prediction["tricks"]) - int(result["dds_tricks"]))
    regret = result.get("dd_regret")
    return 13.0 if regret is None else float(regret)


def _pav_blocks(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for block in blocks:
        out.append(dict(block))
        while len(out) >= 2 and out[-2]["rate"] > out[-1]["rate"]:
            right = out.pop()
            left = out.pop()
            n = int(left["n"]) + int(right["n"])
            successes = int(left["successes"]) + int(right["successes"])
            out.append(
                {
                    "raw_min": min(float(left["raw_min"]), float(right["raw_min"])),
                    "raw_max": max(float(left["raw_max"]), float(right["raw_max"])),
                    "n": n,
                    "successes": successes,
                    "rate": successes / n if n else 0.0,
                    "mean_raw": (
                        float(left["mean_raw"]) * int(left["n"])
                        + float(right["mean_raw"]) * int(right["n"])
                    )
                    / n,
                }
            )
    return out


def fit_isotonic_probability(
    observations: Sequence[tuple[float, bool]],
    *,
    maximum_bins: int = 12,
) -> dict:
    if not observations:
        return {"n": 0, "segments": [], "brier": None, "ece": None}
    ordered = sorted((_clamp(raw, 0.0, 1.0), bool(success)) for raw, success in observations)
    bin_count = max(1, min(int(maximum_bins), int(math.sqrt(len(ordered))) or 1))
    bin_size = max(1, math.ceil(len(ordered) / bin_count))
    primitive: list[dict] = []
    for start in range(0, len(ordered), bin_size):
        chunk = ordered[start : start + bin_size]
        successes = sum(success for _, success in chunk)
        primitive.append(
            {
                "raw_min": chunk[0][0],
                "raw_max": chunk[-1][0],
                "n": len(chunk),
                "successes": successes,
                "rate": successes / len(chunk),
                "mean_raw": statistics.fmean(raw for raw, _ in chunk),
            }
        )
    blocks = _pav_blocks(primitive)
    segments = []
    for block in blocks:
        segments.append(
            {
                **block,
                "calibrated_probability": float(block["rate"]),
                "lower_confidence_bound": wilson_lower_bound(block["successes"], block["n"]),
            }
        )
    brier = statistics.fmean((raw - (1.0 if success else 0.0)) ** 2 for raw, success in ordered)
    ece = sum(abs(segment["calibrated_probability"] - segment["mean_raw"]) * segment["n"] for segment in segments) / len(ordered)
    return {"n": len(ordered), "segments": segments, "brier": brier, "ece": ece}


def apply_isotonic_probability(model: Mapping[str, object], raw_probability: float) -> dict:
    raw = _clamp(raw_probability, 0.0, 1.0)
    segments = list(model.get("segments", []))
    if not segments:
        return {
            "calibrated_probability": 0.0,
            "lower_confidence_bound": 0.0,
            "support_count": 0,
        }
    selected = segments[-1]
    for segment in segments:
        if raw <= float(segment["raw_max"]) + 1e-12:
            selected = segment
            break
    return {
        "calibrated_probability": float(selected["calibrated_probability"]),
        "lower_confidence_bound": float(selected["lower_confidence_bound"]),
        "support_count": int(selected["n"]),
    }


def _calibration_keys(row: Mapping[str, object]) -> list[str]:
    prediction = row["prediction"]
    if not isinstance(prediction, Mapping):
        raise TypeError("prediction must be a mapping")
    family = task_family(str(row["task_type"]), row.get("strain"))
    backoff = str(prediction.get("model_backoff_level", "unknown"))
    return [f"family+backoff:{family}:{backoff}", f"family:{family}", "global"]


def fit_segmented_oof_calibrator(
    rows: Sequence[Mapping[str, object]],
    *,
    minimum_support: int = 50,
    maximum_bins: int = 12,
    review_threshold: float = 0.65,
) -> dict:
    eligible = [row for row in rows if bool(row.get("out_of_fold"))]
    if not eligible:
        raise ValueError("No out-of-fold rows supplied")
    grouped: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for row in eligible:
        raw = prediction_raw_probability(row["prediction"])
        success = row_success(row)
        for key in _calibration_keys(row):
            grouped[key].append((raw, success))
    groups = {
        key: fit_isotonic_probability(values, maximum_bins=maximum_bins)
        for key, values in sorted(grouped.items())
    }
    return {
        "schema": CALIBRATION_SCHEMA,
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "source": "family_safe_out_of_fold_train_only",
        "minimum_support": int(minimum_support),
        "review_threshold": float(review_threshold),
        "rows": len(eligible),
        "groups": groups,
    }


def apply_segmented_calibration(
    row: Mapping[str, object],
    calibrator: Mapping[str, object],
) -> dict:
    groups = calibrator.get("groups", {})
    minimum_support = int(calibrator.get("minimum_support", 50))
    raw = prediction_raw_probability(row["prediction"])
    selected_key = "global"
    selected_model = groups.get("global", {"segments": []})
    for key in _calibration_keys(row):
        candidate = groups.get(key)
        if candidate and int(candidate.get("n", 0)) >= minimum_support:
            selected_key = key
            selected_model = candidate
            break
    applied = apply_isotonic_probability(selected_model, raw)
    lower = float(applied["lower_confidence_bound"])
    supported = int(applied["support_count"]) >= minimum_support
    threshold = float(calibrator.get("review_threshold", 0.65))
    return {
        **applied,
        "raw_probability": raw,
        "calibration_group": selected_key,
        "confidence_supported": supported,
        "accept": bool(supported and lower >= threshold),
        "requires_deeper_review": bool((not supported) or lower < threshold),
        "calibration_schema": calibrator.get("schema"),
    }


def _sample_variance(values: Sequence[float]) -> float:
    return statistics.variance(values) if len(values) >= 2 else 0.0


def fit_hierarchical_residual_model(
    samples: Sequence[Mapping[str, object]],
    *,
    prior_strength: float = 25.0,
    minimum_support: int = 8,
    minimum_gain: float = 0.01,
) -> dict:
    if not samples:
        raise ValueError("No residual samples supplied")
    grouped: dict[str, dict[str, list[tuple[float, float, float]]]] = defaultdict(lambda: defaultdict(list))
    family_residuals: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        family = str(sample["family"])
        baseline = float(sample["baseline"])
        target = float(sample["target"])
        residual = target - baseline
        family_residuals[family].append(residual)
        levels = sample.get("levels")
        if not isinstance(levels, Sequence) or not levels:
            raise ValueError("Each residual sample requires levels")
        for level in levels:
            if not isinstance(level, Sequence) or len(level) != 2:
                raise ValueError("level must be (name, key)")
            name, key = str(level[0]), str(level[1])
            grouped[family][f"{name}:{key}"].append((baseline, target, residual))

    family_prior = {family: statistics.fmean(values) for family, values in family_residuals.items()}
    records: dict[str, dict[str, dict]] = {}
    for family, entries in grouped.items():
        records[family] = {}
        prior = float(family_prior[family])
        for composite, values in entries.items():
            residuals = [value[2] for value in values]
            n = len(values)
            mean = statistics.fmean(residuals)
            variance = _sample_variance(residuals)
            weight = n / (n + float(prior_strength) * (1.0 + variance))
            correction = weight * mean + (1.0 - weight) * prior
            baseline_mae = statistics.fmean(abs(target - baseline) for baseline, target, _ in values)
            candidate_mae = statistics.fmean(abs(target - round(baseline + correction)) for baseline, target, _ in values)
            gain = baseline_mae - candidate_mae
            level, key = composite.split(":", 1)
            records[family][composite] = {
                "level": level,
                "key": key,
                "n": n,
                "mean_residual": mean,
                "variance": variance,
                "shrinkage_weight": weight,
                "correction": correction,
                "baseline_mae": baseline_mae,
                "candidate_mae": candidate_mae,
                "estimated_gain": gain,
                "enabled": bool(n >= minimum_support and gain >= minimum_gain),
            }
    return {
        "schema": RESIDUAL_MODEL_SCHEMA,
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "prior_strength": float(prior_strength),
        "minimum_support": int(minimum_support),
        "minimum_gain": float(minimum_gain),
        "family_prior": family_prior,
        "records": records,
    }


def predict_hierarchical_residual(
    *,
    baseline: float,
    family: str,
    levels: Sequence[tuple[str, str]],
    model: Mapping[str, object],
    low: int = 0,
    high: int = 13,
) -> dict:
    family_records = model.get("records", {}).get(family, {})
    selected = None
    # Levels are expected broad -> exact; search exact -> broad.
    for name, key in reversed(list(levels)):
        record = family_records.get(f"{name}:{key}")
        if record and bool(record.get("enabled")):
            selected = record
            break
    if selected is None:
        correction = 0.0
        level = "baseline"
        support = 0
        variance = 0.0
        gain = 0.0
    else:
        correction = float(selected["correction"])
        level = str(selected["level"])
        support = int(selected["n"])
        variance = float(selected["variance"])
        gain = float(selected["estimated_gain"])
    prediction = max(low, min(high, int(round(float(baseline) + correction))))
    raw_probability = _clamp(1.0 / (1.0 + variance + abs(correction) * 0.25), 0.02, 0.98)
    return {
        "prediction": prediction,
        "baseline": float(baseline),
        "correction": correction,
        "backoff_level": level,
        "support_count": support,
        "residual_variance": variance,
        "estimated_gain": gain,
        "raw_confidence_probability": raw_probability,
    }


def fit_opening_lead_family_model(
    samples: Sequence[Mapping[str, object]],
    *,
    prior_strength: float = 30.0,
    minimum_support: int = 12,
) -> dict:
    if not samples:
        raise ValueError("No opening-lead samples supplied")
    family_values: dict[str, list[float]] = defaultdict(list)
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sample in samples:
        family = str(sample["family"])
        regret = max(0.0, float(sample["regret"]))
        family_values[family].append(regret)
        for name, key in sample["levels"]:
            grouped[family][f"{name}:{key}"].append(regret)
    priors = {
        family: {
            "mean_regret": statistics.fmean(values),
            "risk_2plus": sum(value >= 2.0 for value in values) / len(values),
            "optimal_rate": sum(value == 0.0 for value in values) / len(values),
            "n": len(values),
        }
        for family, values in family_values.items()
    }
    records: dict[str, dict[str, dict]] = {}
    for family, entries in grouped.items():
        records[family] = {}
        prior = priors[family]
        for composite, values in entries.items():
            n = len(values)
            variance = _sample_variance(values)
            weight = n / (n + float(prior_strength) * (1.0 + variance))
            mean = statistics.fmean(values)
            risk = sum(value >= 2.0 for value in values) / n
            optimal = sum(value == 0.0 for value in values) / n
            level, key = composite.split(":", 1)
            records[family][composite] = {
                "level": level,
                "key": key,
                "n": n,
                "variance": variance,
                "expected_regret": weight * mean + (1.0 - weight) * prior["mean_regret"],
                "risk_2plus": weight * risk + (1.0 - weight) * prior["risk_2plus"],
                "optimal_probability": weight * optimal + (1.0 - weight) * prior["optimal_rate"],
                "enabled": n >= minimum_support,
            }
    return {
        "schema": LEAD_MODEL_SCHEMA,
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "prior_strength": float(prior_strength),
        "minimum_support": int(minimum_support),
        "family_prior": priors,
        "records": records,
    }


def rank_opening_leads(
    candidates: Sequence[Mapping[str, object]],
    *,
    family: str,
    model: Mapping[str, object],
    alternatives: int = 3,
) -> dict:
    if not candidates:
        raise ValueError("No opening-lead candidates supplied")
    family_records = model.get("records", {}).get(family, {})
    prior = model.get("family_prior", {}).get(
        family,
        {"mean_regret": 1.0, "risk_2plus": 0.25, "optimal_rate": 0.25, "n": 0},
    )
    scored = []
    for candidate in candidates:
        selected = None
        for name, key in reversed(list(candidate["levels"])):
            record = family_records.get(f"{name}:{key}")
            if record and bool(record.get("enabled")):
                selected = record
                break
        if selected is None:
            expected_regret = float(prior["mean_regret"])
            risk_2plus = float(prior["risk_2plus"])
            optimal_probability = float(prior["optimal_rate"])
            support = int(prior.get("n", 0))
            backoff = "family_prior"
        else:
            expected_regret = float(selected["expected_regret"])
            risk_2plus = float(selected["risk_2plus"])
            optimal_probability = float(selected["optimal_probability"])
            support = int(selected["n"])
            backoff = str(selected["level"])
        heuristic = float(candidate.get("heuristic", 0.0))
        selection_score = expected_regret - 0.002 * heuristic
        scored.append(
            {
                "card": str(candidate["card"]).upper(),
                "expected_regret": expected_regret,
                "risk_2plus": risk_2plus,
                "raw_confidence_probability": optimal_probability,
                "support_count": support,
                "backoff_level": backoff,
                "selection_score": selection_score,
            }
        )
    scored.sort(key=lambda row: (row["selection_score"], row["risk_2plus"], row["card"]))
    best = scored[0]
    return {
        "card": best["card"],
        "expected_regret": best["expected_regret"],
        "risk_2plus": best["risk_2plus"],
        "raw_confidence_probability": best["raw_confidence_probability"],
        "support_count": best["support_count"],
        "model_backoff_level": best["backoff_level"],
        "alternatives": scored[: max(1, int(alternatives))],
        "family": family,
    }


def _stable_noise(seed: int, identity: str) -> float:
    digest = hashlib.sha256(f"{seed}:{identity}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def balanced_curriculum(
    items: Sequence[Mapping[str, object]],
    *,
    total: int,
    seed: int = 20260818,
) -> list[dict]:
    if total < 1:
        return []
    actors = {"declarer": [], "defense": []}
    for raw in items:
        actor = str(raw.get("actor", ""))
        if actor not in actors:
            continue
        item = dict(raw)
        identity = str(item.get("task_id") or item.get("position_id") or canonical_json(item).hex())
        item["_noise"] = _stable_noise(seed, identity)
        actors[actor].append(item)
    for actor in actors:
        actors[actor].sort(
            key=lambda row: (
                -float(row.get("priority", 0.0)),
                -float(row.get("severity", 0.0)),
                row["_noise"],
                str(row.get("task_id", "")),
            )
        )
    target_declarer = total // 2
    target_defense = total - target_declarer
    selected = actors["declarer"][:target_declarer] + actors["defense"][:target_defense]
    if len(selected) < total:
        chosen_ids = {str(item.get("task_id")) for item in selected}
        remainder = [
            item
            for actor in ("declarer", "defense")
            for item in actors[actor]
            if str(item.get("task_id")) not in chosen_ids
        ]
        remainder.sort(key=lambda row: (-float(row.get("priority", 0.0)), row["_noise"]))
        selected.extend(remainder[: total - len(selected)])
    for item in selected:
        item.pop("_noise", None)
    selected.sort(key=lambda row: (str(row.get("actor")), -float(row.get("priority", 0.0)), str(row.get("task_id", ""))))
    return selected[:total]


def aggregate_review_queue(
    rows: Sequence[Mapping[str, object]],
    *,
    max_tasks_per_group: int = 250,
    representative_limit: int = 10,
) -> list[dict]:
    grouped: dict[tuple[str, str, str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("skill_key", "unknown")),
            str(row.get("error_code", "unknown")),
            str(row.get("strain", "unknown")),
            str(row.get("mechanism", "unknown")),
            str(row.get("due_window", "unknown")),
        )
        grouped[key].append(row)
    out = []
    for key, values in sorted(grouped.items()):
        requested = min(max_tasks_per_group, sum(max(1, int(value.get("requested_tasks", 1))) for value in values))
        ordered = sorted(
            values,
            key=lambda row: (
                -float(row.get("severity", row.get("regret", 0.0) or 0.0)),
                -float(row.get("confidence_probability", 0.0)),
                str(row.get("task_id", "")),
            ),
        )
        representatives = []
        seen_families = set()
        for value in ordered:
            family = str(value.get("family_id") or value.get("root_deal_id") or value.get("deal_id") or "")
            if family in seen_families and len(representatives) < representative_limit // 2:
                continue
            representatives.append(str(value.get("task_id", "")))
            seen_families.add(family)
            if len(representatives) >= representative_limit:
                break
        out.append(
            {
                "skill_key": key[0],
                "error_code": key[1],
                "strain": key[2],
                "mechanism": key[3],
                "due_window": key[4],
                "source_rows": len(values),
                "requested_tasks": requested,
                "representative_task_ids": representatives,
                "distinct_families": len(
                    {
                        str(value.get("family_id") or value.get("root_deal_id") or value.get("deal_id") or "")
                        for value in values
                    }
                ),
            }
        )
    return out


def investigation_resolution_status(
    prediction: Mapping[str, object],
    *,
    trajectory: Mapping[str, object] | None = None,
) -> dict:
    line = prediction.get("line") or prediction.get("continuation_plan") or []
    if not isinstance(line, Sequence) or isinstance(line, (str, bytes)):
        line = []
    first_error = None if trajectory is None else trajectory.get("first_error")
    decision_errors = [] if trajectory is None else list(trajectory.get("decision_errors", []))
    if line and (first_error or decision_errors):
        return {
            "resolution_status": "resolved_at_card_level",
            "promotion_eligible": True,
            "line_cards": len(line),
            "first_error": first_error or decision_errors[0],
        }
    return {
        "resolution_status": "resolved_structurally",
        "promotion_eligible": False,
        "line_cards": len(line),
        "requires_line_bearing_recheck": True,
    }


def build_current_stage_manifest(
    *,
    current_stage: str,
    current_algorithm: str,
    canonical_files: Mapping[str, Path],
    holdout_status: str,
    sealed_status: str,
    next_gate: str,
    metadata: Mapping[str, object] | None = None,
) -> dict:
    files = {}
    for role, path in sorted(canonical_files.items()):
        if not path.is_file():
            raise FileNotFoundError(f"Canonical file for {role!r} is missing: {path}")
        files[role] = {
            "path": path.name,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    return {
        "schema": CURRENT_STAGE_SCHEMA,
        "current_stage": current_stage,
        "current_algorithm": current_algorithm,
        "canonical_files": files,
        "holdout_status": holdout_status,
        "sealed_status": sealed_status,
        "next_gate": next_gate,
        "metadata": dict(metadata or {}),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
