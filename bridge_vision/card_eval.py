"""Deterministic fail-closed evaluation of card + seat observations.

The evaluator compares only explicitly labelled and explicitly predicted
``(seat, card)`` pairs.  It never completes a hand or derives a missing card.
Rejected/ambiguous recognizer observations are reported separately and cannot
be counted as true positives.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

CARD_EVAL_VERSION = "bridge-card-eval-v1"
_AMBIGUOUS_REASONS = {
    "AMBIGUOUS_GLYPH",
    "AMBIGUOUS_SEAT",
    "AMBIGUOUS_SUIT_SHAPE",
    "INCOMPLETE_OR_INVALID_CARD",
    "LOW_GLYPH_SCORE",
    "LOW_GRAPHIC_CONFIDENCE",
    "LOW_RANK_CONFIDENCE",
    "LOW_SUIT_CONFIDENCE",
    "LOW_SUIT_SCORE",
}


def _pairs(hands: Mapping[str, Iterable[str]]) -> set[tuple[str, str]]:
    canonical = canonicalize_video_deal({"hands": dict(hands)}).to_dict()["hands"]
    return {
        (seat, card)
        for seat in SEATS
        for card in canonical[seat]["cards"]
    }


def _json_pairs(values: set[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"seat": seat, "card": card}
        for seat, card in sorted(values, key=lambda item: (SEATS.index(item[0]), item[1]))
    ]


def _ambiguous(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in ("rejected", "channel_rejections", "pending"):
        raw_rows = evidence.get(channel) or []
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
            raise ValueError(f"evidence.{channel} must be an array")
        for raw in raw_rows:
            if not isinstance(raw, Mapping):
                raise ValueError(f"evidence.{channel} item must be an object")
            reason = str(raw.get("reason") or "").strip().upper()
            if reason in _AMBIGUOUS_REASONS or reason.startswith("AMBIGUOUS_"):
                rows.append({"channel": channel, **dict(raw), "reason": reason})
    graphic = evidence.get("graphic_evidence") or {}
    if not isinstance(graphic, Mapping):
        raise ValueError("evidence.graphic_evidence must be an object")
    raw_rows = graphic.get("rejected") or []
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise ValueError("evidence.graphic_evidence.rejected must be an array")
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("evidence.graphic_evidence.rejected item must be an object")
        reason = str(raw.get("reason") or "").strip().upper()
        if reason in _AMBIGUOUS_REASONS or reason.startswith("AMBIGUOUS_"):
            rows.append({"channel": "graphic_evidence.rejected", **dict(raw), "reason": reason})
    return rows


def evaluate_frame(
    *,
    frame_sha256: str,
    expected_hands: Mapping[str, Iterable[str]],
    detector_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Return exact TP/FP/FN and ambiguous observations for one gold frame."""
    if len(frame_sha256) != 64 or any(c not in "0123456789abcdef" for c in frame_sha256):
        raise ValueError("frame_sha256 must be a lowercase sha256 digest")
    if not isinstance(detector_result, Mapping):
        raise ValueError("detector_result must be an object")
    expected = _pairs(expected_hands)
    predicted = _pairs(detector_result.get("hands") or {})
    evidence = detector_result.get("evidence") or {}
    if not isinstance(evidence, Mapping):
        raise ValueError("detector_result.evidence must be an object")
    ambiguous = _ambiguous(evidence)
    tp = expected & predicted
    fp = predicted - expected
    fn = expected - predicted
    expected_seat_by_card = {card: seat for seat, card in expected}
    seat_errors = [
        {"card": card, "expected_seat": expected_seat_by_card[card], "predicted_seat": seat}
        for seat, card in sorted(predicted)
        if card in expected_seat_by_card and expected_seat_by_card[card] != seat
    ]
    return {
        "schema": CARD_EVAL_VERSION,
        "frame_sha256": frame_sha256,
        "status": "REVIEW",
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "counts": {"tp": len(tp), "fp": len(fp), "fn": len(fn), "ambiguous": len(ambiguous)},
        "true_positives": _json_pairs(tp),
        "false_positives": _json_pairs(fp),
        "false_negatives": _json_pairs(fn),
        "seat_errors": seat_errors,
        "ambiguous_observations": ambiguous,
    }


def summarize_reports(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not reports:
        raise ValueError("at least one frame report is required")
    seen: set[str] = set()
    totals = {"tp": 0, "fp": 0, "fn": 0, "ambiguous": 0}
    seat_errors = 0
    for report in reports:
        if report.get("schema") != CARD_EVAL_VERSION:
            raise ValueError("unexpected card evaluation schema")
        sha = str(report.get("frame_sha256") or "")
        if sha in seen:
            raise ValueError("duplicate frame report")
        seen.add(sha)
        counts = report.get("counts") or {}
        for key in totals:
            value = counts.get(key)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid {key} count")
            totals[key] += value
        seat_errors += len(report.get("seat_errors") or [])
    predicted = totals["tp"] + totals["fp"]
    expected = totals["tp"] + totals["fn"]
    return {
        "schema": "bridge-card-eval-summary-v1",
        "status": "SHADOW_REVIEW",
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "frames": len(reports),
        "counts": {**totals, "seat_errors": seat_errors},
        "precision": totals["tp"] / predicted if predicted else (1.0 if not expected else 0.0),
        "recall": totals["tp"] / expected if expected else 1.0,
    }


__all__ = ["CARD_EVAL_VERSION", "evaluate_frame", "summarize_reports"]
