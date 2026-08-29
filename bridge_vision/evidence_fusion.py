"""Fuse visual, layout and attributed speech card evidence without guessing.

Language-specific extraction is deliberately outside this module.  An upstream
extractor may submit normalized declarations. Bounded, attributable,
high-confidence teacher statements may become card observations. Student
statements remain review suggestions unless independently observed.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

FUSION_SCHEMA = "bridge-card-evidence-fusion-v2"
DEFAULT_MIN_DECLARATION_CONFIDENCE = 0.90
DEFAULT_MIN_SPEAKER_CONFIDENCE = 0.90
MAX_DECLARATIONS = 500
MAX_LAYOUT_SUGGESTIONS = 104
_LOCATOR = re.compile(r"^transcript\.jsonl#segment=[0-9]{1,9}$")
_SPEAKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
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
        except (TypeError, ValueError):
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


def _unevaluated_student_suggestions(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **dict(event),
            "resolution": "NOT_EVALUATED_DUE_TO_HARD_CONFLICT",
            "provenance_class": "STUDENT_SPEECH_SUGGESTION",
            "accepted_as_observation": False,
        }
        for event in events
    ]


def fuse_card_evidence(
    visual_hands: Mapping[str, Any],
    teacher_declarations: Iterable[Mapping[str, Any]],
    *,
    min_declaration_confidence: float = DEFAULT_MIN_DECLARATION_CONFIDENCE,
    min_speaker_confidence: float = DEFAULT_MIN_SPEAKER_CONFIDENCE,
    layout_suggestions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a canonical deal plus bounded evidence provenance.

    A declaration has the normalized boundary shape::

        {"card": "AS", "seat": "W", "confidence": 0.97,
         "speaker_role": "TEACHER", "speaker_id": "speaker-0",
         "speaker_identity_verified": true,
         "speaker_assignment_confidence": 0.98,
         "evidence_locator": "transcript.jsonl#segment=12",
         "start": 42.1, "end": 44.0}

    Low-confidence or unattributed declarations are retained as rejected
    candidates. Teacher cross-seat contradiction is a hard conflict. Student
    declarations never add cards or create a hard conflict by themselves.
    """

    if not isinstance(visual_hands, Mapping):
        raise CardEvidenceFusionError("visual hands must be an object")
    if not 0.0 <= min_declaration_confidence <= 1.0:
        raise ValueError("min declaration confidence outside [0,1]")
    if not 0.0 <= min_speaker_confidence <= 1.0:
        raise ValueError("min speaker confidence outside [0,1]")
    if isinstance(teacher_declarations, (str, bytes)):
        raise CardEvidenceFusionError("speech declarations must be an iterable of objects")
    if isinstance(layout_suggestions, (str, bytes)):
        raise CardEvidenceFusionError("layout suggestions must be an iterable of objects")

    layout_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for layout_index, suggestion in enumerate(layout_suggestions):
        if layout_index >= MAX_LAYOUT_SUGGESTIONS:
            raise CardEvidenceFusionError("too many layout suggestions")
        if not isinstance(suggestion, Mapping):
            raise CardEvidenceFusionError("layout suggestion must be an object")
        if (
            suggestion.get("provenance_class") != "LAYOUT_SUGGESTION"
            or suggestion.get("accepted_as_observation") is not False
            or suggestion.get("resolution") != "LAYOUT_UNIQUE_SUGGESTION"
        ):
            continue
        seat = str(suggestion.get("seat") or "").upper()
        if seat not in SEATS:
            continue
        try:
            card = _normalise_card(suggestion.get("suggested_card"))
        except (TypeError, ValueError):
            continue
        layout_lookup[(seat, card)] = {"layout_index": layout_index, **dict(suggestion)}

    hands = _normalise_visual_hands(visual_hands)
    card_to_seat = {card: seat for seat, cards in hands.items() for card in cards}
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = {
        (seat, card): [{"source": "VISUAL"}]
        for seat, cards in hands.items()
        for card in cards
    }
    accepted: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    student_raw: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    speech_layout_corroborations: list[dict[str, Any]] = []

    for index, raw in enumerate(teacher_declarations):
        if index >= MAX_DECLARATIONS:
            rejected.append({"index": index, "reason": "DECLARATION_LIMIT_REACHED"})
            break
        if not isinstance(raw, Mapping):
            raise CardEvidenceFusionError("speech declaration must be an object")
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
        role = str(raw.get("speaker_role") or "").strip().upper()
        if role not in {"TEACHER", "STUDENT"}:
            rejected.append({"index": index, **claim, "seat": seat, "reason": "UNVERIFIED_SPEAKER_ROLE"})
            continue
        speaker_id = str(raw.get("speaker_id") or "")
        if raw.get("speaker_identity_verified") is not True or not _SPEAKER_ID.fullmatch(speaker_id):
            rejected.append({"index": index, **claim, "seat": seat, "reason": "UNVERIFIED_SPEAKER"})
            continue
        try:
            speaker_confidence = float(raw.get("speaker_assignment_confidence"))
        except (TypeError, ValueError):
            rejected.append({"index": index, **claim, "seat": seat, "reason": "INVALID_SPEAKER_CONFIDENCE"})
            continue
        if (
            not math.isfinite(speaker_confidence)
            or not 0.0 <= speaker_confidence <= 1.0
            or speaker_confidence < min_speaker_confidence
        ):
            rejected.append({
                "index": index,
                **claim,
                "seat": seat,
                "reason": "LOW_SPEAKER_CONFIDENCE",
                "speaker_assignment_confidence": speaker_confidence,
            })
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

        common_event = {
            "index": index,
            **claim,
            "seat": seat,
            "confidence": confidence,
            "speaker_role": role,
            "speaker_id": speaker_id,
            "speaker_assignment_confidence": speaker_confidence,
            "evidence_locator": locator,
            "start": start,
            "end": end,
        }
        if role == "STUDENT":
            student_raw.append(common_event)
            continue

        if card is None:
            partial.append(
                {
                    **common_event,
                    "constraint": constraint,
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
            **common_event,
            "card": card,
        }
        accepted.append(event)
        evidence.setdefault((seat, card), []).append(
            {
                "source": "TEACHER_SPEECH",
                "confidence": confidence,
                "speaker_id": speaker_id,
                "speaker_assignment_confidence": speaker_confidence,
                "evidence_locator": locator,
                "start": start,
                "end": end,
            }
        )
        if previous is None:
            card_to_seat[card] = seat
            hands[seat].append(card)
        layout_match = layout_lookup.get((seat, card))
        if layout_match is not None:
            speech_layout_corroborations.append({
                "seat": seat,
                "card": card,
                "speech_source": "TEACHER_SPEECH",
                "speech_evidence_locator": locator,
                "layout_index": layout_match["layout_index"],
                "accepted_as_observation": False,
                "speech_declaration_accepted_as_observation": True,
                "layout_accepted_as_observation": False,
            })

    if conflicts:
        return {
            "schema": FUSION_SCHEMA,
            "status": "CONFLICT",
            "deal": None,
            "accepted_declarations": accepted,
            "student_speech_suggestions": _unevaluated_student_suggestions(student_raw),
            "speech_layout_corroborations": speech_layout_corroborations,
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
            "student_speech_suggestions": _unevaluated_student_suggestions(student_raw),
            "speech_layout_corroborations": speech_layout_corroborations,
            "resolved_partial_declarations": resolved_partial,
            "unresolved_partial_declarations": unresolved_partial,
            "rejected_declarations": rejected,
            "conflicts": conflicts,
            "observed_card_evidence": [],
            "constraint_evidence": constraint_evidence,
            "canonical_promotion_allowed": False,
        }

    observed_card_to_seat = {
        card: seat
        for (seat, card), entries in evidence.items()
        if any(item.get("source") in {"VISUAL", "TEACHER_SPEECH"} for item in entries)
    }
    derived_card_to_seat = {
        card: seat
        for seat in SEATS
        for card in deal["card_provenance"][seat]["derived_cards"]
    }
    student_suggestions: list[dict[str, Any]] = []
    for event in student_raw:
        seat = event["seat"]
        card = event.get("card")
        constraint = event.get("constraint")
        suggestion = {
            **event,
            "provenance_class": "STUDENT_SPEECH_SUGGESTION",
            "accepted_as_observation": False,
        }
        if card is not None:
            observed_seat = observed_card_to_seat.get(card)
            derived_seat = derived_card_to_seat.get(card)
            layout_match = layout_lookup.get((seat, card))
            if observed_seat == seat:
                resolution = "CONFIRMS_ACCEPTED_EVIDENCE"
            elif observed_seat is not None:
                resolution = "CONTRADICTS_ACCEPTED_EVIDENCE"
            elif derived_seat == seat:
                resolution = "CONSISTENT_WITH_DERIVED_DEAL"
            elif derived_seat is not None:
                resolution = "CONTRADICTS_DERIVED_DEAL"
            elif deal["hands"][seat]["unknown_count"] == 0:
                resolution = "CONTRADICTS_COMPLETE_HAND"
            elif layout_match is not None:
                resolution = "CORROBORATES_LAYOUT_SUGGESTION"
            else:
                resolution = "UNCONFIRMED_STUDENT_SUGGESTION"
            student_suggestions.append({**suggestion, "resolution": resolution})
            if layout_match is not None:
                speech_layout_corroborations.append({
                    "seat": seat,
                    "card": card,
                    "speech_source": "STUDENT_SPEECH_SUGGESTION",
                    "speech_evidence_locator": event["evidence_locator"],
                    "layout_index": layout_match["layout_index"],
                    "accepted_as_observation": False,
                    "speech_declaration_accepted_as_observation": False,
                    "layout_accepted_as_observation": False,
                })
            continue

        candidates = [
            candidate
            for candidate in deal["hands"][seat]["cards"]
            if (constraint.get("rank") is None or candidate[0] == constraint["rank"])
            and (constraint.get("suit") is None or candidate[1] == constraint["suit"])
        ]
        if len(candidates) == 1:
            resolution = "PARTIAL_UNIQUE_WITHIN_CURRENT_DEAL"
        elif not candidates and deal["hands"][seat]["unknown_count"] == 0:
            resolution = "PARTIAL_CONTRADICTS_COMPLETE_HAND"
        else:
            resolution = "PARTIAL_AMBIGUOUS"
        student_suggestions.append({
            **suggestion,
            "resolution": resolution,
            "candidate_cards": candidates[:13],
        })

    observed_card_evidence = [
        {"seat": seat, "card": card, "evidence": entries}
        for (seat, card), entries in sorted(evidence.items())
    ]
    return {
        "schema": FUSION_SCHEMA,
        "status": "REVIEW" if rejected or unresolved_partial or student_suggestions else "PASS",
        "deal": deal,
        "accepted_declarations": accepted,
        "student_speech_suggestions": student_suggestions,
        "speech_layout_corroborations": speech_layout_corroborations,
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
    "DEFAULT_MIN_SPEAKER_CONFIDENCE",
    "FUSION_SCHEMA",
    "MAX_DECLARATIONS",
    "MAX_LAYOUT_SUGGESTIONS",
    "CardEvidenceFusionError",
    "fuse_card_evidence",
]
