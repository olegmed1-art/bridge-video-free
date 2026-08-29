"""Gold-set evaluation for school-owned Bridge Vision detectors."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class GoldMetrics:
    frames: int
    expected_cards: int
    predicted_cards: int
    true_positive_cards: int
    seat_errors: int
    precision: float
    recall: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _pairs(hands: Mapping[str, Iterable[str]]) -> set[tuple[str, str]]:
    return {(str(seat), str(card)) for seat, cards in hands.items() for card in cards}


def evaluate_card_detector(detector, cases: Iterable[Mapping[str, Any]]) -> GoldMetrics:
    frames = 0
    expected_total = predicted_total = tp_total = seat_errors = 0
    for case in cases:
        frames += 1
        frame = Path(str(case.get("frame") or f"gold-{frames}.jpg"))
        expected = _pairs(case.get("hands") or {})
        raw = detector(frame)
        predicted = _pairs(raw.get("hands") or {})
        expected_total += len(expected)
        predicted_total += len(predicted)
        tp_total += len(expected & predicted)
        expected_by_card = {card: seat for seat, card in expected}
        for seat, card in predicted:
            correct = expected_by_card.get(card)
            if correct is not None and correct != seat:
                seat_errors += 1
    precision = tp_total / predicted_total if predicted_total else (1.0 if expected_total == 0 else 0.0)
    recall = tp_total / expected_total if expected_total else 1.0
    return GoldMetrics(frames, expected_total, predicted_total, tp_total, seat_errors, precision, recall)


def evaluate_card_detector_report(detector, cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return deterministic TP/FP/FN/ambiguous evidence per real gold frame."""
    rows: list[dict[str, Any]] = []
    totals = {"tp": 0, "fp": 0, "fn": 0, "ambiguous": 0, "seat_errors": 0}
    for index, case in enumerate(cases, start=1):
        frame = Path(str(case.get("frame") or f"gold-{index}.jpg"))
        expected = _pairs(case.get("hands") or {})
        raw = detector(frame)
        predicted = _pairs(raw.get("hands") or {})
        ambiguous_raw = raw.get("ambiguous") or []
        if not isinstance(ambiguous_raw, (list, tuple)):
            raise ValueError("detector ambiguous evidence must be an array")
        expected_by_card = {card: seat for seat, card in expected}
        seat_errors = sum(
            1 for seat, card in predicted
            if card in expected_by_card and expected_by_card[card] != seat
        )
        row = {
            "frame": str(frame),
            "tp": len(expected & predicted),
            "fp": len(predicted - expected),
            "fn": len(expected - predicted),
            "ambiguous": len(ambiguous_raw),
            "seat_errors": seat_errors,
        }
        rows.append(row)
        for key in totals:
            totals[key] += row[key]
    predicted_total = totals["tp"] + totals["fp"]
    expected_total = totals["tp"] + totals["fn"]
    totals["precision"] = totals["tp"] / predicted_total if predicted_total else (1.0 if expected_total == 0 else 0.0)
    totals["recall"] = totals["tp"] / expected_total if expected_total else 1.0
    return {"frames": rows, "totals": totals}


def passes_card_gold_gate(metrics: GoldMetrics, *, min_precision: float = 0.995, min_recall: float = 0.95) -> bool:
    return (
        metrics.frames > 0
        and metrics.seat_errors == 0
        and metrics.precision >= min_precision
        and metrics.recall >= min_recall
    )


__all__ = [
    "GoldMetrics",
    "evaluate_card_detector",
    "evaluate_card_detector_report",
    "passes_card_gold_gate",
]
