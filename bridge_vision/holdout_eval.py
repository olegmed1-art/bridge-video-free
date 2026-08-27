"""Deterministic holdout metrics for explicit human-labelled glyph evidence.

This module does not read lesson media and never invents labels. Callers supply
human gold labels plus classifier outcomes from frames excluded from templates.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

HOLDOUT_EVAL_VERSION = "bridgit-glyph-holdout-v1"


def evaluate_labelled_outcomes(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not outcomes:
        return {"version": HOLDOUT_EVAL_VERSION, "status": "INCONCLUSIVE", "reason": "NO_HOLDOUT", "counts": {}}
    counts = Counter()
    confusion: dict[str, Counter] = defaultdict(Counter)
    for item in outcomes:
        gold = str(item.get("gold") or "").strip().upper()
        if not gold:
            raise ValueError("holdout gold label must be explicit")
        predicted_raw = item.get("predicted")
        predicted = None if predicted_raw is None else str(predicted_raw).strip().upper()
        reason = str(item.get("reason") or "").strip().upper()
        if predicted:
            if predicted == gold:
                counts["accepted_correct"] += 1
            else:
                counts["accepted_wrong"] += 1
                confusion[gold][predicted] += 1
        elif reason == "AMBIGUOUS_GLYPH":
            counts["rejected_ambiguous"] += 1
        elif reason == "LOW_GLYPH_SCORE":
            counts["rejected_low_score"] += 1
        else:
            counts["rejected_other"] += 1
    accepted = counts["accepted_correct"] + counts["accepted_wrong"]
    total = len(outcomes)
    status = "PASS" if counts["accepted_wrong"] == 0 and accepted > 0 else "FAIL"
    return {
        "version": HOLDOUT_EVAL_VERSION,
        "status": status,
        "counts": dict(counts),
        "total": total,
        "coverage": accepted / total,
        "accepted_precision": counts["accepted_correct"] / accepted if accepted else 0.0,
        "confusion": {gold: dict(values) for gold, values in sorted(confusion.items())},
    }


__all__ = ["HOLDOUT_EVAL_VERSION", "evaluate_labelled_outcomes"]
