"""Fail-closed metrics for independent, explicitly labelled holdout outcomes."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

HOLDOUT_EVAL_VERSION = "bridge-vision-independent-holdout-v2"
CHANNELS = {"rank", "suit", "card"}
MIN_PRECISION = 0.995
MIN_RECALL = 0.95
_RANKS = set("AKQJT98765432")
_SUITS = {"S", "H", "D", "C"}
_SEATS = {"N", "E", "S", "W"}


def _valid_label(channel: str, label: str) -> bool:
    return (
        channel == "rank" and label in _RANKS
        or channel == "suit" and label in _SUITS
        or channel == "card" and len(label) == 2 and label[0] in _RANKS and label[1] in _SUITS
    )


def evaluate_labelled_outcomes(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    channel: str,
    dataset_partition: str,
    min_precision: float = MIN_PRECISION,
    min_recall: float = MIN_RECALL,
) -> dict[str, Any]:
    """Evaluate one independent channel without inferring any missing label.

    Each row represents one human-labelled holdout target. A wrong accepted
    prediction is both an FP for the predicted class and an FN for the gold
    class. A rejection is an FN, while its reason remains separately auditable.
    """
    normalized_channel = str(channel).strip().lower()
    if normalized_channel not in CHANNELS:
        raise ValueError("channel must be rank, suit, or card")
    if dataset_partition != "HOLDOUT":
        raise ValueError("evaluation requires the independent HOLDOUT partition")
    if min_precision < MIN_PRECISION or min_recall < MIN_RECALL:
        raise ValueError("quality thresholds cannot be lowered below the Video 3.1 gate")
    if not outcomes:
        return {
            "schema": HOLDOUT_EVAL_VERSION,
            "channel": normalized_channel,
            "dataset_partition": "HOLDOUT",
            "status": "INCONCLUSIVE",
            "reason": "NO_HOLDOUT",
            "counts": {},
            "quality_gate_passed": False,
            "production_activation_allowed": False,
        }
    counts: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[tuple[str, str]] = set()
    for raw in outcomes:
        if not isinstance(raw, Mapping):
            raise ValueError("holdout outcome must be an object")
        frame_sha = str(raw.get("frame_sha256") or "")
        observation_id = str(raw.get("observation_id") or "").strip()
        if len(frame_sha) != 64 or any(c not in "0123456789abcdef" for c in frame_sha):
            raise ValueError("frame_sha256 must be a lowercase sha256 digest")
        if not observation_id or (frame_sha, observation_id) in seen:
            raise ValueError("observation_id must be unique within a frame")
        seen.add((frame_sha, observation_id))
        gold = str(raw.get("gold") or "").strip().upper()
        if not gold:
            raise ValueError("holdout gold label must be explicit")
        if not _valid_label(normalized_channel, gold):
            raise ValueError("holdout gold label is invalid for its channel")
        predicted_raw = raw.get("predicted")
        predicted = None if predicted_raw is None else str(predicted_raw).strip().upper() or None
        if predicted is not None and not _valid_label(normalized_channel, predicted):
            raise ValueError("predicted label is invalid for its channel")
        reason = str(raw.get("reason") or "").strip().upper()
        gold_seat = str(raw.get("gold_seat") or "").strip().upper() or None
        predicted_seat = str(raw.get("predicted_seat") or "").strip().upper() or None
        if gold_seat is not None and gold_seat not in _SEATS:
            raise ValueError("gold_seat must be N, E, S, or W")
        if predicted_seat is not None and predicted_seat not in _SEATS:
            raise ValueError("predicted_seat must be N, E, S, or W")
        if predicted:
            if predicted == gold:
                counts["accepted_correct"] += 1
                counts["tp"] += 1
            else:
                counts["accepted_wrong"] += 1
                counts["fp"] += 1
                counts["fn"] += 1
                confusion[gold][predicted] += 1
            if gold_seat is not None and predicted_seat != gold_seat:
                counts["seat_errors"] += 1
        else:
            counts["fn"] += 1
            if reason.startswith("AMBIGUOUS_"):
                counts["rejected_ambiguous"] += 1
            elif reason.startswith("LOW_") or "LOW_CONFIDENCE" in reason:
                counts["rejected_low_confidence"] += 1
            else:
                counts["rejected_other"] += 1
    for key in (
        "tp", "fp", "fn", "accepted_correct", "accepted_wrong",
        "rejected_ambiguous", "rejected_low_confidence", "rejected_other", "seat_errors",
    ):
        counts[key] += 0
    precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
    recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
    passed = precision >= min_precision and recall >= min_recall and counts["seat_errors"] == 0
    return {
        "schema": HOLDOUT_EVAL_VERSION,
        "channel": normalized_channel,
        "dataset_partition": "HOLDOUT",
        "status": "PASS" if passed else "FAIL",
        "counts": dict(counts),
        "precision": precision,
        "recall": recall,
        "thresholds": {"min_precision": min_precision, "min_recall": min_recall, "seat_errors": 0},
        "confusion": {gold: dict(values) for gold, values in sorted(confusion.items())},
        "quality_gate_passed": passed,
        "production_activation_allowed": False,
    }


__all__ = [
    "CHANNELS", "HOLDOUT_EVAL_VERSION", "MIN_PRECISION", "MIN_RECALL",
    "evaluate_labelled_outcomes",
]
