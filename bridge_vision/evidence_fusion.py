"""Fuse visual and teacher-speech card evidence without guessing.

Language-specific extraction is deliberately outside this module.  An upstream
extractor may submit normalized declarations, but only bounded, attributable,
high-confidence teacher statements become card observations here.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal


FUSION_SCHEMA = "bridge-card-evidence-fusion-v1"
DEFAULT_MIN_DECLARATION_CONFIDENCE = 0.90
_LOCATOR = re.compile(r"^transcript\.jsonl#segment=[0-9]{1,9}$")
_RANKS = frozenset("AKQJT98765432")
_SUITS = frozenset("SHDC")
_UNICODE_SUITS = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}


class CardEvidenceFusionError(ValueError):
    pass


def _normalise_card(value: Any) -> str:
    deal = canonicalize_video_deal({"hands": {"N": [value]}}).to_dict()
    return deal["hands"]["N"]["cards"][0]


def _normalise_visual_hands(hands: Any) -> dict[str, list[str]]:
    deal = canonicalize_video_deal({"hands": hands}).to_dict()
    return {seat: list(deal["hands"][seat]["cards"]) for seat in SEATS}


def _normalise_rank(value: Any) -> str | None:
    if value is None:
        return None
    rank = str(value).strip().upper()
    if rank == "10":
        rank = "T"
    if rank not in _RANKS:
        raise CardEvidenceFusionError("invalid partial card rank")
    return rank


def _normalise_suit(value: Any) -> str | None:
    if value is None:
        return None
    suit = str(value).strip().upper()
    suit = _UNICODE_SUITS.get(suit, suit)
    if suit not in _SUITS:
        raise CardEvidenceFusionError("invalid partial card suit")
    return suit


def _card_claim(raw: Mapping[str, Any]) -> tuple[str | None, dict[str, str] | None]:
    value = raw.get("card")
    if value is not None:
        try:
            return _normalise_card(value), None
        except Exception:
            token = str(value).strip().upper()
            token = _UNICODE_SUITS.get(token, token)
            if token == "10":
                token = "T"
            if token in _RANKS:
                return None, {"rank": token}
            if token in _SUITS:
                return None, {"suit": token}
            raise CardEvidenceFusionError("invalid card claim")
    rank = _normalise_rank(raw.get("rank"))
    suit = _normalise_suit(raw.get("suit"))
    if rank is None and suit is None:
        raise CardEvidenceFusionError("card claim is empty")
    if rank is not None and suit is not None:
        return _normalise_card(rank + suit), None
    constraint: dict[str, str] = {}
    if rank is not None:
        constraint["rank"] = rank
    if suit is not None:
        constraint["suit"] = suit
    return None, constraint


def fuse_card_evidence(
    visual_hands: Mapping[str, Any],
    teacher_declarations: Iterable[Mapping[str, Any]],
    *,
    min_declaration_confidence: float = DEFAULT_MIN_DECLARATION_CONFIDENCE,
) -> dict[str, Any]:
    """Return a canonical deal plus bounded evidence provenance.

    A declaration has the normalized boundary shape::

        {"card": "AS", "seat": "W", "confidence": 0.97,
         "speaker_role": "TEACHER",
         "evidence_locator": "transcript.jsonl#segment=12",
         "start": 42.1, "end": 44.0}

    Low-confidence or unattributed declarations are retained as rejected
    candidates.  A cross-seat contradiction is a hard conflict and no fused
    deal is emitted.
    """

    if not isinstance(visual_hands, Mapping):
        raise CardEvidenceFusionError("visual hands must be an object")
    if not 0.0 <= min_declaration_confidence <= 1.0:
        raise ValueError("min declaration confidence outside [0,1]")
    if isinstance(teacher_declarations, (str, bytes)):
        raise CardEvidenceFusionError("teacher declarations must be an iterable of objects")

    hands = _normalise_visual_hands(visual_hands)
    card_to_seat = {card: seat for seat, cards in hands.items() for card in cards}
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = {
        (seat, card): [{"source": "VISUAL"}]
        for seat, cards in hands.items()
        for card in cards
    }
    accepted: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for index, raw in enumerate(teacher_declarations):
        if not isinstance(raw, Mapping):
            raise CardEvidenceFusionError("teacher declaration must be an object")
        try:
            card, constraint = _card_claim(raw)
        except CardEvidenceFusionError:
            rejected.append({"index": index, "reason": "INVALID_CARD"})
            continue
        claim = {"card": card} if card is not None else {"constraint": constraint}
        seat = str(raw.get("seat") or "").strip().upper()
        if seat not in SEATS:
            rejected.append({"index": index, **claim, "reason": "AMBIGUOUS_SEAT"})
            continue
        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            rejected.append({"index": index, **claim, "seat": seat, "reason": "INVALID_CONFIDENCE"})
            continue
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            rejected.append({"index": index, **claim, "seat": seat, "reason": "INVALID_CONFIDENCE"})
            continue
        locator = str(raw.get("evidence_locator") or "")
        if not _LOCATOR.fullmatch(locator):
            rejected.append({"index": index, **claim, "seat": seat, "reason": "MISSING_TRANSCRIPT_EVIDENCE"})
            continue
        if str(raw.get("speaker_role") or "").strip().upper() != "TEACHER":
            rejected.append({"index": index, **claim, "seat": seat, "reason": "UNVERIFIED_SPEAKER"})
            continue
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError):
            rejected.append({"index": index, **claim, "seat": seat, "reason": "INVALID_TIMELINE"})
            continue
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            rejected.append({"index": index, **claim, "seat": seat, "reason": "INVALID_TIMELINE"})
            continue
        if confidence < min_declaration_confidence:
            rejected.append(
                {
                    "index": index,
                    **claim,
                    "seat": seat,
                    "reason": "LOW_CONFIDENCE",
                    "confidence": confidence,
                    "evidence_locator": locator,
                }
            )
            continue

        if card is None:
            partial.append(
                {
                    "index": index,
                    "constraint": constraint,
                    "seat": seat,
                    "confidence": confidence,
                    "speaker_role": "TEACHER",
                    "evidence_locator": locator,
                    "start": start,
                    "end": end,
                }
            )
            continue

        previous = card_to_seat.get(card)
        if previous is not None and previous != seat:
            conflicts.append(
                {
                    "card": card,
                    "visual_or_prior_seat": previous,
                    "declared_seat": seat,
                    "evidence_locator": locator,
                }
            )
            continue
        if previous is None and len(hands[seat]) >= 13:
            conflicts.append(
                {
                    "card": card,
                    "declared_seat": seat,
                    "reason": "EXACT_CARD_CONTRADICTS_COMPLETE_HAND",
                    "evidence_locator": locator,
                }
            )
            continue
        event = {
            "index": index,
            "card": card,
            "seat": seat,
            "confidence": confidence,
            "speaker_role": "TEACHER",
            "evidence_locator": locator,
            "start": start,
            "end": end,
        }
        accepted.append(event)
        evidence.setdefault((seat, card), []).append(
            {
                "source": "TEACHER_SPEECH",
                "confidence": confidence,
                "evidence_locator": locator,
                "start": start,
                "end": end,
            }
        )
        if previous is None:
            card_to_seat[card] = seat
            hands[seat].append(card)

    if conflicts:
        return {
            "schema": FUSION_SCHEMA,
            "status": "CONFLICT",
            "deal": None,
            "accepted_declarations": accepted,
            "resolved_partial_declarations": [],
            "unresolved_partial_declarations": partial,
            "rejected_declarations": rejected,
            "conflicts": conflicts,
            "observed_card_evidence": [],
            "constraint_evidence": [],
            "canonical_promotion_allowed": False,
        }

    deal = canonicalize_video_deal({"hands": hands}, derive_fourth_hand=True).to_dict()
    resolved_partial: list[dict[str, Any]] = []
    unresolved_partial: list[dict[str, Any]] = []
    constraint_evidence: list[dict[str, Any]] = []
    for event in partial:
        seat = event["seat"]
        constraint = event["constraint"]
        candidates = [
            card
            for card in deal["hands"][seat]["cards"]
            if (constraint.get("rank") is None or card[0] == constraint["rank"])
            and (constraint.get("suit") is None or card[1] == constraint["suit"])
        ]
        if len(candidates) == 1:
            resolved = {**event, "resolved_card": candidates[0], "resolution": "UNIQUE_WITHIN_CANONICAL_HAND"}
            resolved_partial.append(resolved)
            constraint_evidence.append(
                {
                    "source": "TEACHER_SPEECH_PARTIAL_CONSTRAINT",
                    "seat": seat,
                    "constraint": constraint,
                    "resolved_card": candidates[0],
                    "resolution": "UNIQUE_WITHIN_CANONICAL_HAND",
                    "confidence": event["confidence"],
                    "evidence_locator": event["evidence_locator"],
                    "start": event["start"],
                    "end": event["end"],
                }
            )
            continue
        if not candidates and deal["hands"][seat]["unknown_count"] == 0:
            conflicts.append(
                {
                    "constraint": constraint,
                    "declared_seat": seat,
                    "reason": "PARTIAL_CARD_CONTRADICTS_COMPLETE_HAND",
                    "evidence_locator": event["evidence_locator"],
                }
            )
            continue
        unresolved_partial.append(
            {
                **event,
                "reason": "PARTIAL_CARD_AMBIGUOUS",
                "candidate_cards": candidates[:13],
            }
        )
    if conflicts:
        return {
            "schema": FUSION_SCHEMA,
            "status": "CONFLICT",
            "deal": None,
            "accepted_declarations": accepted,
            "resolved_partial_declarations": resolved_partial,
            "unresolved_partial_declarations": unresolved_partial,
            "rejected_declarations": rejected,
            "conflicts": conflicts,
            "observed_card_evidence": [],
            "constraint_evidence": constraint_evidence,
            "canonical_promotion_allowed": False,
        }
    observed_card_evidence = [
        {"seat": seat, "card": card, "evidence": entries}
        for (seat, card), entries in sorted(evidence.items())
    ]
    return {
        "schema": FUSION_SCHEMA,
        "status": "REVIEW" if rejected or unresolved_partial else "PASS",
        "deal": deal,
        "accepted_declarations": accepted,
        "resolved_partial_declarations": resolved_partial,
        "unresolved_partial_declarations": unresolved_partial,
        "rejected_declarations": rejected,
        "conflicts": [],
        "observed_card_evidence": observed_card_evidence,
        "constraint_evidence": constraint_evidence,
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "DEFAULT_MIN_DECLARATION_CONFIDENCE",
    "FUSION_SCHEMA",
    "CardEvidenceFusionError",
    "fuse_card_evidence",
]
