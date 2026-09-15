"""Exact fail-closed evaluation of explicit ``(seat, card)`` observations."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal
from bridge_vision.holdout_eval import MIN_PRECISION, MIN_RECALL

CARD_EVAL_VERSION = "bridge-card-eval-v2"


def _pairs(hands: Mapping[str, Iterable[str]]) -> set[tuple[str, str]]:
    canonical = canonicalize_video_deal({"hands": dict(hands)}).to_dict()["hands"]
    return {(seat, card) for seat in SEATS for card in canonical[seat]["cards"]}


def _json_pairs(values: set[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"seat": seat, "card": card}
        for seat, card in sorted(values, key=lambda item: (SEATS.index(item[0]), item[1]))
    ]


def evaluate_frame(
    *, frame_sha256: str, expected_hands: Mapping[str, Iterable[str]],
    detector_result: Mapping[str, Any],
) -> dict[str, Any]:
    if len(frame_sha256) != 64 or any(c not in "0123456789abcdef" for c in frame_sha256):
        raise ValueError("frame_sha256 must be a lowercase sha256 digest")
    if not isinstance(detector_result, Mapping):
        raise ValueError("detector_result must be an object")
    expected = _pairs(expected_hands)
    predicted = _pairs(detector_result.get("hands") or {})
    tp, fp, fn = expected & predicted, predicted - expected, expected - predicted
    expected_seat = {card: seat for seat, card in expected}
    seat_errors = [
        {"card": card, "expected_seat": expected_seat[card], "predicted_seat": seat}
        for seat, card in sorted(predicted)
        if card in expected_seat and expected_seat[card] != seat
    ]
    evidence = detector_result.get("evidence") or {}
    if not isinstance(evidence, Mapping):
        raise ValueError("detector_result.evidence must be an object")
    rejection_counts = {"rejected_ambiguous": 0, "rejected_low_confidence": 0, "rejected_other": 0}
    for raw in evidence.get("rejected") or []:
        if not isinstance(raw, Mapping):
            raise ValueError("detector_result.evidence.rejected item must be an object")
        reason = str(raw.get("reason") or "").strip().upper()
        if reason.startswith("AMBIGUOUS_"):
            rejection_counts["rejected_ambiguous"] += 1
        elif reason.startswith("LOW_") or "LOW_CONFIDENCE" in reason:
            rejection_counts["rejected_low_confidence"] += 1
        else:
            rejection_counts["rejected_other"] += 1
    return {
        "schema": CARD_EVAL_VERSION, "frame_sha256": frame_sha256,
        "status": "REVIEW", "result_scope": "SHADOW_ONLY", "production_activation_allowed": False,
        "counts": {
            "tp": len(tp), "fp": len(fp), "fn": len(fn),
            "accepted_correct": len(tp), "accepted_wrong": len(fp), **rejection_counts,
        },
        "true_positives": _json_pairs(tp), "false_positives": _json_pairs(fp),
        "false_negatives": _json_pairs(fn), "seat_errors": seat_errors,
    }


def summarize_reports(
    reports: Sequence[Mapping[str, Any]], *,
    min_precision: float = MIN_PRECISION, min_recall: float = MIN_RECALL,
) -> dict[str, Any]:
    if min_precision < MIN_PRECISION or min_recall < MIN_RECALL:
        raise ValueError("quality thresholds cannot be lowered below the Video 3.1 gate")
    if not reports:
        return {
            "schema": "bridge-card-eval-summary-v2", "status": "INCONCLUSIVE",
            "reason": "NO_HOLDOUT", "quality_gate_passed": False,
            "production_activation_allowed": False,
        }
    totals = {
        "tp": 0, "fp": 0, "fn": 0, "accepted_correct": 0, "accepted_wrong": 0,
        "rejected_ambiguous": 0, "rejected_low_confidence": 0, "rejected_other": 0,
    }
    seen: set[str] = set()
    seat_errors = 0
    for report in reports:
        if report.get("schema") != CARD_EVAL_VERSION:
            raise ValueError("unexpected card evaluation schema")
        frame_sha = str(report.get("frame_sha256") or "")
        if frame_sha in seen:
            raise ValueError("duplicate frame report")
        seen.add(frame_sha)
        counts = report.get("counts") or {}
        for key in totals:
            value = counts.get(key)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid {key} count")
            totals[key] += value
        errors = report.get("seat_errors") or []
        if not isinstance(errors, list):
            raise ValueError("seat_errors must be an array")
        seat_errors += len(errors)
    precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
    recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
    passed = precision >= min_precision and recall >= min_recall and seat_errors == 0
    return {
        "schema": "bridge-card-eval-summary-v2", "status": "PASS" if passed else "FAIL",
        "result_scope": "INDEPENDENT_HOLDOUT", "frames": len(reports),
        "counts": {**totals, "seat_errors": seat_errors}, "precision": precision, "recall": recall,
        "thresholds": {"min_precision": min_precision, "min_recall": min_recall, "seat_errors": 0},
        "quality_gate_passed": passed, "production_activation_allowed": False,
    }


__all__ = ["CARD_EVAL_VERSION", "evaluate_frame", "summarize_reports"]
