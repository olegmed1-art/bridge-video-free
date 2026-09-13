"""Portable freeze-bound evaluator for the Bridgit independent holdout.

This module is metadata-only and stdlib-only. It never imports or invokes the
recognizer, OpenCV, media/server adapters, Oracle/IBM APIs, Drive, Neon, or
Canon code. The same sealed metadata bundle can therefore be scored on either
Oracle or IBM when the exact evaluator source and freeze receipt are preserved.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bridge_vision.bridgit_holdout_measurement import (
    HoldoutMeasurementError,
    MINIMUM_PRECISION,
    MINIMUM_RECALL,
    SCORER_VERSION,
    score_holdout,
)

PORTABLE_EVALUATOR_VERSION = "bridgit-holdout-portable-evaluator-v1"
FREEZE_RECEIPT_VERSION = "bridgit-holdout-freeze-v1"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HASH_BINDINGS = (
    "profile_sha256",
    "config_sha256",
    "train_template_exclusion_manifest_sha256",
    "holdout_manifest_sha256",
    "human_gold_sha256",
    "recognizer_output_sha256",
    "evaluator_source_sha256",
)


def frozen_thresholds() -> dict[str, Any]:
    """Return the exact mechanical thresholds bound before holdout reveal."""
    return {
        "minimum_precision": MINIMUM_PRECISION,
        "minimum_recall": MINIMUM_RECALL,
        "maximum_seat_errors": 0,
        "maximum_false_complete_deals": 0,
        "frozen_before_holdout_reveal": True,
    }


def frozen_thresholds_sha256() -> str:
    payload = json.dumps(
        frozen_thresholds(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluator_source_sha256() -> str:
    """Hash the exact evaluator bytes that must be identical on Oracle and IBM."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _require_bool(raw: Mapping[str, Any], field: str, expected: bool) -> None:
    if raw.get(field) is not expected:
        raise HoldoutMeasurementError(f"freeze receipt requires {field}={expected}")


def _require_hash(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise HoldoutMeasurementError(f"invalid freeze hash: {field}")
    return value


def _validate_freeze_receipt(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise HoldoutMeasurementError("freeze must be an object")
    if raw.get("freeze_receipt_version") != FREEZE_RECEIPT_VERSION:
        raise HoldoutMeasurementError("invalid freeze receipt version")
    head = raw.get("recognizer_head_git_sha")
    if not isinstance(head, str) or _HEX40.fullmatch(head) is None:
        raise HoldoutMeasurementError("invalid recognizer_head_git_sha")
    if raw.get("portable_evaluator_version") != PORTABLE_EVALUATOR_VERSION:
        raise HoldoutMeasurementError("portable evaluator version mismatch")
    if raw.get("measurement_scorer_version") != SCORER_VERSION:
        raise HoldoutMeasurementError("measurement scorer version mismatch")
    for field in _HASH_BINDINGS:
        _require_hash(raw, field)
    if raw.get("evaluator_source_sha256") != evaluator_source_sha256():
        raise HoldoutMeasurementError("evaluator source digest mismatch")
    if raw.get("thresholds_sha256") != frozen_thresholds_sha256():
        raise HoldoutMeasurementError("frozen threshold digest mismatch")
    _require_bool(raw, "frozen_before_holdout_reveal", True)
    _require_bool(raw, "threshold_tuning_on_holdout", False)
    _require_bool(raw, "fourth_hand_reconstruction_allowed", False)
    _require_bool(raw, "logical_inference_as_visual_allowed", False)
    return dict(raw)


def _split_unknown_review(cases: Sequence[Any]) -> tuple[int, int]:
    unknown = 0
    needs_review = 0
    for raw in cases:
        if not isinstance(raw, Mapping):
            raise HoldoutMeasurementError("case must be an object")
        gold_raw = raw.get("gold_card_seat_pairs")
        accepted_raw = raw.get("accepted_card_seat_pairs")
        if not isinstance(gold_raw, Sequence) or isinstance(gold_raw, (str, bytes)):
            raise HoldoutMeasurementError("gold_card_seat_pairs must be an array")
        if not isinstance(accepted_raw, Sequence) or isinstance(accepted_raw, (str, bytes)):
            raise HoldoutMeasurementError("accepted_card_seat_pairs must be an array")
        gold_pairs = {
            (item.get("card"), item.get("seat"))
            for item in gold_raw
            if isinstance(item, Mapping)
        }
        accepted_pairs = [
            (item.get("card"), item.get("seat"))
            for item in accepted_raw
            if isinstance(item, Mapping)
        ]
        accepted_correct = sum(pair in gold_pairs for pair in accepted_pairs)
        accepted_wrong_pair = len(accepted_pairs) - accepted_correct
        unresolved = max(0, len(gold_raw) - accepted_correct - accepted_wrong_pair)
        rejection_kind = raw.get("rejection_kind", "NONE")
        if rejection_kind == "REVIEW":
            needs_review += unresolved
        elif rejection_kind in {"NONE", "UNKNOWN"}:
            unknown += unresolved
    return unknown, needs_review


def score_portable_frozen_holdout(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Score a sealed holdout only after all platform-neutral freeze gates pass."""
    if not isinstance(bundle, Mapping):
        raise HoldoutMeasurementError("bundle must be an object")
    freeze = _validate_freeze_receipt(bundle.get("freeze"))
    report = score_holdout(bundle)
    cases = bundle.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise HoldoutMeasurementError("cases must be an array")
    unknown, needs_review = _split_unknown_review(cases)
    if unknown + needs_review != report["unknown_or_review"]:
        raise HoldoutMeasurementError("UNKNOWN/REVIEW taxonomy mismatch")
    return {
        **report,
        "portable_evaluator_version": PORTABLE_EVALUATOR_VERSION,
        "freeze_receipt_version": FREEZE_RECEIPT_VERSION,
        "freeze": freeze,
        "total": report["gold_visible_targets"],
        "unknown": unknown,
        "needs_review": needs_review,
        "wrong_card_and_seat": report["accepted_wrong_card_seat"],
        "per_source": report["per_source_metrics"],
        "per_layout": report["per_layout_metrics"],
        "per_theme": report["per_theme_metrics"],
        "portable_freeze_valid": True,
    }
