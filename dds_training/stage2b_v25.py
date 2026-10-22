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
    OPENING_LEAD_OUTCOME_POLICY,
    apply_segmented_calibration,
    fit_segmented_oof_calibrator,
    opening_lead_loss,
    row_success,
    task_family,
)

CANDIDATE_ALGORITHM_VERSION = "dds-learning-v2.5-stage2b-candidate"
STRATIFIED_COMPARISON_SCHEMA = "dds-stage2b-stratified-oof-comparison-v2"
CALIBRATION_DIAGNOSTICS_SCHEMA = "dds-stage2b-calibration-diagnostics-v2"
CROSSFIT_CALIBRATION_DIAGNOSTICS_SCHEMA = "dds-stage2b-crossfit-calibration-diagnostics-v1"
FAMILY_POLICY_SCHEMA = "dds-stage2b-family-selection-policy-v1"
OOF_INPUT_POLICY = "explicit-train-oof-family-fold-v1"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _finite_prediction_number(prediction: Mapping[str, object], key: str, default: float) -> float:
    """Only an absent field may use its policy default; explicit null is invalid."""
    value = prediction.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Prediction {key} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Prediction {key} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"Prediction {key} must be a finite number")
    return number


def _prediction_support(prediction: Mapping[str, object]) -> int:
    support = _finite_prediction_number(prediction, "model_evidence_count", 0)
    if support < 0 or not support.is_integer():
        raise ValueError("Prediction model_evidence_count must be a nonnegative integer")
    return int(support)


def support_aware_contract_probability(prediction: Mapping[str, object]) -> float:
    """Estimate pre-calibration exactness without rewarding missing evidence.

    v2.4 used ``1/(1+variance+|correction|/4)``.  A baseline fallback has zero
    stored variance and zero correction, so that formula incorrectly returned
    one even though the supporting sample count was zero.  v2.5 makes support a
    mandatory multiplicative term and assigns a deliberately low prior to an
    unsupported fallback. Missing support/variance/correction default to zero;
    provided values must be finite, with nonnegative integral support and
    nonnegative variance. Correction is signed. Null is not a missing field.
    """

    support = _prediction_support(prediction)
    backoff = str(prediction.get("model_backoff_level", "baseline"))
    variance = _finite_prediction_number(prediction, "residual_variance", 0.0)
    if variance < 0:
        raise ValueError("Prediction residual_variance must be nonnegative")
    correction = abs(_finite_prediction_number(prediction, "correction", 0.0))

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


def validate_oof_rows(rows: Sequence[Mapping[str, object]]) -> None:
    """Reject missing/contradictory provenance; never manufacture OOF status.

    This checks metadata consistency, not the truth of a caller's assertion.
    The producer must still prove training/heldout family exclusion.
    """
    if not rows:
        raise ValueError("No explicit TRAIN OOF rows supplied")
    task_ids: set[str] = set()
    family_folds: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("OOF rows must be mappings")
        if row.get("out_of_fold") is not True or row.get("split") != "train":
            raise ValueError("Every calibration row must explicitly be TRAIN and out_of_fold=true")
        for key in ("task_id", "family_id"):
            value = row.get(key)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"OOF {key} must be a nonempty canonical string")
        task_id, family = row["task_id"], row["family_id"]
        if task_id in task_ids:
            raise ValueError("Duplicate OOF task_id would inflate calibration support")
        task_ids.add(task_id)
        fold = row.get("heldout_fold")
        if type(fold) is not int or fold < 0:
            raise ValueError("OOF heldout_fold must be a nonnegative integer")
        if "fold" in row and (type(row["fold"]) is not int or row["fold"] != fold):
            raise ValueError("OOF fold contradicts heldout_fold")
        if family in family_folds and family_folds[family] != fold:
            raise ValueError("OOF family appears in multiple heldout folds")
        family_folds[family] = fold
        prediction = row.get("prediction")
        if not isinstance(prediction, Mapping) or not isinstance(row.get("result"), Mapping):
            raise ValueError("OOF prediction and result must be mappings")
        if "out_of_fold" in prediction and prediction["out_of_fold"] is not True:
            raise ValueError("Prediction contradicts explicit OOF provenance")
        for name in ("prediction", "result"):
            record = row[name]
            for key, expected in (("task_id", task_id), ("split", "train"),
                                  ("family_id", family), ("task_type", row.get("task_type"))):
                if key in record and record[key] != expected:
                    raise ValueError(f"OOF {name} {key} contradicts row provenance")
            for key in ("fold", "heldout_fold"):
                if key in record and (type(record[key]) is not int or record[key] != fold):
                    raise ValueError(f"OOF {name} fold contradicts row provenance")
        if "task" in row:
            task = row["task"]
            if not isinstance(task, Mapping):
                raise ValueError("OOF task metadata must be a mapping")
            if task.get("split") != "train" or task.get("task_id") != task_id:
                raise ValueError("OOF task identity/split contradicts row provenance")
            if "task_type" in task and task["task_type"] != row["task_type"]:
                raise ValueError("OOF task type contradicts row provenance")
            task_fold = task.get("crossfit_fold")
            if type(task_fold) is not int or task_fold != fold:
                raise ValueError("OOF task fold contradicts heldout_fold")
            if (task.get("root_deal_id") or task.get("deal_id")) != family:
                raise ValueError("OOF task family contradicts row provenance")


def rewrite_oof_raw_probabilities(rows: Sequence[Mapping[str, object]]) -> list[dict]:
    """Return copied OOF rows with support-aware raw probabilities.

    Opening-lead models already estimate the empirical probability that a card
    is equal-optimal.  Their probability is retained unless the record has no
    support, in which case a conservative family prior is used. Every provided
    raw/confidence probability must be finite in [0, 1], even when superseded.
    Only absent raw probability may use confidence_probability; if both are
    absent the explicit missing-evidence prior is 0.25. Missing support is zero.
    Valid boundary probabilities still follow the existing [0.02, 0.98] policy.
    """

    validate_oof_rows(rows)
    rewritten: list[dict] = []
    for raw in rows:
        row = dict(raw)
        prediction = dict(row["prediction"])
        for key in ("raw_confidence_probability", "confidence_probability"):
            if key in prediction:
                provided = _finite_prediction_number(prediction, key, 0.25)
                if not 0.0 <= provided <= 1.0:
                    raise ValueError(f"Prediction {key} must be in [0, 1]")
        if str(row["task_type"]) == "contract_tricks":
            probability = support_aware_contract_probability(prediction)
        else:
            support = _prediction_support(prediction)
            probability = float(
                prediction.get(
                    "raw_confidence_probability",
                    prediction.get("confidence_probability", 0.25),
                )
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
    calibrator = fit_segmented_oof_calibrator(
        rewritten,
        minimum_support=minimum_support,
        maximum_bins=12,
        review_threshold=review_threshold,
    )
    calibrator["algorithm_version"] = CANDIDATE_ALGORITHM_VERSION
    calibrator["raw_probability_policy"] = "support-aware-v2"
    calibrator["oof_input_policy"] = OOF_INPUT_POLICY

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
    if task_type == "opening_lead":
        return opening_lead_loss(prediction, result)
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
    """Describe final-calibrator fit rows; this is resubstitution, not evaluation."""
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
        "opening_lead_outcome_policy": OPENING_LEAD_OUTCOME_POLICY,
        "evaluation_sample": "fit_sample",
        "sampling": "in_sample",
        "calibrator_fit_family_disjoint": False,
        "independent_evaluation": False,
        "upstream_model_training_lineage_verified": False,
        "full_pipeline_quality": False,
        "full_pipeline_quality_evidence": False,
        "promotion": False,
        "groups": groups,
    }


def _identifier_set_sha256(rows: Sequence[Mapping[str, object]], key: str) -> str:
    identities = sorted({row[key] for row in rows})
    payload = json.dumps(identities, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def crossfit_calibration_diagnostics(
    rows: Sequence[Mapping[str, object]],
    *,
    minimum_support: int = 50,
    review_threshold: float = 0.65,
) -> dict:
    """Assess direct calibrator fit-family exclusion on supplied TRAIN OOF rows.

    Each calibrator excludes the families whose predictions it scores. This
    is conditional on supplied base predictions, not a nested pipeline test:
    another fold's base model may have trained on the evaluated families.
    Upstream fits and hyperparameter selection require a separate outer-family
    holdout before any full-pipeline quality or promotion claim is possible.
    Input evidence and the final all-row application calibrator are untouched.
    """
    rewritten = sorted(rewrite_oof_raw_probabilities(rows), key=lambda row: row["task_id"])
    folds = sorted({row["heldout_fold"] for row in rewritten})
    if len(folds) < 2:
        raise ValueError("Calibration assessment requires at least two nonempty family folds")

    evaluated = []
    fold_reports = []
    for fold in folds:
        fit_rows = [row for row in rewritten if row["heldout_fold"] != fold]
        heldout = [row for row in rewritten if row["heldout_fold"] == fold]
        fit_families = {row["family_id"] for row in fit_rows}
        heldout_families = {row["family_id"] for row in heldout}
        family_overlap = fit_families & heldout_families
        task_overlap = {row["task_id"] for row in fit_rows} & {row["task_id"] for row in heldout}
        if family_overlap or task_overlap:
            raise ValueError("Calibrator fit and evaluation families/tasks must be disjoint")
        calibrator = fit_segmented_oof_calibrator(
            fit_rows, minimum_support=minimum_support,
            maximum_bins=12, review_threshold=review_threshold,
        )
        calibrator["algorithm_version"] = CANDIDATE_ALGORITHM_VERSION
        calibrator["raw_probability_policy"] = "support-aware-v2"
        calibrator["oof_input_policy"] = OOF_INPUT_POLICY
        calibrated = [
            {**row, "prediction": {**row["prediction"], **apply_segmented_calibration(row, calibrator)}}
            for row in heldout
        ]
        evaluated.extend(calibrated)
        fold_reports.append({
            "heldout_fold": fold,
            "fit_folds": [value for value in folds if value != fold],
            "fit_rows": len(fit_rows),
            "heldout_rows": len(heldout),
            "fit_families": len(fit_families),
            "heldout_families": len(heldout_families),
            "family_overlap": len(family_overlap),
            "task_overlap": len(task_overlap),
            "fit_task_ids_sha256": _identifier_set_sha256(fit_rows, "task_id"),
            "heldout_task_ids_sha256": _identifier_set_sha256(heldout, "task_id"),
            "fit_family_ids_sha256": _identifier_set_sha256(fit_rows, "family_id"),
            "heldout_family_ids_sha256": _identifier_set_sha256(heldout, "family_id"),
            "calibrator_sha256": hashlib.sha256(json.dumps(
                calibrator, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest(),
            "groups": calibration_diagnostics(calibrated)["groups"],
        })
    return {
        "schema": CROSSFIT_CALIBRATION_DIAGNOSTICS_SCHEMA,
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "opening_lead_outcome_policy": OPENING_LEAD_OUTCOME_POLICY,
        "scope": "calibrator_fit_only",
        "conditional_on_supplied_base_predictions": True,
        "evaluation_sample": "heldout_train_families",
        "calibrator_fit_family_disjoint": True,
        "upstream_model_training_lineage_verified": False,
        "full_pipeline_quality": False,
        "full_pipeline_quality_evidence": False,
        "promotion": False,
        "limitation": "Base models for calibrator-fit rows may have trained on evaluated families; no nested pipeline qualification.",
        "identity_hash_format": "sha256-sorted-unique-ids-compact-utf8-json-v1",
        "rows": len(evaluated),
        "families": len({row["family_id"] for row in evaluated}),
        "folds": fold_reports,
        "groups": calibration_diagnostics(evaluated)["groups"],
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
