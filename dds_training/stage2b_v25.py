from __future__ import annotations

"""Evidence-driven refinements for DDS Learning Stage 2B candidate v2.5.

This module builds on the v2.4 preparation code after the first real 42,000-row
OOF run exposed four concrete limitations:

* zero-support contract fallbacks received an implausibly high raw probability;
* the aggregate comparison mixed baseline-v0.1 and adaptive-v0.2 sources;
* 500 line sources yielded 1,062 declarer versus 938 defense continuations;
* the operational review projection lost the actual NT/suit denomination.

The v2.5 helpers are fail-closed and TRAIN-only.  They do not call DDS, open a
holdout, mutate historical evidence, or promote a candidate automatically.
"""

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from typing import Mapping, Sequence

from stage2b_v24 import (
    apply_segmented_calibration,
    fit_segmented_oof_calibrator,
    row_success,
    task_family,
)

CANDIDATE_ALGORITHM_VERSION = "dds-learning-v2.5-stage2b-candidate"
STRATIFIED_COMPARISON_SCHEMA = "dds-stage2b-stratified-oof-comparison-v2"
CALIBRATION_DIAGNOSTICS_SCHEMA = "dds-stage2b-calibration-diagnostics-v2"
FAMILY_POLICY_SCHEMA = "dds-stage2b-family-selection-policy-v1"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def support_aware_contract_probability(prediction: Mapping[str, object]) -> float:
    """Estimate pre-calibration exactness without rewarding missing evidence.

    v2.4 used ``1/(1+variance+|correction|/4)``.  A baseline fallback has zero
    stored variance and zero correction, so that formula incorrectly returned
    one even though the supporting sample count was zero.  v2.5 makes support a
    mandatory multiplicative term and assigns a deliberately low prior to an
    unsupported fallback.
    """

    support = max(0, int(prediction.get("model_evidence_count", 0) or 0))
    backoff = str(prediction.get("model_backoff_level", "baseline"))
    variance = max(0.0, float(prediction.get("residual_variance", 0.0) or 0.0))
    correction = abs(float(prediction.get("correction", 0.0) or 0.0))

    if support == 0 or backoff == "baseline":
        return 0.18

    support_factor = support / (support + 75.0)
    stability_factor = 1.0 / (1.0 + variance)
    correction_factor = math.exp(-0.20 * correction)
    backoff_factor = {
        "exact": 1.00,
        "coarse": 0.90,
        "broad": 0.76,
        "family": 0.66,
    }.get(backoff, 0.60)
    probability = 0.12 + 0.76 * support_factor * stability_factor * correction_factor * backoff_factor
    return _clamp(probability, 0.05, 0.95)


def rewrite_oof_raw_probabilities(rows: Sequence[Mapping[str, object]]) -> list[dict]:
    """Return copied OOF rows with support-aware raw probabilities.

    Opening-lead models already estimate the empirical probability that a card
    is equal-optimal.  Their probability is retained unless the record has no
    support, in which case a conservative family prior is used.
    """

    rewritten: list[dict] = []
    for raw in rows:
        row = dict(raw)
        prediction = dict(row["prediction"])
        if str(row["task_type"]) == "contract_tricks":
            probability = support_aware_contract_probability(prediction)
        else:
            support = max(0, int(prediction.get("model_evidence_count", 0) or 0))
            probability = float(
                prediction.get(
                    "raw_confidence_probability",
                    prediction.get("confidence_probability", 0.25),
                )
                or 0.0
            )
            if support == 0:
                probability = min(probability, 0.25)
            probability = _clamp(probability, 0.02, 0.98)
        prediction["raw_confidence_probability"] = probability
        prediction["confidence_probability"] = probability
        prediction["raw_confidence_policy"] = "support-aware-v2"
        row["prediction"] = prediction
        rewritten.append(row)
    return rewritten


def recalibrate_oof_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    minimum_support: int = 50,
    review_threshold: float = 0.65,
) -> tuple[list[dict], dict]:
    rewritten = rewrite_oof_raw_probabilities(rows)
    calibration_rows = [
        {
            "task_type": row["task_type"],
            "strain": row["strain"],
            "prediction": row["prediction"],
            "result": row["result"],
            "out_of_fold": True,
        }
        for row in rewritten
    ]
    calibrator = fit_segmented_oof_calibrator(
        calibration_rows,
        minimum_support=minimum_support,
        maximum_bins=12,
        review_threshold=review_threshold,
    )
    calibrator["algorithm_version"] = CANDIDATE_ALGORITHM_VERSION
    calibrator["raw_probability_policy"] = "support-aware-v2"

    output: list[dict] = []
    for row in rewritten:
        calibrated = apply_segmented_calibration(
            {
                "task_type": row["task_type"],
                "strain": row["strain"],
                "prediction": row["prediction"],
            },
            calibrator,
        )
        out = dict(row)
        out["prediction"] = {**row["prediction"], **calibrated}
        output.append(out)
    return output, calibrator


def _task_loss(task_type: str, prediction: Mapping[str, object], result: Mapping[str, object]) -> float:
    if task_type == "contract_tricks":
        return abs(int(prediction["tricks"]) - int(result["dds_tricks"]))
    scores = {str(card).upper(): float(value) for card, value in result.get("scores", {}).items()}
    chosen = str(prediction.get("card", "")).upper()
    if scores and chosen in scores:
        return max(scores.values()) - scores[chosen]
    regret = result.get("dd_regret")
    return 13.0 if regret is None else float(regret)


def paired_bootstrap_interval(
    improvements: Sequence[float],
    *,
    samples: int = 2000,
    seed: int = 20260818,
) -> tuple[float, float]:
    if not improvements:
        return 0.0, 0.0
    if len(improvements) == 1:
        value = float(improvements[0])
        return value, value
    rng = random.Random(seed)
    values = [float(value) for value in improvements]
    n = len(values)
    means = []
    for _ in range(max(100, int(samples))):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lower_index = max(0, int(0.025 * (len(means) - 1)))
    upper_index = min(len(means) - 1, int(0.975 * (len(means) - 1)))
    return means[lower_index], means[upper_index]


def _group_metrics(rows: Sequence[Mapping[str, object]], *, seed: int) -> dict:
    old_losses = []
    new_losses = []
    improvements = []
    old_success = new_success = 0
    old_2plus = new_2plus = 0
    task_type = str(rows[0]["task_type"])

    for row in rows:
        old_loss = _task_loss(task_type, row["source_prediction"], row["result"])
        new_loss = _task_loss(task_type, row["prediction"], row["result"])
        old_losses.append(old_loss)
        new_losses.append(new_loss)
        improvements.append(old_loss - new_loss)
        old_success += old_loss == 0.0
        new_success += new_loss == 0.0
        old_2plus += old_loss >= 2.0
        new_2plus += new_loss >= 2.0

    lower, upper = paired_bootstrap_interval(improvements, seed=seed)
    n = len(rows)
    mean_improvement = statistics.fmean(improvements)
    practical_threshold = 0.01
    supported = bool(mean_improvement >= practical_threshold and lower > 0.0)
    return {
        "n": n,
        "source_mean_loss": statistics.fmean(old_losses),
        "candidate_mean_loss": statistics.fmean(new_losses),
        "mean_improvement": mean_improvement,
        "bootstrap_95_lower": lower,
        "bootstrap_95_upper": upper,
        "source_zero_loss_rate": old_success / n,
        "candidate_zero_loss_rate": new_success / n,
        "source_loss_2plus_rate": old_2plus / n,
        "candidate_loss_2plus_rate": new_2plus / n,
        "candidate_better": sum(new < old for old, new in zip(old_losses, new_losses)),
        "candidate_worse": sum(new > old for old, new in zip(old_losses, new_losses)),
        "ties": sum(new == old for old, new in zip(old_losses, new_losses)),
        "practical_threshold": practical_threshold,
        "candidate_supported": supported,
    }


def stratified_oof_comparison(rows: Sequence[Mapping[str, object]]) -> dict:
    """Compare the candidate by source predictor and bridge family.

    The first v2.4 report combined pilot baseline-v0.1 rows with main
    adaptive-v0.2 rows.  v2.5 keeps that historical aggregate visible but makes
    the adaptive-v0.2 family comparison the primary model-selection evidence.
    """

    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        source_version = str(row["source_prediction"].get("predictor_version", "unknown"))
        family = task_family(str(row["task_type"]), row.get("strain"))
        grouped[(source_version, family)].append(row)

    source_versions: dict[str, dict[str, dict]] = defaultdict(dict)
    for index, ((source_version, family), group) in enumerate(sorted(grouped.items())):
        source_versions[source_version][family] = _group_metrics(group, seed=20260818 + index)

    primary_source = "bridge-adaptive-v0.2"
    primary = source_versions.get(primary_source, {})
    family_policy = {}
    for family in sorted({family for _, family in grouped}):
        metrics = primary.get(family)
        if metrics and metrics["candidate_supported"]:
            selected = "candidate_v0.3"
            reason = "paired OOF improvement exceeds the practical threshold and bootstrap lower bound is positive"
        else:
            selected = "source_v0.2_fallback"
            reason = "candidate improvement is not yet statistically and practically established on adaptive-v0.2 OOF rows"
        family_policy[family] = {
            "selected_for_future_validation": selected,
            "reason": reason,
            "metrics": metrics,
        }

    return {
        "schema": STRATIFIED_COMPARISON_SCHEMA,
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "primary_source_predictor": primary_source,
        "source_versions": dict(source_versions),
        "family_policy": {
            "schema": FAMILY_POLICY_SCHEMA,
            "automatic_promotion": False,
            "families": family_policy,
        },
    }


def _ece(probabilities: Sequence[float], outcomes: Sequence[int], *, bins: int = 10) -> float:
    if not probabilities:
        return 0.0
    bucketed: dict[int, list[int]] = defaultdict(list)
    for index, probability in enumerate(probabilities):
        bucket = min(bins - 1, max(0, int(_clamp(probability) * bins)))
        bucketed[bucket].append(index)
    total = len(probabilities)
    return sum(
        abs(
            statistics.fmean(probabilities[index] for index in indexes)
            - statistics.fmean(outcomes[index] for index in indexes)
        )
        * len(indexes)
        / total
        for indexes in bucketed.values()
    )


def calibration_diagnostics(rows: Sequence[Mapping[str, object]]) -> dict:
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        family = task_family(str(row["task_type"]), row.get("strain"))
        backoff = str(row["prediction"].get("model_backoff_level", "unknown"))
        grouped[(family, backoff)].append(row)

    groups: dict[str, dict] = {}
    for (family, backoff), group in sorted(grouped.items()):
        raw = [float(row["prediction"].get("raw_probability", row["prediction"].get("raw_confidence_probability", 0.0))) for row in group]
        calibrated = [float(row["prediction"].get("calibrated_probability", 0.0)) for row in group]
        lower = [float(row["prediction"].get("lower_confidence_bound", 0.0)) for row in group]
        outcomes = [1 if row_success(row) else 0 for row in group]
        accepted = [bool(row["prediction"].get("accept", False)) for row in group]
        accepted_indexes = [index for index, value in enumerate(accepted) if value]
        key = f"{family}:{backoff}"
        groups[key] = {
            "family": family,
            "backoff": backoff,
            "n": len(group),
            "raw_brier": statistics.fmean((p - y) ** 2 for p, y in zip(raw, outcomes)),
            "calibrated_brier": statistics.fmean((p - y) ** 2 for p, y in zip(calibrated, outcomes)),
            "raw_ece": _ece(raw, outcomes),
            "calibrated_ece": _ece(calibrated, outcomes),
            "mean_raw_probability": statistics.fmean(raw),
            "mean_calibrated_probability": statistics.fmean(calibrated),
            "mean_lower_bound": statistics.fmean(lower),
            "accepted": len(accepted_indexes),
            "accepted_actual_success_rate": (
                None
                if not accepted_indexes
                else statistics.fmean(outcomes[index] for index in accepted_indexes)
            ),
        }
    return {
        "schema": CALIBRATION_DIAGNOSTICS_SCHEMA,
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "groups": groups,
    }


def exact_balanced_curriculum(
    items: Sequence[Mapping[str, object]],
    *,
    per_actor: int,
    seed: int = 20260818,
) -> list[dict]:
    """Select exactly the same count of declarer and defense positions.

    A missing side is a preparation blocker rather than a reason to silently
    replace it with positions from the other side.
    """

    actors: dict[str, list[dict]] = {"declarer": [], "defense": []}
    seen = set()
    for raw in items:
        actor = str(raw.get("actor", ""))
        if actor not in actors:
            continue
        identity = str(raw.get("position_id") or raw.get("task_id"))
        unique = (actor, identity)
        if unique in seen:
            continue
        seen.add(unique)
        item = dict(raw)
        digest = hashlib.sha256(f"{seed}:{identity}".encode("utf-8")).digest()
        item["_noise"] = int.from_bytes(digest[:8], "big") / 2**64
        actors[actor].append(item)

    for actor, values in actors.items():
        values.sort(
            key=lambda row: (
                -float(row.get("priority", 0.0)),
                -float(row.get("severity", 0.0)),
                row["_noise"],
                str(row.get("task_id", "")),
            )
        )
        if len(values) < per_actor:
            raise ValueError(
                f"Insufficient {actor} continuation candidates: {len(values)} < {per_actor}"
            )

    selected = actors["declarer"][:per_actor] + actors["defense"][:per_actor]
    for row in selected:
        row.pop("_noise", None)
    selected.sort(
        key=lambda row: (
            str(row.get("actor")),
            -float(row.get("priority", 0.0)),
            str(row.get("task_id", "")),
        )
    )
    return selected


def enrich_review_rows(
    rows: Sequence[Mapping[str, object]],
    task_metadata: Mapping[str, Mapping[str, object]],
) -> list[dict]:
    output = []
    for raw in rows:
        row = dict(raw)
        task = task_metadata.get(str(row.get("task_id", "")), {})
        strain = task.get("strain", row.get("strain", "unknown"))
        row["strain"] = "NT" if str(strain).upper() in {"4", "NT"} else str(strain)
        row["family_id"] = str(
            task.get("root_deal_id")
            or task.get("deal_id")
            or row.get("family_id")
            or row.get("deal_id")
            or "unknown"
        )
        output.append(row)
    return output
