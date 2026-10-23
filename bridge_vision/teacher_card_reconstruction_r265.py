"""ASR-backed, provenance-preserving bridge deal reconstruction for r26.5.

The module deliberately runs *after* visual recognition.  Confident visual
cards are authoritative observations and are never sent through a speech
corroboration gate.  Attributed teacher speech may fill only cards that remain
unknown.  A deck complement is applied only after three disjoint hands are
complete, regardless of whether those hands were completed visually or by
accepted teacher declarations.

Language extraction is intentionally conservative.  It accepts an exact card
and exactly one seat in the same reliable teacher transcript segment.  It does
not infer cards from bidding, vague suit/rank references, or student speech.
"""
from __future__ import annotations

import math
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from bridge_contracts.video_deal_r264 import FULL_DECK, SEATS, canonicalize_video_deal
from bridge_vision.original_deal_solver import recover_original_deal

SCHEMA = "bridge-teacher-card-reconstruction-v1"
EXTRACTION_SCHEMA = "bridge-teacher-card-asr-extraction-v1"
MIN_TEACHER_ROLE_CONFIDENCE = 0.90
DEFAULT_DEAL_SPEECH_WINDOW_SECONDS = 120.0
MAX_TRANSCRIPT_SEGMENTS = 20_000
MAX_DECLARATIONS = 1_000

# Conservative vetoes, not a language understanding or factuality classifier.
# Whole-segment rejection also avoids combining a correction with its retraction.
_MODALITY_RULES = (
    ("QUOTED_OTHER_BOARD", r"\b(?:(?:last|earlier|next|prior)\s+(?:board|deal|hand)|(?:прошл\w*|предыдущ\w*|следующ\w*|друг\w*)\s+(?:сдач\w*|раздач\w*))\b"),
    ("HYPOTHETICAL", r"\b(?:unless|assuming|provided|допусти\w*|услови\w*)\b"),
    ("UNKNOWN", r"\b(?:think|guess|believe|дума\w*|кажет\w*|полага\w*|вероят\w*)\b"),
    ("QUOTED_OTHER_BOARD", r'[«»"“”]|\b(?:цитат\w*|цитир\w*|сказал\w*|говорил\w*|quote\w*|said|previous\s+board|another\s+board|на\s+(?:прошлой|другой|предыдущей)\s+сдаче)\b'),
    ("CORRECTION", r"\b(?:нет\s*,|поправ\w*|точнее|вернее|то\s+есть|sorry|rather|instead|actually)"),
    ("QUESTION", r"[?？]|\b(?:разве|ли|неужели|кто|где|does|did|whether|who|where)\b"),
    ("HYPOTHETICAL", r"\b(?:если|бы|например|представ\w*|предполож\w*|допуст\w*|if|would|could|suppose\w*|imagine\w*)\b"),
    ("NEGATION", r"\b(?:не|нет|без|ни|not|no|without|never)\b|\b\w+n['’]t\b"),
    ("UNKNOWN", r"\b(?:возможно|может|вероятно|наверное|или|might|maybe|perhaps|or)\b"),
)
SPEECH_SCOPE_FIELDS = ("source_video_sha256", "episode_id", "board_epoch", "board_number")


def classify_speech_modality(text: str) -> str:
    """Conservative RU/EN ownership grammar, not full NLP or truth validation."""
    if not isinstance(text, str) or not text.strip() or len(text) > 16000:
        return "UNKNOWN"
    for modality, expression in _MODALITY_RULES:
        if re.search(expression, text, re.I):
            return modality
    # Unsupported paraphrases, deictic owners and implicit seat carry stay unknown.
    if (re.search(r"\bу\s+(?:север\w*|восток\w*|юг\w*|запад\w*)\b", text, re.I)
            or re.search(r"\b(?:north|east|south|west)\s+(?:has|holds?)\b", text, re.I)):
        return "ASSERTION"
    return "UNKNOWN"


def declaration_digest(declaration: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(declaration), sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def reviewed_speech_declarations(declarations, *, scope, reviews):
    """Bind existing r265 candidates to separately reviewed audio/deal evidence.

    Neither a role confidence nor the 120-second window is an identity proof.
    Reviews are an independent caller-owned trust boundary, never ASR output.
    """
    if not isinstance(declarations, (list, tuple)) or len(declarations) > MAX_DECLARATIONS:
        raise ValueError("invalid bounded declarations")
    if not isinstance(reviews, (list, tuple)) or len(reviews) > MAX_DECLARATIONS:
        raise ValueError("invalid bounded speech reviews")
    accepted, rejected = [], []
    scope_valid = (isinstance(scope, Mapping)
                   and isinstance(scope.get("source_video_sha256"), str)
                   and re.fullmatch(r"[0-9a-f]{64}", scope["source_video_sha256"])
                   and isinstance(scope.get("episode_id"), str) and bool(scope["episode_id"])
                   and all(type(scope.get(k)) is int and scope[k] >= 1 for k in ("board_epoch", "board_number"))
                   and type(scope.get("deal_start_ms")) is int and type(scope.get("deal_end_ms")) is int
                   and 0 <= scope["deal_start_ms"] < scope["deal_end_ms"])
    for i, item in enumerate(declarations):
        reason = None
        if not isinstance(item, Mapping):
            rejected.append({"index": i, "reason": "INVALID_DECLARATION"})
            continue
        if (item.get("source") != "TEACHER_SPEECH" or item.get("speaker_role") != "TEACHER"
                or item.get("modality") != "ASSERTION"
                or classify_speech_modality(item.get("text")) != "ASSERTION"
                or not isinstance(item.get("card"), str) or item["card"] not in FULL_DECK
                or not isinstance(item.get("seat"), str) or item["seat"] not in SEATS
                or _segment_interval(item) is None):
            reason = "UNREVIEWABLE_SPEECH_CANDIDATE"
        try:
            digest = declaration_digest(item)
        except (TypeError, ValueError):
            digest = None
        matches = [review for review in reviews if isinstance(review, Mapping)
                   and digest is not None and review.get("declaration_sha256") == digest]
        if reason is None and (not scope_valid or len(matches) != 1):
            reason = "SPEECH_DEAL_BINDING_UNPROVEN"
        if reason is None:
            review = matches[0]
            if (any(type(review.get(k)) is not type(scope[k]) or review[k] != scope[k]
                    for k in SPEECH_SCOPE_FIELDS)
                    or not scope["deal_start_ms"] <= item["start"] * 1000 < item["end"] * 1000 <= scope["deal_end_ms"]
                    or review.get("modality") != "ACTUAL_HOLDING"
                    or any(not isinstance(review.get(k), str) or not review[k].strip()
                           for k in ("audio_review_ref", "speaker_review_ref", "evidence_ref"))):
                reason = "SPEECH_DEAL_BINDING_UNPROVEN"
        if reason:
            rejected.append({"index": i, "reason": reason, "declaration": dict(item)})
        else:
            accepted.append({**dict(item), "review_evidence": dict(matches[0])})
    return accepted, rejected

_RANK_PATTERNS: tuple[tuple[str, str], ...] = (
    ("A", r"(?:туз(?:а|ом|у)?|ace)"),
    ("K", r"(?:корол(?:ь|я|ём|ю)|king)"),
    ("Q", r"(?:дам(?:а|ы|у|ой)|queen)"),
    ("J", r"(?:валет(?:а|ом|у)?|jack)"),
    ("T", r"(?:десятк(?:а|и|у|ой)|ten|10)"),
    ("9", r"(?:девятк(?:а|и|у|ой)|nine)"),
    ("8", r"(?:восьм[ёе]рк(?:а|и|у|ой)|eight)"),
    ("7", r"(?:сем[ёе]рк(?:а|и|у|ой)|seven)"),
    ("6", r"(?:шест[ёе]рк(?:а|и|у|ой)|six)"),
    ("5", r"(?:пят[ёе]рк(?:а|и|у|ой)|five)"),
    ("4", r"(?:четв[ёе]рк(?:а|и|у|ой)|four)"),
    ("3", r"(?:тройк(?:а|и|у|ой)|three)"),
    ("2", r"(?:двойк(?:а|и|у|ой)|two)"),
)
_SUIT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("S", r"(?:пик(?:а|и|у|ой)?|spades?|♠)"),
    ("H", r"(?:черв(?:а|и|ей|у|ой)?|hearts?|♥)"),
    ("D", r"(?:буб(?:на|ны|ен|ну|ной)?|diamonds?|♦)"),
    ("C", r"(?:треф(?:а|ы|у|ой)?|clubs?|♣)"),
)
_SEAT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("N", re.compile(r"(?<!\w)(?:север(?:а|е|у|ом)?|north|N)(?!\w)", re.I)),
    ("E", re.compile(r"(?<!\w)(?:восток(?:а|е|у|ом)?|east|E)(?!\w)", re.I)),
    ("S", re.compile(r"(?<!\w)(?:юг(?:а|е|у|ом)?|south|S)(?!\w)", re.I)),
    ("W", re.compile(r"(?<!\w)(?:запад(?:а|е|у|ом)?|west|W)(?!\w)", re.I)),
)


def _compiled_card_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for rank, rank_pattern in _RANK_PATTERNS:
        for suit, suit_pattern in _SUIT_PATTERNS:
            patterns.append(
                (
                    rank + suit,
                    re.compile(
                        rf"(?<!\w)(?:{rank_pattern})\s*(?:[-–—]|of\s+)?\s*(?:{suit_pattern})(?!\w)",
                        re.I,
                    ),
                )
            )
            patterns.append(
                (
                    rank + suit,
                    re.compile(
                        rf"(?<!\w)(?:{suit_pattern})\s*(?:[-–—]|of\s+)?\s*(?:{rank_pattern})(?!\w)",
                        re.I,
                    ),
                )
            )
    return tuple(patterns)


_CARD_PATTERNS = _compiled_card_patterns()


def _probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _teacher_role(segment: Mapping[str, Any]) -> tuple[bool, float]:
    role = str(
        segment.get("speaker_role_candidate")
        or segment.get("speaker_role")
        or ""
    ).strip().casefold()
    confidence = _probability(segment.get("speaker_role_confidence"))
    return role == "teacher", confidence if confidence is not None else 0.0


def _segment_interval(segment: Mapping[str, Any]) -> tuple[float, float] | None:
    if any(type(segment.get(k)) not in (float, int) for k in ("start", "end")):
        return None
    try:
        start = float(segment.get("start"))
        end = float(segment.get("end"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        return None
    return start, end


def _seat_mentions(text: str) -> list[str]:
    return [seat for seat, pattern in _SEAT_PATTERNS if pattern.search(text)]


def _card_mentions(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for card, pattern in _CARD_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), card))
    ordered: list[str] = []
    for _, card in sorted(matches):
        if card not in ordered:
            ordered.append(card)
    return ordered


def extract_teacher_card_declarations(
    transcript_segments: Sequence[Mapping[str, Any]],
    *,
    min_role_confidence: float = MIN_TEACHER_ROLE_CONFIDENCE,
) -> dict[str, Any]:
    """Extract exact card+seat teacher facts from existing ASR text.

    The extractor never runs recognition itself; it consumes the normalized
    local-ASR/Zoom transcript produced by the existing audio stage.  Unreliable
    segments, unproved teacher roles, partial cards and multi-seat segments are
    retained as rejected evidence rather than guessed.
    """

    if isinstance(transcript_segments, (str, bytes)):
        raise TypeError("transcript segments must be a sequence of objects")
    if not 0.0 <= min_role_confidence <= 1.0:
        raise ValueError("min role confidence outside [0,1]")

    declarations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, raw in enumerate(transcript_segments):
        if index >= MAX_TRANSCRIPT_SEGMENTS:
            rejected.append({"segment": index, "reason": "SEGMENT_LIMIT_REACHED"})
            break
        if not isinstance(raw, Mapping):
            rejected.append({"segment": index, "reason": "INVALID_SEGMENT"})
            continue
        text = str(raw.get("text") or "").strip()
        cards = _card_mentions(text)
        seats = _seat_mentions(text)
        if not cards:
            continue
        locator = f"transcript.jsonl#segment={index}"
        if bool(raw.get("unreliable")):
            rejected.append({"segment": index, "reason": "UNRELIABLE_ASR", "evidence_locator": locator})
            continue
        modality = classify_speech_modality(text)
        if modality != "ASSERTION":
            rejected.append({"segment": index, "reason": "NON_ASSERTIVE_OR_CORRECTED_SPEECH",
                             "modality": modality, "evidence_locator": locator, "text": text})
            continue
        is_teacher, role_confidence = _teacher_role(raw)
        if not is_teacher or role_confidence < min_role_confidence:
            rejected.append({
                "segment": index,
                "reason": "TEACHER_ROLE_NOT_PROVED",
                "speaker_role_confidence": role_confidence,
                "evidence_locator": locator,
            })
            continue
        interval = _segment_interval(raw)
        if interval is None:
            rejected.append({"segment": index, "reason": "INVALID_TIMELINE", "evidence_locator": locator})
            continue
        if len(seats) != 1:
            rejected.append({
                "segment": index,
                "reason": "AMBIGUOUS_SEAT",
                "seat_candidates": seats,
                "evidence_locator": locator,
            })
            continue
        start, end = interval
        for card in cards:
            if len(declarations) >= MAX_DECLARATIONS:
                rejected.append({"segment": index, "reason": "DECLARATION_LIMIT_REACHED"})
                break
            declarations.append({
                "card": card,
                "seat": seats[0],
                "source": "TEACHER_SPEECH",
                "text": text,
                "modality": "ASSERTION",
                "evidence_status": "CANDIDATE_REQUIRES_INDEPENDENT_BINDING",
                "confidence": round(role_confidence, 4),
                "speaker_role": "TEACHER",
                "speaker_id": str(raw.get("speaker") or "ANONYMOUS_TEACHER"),
                "speaker_assignment_confidence": round(role_confidence, 4),
                "evidence_locator": locator,
                "segment_id": raw.get("segment_id"),
                "transcript_source": raw.get("source"),
                "timestamp_unit": "seconds",
                "start": start,
                "end": end,
                "accepted_as_visual_observation": False,
            })
    return {
        "schema": EXTRACTION_SCHEMA,
        "status": "EXTRACTED" if declarations else "NO_EXACT_TEACHER_CARD_FACTS",
        "transcript_segments": min(len(transcript_segments), MAX_TRANSCRIPT_SEGMENTS),
        "declarations": declarations,
        "rejected": rejected,
        "asr_text_consumed": True,
        "visual_cards_checked_against_speech": False,
    }


def _card_sort_key(card: str) -> tuple[int, int]:
    suits = {"S": 0, "H": 1, "D": 2, "C": 3}
    ranks = {rank: index for index, rank in enumerate("AKQJT98765432")}
    return suits[card[1]], ranks[card[0]]


def _cards_to_suits(cards: Iterable[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for card in sorted(cards, key=_card_sort_key):
        result.setdefault(card[1], []).append(card[0])
    return result


def _timeline_compatible(
    declaration: Mapping[str, Any],
    deal_timestamp_seconds: float | None,
    window_seconds: float,
) -> bool:
    if (type(deal_timestamp_seconds) not in (float, int)
            or not math.isfinite(deal_timestamp_seconds) or _segment_interval(declaration) is None):
        return False
    start = float(declaration["start"])
    end = float(declaration["end"])
    return start - window_seconds <= deal_timestamp_seconds <= end + window_seconds


def reconstruct_deal(
    visual_hands: Mapping[str, Any],
    declarations: Iterable[Mapping[str, Any]],
    *,
    deal_timestamp_seconds: float | None,
    speech_window_seconds: float = DEFAULT_DEAL_SPEECH_WINDOW_SECONDS,
    deal_identity: Mapping[str, Any] | None = None,
    speech_reviews: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Fill unknown cards without ever rechecking confident visual cards."""

    if speech_window_seconds <= 0 or not math.isfinite(float(speech_window_seconds)):
        raise ValueError("speech window must be a finite positive number")
    visual = canonicalize_video_deal({"hands": visual_hands}).to_dict()
    hands = {
        seat: set(visual["hands"][seat]["cards"])
        for seat in SEATS
    }
    visual_card_to_seat = {
        card: seat for seat in SEATS for card in hands[seat]
    }
    provenance: dict[tuple[str, str], str] = {
        (seat, card): "VISUAL" for card, seat in visual_card_to_seat.items()
    }
    accepted: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    speech_card_to_seat: dict[str, str] = {}

    bounded = []
    for index, declaration in enumerate(declarations):
        if index >= MAX_DECLARATIONS:
            rejected.append({"index": index, "reason": "DECLARATION_LIMIT_REACHED"})
            break
        bounded.append(declaration)
    # Keep visual-authoritative behavior, including the ignored-speech receipt.
    candidates = []
    for index, raw in enumerate(bounded):
        if isinstance(raw, Mapping) and isinstance(raw.get("card"), str) and raw["card"] in visual_card_to_seat:
            ignored.append({"index": index, "card": raw["card"], "declared_seat": raw.get("seat"),
                            "visual_seat": visual_card_to_seat[raw["card"]],
                            "reason": "VISUAL_CARD_ALREADY_RESOLVED", "speech_checked_visual": False})
        elif isinstance(raw, Mapping) and not _timeline_compatible(raw, deal_timestamp_seconds, float(speech_window_seconds)):
            rejected.append({"index": index, "reason": "OUTSIDE_DEAL_WINDOW"})
        else:
            candidates.append(raw)
    if not (isinstance(deal_identity, Mapping)
            and type(deal_identity.get("deal_start_ms")) is int and type(deal_identity.get("deal_end_ms")) is int
            and type(deal_timestamp_seconds) in (int, float) and math.isfinite(deal_timestamp_seconds)
            and deal_identity["deal_start_ms"] <= deal_timestamp_seconds * 1000 < deal_identity["deal_end_ms"]):
        deal_identity = None
    declarations, unbound = reviewed_speech_declarations(candidates, scope=deal_identity, reviews=speech_reviews)
    rejected.extend(unbound)

    for index, raw in enumerate(declarations):
        if index >= MAX_DECLARATIONS:
            rejected.append({"index": index, "reason": "DECLARATION_LIMIT_REACHED"})
            break
        if not isinstance(raw, Mapping):
            rejected.append({"index": index, "reason": "INVALID_DECLARATION"})
            continue
        card = str(raw.get("card") or "").upper()
        seat = str(raw.get("seat") or "").upper()
        if card not in FULL_DECK or seat not in SEATS or raw.get("source") != "TEACHER_SPEECH":
            rejected.append({"index": index, "reason": "UNSUPPORTED_DECLARATION"})
            continue
        if not _timeline_compatible(raw, deal_timestamp_seconds, float(speech_window_seconds)):
            rejected.append({
                "index": index,
                "card": card,
                "seat": seat,
                "reason": "OUTSIDE_DEAL_WINDOW",
                "evidence_locator": raw.get("evidence_locator"),
            })
            continue

        # This is the director-approved precedence rule: once the visual path
        # has recognized a card, speech is neither corroboration nor conflict.
        # It is ignored for reconstruction and cannot alter visual confidence.
        visual_seat = visual_card_to_seat.get(card)
        if visual_seat is not None:
            ignored.append({
                "index": index,
                "card": card,
                "declared_seat": seat,
                "visual_seat": visual_seat,
                "reason": "VISUAL_CARD_ALREADY_RESOLVED",
                "speech_checked_visual": False,
            })
            continue
        if len(hands[seat]) == 13:
            if len(visual["hands"][seat]["cards"]) == 13:
                ignored.append({
                    "index": index,
                    "card": card,
                    "declared_seat": seat,
                    "reason": "VISUAL_HAND_ALREADY_COMPLETE",
                    "speech_checked_visual": False,
                })
            else:
                conflicts.append({
                    "card": card,
                    "declared_seat": seat,
                    "reason": "TEACHER_DECLARATION_EXCEEDS_HAND_CAPACITY",
                })
            continue
        previous_seat = speech_card_to_seat.get(card)
        if previous_seat is not None and previous_seat != seat:
            conflicts.append({
                "card": card,
                "first_speech_seat": previous_seat,
                "second_speech_seat": seat,
                "reason": "CONFLICTING_TEACHER_DECLARATIONS",
            })
            continue
        if card in hands[seat]:
            ignored.append({"index": index, "card": card, "declared_seat": seat, "reason": "DUPLICATE_SPEECH_FACT"})
            continue
        speech_card_to_seat[card] = seat
        hands[seat].add(card)
        provenance[(seat, card)] = "TEACHER_SPEECH"
        accepted.append({
            "index": index,
            "card": card,
            "seat": seat,
            "source": "TEACHER_SPEECH",
            "evidence_locator": raw.get("evidence_locator"),
            "accepted_as_visual_observation": False,
            "review_evidence": raw["review_evidence"],
        })

    if conflicts:
        return {
            "schema": SCHEMA,
            "status": "UNRESOLVED_CONFLICT",
            "deal": None,
            "accepted_speech_cards": accepted,
            "ignored_speech_cards": ignored,
            "rejected_speech_cards": rejected,
            "conflicts": conflicts,
            "visual_cards_checked_against_speech": False,
            "canonical_promotion_allowed": False,
        }

    # The same exact solver serves r265 post-visual fusion and temporal replay.
    # These low-level supplied hands remain premises, not certified pixel truth.
    facts = [{"card": card, "seat": seat, "evidence_id": f"visual:{seat}:{card}"}
             for card, seat in visual_card_to_seat.items()]
    facts.extend({"card": item["card"], "seat": item["seat"],
                  "evidence_id": item["review_evidence"]["evidence_ref"], "source": "TEACHER_SPEECH"}
                 for item in accepted)
    recovery = recover_original_deal(facts)
    complete = [seat for seat in SEATS if len(hands[seat]) == 13]
    derived: list[dict[str, Any]] = []
    if recovery["status"] == "CONFLICT":
        conflicts.extend(recovery["conflicts"])
    else:
        for seat in SEATS:
            newly_derived = {card for card, proof in recovery["inferred"].items() if proof["seat"] == seat}
            if not newly_derived:
                continue
            known_missing = set(hands[seat])
            hands[seat].update(newly_derived)
            for card in newly_derived:
                provenance[(seat, card)] = "INFERRED_DECK_COMPLEMENT"
            derived.append({"type": "DECK_COMPLEMENT_AFTER_EVIDENCE_FUSION", "seat": seat,
                            "source_seats": complete,
                            "known_cards_before_complement": sorted(known_missing, key=_card_sort_key),
                            "computed_cards": sorted(newly_derived, key=_card_sort_key),
                            "provenance": "INFERRED_DECK_COMPLEMENT",
                            "inferred_provenance": {card: recovery["inferred"][card] for card in sorted(newly_derived)},
                            "constraints": recovery["constraints"]})

    if conflicts:
        return {
            "schema": SCHEMA,
            "status": "UNRESOLVED_CONFLICT",
            "deal": None,
            "accepted_speech_cards": accepted,
            "ignored_speech_cards": ignored,
            "rejected_speech_cards": rejected,
            "conflicts": conflicts,
            "visual_cards_checked_against_speech": False,
            "canonical_promotion_allowed": False,
        }

    canonical = canonicalize_video_deal(
        {"hands": {seat: sorted(hands[seat], key=_card_sort_key) for seat in SEATS}}
    ).to_dict()
    counts = {seat: len(hands[seat]) for seat in SEATS}
    full = all(count == 13 for count in counts.values())
    canonical["card_provenance"] = {
        seat: {
            source: [
                card for card in canonical["hands"][seat]["cards"]
                if provenance.get((seat, card)) == source
            ]
            for source in ("VISUAL", "TEACHER_SPEECH", "INFERRED_DECK_COMPLEMENT")
        }
        for seat in SEATS
    }
    canonical["derivations"] = derived
    return {
        "schema": SCHEMA,
        "status": "RECONSTRUCTED_FULL" if full else "PARTIAL_WITH_EVIDENCE",
        "deal": canonical,
        "hands_by_suit": {seat: _cards_to_suits(hands[seat]) for seat in SEATS},
        "seat_counts": counts,
        "unique_cards": len(set().union(*hands.values())),
        "recovery": recovery,
        "verification": "CONDITIONAL_ON_SUPPLIED_EVIDENCE",
        "accepted_speech_cards": accepted,
        "ignored_speech_cards": ignored,
        "rejected_speech_cards": rejected,
        "conflicts": [],
        "visual_cards_checked_against_speech": False,
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "DEFAULT_DEAL_SPEECH_WINDOW_SECONDS",
    "EXTRACTION_SCHEMA",
    "MIN_TEACHER_ROLE_CONFIDENCE",
    "SCHEMA",
    "extract_teacher_card_declarations",
    "reconstruct_deal",
]
