"""Dependency-free metadata scorer for frozen Bridgit holdout evidence.

This module does not import the detector, OpenCV, media/server adapters, Drive,
Neon, or Canon code.  It scores only already-sealed metadata bundles and stays
SHADOW_ONLY.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SCORER_VERSION = "bridgit-holdout-measurement-v2"
MINIMUM_PRECISION = 0.995
MINIMUM_RECALL = 0.95

_VALID_CARDS = {
    rank + suit
    for rank in "AKQJT98765432"
    for suit in "HCDS"
}
_VALID_SEATS = {"N", "E", "S", "W"}
_VALID_REJECTION_KINDS = {
    "NONE",
    "AMBIGUOUS",
    "LOW_SCORE",
    "UNKNOWN",
    "REVIEW",
    "OTHER",
}


class HoldoutMeasurementError(ValueError):
    """The sealed metadata bundle is malformed or violates a fail-closed gate."""


@dataclass
class _Bucket:
    gold_visible_targets: int = 0
    accepted_correct: int = 0
    accepted_wrong: int = 0
    accepted_wrong_card_seat: int = 0
    rejected_ambiguous: int = 0
    rejected_low_score: int = 0
    unknown_or_review: int = 0
    rejected_other: int = 0
    seat_errors: int = 0
    false_complete_deals: int = 0

    def as_dict(self) -> dict[str, Any]:
        accepted = self.accepted_correct + self.accepted_wrong_card_seat
        precision = self.accepted_correct / accepted if accepted else None
        coverage = accepted / self.gold_visible_targets if self.gold_visible_targets else 0.0
        recall = (
            self.accepted_correct / self.gold_visible_targets
            if self.gold_visible_targets
            else 0.0
        )
        unknown_review_rate = (
            self.unknown_or_review / self.gold_visible_targets
            if self.gold_visible_targets
            else 0.0
        )
        return {
            "gold_visible_targets": self.gold_visible_targets,
            "accepted_correct": self.accepted_correct,
            "accepted_wrong": self.accepted_wrong,
            "accepted_wrong_card_seat": self.accepted_wrong_card_seat,
            "rejected_ambiguous": self.rejected_ambiguous,
            "rejected_low_score": self.rejected_low_score,
            "unknown_or_review": self.unknown_or_review,
            "rejected_other": self.rejected_other,
            "coverage": coverage,
            "precision": precision,
            "recall": recall,
            "unknown_review_rate": unknown_review_rate,
            "seat_errors": self.seat_errors,
            "false_complete_deals": self.false_complete_deals,
        }


def _pair(raw: Any, field: str) -> tuple[str, str]:
    if not isinstance(raw, Mapping):
        raise HoldoutMeasurementError(f"{field} must be an object")
    card = raw.get("card")
    seat = raw.get("seat")
    if card not in _VALID_CARDS or seat not in _VALID_SEATS:
        raise HoldoutMeasurementError(f"invalid {field}")
    return str(card), str(seat)


def _string(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise HoldoutMeasurementError(f"invalid {field}")
    return raw


def _strings(raw: Any, field: str) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise HoldoutMeasurementError(f"{field} must be an array")
    result = tuple(_string(value, f"{field}[]") for value in raw)
    if len(set(result)) != len(result):
        raise HoldoutMeasurementError(f"duplicate {field}")
    return result


def _add_case_to_bucket(bucket: _Bucket, case_score: _Bucket) -> None:
    for field in bucket.__dataclass_fields__:
        setattr(bucket, field, getattr(bucket, field) + getattr(case_score, field))


def _score_case(raw: Any) -> tuple[_Bucket, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise HoldoutMeasurementError("case must be an object")
    case_id = _string(raw.get("case_id"), "case_id")
    source_session_id = _string(raw.get("source_session_id"), "source_session_id")
    layout_family = _string(raw.get("layout_family"), "layout_family")
    theme_family = _string(raw.get("theme_family"), "theme_family")
    resolution_group = _string(raw.get("resolution_group"), "resolution_group")
    tags = _strings(raw.get("tags", ()), "tags")

    gold_raw = raw.get("gold_card_seat_pairs")
    accepted_raw = raw.get("accepted_card_seat_pairs")
    if not isinstance(gold_raw, Sequence) or isinstance(gold_raw, (str, bytes)):
        raise HoldoutMeasurementError("gold_card_seat_pairs must be an array")
    if not isinstance(accepted_raw, Sequence) or isinstance(accepted_raw, (str, bytes)):
        raise HoldoutMeasurementError("accepted_card_seat_pairs must be an array")
    gold = [_pair(item, "gold_card_seat_pairs[]") for item in gold_raw]
    accepted = [_pair(item, "accepted_card_seat_pairs[]") for item in accepted_raw]
    if len({card for card, _ in gold}) != len(gold):
        raise HoldoutMeasurementError("duplicate gold card")
    if len(set(gold)) != len(gold):
        raise HoldoutMeasurementError("duplicate gold card+seat")
    if len({card for card, _ in accepted}) != len(accepted):
        raise HoldoutMeasurementError("duplicate accepted card")

    rejection_kind = raw.get("rejection_kind", "NONE")
    if rejection_kind not in _VALID_REJECTION_KINDS:
        raise HoldoutMeasurementError("invalid rejection_kind")
    full_layout_emitted = raw.get("full_layout_emitted")
    if not isinstance(full_layout_emitted, bool):
        raise HoldoutMeasurementError("full_layout_emitted must be boolean")
    full_layout_forbidden = raw.get("full_layout_forbidden")
    if not isinstance(full_layout_forbidden, bool):
        raise HoldoutMeasurementError("full_layout_forbidden must be boolean")

    gold_by_card = dict(gold)
    gold_set = set(gold)
    case = _Bucket(gold_visible_targets=len(gold))
    for card, seat in accepted:
        if (card, seat) in gold_set:
            case.accepted_correct += 1
        else:
            case.accepted_wrong_card_seat += 1
            if card not in gold_by_card:
                case.accepted_wrong += 1
            elif gold_by_card[card] != seat:
                case.seat_errors += 1

    unresolved = len(gold) - case.accepted_correct
    if unresolved < 0:
        raise HoldoutMeasurementError("accepted outputs exceed gold targets")
    unresolved -= case.accepted_wrong_card_seat
    if unresolved < 0:
        unresolved = 0
    if rejection_kind == "AMBIGUOUS":
        case.rejected_ambiguous = unresolved
    elif rejection_kind == "LOW_SCORE":
        case.rejected_low_score = unresolved
    elif rejection_kind in {"UNKNOWN", "REVIEW"}:
        case.unknown_or_review = unresolved
    elif rejection_kind == "OTHER":
        case.rejected_other = unresolved
    elif unresolved:
        case.unknown_or_review = unresolved

    if full_layout_emitted and (full_layout_forbidden or case.accepted_wrong_card_seat):
        case.false_complete_deals = 1

    metadata = {
        "case_id": case_id,
        "source_session_id": source_session_id,
        "layout_family": layout_family,
        "theme_family": theme_family,
        "resolution_group": resolution_group,
        "tags": tags,
    }
    return case, metadata


def score_holdout(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Score one sealed metadata bundle without invoking recognition code."""
    if not isinstance(bundle, Mapping):
        raise HoldoutMeasurementError("bundle must be an object")
    if bundle.get("result_scope") != "SHADOW_ONLY":
        raise HoldoutMeasurementError("holdout scoring must remain SHADOW_ONLY")
    if bundle.get("canonical_promotion_allowed") is not False:
        raise HoldoutMeasurementError("canonical promotion must be false")
    if bundle.get("frozen_before_holdout_reveal") is not True:
        raise HoldoutMeasurementError("threshold/config freeze not proven")

    cases = bundle.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)) or not cases:
        raise HoldoutMeasurementError("cases must be a non-empty array")

    global_bucket = _Bucket()
    per_source: dict[str, _Bucket] = defaultdict(_Bucket)
    per_layout: dict[str, _Bucket] = defaultdict(_Bucket)
    per_theme: dict[str, _Bucket] = defaultdict(_Bucket)
    per_resolution: dict[str, _Bucket] = defaultdict(_Bucket)
    per_tag: dict[str, _Bucket] = defaultdict(_Bucket)
    seen_case_ids: set[str] = set()

    for raw in cases:
        case_score, metadata = _score_case(raw)
        case_id = metadata["case_id"]
        if case_id in seen_case_ids:
            raise HoldoutMeasurementError("duplicate case_id")
        seen_case_ids.add(case_id)
        _add_case_to_bucket(global_bucket, case_score)
        _add_case_to_bucket(per_source[metadata["source_session_id"]], case_score)
        _add_case_to_bucket(per_layout[metadata["layout_family"]], case_score)
        _add_case_to_bucket(per_theme[metadata["theme_family"]], case_score)
        _add_case_to_bucket(per_resolution[metadata["resolution_group"]], case_score)
        for tag in metadata["tags"]:
            _add_case_to_bucket(per_tag[tag], case_score)

    metrics = global_bucket.as_dict()
    precision = metrics["precision"]
    thresholds_pass = (
        precision is not None
        and precision >= MINIMUM_PRECISION
        and metrics["recall"] >= MINIMUM_RECALL
        and metrics["seat_errors"] == 0
        and metrics["false_complete_deals"] == 0
    )
    return {
        "scorer_version": SCORER_VERSION,
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "case_count": len(cases),
        "source_session_count": len(per_source),
        **metrics,
        "per_source_metrics": {key: value.as_dict() for key, value in sorted(per_source.items())},
        "per_layout_metrics": {key: value.as_dict() for key, value in sorted(per_layout.items())},
        "per_theme_metrics": {key: value.as_dict() for key, value in sorted(per_theme.items())},
        "per_resolution_metrics": {key: value.as_dict() for key, value in sorted(per_resolution.items())},
        "per_stress_tag_metrics": {key: value.as_dict() for key, value in sorted(per_tag.items())},
        "thresholds": {
            "minimum_precision": MINIMUM_PRECISION,
            "minimum_recall": MINIMUM_RECALL,
            "maximum_seat_errors": 0,
            "maximum_false_complete_deals": 0,
            "frozen_before_holdout_reveal": True,
        },
        "mechanical_thresholds_pass": thresholds_pass,
    }
